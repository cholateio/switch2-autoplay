import logging
import threading
import time
from collections import deque
from collections.abc import Callable

import cv2

from autoplay.core import clock
from autoplay.core.capture.base import CaptureError, CaptureStats, Frame, FrameSource

BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF}

_FIRST_FRAME_TIMEOUT_S = 2.0
_STALE_AFTER_S = 0.5
_FPS_WINDOW_S = 1.0
_READ_FAIL_BACKOFF_S = 0.005
_JOIN_TIMEOUT_S = 2.0

_log = logging.getLogger(__name__)


def fourcc_str(value: float) -> str:
    """Decode a `CAP_PROP_FOURCC` value into its four characters."""
    code = int(value)
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))


def _check_backend(backend: str) -> None:
    if backend not in BACKENDS:
        raise ValueError(
            f"unknown backend {backend!r}; expected one of {sorted(BACKENDS)}"
        )


def open_capture(
    index: int,
    *,
    backend: str,
    width: int,
    height: int,
    fps: int,
    fourcc: str,
    opener: Callable[..., "cv2.VideoCapture"] | None = None,
) -> "cv2.VideoCapture":
    """Open and configure a capture device; raise `CaptureError` naming the
    index and backend if it cannot be opened."""
    _check_backend(backend)
    cap = (opener or cv2.VideoCapture)(index, BACKENDS[backend])
    if not cap.isOpened():
        cap.release()
        raise CaptureError(
            f"cannot open capture device index {index} with backend {backend}"
        )
    # DirectShow ignores FOURCC set after the frame size, so FOURCC goes first.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
    return cap


class DeviceSource(FrameSource):
    """A capture device read by a background thread that keeps only the newest
    frame. `opener(index, api_preference)` replaces `cv2.VideoCapture` in tests.

    A source is single-use: a second `start()`, including one after `close()`,
    raises RuntimeError; create a new source instead."""

    def __init__(
        self,
        index: int = 0,
        *,
        backend: str = "dshow",
        width: int = 1920,
        height: int = 1080,
        fps: int = 60,
        fourcc: str = "YUY2",
        opener: Callable[..., "cv2.VideoCapture"] | None = None,
    ) -> None:
        _check_backend(backend)
        self._index = index
        self._backend = backend
        self._config = {"width": width, "height": height, "fps": fps, "fourcc": fourcc}
        self._opener = opener
        self._started = False
        self._closed = False
        self._cap = None
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
        self._cap = open_capture(
            self._index, backend=self._backend, opener=self._opener, **self._config
        )
        self._fourcc = fourcc_str(self._cap.get(cv2.CAP_PROP_FOURCC))
        self._thread = threading.Thread(
            target=self._run,
            args=(self._cap,),
            name=f"capture-device-{self._index}",
            daemon=True,
        )
        self._thread.start()
        with self._cond:
            got = self._cond.wait_for(
                lambda: self._latest is not None, _FIRST_FRAME_TIMEOUT_S
            )
        if not got:
            self.close()
            raise CaptureError(
                f"no frame from capture device index {self._index} with backend "
                f"{self._backend} within {_FIRST_FRAME_TIMEOUT_S} s"
            )

    # The reader thread owns release(): releasing from another thread while
    # read() is in flight can crash the process inside the driver.
    def _run(self, cap) -> None:
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
                "capture device index %d (%s): read() still blocked after %.1f s; "
                "the device is released when it returns",
                self._index,
                self._backend,
                _JOIN_TIMEOUT_S,
            )
            return
        self._thread = None
        self._cap = None
