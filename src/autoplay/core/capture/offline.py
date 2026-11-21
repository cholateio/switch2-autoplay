from abc import abstractmethod
from pathlib import Path

import cv2
import numpy as np

from autoplay.core.capture.base import CaptureError, CaptureStats, Frame, FrameSource

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")


def read_image(path: Path) -> np.ndarray:
    """Read an image file as BGR; raise CaptureError naming the file if it
    cannot be read."""
    # cv2.imread cannot open non-ASCII paths on Windows.
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError as exc:
        raise CaptureError(f"cannot read image {path}: {exc}") from exc
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise CaptureError(f"cannot decode image {path}")
    return img


class OfflineSource(FrameSource):
    """A deterministic source: no thread, no clock. Each `wait_next()` decodes
    forward, `Frame.ts` is media time (`index / fps`, still rising across
    loops) and `Frame.seq` counts from 1. At the end `wait_next()` returns
    None, or starts again from the first frame when `loop` is true."""

    kind = ""

    def __init__(self, path: Path, *, fps: float, loop: bool) -> None:
        self.path = Path(path)
        self._fps = fps
        self._loop = loop
        self._started = False
        self._latest: Frame | None = None
        self._count = 0

    def start(self) -> None:
        if self._started:
            raise RuntimeError(f"{type(self).__name__} is already started")
        self._open()
        self._started = True

    @abstractmethod
    def _open(self) -> None:
        """Open the media and set `self._fps`; raise CaptureError if it holds
        no frame."""

    @abstractmethod
    def _read(self) -> np.ndarray | None:
        """Return the next image, or None at the end."""

    @abstractmethod
    def _rewind(self) -> None: ...

    def _advance(self) -> Frame | None:
        if not self._started:
            raise RuntimeError(f"{type(self).__name__} is not started")
        img = self._read()
        if img is None and self._loop:
            self._rewind()
            img = self._read()
        if img is None:
            return None
        self._latest = Frame(bgr=img, ts=self._count / self._fps, seq=self._count + 1)
        self._count += 1
        return self._latest

    def latest(self) -> Frame | None:
        if self._latest is None:
            self._advance()
        return self._latest

    def wait_next(self, after_seq: int, timeout: float) -> Frame | None:
        while True:
            frame = self._advance()
            if frame is None or frame.seq > after_seq:
                return frame

    def stats(self) -> CaptureStats:
        frame = self._latest
        height, width = frame.bgr.shape[:2] if frame is not None else (0, 0)
        return CaptureStats(
            width=width,
            height=height,
            fourcc=self.kind,
            fps=float(self._fps),
            frames=self._count,
            stale=False,
        )


class VideoFileSource(OfflineSource):
    kind = "video"

    def __init__(self, path: Path, *, loop: bool = False) -> None:
        super().__init__(path, fps=0.0, loop=loop)
        self._cap: cv2.VideoCapture | None = None

    def _open(self) -> None:
        if not self.path.is_file():
            raise CaptureError(f"video file not found: {self.path}")
        cap = cv2.VideoCapture(str(self.path))
        fps = cap.get(cv2.CAP_PROP_FPS) if cap.isOpened() else 0.0
        if not fps or fps <= 0:
            cap.release()
            raise CaptureError(f"cannot open video file {self.path}")
        self._cap, self._fps = cap, fps

    def _read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, img = self._cap.read()
        return img if ok else None

    def _rewind(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = cv2.VideoCapture(str(self.path))

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class ImageDirSource(OfflineSource):
    """The image files of one directory (not its subdirectories), in name order."""

    kind = "images"

    def __init__(self, path: Path, *, fps: float = 30.0, loop: bool = False) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        super().__init__(path, fps=fps, loop=loop)
        self._files: list[Path] = []
        self._next = 0

    def _open(self) -> None:
        if not self.path.is_dir():
            raise CaptureError(f"image directory not found: {self.path}")
        self._files = sorted(
            (p for p in self.path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES),
            key=lambda p: p.name,
        )
        if not self._files:
            raise CaptureError(f"no image files in {self.path}")

    def _read(self) -> np.ndarray | None:
        if self._next >= len(self._files):
            return None
        img = read_image(self._files[self._next])
        self._next += 1
        return img

    def _rewind(self) -> None:
        self._next = 0

    def close(self) -> None:
        self._files = []
        self._next = 0
