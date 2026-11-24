from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Self

import numpy as np


class CaptureError(RuntimeError):
    """A frame source could not be opened or delivered no frames."""


@dataclass(frozen=True)
class Frame:
    """One captured image.

    `bgr` is HxWx3 uint8. `ts` is in seconds: `autoplay.core.clock.now()` at grab
    for a device, media time for a file. `seq` is strictly increasing per source.
    """

    bgr: np.ndarray
    ts: float
    seq: int


@dataclass(frozen=True)
class CaptureStats:
    """Source health: `fps` is measured over the last second; `stale` means no
    new frame for more than 0.5 s."""

    width: int
    height: int
    fourcc: str
    fps: float
    frames: int
    stale: bool


class FrameSource(ABC):
    """A source of frames. `start()` must be called before reading; using the
    source as a context manager guarantees `close()` on exit."""

    @abstractmethod
    def start(self) -> None:
        """Open the source; raise `CaptureError` if it delivers no frame."""

    @abstractmethod
    def latest(self) -> Frame | None:
        """Return the newest frame, or None before the first one."""

    @abstractmethod
    def wait_next(self, after_seq: int, timeout: float) -> Frame | None:
        """Block until a frame with `seq > after_seq` exists and return the
        newest one, or return None after `timeout` seconds."""

    @abstractmethod
    def stats(self) -> CaptureStats:
        """Return current source statistics."""

    def ended(self) -> bool:
        """True once the source can never deliver another frame: a file that
        has been played to its end. A live source never ends; one that stops
        delivering shows it as `stats().stale`."""
        return False

    @abstractmethod
    def close(self) -> None:
        """Release the source; safe to call more than once."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
