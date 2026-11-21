import json

import pytest
from synth_pack import FakeConsole, FakeScreen, screen, write_pack

from autoplay.core.capture.base import CaptureError
from autoplay.core.controller.base import UnknownButton
from autoplay.core.controller.dummy import DummyController
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
