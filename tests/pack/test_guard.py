import json

import pytest
from synth_pack import FakeConsole, FakeScreen, screen, write_pack

from autoplay.core.capture.base import CaptureError
from autoplay.core.controller.base import UnknownButton
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.types import Button
from autoplay.core.pack.guard import ForbiddenInput, InputGuard
from autoplay.core.pack.loader import load_pack
from autoplay.core.runtime.runlog import RunLog


@pytest.fixture
def pack(tmp_path):
    return load_pack(write_pack(tmp_path / "p"))


def inputs(log: RunLog) -> list[dict]:
    lines = (log.dir / "events.jsonl").read_text("utf-8").splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == "input"]


def test_forbidden_button_never_reaches_the_pad(pack, tmp_path):
    pad, log = DummyController(), RunLog("t", tmp_path / "runs")
    guard = InputGuard(pack, FakeScreen("bag"), pad, log)
    with pytest.raises(ForbiddenInput) as info:
        guard.press("PLUS")
    assert (info.value.button, info.value.context) == ("PLUS", "bag")
    assert pad.log == []
    assert guard.press("A") == "bag"
    assert len(pad.log) == 2
    events = inputs(log)
    assert [(e["button"], e["context"], e["outcome"]) for e in events] == [
        ("PLUS", "bag", "refused"),
        ("A", "bag", "sent"),
    ]
    assert all(e["seq"] > 0 for e in events)


def test_unrecognised_picture_refuses_every_button_forbidden_anywhere(pack):
    pad = DummyController()
    guard = InputGuard(pack, FakeScreen(), pad)
    for button in ("PLUS", "MINUS"):
        with pytest.raises(ForbiddenInput) as info:
            guard.press(button)
        assert info.value.context is None
    assert pad.log == []
    assert guard.press("A") is None


def test_each_press_reads_the_context_again(pack):
    source = FakeScreen("explore")
    pad = FakeConsole(source, {"X": ("bag",)})
    guard = InputGuard(pack, source, pad)
    assert guard.press("X") == "explore"
    with pytest.raises(ForbiddenInput) as info:
        guard.press("PLUS")
    assert info.value.context == "bag"
    assert not pad.pressed("PLUS")


def test_two_frames_that_disagree_count_as_unrecognised(pack):
    source = FakeScreen("explore")
    source.queued = [screen("explore"), screen("bag")]
    pad = DummyController()
    guard = InputGuard(pack, source, pad)
    with pytest.raises(ForbiddenInput) as info:
        guard.press("PLUS")
    assert info.value.context is None
    assert pad.log == []


def test_frame_matching_two_contexts_counts_as_unrecognised(pack):
    pad = DummyController()
    guard = InputGuard(pack, FakeScreen("explore", "bag"), pad)
    assert guard.context() is None
    with pytest.raises(ForbiddenInput):
        guard.press("MINUS")
    assert pad.log == []


def test_stalled_source_is_an_error_not_an_old_answer(pack):
    source = FakeScreen("explore")
    pad = DummyController()
    guard = InputGuard(pack, source, pad)
    assert guard.context() == "explore"
    source.stalled = True
    with pytest.raises(CaptureError):
        guard.press("A")
    with pytest.raises(CaptureError):
        guard.context()
    assert pad.log == []


def test_context_uses_frames_newer_than_the_call(pack):
    source = FakeScreen("bag")
    guard = InputGuard(pack, source, DummyController())
    source.show("explore")
    assert guard.context() == "explore"


def test_unknown_button_is_refused_before_looking(pack):
    source = FakeScreen("explore")
    source.stalled = True
    with pytest.raises(UnknownButton):
        InputGuard(pack, source, DummyController()).press("START")


def test_stick_is_sent_and_logged(pack, tmp_path):
    pad, log = DummyController(), RunLog("t", tmp_path / "runs")
    guard = InputGuard(pack, FakeScreen("explore"), pad, log)
    assert guard.hold_stick("L", 1.0, 0.0, 200) == "explore"
    assert pad.log[0][0].lx == 255
    (event,) = inputs(log)
    assert (event["button"], event["outcome"], event["ms"]) == ("L_STICK", "sent", 200)


def test_a_press_the_pad_rejects_is_logged_as_failed(pack, tmp_path):
    pad, log = DummyController(), RunLog("t", tmp_path / "runs")
    guard = InputGuard(pack, FakeScreen("explore"), pad, log)
    with pytest.raises(ValueError):
        guard.press("A", 70000)
    (event,) = inputs(log)
    assert event["outcome"] == "failed"


def test_combo_holds_then_acts_then_releases(pack, tmp_path):
    from autoplay.core.controller.types import Button

    pad, log = DummyController(), RunLog("t", tmp_path / "runs")
    guard = InputGuard(pack, FakeScreen("explore"), pad, log)
    assert guard.combo("ZL", 100, button="A") == "explore"
    held = [state.buttons for state, _ in pad.log]
    assert held == [Button.ZL, Button.ZL | Button.A, Button.ZL, Button(0)]
    (event,) = inputs(log)
    assert (event["button"], event["held"], event["outcome"]) == ("A", "ZL", "sent")
    assert event["reached"] == ["ZL", "A"]


def test_combo_with_a_stick_keeps_the_button_down(pack):
    from autoplay.core.controller.types import Button

    pad = DummyController()
    guard = InputGuard(pack, FakeScreen("explore"), pad)
    guard.combo("L", 200, stick=("L", 1.0, 0.0))
    deflected = [s for s, _ in pad.log if s.lx != 128]
    assert deflected and all(s.buttons == Button.L for s in deflected)
    assert pad.state.buttons == Button(0) and pad.state.lx == 128


def test_combo_is_refused_whole_when_any_button_is_forbidden(pack, tmp_path):
    pad, log = DummyController(), RunLog("t", tmp_path / "runs")
    guard = InputGuard(pack, FakeScreen("bag"), pad, log)
    for held, button in (("PLUS", "A"), ("ZL", "PLUS")):
        with pytest.raises(ForbiddenInput) as info:
            guard.combo(held, 100, button=button)
        assert info.value.button == "PLUS"
    assert pad.log == []
    assert [e["outcome"] for e in inputs(log)] == ["refused", "refused"]


@pytest.mark.parametrize(
    "held, kwargs",
    [
        ("ZL", {}),
        ("ZL", {"button": "A", "stick": ("L", 0.0, 0.0)}),
        ("A", {"button": "A"}),
        ("NOPE", {"button": "A"}),
    ],
)
def test_malformed_combo_sends_nothing(pack, held, kwargs):
    pad = DummyController()
    guard = InputGuard(pack, FakeScreen("explore"), pad)
    with pytest.raises(ValueError):
        guard.combo(held, 100, **kwargs)
    assert pad.log == []


def test_a_held_button_that_changes_the_screen_does_not_open_a_way_round(
    pack, tmp_path
):
    source = FakeScreen("explore")
    pad, log = FakeConsole(source, {"X": ("bag",)}), RunLog("t", tmp_path / "runs")
    guard = InputGuard(pack, source, pad, log)
    with pytest.raises(ForbiddenInput) as info:
        guard.combo("X", 100, button="PLUS")
    assert (info.value.button, info.value.context) == ("PLUS", "bag")
    assert pad.pressed("X") and not pad.pressed("PLUS")
    assert pad.state.buttons == 0
    (event,) = inputs(log)
    assert (event["outcome"], event["reached"], event["context_held"]) == (
        "refused",
        ["X"],
        "bag",
    )


class _Breaking(DummyController):
    """Fails at one step of a combo: `press` after the button went down, or `release`."""

    def __init__(self, step: str, error: type[BaseException]) -> None:
        super().__init__()
        self.step, self.error = step, error

    def press(self, button, ms=None):
        if self.step == "press":
            self.hold(button)
            raise self.error("stopped")
        super().press(button, ms)

    def release(self, button):
        if self.step == "release":
            raise self.error("stopped")
        super().release(button)


@pytest.mark.parametrize("step", ["press", "release"])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_a_combo_that_stops_halfway_leaves_nothing_down_and_says_what_went_down(
    pack, tmp_path, step, error
):
    pad, log = _Breaking(step, error), RunLog("t", tmp_path / "runs")
    guard = InputGuard(pack, FakeScreen("explore"), pad, log)
    with pytest.raises(error):
        guard.combo("ZL", 100, button="A")
    assert pad.state.buttons == 0
    (event,) = inputs(log)
    assert (event["outcome"], event["reached"]) == ("failed", ["ZL", "A"])
    assert event["error"] == error.__name__


def test_a_failing_log_does_not_replace_the_failure(pack):
    class DeadLog:
        def event(self, kind, **fields):
            raise OSError("disk full")

    guard = InputGuard(
        pack, FakeScreen("explore"), _Breaking("press", RuntimeError), DeadLog()
    )
    with pytest.raises(RuntimeError, match="stopped"):
        guard.combo("ZL", 100, button="A")


def test_an_interrupt_during_the_cleanup_does_not_replace_the_failure(pack):
    class Interrupted(_Breaking):
        def release_all(self):
            raise KeyboardInterrupt

    guard = InputGuard(pack, FakeScreen("explore"), Interrupted("press", RuntimeError))
    with pytest.raises(RuntimeError, match="stopped"):
        guard.combo("ZL", 100, button="A")


def test_the_second_look_waits_for_a_screen_that_opens_late(pack):
    class SlowBag(DummyController):
        # The bag appears a few frames after X goes down, as on the console.
        def _apply(self, state, hold_ms):
            opened = state.buttons & ~self.state.buttons & Button.X
            super()._apply(state, hold_ms)
            if opened:
                source.queued = [screen("explore")] * 6 + [screen("bag")]

    source = FakeScreen("explore")
    pad = SlowBag()
    guard = InputGuard(pack, source, pad)
    with pytest.raises(ForbiddenInput) as info:
        guard.combo("X", 100, button="PLUS")
    assert info.value.context == "bag"
    assert not any(state.buttons & Button.PLUS for state, _ in pad.log)
    assert pad.state.buttons == 0
