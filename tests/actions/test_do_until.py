import json

import cv2
import numpy as np
import pytest

from autoplay.core.actions.do_until import ActionFailed, do_until, wait_until
from autoplay.core.capture.base import CaptureStats, Frame, FrameSource
from autoplay.core.capture.offline import ImageDirSource
from autoplay.core.controller.dummy import DummyController
from autoplay.core.runtime.runlog import RunLog

FPS = 60.0


def bright(frame) -> bool:
    return float(frame.bgr.mean()) > 128


@pytest.fixture
def play(tmp_path):
    """play("0011") -> a started 60 fps source with one frame per character:
    '1' is a frame the `bright` predicate accepts."""
    sources = []

    def make(pattern: str) -> ImageDirSource:
        folder = tmp_path / f"seq{len(sources)}"
        folder.mkdir()
        for i, char in enumerate(pattern):
            img = np.full((36, 64, 3), 255 if char == "1" else 0, np.uint8)
            # The frame number, so a saved frame can be told from the others.
            img[0, 0] = (i, i, i)
            assert cv2.imwrite(str(folder / f"{i:04d}.png"), img)
        source = ImageDirSource(folder, fps=FPS)
        source.start()
        sources.append(source)
        return source

    yield make
    for source in sources:
        source.close()


class Presser:
    def __init__(self):
        self.pad = DummyController()
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.pad.press("A", 16)


def test_returns_frame_when_predicate_true_after_action(play):
    press = Presser()
    frame = do_until(press, bright, source=play("000" + "1" * 10))
    assert press.calls == 1
    assert len(press.pad.log) >= 1
    assert bright(frame)


def test_ignores_frames_older_than_action(play):
    press = Presser()
    with pytest.raises(ActionFailed) as info:
        do_until(press, bright, source=play("1" + "0" * 20), timeout=0.1, retries=0)
    assert info.value.reason == "timeout"
    assert press.calls == 1


def test_single_transient_match_does_not_count(play):
    source = play("00100" + "1" * 10)
    frame = do_until(Presser(), bright, source=source)
    # The lone match is frame seq 3; the stable span begins at seq 6.
    assert frame.seq == 12


def test_returns_last_frame_of_stable_span(play):
    source = play("0000" + "1" * 20)
    frame = do_until(Presser(), bright, source=source, stable_ms=100)
    # The span starts at seq 5; six frame periods later it covers 100 ms.
    assert frame.seq == 11
    assert frame.ts - 4 / FPS == pytest.approx(0.1)


def test_stable_ms_zero_returns_first_match(play):
    frame = do_until(Presser(), bright, source=play("00100"), stable_ms=0)
    assert frame.seq == 3


def test_retries_action_on_timeout(play):
    press = Presser()
    frame = do_until(
        press, bright, source=play("0" * 12 + "1" * 10), timeout=0.1, stable_ms=0
    )
    assert press.calls == 2
    assert frame.seq == 13


def test_a_span_holding_at_the_time_limit_is_not_cut_off(play):
    press = Presser()
    # The limit (0.1 s after seq 2) falls on seq 8, in the middle of the span.
    frame = do_until(press, bright, source=play("0" * 5 + "1" * 12), timeout=0.1)
    assert press.calls == 1
    assert frame.seq == 12


def test_retries_then_raises_with_dump(play, tmp_path):
    press = Presser()
    log = RunLog("test", root=tmp_path / "runs")
    with pytest.raises(ActionFailed) as info:
        do_until(
            press,
            bright,
            source=play("0" * 60),
            timeout=0.1,
            retries=2,
            name="open menu",
            runlog=log,
            keep_frames=5,
        )
    log.close()
    failure = info.value
    assert (failure.name, failure.reason, failure.attempts) == (
        "open menu",
        "timeout",
        3,
    )
    assert press.calls == 3
    assert "open menu" in str(failure)
    assert failure.dump_dir == log.dir
    names = sorted(p.name for p in log.dir.glob("*.png"))
    assert names == [
        "0001-open_menu-before.png",
        "0002-open_menu-last1.png",
        "0003-open_menu-last2.png",
        "0004-open_menu-last3.png",
        "0005-open_menu-last4.png",
        "0006-open_menu-last5.png",
    ]
    # Three attempts of 7 frames each after the frame from before the action.
    numbers = [int(cv2.imread(str(log.dir / name))[0, 0, 0]) for name in names]
    assert numbers == [0, 17, 18, 19, 20, 21]
    lines = (log.dir / "events.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in lines]
    assert len(events) == 1
    assert events[0]["kind"] == "action_failed"
    assert events[0]["reason"] == "timeout"
    assert events[0]["attempts"] == 3
    assert events[0]["frames"] == names


def test_failure_without_a_runlog_has_no_dump(play):
    with pytest.raises(ActionFailed) as info:
        do_until(Presser(), bright, source=play("0" * 30), timeout=0.1, retries=0)
    assert info.value.dump_dir is None
    assert info.value.attempts == 1


def test_source_ended_reason(play):
    press = Presser()
    with pytest.raises(ActionFailed) as info:
        do_until(press, bright, source=play("0000"), timeout=5.0)
    assert info.value.reason == "source ended"
    # Nothing can be confirmed any more, so the action is not repeated blind.
    assert press.calls == 1


class DeadCapture(FrameSource):
    """A live source that has stopped delivering frames."""

    def start(self):
        pass

    def latest(self):
        return None

    def wait_next(self, after_seq, timeout):
        return None

    def stats(self):
        return CaptureStats(1920, 1080, "YUY2", 0.0, 0, stale=True)

    def close(self):
        pass


def test_capture_stale_reason():
    press = Presser()
    with pytest.raises(ActionFailed) as info:
        do_until(press, bright, source=DeadCapture())
    assert info.value.reason == "capture stale"
    assert press.calls == 1
    assert wait_until(bright, source=DeadCapture(), timeout=1.0) is None


def test_action_exception_propagates_without_retry(play):
    calls = []

    def broken():
        calls.append(1)
        raise OSError("port gone")

    with pytest.raises(OSError, match="port gone"):
        do_until(broken, bright, source=play("1" * 20))
    assert len(calls) == 1


def test_heartbeat_runs_for_every_frame_and_its_error_propagates(play):
    beats = []
    do_until(
        Presser(),
        bright,
        source=play("0" * 6 + "1"),
        stable_ms=0,
        heartbeat=lambda: beats.append(1),
    )
    assert len(beats) == 6

    def stop():
        raise RuntimeError("emergency stop")

    press = Presser()
    with pytest.raises(RuntimeError, match="emergency stop"):
        do_until(press, bright, source=play("0" * 30), heartbeat=stop)
    assert press.calls == 1


def test_negative_retries_are_refused(play):
    with pytest.raises(ValueError, match="retries"):
        do_until(Presser(), bright, source=play("1"), retries=-1)


def test_wait_until_returns_none_on_timeout(play):
    assert wait_until(bright, source=play("0" * 30), timeout=0.1) is None
    source = play("0" * 30)
    assert wait_until(bright, source=source, timeout=0.1) is None
    # 0.1 s of frames were looked at, no more.
    assert source.latest().seq == 7


def test_wait_until_counts_the_current_frame(play):
    frame = wait_until(bright, source=play("1" * 10 + "0" * 5), timeout=1.0)
    assert frame.seq == 7
    assert wait_until(bright, source=play("1000"), timeout=1.0, stable_ms=0).seq == 1


def test_wait_until_returns_none_when_the_source_ends(play):
    assert wait_until(bright, source=play("0011"), timeout=5.0) is None


class LiveSource(FrameSource):
    """Behaves like a capture device: `latest()` is the newest frame and
    `wait_next()` returns it again for as long as it is newer than asked for.
    One new frame "arrives" whenever the caller has caught up."""

    def __init__(self, pattern: str) -> None:
        self._frames = [
            Frame(np.full((4, 4, 3), 255 if c == "1" else 0, np.uint8), i / FPS, i + 1)
            for i, c in enumerate(pattern)
        ]
        self._at = 0
        self.waits = 0

    def start(self):
        pass

    def latest(self):
        return self._frames[self._at]

    def wait_next(self, after_seq, timeout):
        self.waits += 1
        assert self.waits < 500, "the same frame is being asked for again and again"
        if self._frames[self._at].seq <= after_seq and self._at + 1 < len(self._frames):
            self._at += 1
        frame = self._frames[self._at]
        return frame if frame.seq > after_seq else None

    def stats(self):
        return CaptureStats(4, 4, "YUY2", FPS, self._at + 1, stale=True)

    def close(self):
        pass


def test_a_live_source_never_gets_its_old_frame_counted():
    # The frame on screen before the action matches; it must not be the answer.
    source = LiveSource("1" + "0" * 5 + "1" * 10)
    frame = do_until(Presser(), bright, source=source, stable_ms=0)
    assert frame.seq == 7


def test_each_live_frame_is_looked_at_once():
    seen = []

    def record(frame):
        seen.append(frame.seq)
        return bright(frame)

    source = LiveSource("0" * 4 + "1" * 10)
    assert do_until(Presser(), record, source=source).seq == 11
    assert seen == list(range(2, 12))
    # When the frames stop coming the capture is stale, and that is the reason.
    with pytest.raises(ActionFailed) as info:
        do_until(Presser(), bright, source=LiveSource("0" * 8), timeout=10.0)
    assert info.value.reason == "capture stale"
