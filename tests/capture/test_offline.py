import cv2
import numpy as np
import pytest

from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.device import DeviceSource
from autoplay.core.capture.factory import open_source
from autoplay.core.capture.offline import ImageDirSource, VideoFileSource

FPS = 20.0
STEP = 20  # grey level per frame; far apart enough to survive lossy video


def _image(index: int) -> np.ndarray:
    return np.full((48, 64, 3), index * STEP, np.uint8)


def _index_of(bgr: np.ndarray) -> int:
    return round(float(bgr.mean()) / STEP)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (64, 48))
    assert writer.isOpened()
    for i in range(10):
        writer.write(_image(i))
    writer.release()
    return path


@pytest.fixture
def image_dir(tmp_path):
    path = tmp_path / "frames"
    path.mkdir()
    # Written out of order: the source must sort by name.
    for i in (3, 0, 4, 1, 2):
        assert cv2.imwrite(str(path / f"{i:03d}.png"), _image(i))
    (path / "notes.txt").write_text("not an image")
    return path


def _drain(source):
    frames = []
    seq = 0
    while (frame := source.wait_next(seq, 0.0)) is not None:
        frames.append(frame)
        seq = frame.seq
    return frames


def test_video_frames_come_in_order_with_media_ts(video):
    with VideoFileSource(video) as source:
        source.start()
        frames = _drain(source)
    assert [f.seq for f in frames] == list(range(1, 11))
    assert [_index_of(f.bgr) for f in frames] == list(range(10))
    for i, frame in enumerate(frames):
        assert frame.ts == pytest.approx(i / FPS)


def test_video_wait_next_returns_none_at_end(video):
    with VideoFileSource(video) as source:
        source.start()
        assert len(_drain(source)) == 10
        assert source.wait_next(10, 0.0) is None
        assert source.wait_next(0, 0.0) is None
        assert source.latest().seq == 10


def test_latest_decodes_the_first_frame_once(video):
    with VideoFileSource(video) as source:
        source.start()
        first = source.latest()
        assert first.seq == 1
        assert source.latest() is first
        assert source.wait_next(first.seq, 0.0).seq == 2


def test_wait_next_skips_to_a_frame_after_the_given_seq(image_dir):
    with ImageDirSource(image_dir) as source:
        source.start()
        assert source.wait_next(3, 0.0).seq == 4


def test_image_dir_sorted_by_name(image_dir):
    with ImageDirSource(image_dir, fps=10.0) as source:
        source.start()
        frames = _drain(source)
    assert [_index_of(f.bgr) for f in frames] == [0, 1, 2, 3, 4]
    assert [f.ts for f in frames] == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])


@pytest.mark.parametrize("kind", ["video", "dir"])
def test_loop_restarts(kind, video, image_dir):
    source = (
        VideoFileSource(video, loop=True)
        if kind == "video"
        else ImageDirSource(image_dir, fps=FPS, loop=True)
    )
    length = 10 if kind == "video" else 5
    with source:
        source.start()
        frames = [source.wait_next(i, 0.0) for i in range(length + 2)]
    assert [_index_of(f.bgr) for f in frames[length:]] == [0, 1]
    # seq and ts keep rising across the restart.
    assert [f.seq for f in frames] == list(range(1, length + 3))
    assert frames[length].ts == pytest.approx(length / FPS)


def test_stats_report_size_and_are_never_stale(image_dir):
    with ImageDirSource(image_dir) as source:
        source.start()
        _drain(source)
        stats = source.stats()
    assert (stats.width, stats.height, stats.frames) == (64, 48, 5)
    assert stats.fps == 30.0
    assert not stats.stale


def test_open_source_dispatch(video, image_dir):
    assert isinstance(open_source(f"video:{video}"), VideoFileSource)
    assert isinstance(open_source(f"dir:{image_dir}"), ImageDirSource)
    assert isinstance(open_source("device:2"), DeviceSource)
    assert open_source(f"dir:{image_dir}", fps=5.0, loop=True).stats().fps == 5.0
    # A Windows drive letter holds a second colon.
    assert str(open_source("video:C:\\clips\\a.avi").path) == "C:\\clips\\a.avi"


@pytest.mark.parametrize(
    "spec", ["bogus:1", "device:", "device:x", "device:-1", "video:", "dir", "0", ""]
)
def test_open_source_rejects_other_specs(spec):
    with pytest.raises(ValueError, match="unsupported source"):
        open_source(spec)


def test_missing_path_raises_capture_error(tmp_path):
    with pytest.raises(CaptureError, match="not found"):
        VideoFileSource(tmp_path / "none.avi").start()
    with pytest.raises(CaptureError, match="not found"):
        ImageDirSource(tmp_path / "none").start()


def test_unreadable_media_raise_capture_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CaptureError, match="no image files"):
        ImageDirSource(tmp_path / "empty").start()
    bad = tmp_path / "bad.avi"
    bad.write_bytes(b"not a video")
    with pytest.raises(CaptureError, match="cannot open"):
        VideoFileSource(bad).start()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "a.png").write_bytes(b"not a png")
    source = ImageDirSource(broken)
    source.start()
    with pytest.raises(CaptureError, match="a.png"):
        source.latest()


def test_reading_before_start_is_an_error(image_dir):
    with pytest.raises(RuntimeError, match="not started"):
        ImageDirSource(image_dir).latest()
