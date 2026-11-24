from autoplay.core.actions.timing import Timing
from autoplay.core.controller.base import Controller
from autoplay.core.controller.types import PadState


class DummyController(Controller):
    """A controller with no device: it records what it was told and returns
    at once. `log` holds each `(state, hold_ms)` in order."""

    def __init__(self, timing: Timing | None = None) -> None:
        super().__init__(timing)
        self.log: list[tuple[PadState, int]] = []
        self.release_all_calls = 0

    def _apply(self, state: PadState, hold_ms: int) -> None:
        self.log.append((state, hold_ms))

    def _release_all(self) -> None:
        self.release_all_calls += 1
