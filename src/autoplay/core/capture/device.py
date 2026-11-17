import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Protocol

import av
import av.logging
import numpy as np

from autoplay.core import clock
from autoplay.core.capture.base import CaptureError, CaptureStats, Frame, FrameSource

# Uncompressed formats only: capture never goes through a codec.
FOURCC_PIXEL_FORMATS = {"YUY2": "yuyv422", "NV12": "nv12", "UYVY": "uyvy422"}

_INPUT_FORMAT = "dshow"
_FIRST_FRAME_TIMEOUT_S = 2.0
_STALE_AFTER_S = 0.5
_FPS_WINDOW_S = 1.0
_READ_FAIL_BACKOFF_S = 0.005
_JOIN_TIMEOUT_S = 2.0
_READ_POLL_S = 0.001
_READ_TIMEOUT_S = 0.1
_DRAIN_TIMEOUT_S = 1.0
# The input queues frames the reader has not taken yet and, once full, drops the
# newest ones. A queue of a few frames bounds how stale a frame can get after
# the reader stalls. 4 bytes per pixel is an upper bound for every format above.
_QUEUE_FRAMES = 4
_DEVICE_LINE = re.compile(r'^"(?P<name>.+)" \((?P<kinds>[^)]*)\)$')

_log = logging.getLogger(__name__)


class Capture(Protocol):
    """An open capture device, as returned by an opener."""

    fourcc: str

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Return `(True, bgr)` for the next frame, or `(False, None)` if none
        arrived within about 0.1 s."""

    def release(self) -> None: ...


Opener = Callable[[int, Mapping[str, str]], Capture]


def parse_device_list(text: str) -> list[str]:
    """Extract the video device names, in order, from the device list the
    DirectShow input logs."""
    names = []
    for line in text.splitlines():
        match = _DEVICE_LINE.match(line.strip())
        if match and "video" in match["kinds"]:
            names.append(match["name"])
    return names


def list_devices() -> list[str]:
    """Return the names of the DirectShow video capture devices; a device's
    index is its position in this list."""
    if _INPUT_FORMAT not in av.formats_available:
        raise CaptureError("capture devices are only available on Windows")
    # The input offers its device list only as log output: the built-in
    # enumeration fails on a machine with no audio-only device.
    av.logging.set_level(av.logging.INFO)
    try:
        with av.logging.Capture() as logs:
            try:
                av.open("dummy", format=_INPUT_FORMAT, options={"list_devices": "true"})
            except av.error.ExitError:
                pass
    finally:
        av.logging.set_level(None)
    return parse_device_list("".join(message for _, _, message in logs))


class AvCapture:
    """A capture input read through PyAV without blocking, so a source that
    stops delivering frames cannot hang the reader."""

    def __init__(self, url: str, input_format: str, options: Mapping[str, str]) -> None:
        self._container = av.open(url, format=input_format, options=dict(options))
        try:
            self._stream = self._container.streams.video[0]
            self._container.flags |= av.container.Flags.non_block.value
            self.fourcc = self._stream.codec_context.codec_tag
        except BaseException:
            self._container.close()
            raise

    def _next_frame(self) -> np.ndarray | None:
        try:
            packet = next(self._container.demux(self._stream))
        except (av.error.BlockingIOError, StopIteration):
            return None
        for frame in packet.decode():
            return frame.to_ndarray(format="bgr24")
        return None

    def drain(self) -> None:
        """Discard the frames queued while the input was opening, so the next
        frame read is a fresh one."""
        deadline = clock.now() + _DRAIN_TIMEOUT_S
        while self._next_frame() is not None and clock.now() < deadline:
            pass

    def read(self) -> tuple[bool, np.ndarray | None]:
        deadline = clock.now() + _READ_TIMEOUT_S
        while True:
            img = self._next_frame()
            if img is not None:
                return True, img
            if clock.now() >= deadline:
                return False, None
            time.sleep(_READ_POLL_S)

    def release(self) -> None:
        self._container.close()


def _open_device(index: int, options: Mapping[str, str]) -> Capture:
    names = list_devices()
    if not 0 <= index < len(names):
        found = ", ".join(f'{i}: "{name}"' for i, name in enumerate(names)) or "none"
        raise CaptureError(f"no capture device index {index}; found {found}")
    cap = AvCapture(f"video={names[index]}", _INPUT_FORMAT, options)
    try:
        cap.drain()
    except BaseException:
        cap.release()
        raise
    return cap


def _check_fourcc(fourcc: str) -> None:
    if fourcc not in FOURCC_PIXEL_FORMATS:
        raise ValueError(
            f"unknown fourcc {fourcc!r}; expected one of {sorted(FOURCC_PIXEL_FORMATS)}"
        )


def open_capture(
    index: int,
    *,
    width: int,
    height: int,
    fps: int,
    fourcc: str,
    opener: Opener | None = None,
) -> Capture:
    """Open capture device `index` in the given uncompressed mode; raise
    `CaptureError` naming the index if it cannot be opened in that mode.
    `opener(index, options)` replaces the real device in tests."""
    _check_fourcc(fourcc)
    options = {
        "video_size": f"{width}x{height}",
        "framerate": str(fps),
        "pixel_format": FOURCC_PIXEL_FORMATS[fourcc],
        "rtbufsize": str(width * height * 4 * _QUEUE_FRAMES),
    }
    try:
        return (opener or _open_device)(index, options)
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(
            f"cannot open capture device index {index} as {width}x{height} "
            f"{fourcc} at {fps} fps: {exc}"
        ) from exc


class DeviceSource(FrameSource):
    """A capture device read by a background thread that keeps only the newest
    frame. `opener(index, options)` replaces the real device in tests.

    A source is single-use: a second `start()`, including one after `close()`,
    raises RuntimeError; create a new source instead."""

    def __init__(
        self,
        index: int = 0,
        *,
        width: int = 1920,
        height: int = 1080,
        fps: int = 60,
        fourcc: str = "YUY2",
        opener: Opener | None = None,
    ) -> None:
        _check_fourcc(fourcc)
        self._index = index
        self._config = {"width": width, "height": height, "fps": fps, "fourcc": fourcc}
        self._opener = opener
        self._started = False
        self._closed = False
        self._cap: Capture | None = None
        self._fourcc = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._frames = 0
        self._recent: deque[float] = deque()

    def start(self) -> None:
        if self._started:
            raise RuntimeError(
                "DeviceSource is single-use; create a new one to restart"
            )
        self._started = True
        self._cap = open_capture(self._index, opener=self._opener, **self._config)
        try:
            self._fourcc = self._cap.fourcc
            thread = threading.Thread(
                target=self._run,
                args=(self._cap,),
                name=f"capture-device-{self._index}",
                daemon=True,
            )
            thread.start()
        except BaseException:
            # No reader thread exists yet, so nothing else would release the device.
            self._cap.release()
            self._cap = None
            raise
        self._thread = thread
        with self._cond:
            got = self._cond.wait_for(
                lambda: self._latest is not None, _FIRST_FRAME_TIMEOUT_S
            )
        if not got:
            self.close()
            raise CaptureError(
                f"no frame from capture device index {self._index} within "
                f"{_FIRST_FRAME_TIMEOUT_S} s; check that the input has a signal"
            )

    # The reader thread owns release(): releasing from another thread while
    # read() is in flight can crash the process inside the driver.
    def _run(self, cap: Capture) -> None:
        try:
            while not self._stop.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    time.sleep(_READ_FAIL_BACKOFF_S)
                    continue
                # read() returns once the frame is delivered, so ts is an upper bound on grab time.
                ts = clock.now()
                with self._cond:
                    self._frames += 1
                    self._latest = Frame(bgr=img, ts=ts, seq=self._frames)
                    self._recent.append(ts)
                    self._prune(ts)
                    self._cond.notify_all()
        finally:
            cap.release()

    def _prune(self, now: float) -> None:
        while self._recent and now - self._recent[0] > _FPS_WINDOW_S:
            self._recent.popleft()

    def latest(self) -> Frame | None:
        with self._cond:
            return self._latest

    def wait_next(self, after_seq: int, timeout: float) -> Frame | None:
        def ready() -> bool:
            return self._stop.is_set() or (
                self._latest is not None and self._latest.seq > after_seq
            )

        with self._cond:
            self._cond.wait_for(ready, timeout)
            if self._latest is not None and self._latest.seq > after_seq:
                return self._latest
            return None

    def stats(self) -> CaptureStats:
        now = clock.now()
        with self._cond:
            self._prune(now)
            frame = self._latest
            height, width = frame.bgr.shape[:2] if frame is not None else (0, 0)
            return CaptureStats(
                width=width,
                height=height,
                fourcc=self._fourcc,
                fps=float(len(self._recent)),
                frames=self._frames,
                stale=frame is None or now - frame.ts > _STALE_AFTER_S,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is None:
            return
        self._thread.join(timeout=_JOIN_TIMEOUT_S)
        if self._thread.is_alive():
            _log.warning(
                "capture device index %d: read() still blocked after %.1f s; "
                "the device is released when it returns",
                self._index,
                _JOIN_TIMEOUT_S,
            )
            return
        self._thread = None
        self._cap = None
