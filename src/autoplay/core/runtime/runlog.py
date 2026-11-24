import json
import re
import threading
from datetime import datetime
from pathlib import Path

import cv2

from autoplay.core import clock
from autoplay.core.capture.base import Frame

_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
_RESERVED = {"t", "kind"}


class RunLog:
    """One run's record: a directory `runs/<YYYYmmdd-HHMMSS>-<name>/` holding
    `events.jsonl` and any saved frames."""

    def __init__(self, name: str, root: Path = Path("runs")) -> None:
        if not _NAME_RE.fullmatch(name):
            raise ValueError(
                f"invalid run name {name!r}; use letters, digits, '_' or '-'"
            )
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}-{name}"
        self.dir = Path(root) / stamp
        suffix = 2
        while self.dir.exists():
            self.dir = Path(root) / f"{stamp}-{suffix}"
            suffix += 1
        self.dir.mkdir(parents=True)
        self._start = clock.now()
        self._lock = threading.Lock()
        self._events = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        self._frames = 0

    def event(self, kind: str, **fields) -> None:
        """Append one JSON line with `kind`, the fields, and `t`: seconds
        since the run started."""
        clash = _RESERVED & fields.keys()
        if clash:
            raise ValueError(f"event fields may not be named {sorted(clash)}")
        line = json.dumps(
            {"t": round(clock.now() - self._start, 6), "kind": kind, **fields}
        )
        with self._lock:
            self._events.write(line + "\n")
            # A run that is killed must still leave its events behind.
            self._events.flush()

    def save_frame(self, tag: str, frame: Frame) -> Path:
        """Write `frame` as `<n>-<tag>.png`, n counting saved frames from 1,
        and return the path."""
        if not _NAME_RE.fullmatch(tag):
            raise ValueError(
                f"invalid frame tag {tag!r}; use letters, digits, '_' or '-'"
            )
        with self._lock:
            self._frames += 1
            path = self.dir / f"{self._frames:04d}-{tag}.png"
        if not cv2.imwrite(str(path), frame.bgr):
            raise OSError(f"could not write {path}")
        return path

    def close(self) -> None:
        with self._lock:
            self._events.close()
