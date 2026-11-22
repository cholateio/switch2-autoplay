import time
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from autoplay.core import clock
from autoplay.core.capture.base import CaptureError, FrameSource
from autoplay.core.controller.base import Controller
from autoplay.core.pack.guard import FRAME_TIMEOUT_S, InputGuard
from autoplay.core.pack.loader import GamePack, PackError
from autoplay.core.perception.geometry import BASE_H, BASE_W
from autoplay.core.runtime.runlog import RunLog

_HEARTBEAT_S = 0.05


class Hands:
    """What a pack routine works with. Every input goes through the guard, so
    the buttons the pack forbids stay forbidden inside a routine; a routine
    cannot answer a prompt that needs confirmation. Pictures are returned at
    the reference resolution whatever the capture delivers."""

    def __init__(
        self,
        pack: GamePack,
        guard: InputGuard,
        pad: Controller,
        source: FrameSource,
        log: RunLog | None = None,
    ) -> None:
        self._pack = pack
        self._guard = guard
        self._pad = pad
        self._source = source
        self._log = log
        self._templates: dict[str, np.ndarray] = {}

    def press(self, button: str, ms: int | None = None) -> str | None:
        return self._guard.press(button, ms)

    def stick(
        self, which: Literal["L", "R"], x: float, y: float, ms: int
    ) -> str | None:
        return self._guard.hold_stick(which, x, y, ms)

    def combo(
        self,
        held: str,
        ms: int,
        button: str | None = None,
        stick: tuple[Literal["L", "R"], float, float] | None = None,
    ) -> str | None:
        return self._guard.combo(held, ms, button=button, stick=stick)

    def wait(self, ms: int) -> None:
        """Do nothing for `ms`, keeping the gamepad's lease alive."""
        deadline = clock.now() + ms / 1000
        while True:
            self._pad.heartbeat()
            remaining = deadline - clock.now()
            if remaining <= 0:
                return
            time.sleep(min(_HEARTBEAT_S, remaining))

    def frame(self) -> np.ndarray:
        """A picture captured after this call began. Raises CaptureError."""
        self._pad.heartbeat()
        latest = self._source.latest()
        frame = self._source.wait_next(latest.seq if latest else 0, FRAME_TIMEOUT_S)
        if frame is None:
            raise CaptureError(f"no new frame within {FRAME_TIMEOUT_S} s")
        bgr = frame.bgr
        if bgr.shape[:2] != (BASE_H, BASE_W):
            bgr = cv2.resize(bgr, (BASE_W, BASE_H), interpolation=cv2.INTER_AREA)
        return bgr

    def context(self) -> str | None:
        return self._guard.context()

    def template(self, name: str) -> np.ndarray:
        """The picture `templates/<name>` of the pack. Raises PackError when it
        is missing: templates are local only."""
        if name not in self._templates:
            folder = (self._pack.root / "templates").resolve()
            path = (folder / name).resolve()
            image = cv2.imread(str(path)) if path.is_relative_to(folder) else None
            if image is None:
                raise PackError(
                    f"routine template {Path('templates') / name} is missing from "
                    f"{self._pack.root}; templates are local only"
                )
            self._templates[name] = image
        return self._templates[name]

    def log(self, **fields) -> None:
        if self._log is not None:
            self._log.event("routine_step", **fields)
