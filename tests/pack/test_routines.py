import json

import cv2
import numpy as np
import pytest
import yaml
from synth_pack import FakeScreen, _patch, screen, write_pack

from autoplay.core.controller.dummy import DummyController
from autoplay.core.pack.guard import ForbiddenInput, InputGuard, NeedsConfirmation
from autoplay.core.pack.hands import Hands
from autoplay.core.pack.loader import PackError, load_pack
from autoplay.core.pack.routines import Param, RoutineStopped
from autoplay.core.runtime.runlog import RunLog

ROUTINES = """
from autoplay.core.pack.routines import Param, RoutineStopped, routine


@routine("Press A a number of times.", times=Param(int, 1, 5), side=Param(str, choices=("e", "w")))
def tap(hands, times, side):
    for _ in range(times):
        hands.press("A")
    return f"tapped {times} {side}"


@routine("Go home.")
def leave(hands):
    return "left"


def helper():
    return 1
"""


def pack_with(tmp_path, code=ROUTINES, **manifest):
    root = write_pack(tmp_path / "p", routines="routines.py", **manifest)
    (root / "routines.py").write_text(code, "utf-8")
    return root


def test_a_pack_without_routines_has_none(tmp_path):
    pack = load_pack(write_pack(tmp_path / "p"))
    assert pack.routines == {} and pack.on_stop is None and pack.confirm == {}


def test_routines_load_with_their_arguments(tmp_path):
    pack = load_pack(pack_with(tmp_path, on_stop="leave"))
    assert list(pack.routines) == ["tap", "leave"]
    tap = pack.routines["tap"]
    assert tap.description == "Press A a number of times."
    assert tap.params == {
        "times": Param(int, 1, 5),
        "side": Param(str, choices=("e", "w")),
    }
    assert pack.on_stop == "leave"
    assert tap.check_args({"times": 3, "side": "e"}) == {"times": 3, "side": "e"}


@pytest.mark.parametrize(
    "args",
    [
        {"times": 0, "side": "e"},
        {"times": 6, "side": "e"},
        {"times": 2.5, "side": "e"},
        {"times": True, "side": "e"},
        {"times": 2, "side": "n"},
        {"times": 2},
        {"times": 2, "side": "e", "extra": 1},
    ],
)
def test_arguments_outside_what_is_declared_are_refused(tmp_path, args):
    tap = load_pack(pack_with(tmp_path)).routines["tap"]
    with pytest.raises(ValueError, match="times|side|arguments"):
        tap.check_args(args)


@pytest.mark.parametrize(
    ("code", "says"),
    [
        ("import nothing_like_this\n", "ModuleNotFoundError"),
        ("def broken(:\n", "SyntaxError"),
        (
            (
                "from autoplay.core.pack.routines import routine\n"
                "@routine('x', n=7)\ndef bad(hands, n):\n    return ''\n"
            ),
            "must be a Param",
        ),
        (
            (
                "from autoplay.core.pack.routines import Param, routine\n"
                "@routine('x', n=Param(list))\ndef bad(hands, n):\n    return ''\n"
            ),
            "must be a Param",
        ),
        (
            (
                "from autoplay.core.pack.routines import routine\n"
                "@routine('')\ndef bad(hands):\n    return ''\n"
            ),
            "needs a description",
        ),
    ],
)
def test_a_bad_routines_file_names_itself(tmp_path, code, says):
    root = pack_with(tmp_path, code)
    with pytest.raises(PackError, match="routines.py") as info:
        load_pack(root)
    assert says in str(info.value)


def test_a_missing_routines_file_and_a_bad_on_stop_are_pack_errors(tmp_path):
    with pytest.raises(PackError, match="missing"):
        load_pack(write_pack(tmp_path / "a", routines="routines.py"))
    with pytest.raises(PackError, match="on_stop"):
        load_pack(pack_with(tmp_path, on_stop="nowhere"))
    with pytest.raises(PackError, match="on_stop"):
        load_pack(pack_with(tmp_path / "b", on_stop="tap"))
    with pytest.raises(PackError, match="outside"):
        load_pack(write_pack(tmp_path / "c", routines="../routines.py"))


def hands_for(pack, source, pad, log=None):
    return Hands(pack, InputGuard(pack, source, pad, log), pad, source, log)


def test_hands_send_through_the_guard(tmp_path):
    pack = load_pack(pack_with(tmp_path))
    pad, log = DummyController(), RunLog("t", tmp_path / "runs")
    hands = hands_for(pack, FakeScreen("bag"), pad, log)
    with pytest.raises(ForbiddenInput):
        hands.press("PLUS")
    assert pad.log == []
    assert pack.routines["tap"].run(hands, times=2, side="e") == "tapped 2 e"
    events = [
        json.loads(line)
        for line in (log.dir / "events.jsonl").read_text("utf-8").splitlines()
    ]
    assert [(e["button"], e["outcome"]) for e in events if e["kind"] == "input"] == [
        ("PLUS", "refused"),
        ("A", "sent"),
        ("A", "sent"),
    ]


def test_waiting_keeps_the_lease_alive(tmp_path):
    pack = load_pack(pack_with(tmp_path))

    class Counting(DummyController):
        beats = 0

        def heartbeat(self):
            self.beats += 1
            super().heartbeat()

    pad = Counting()
    hands_for(pack, FakeScreen("explore"), pad).wait(200)
    assert pad.beats > 2


def test_frames_come_at_the_reference_size_and_templates_from_the_pack(tmp_path):
    pack = load_pack(pack_with(tmp_path))
    source = FakeScreen("explore")
    source.picture = cv2.resize(screen("explore"), (1280, 720))
    hands = hands_for(pack, source, DummyController())
    assert hands.frame().shape == (1080, 1920, 3)
    assert hands.template("bag.png").shape[:2] == (120, 160)
    with pytest.raises(PackError, match="local only"):
        hands.template("nothing.png")
    with pytest.raises(PackError):
        hands.template("../manifest.yaml")


def test_routine_stopped_carries_its_step():
    stop = RoutineStopped("no outline", step="break 2")
    assert (str(stop), stop.step) == ("no outline", "break 2")


OVERLAY_SPOT = (900, 500, 11)


def choice_pack(tmp_path, confirm):
    root = write_pack(tmp_path / "p", confirm=confirm)
    x, y, seed = OVERLAY_SPOT
    cv2.imwrite(str(root / "templates" / "choice.png"), _patch(seed))
    states_path = root / "states.yaml"
    data = yaml.safe_load(states_path.read_text("utf-8"))
    data["states"].append(
        {
            "name": "choice",
            "kind": "template",
            "template": "templates/choice.png",
            "roi": [x - 20, y - 20, 200, 160],
            "overlay": True,
        }
    )
    states_path.write_text(yaml.safe_dump(data), "utf-8")
    return root


def with_choice(*contexts) -> np.ndarray:
    img = screen(*contexts)
    x, y, seed = OVERLAY_SPOT
    img[y : y + 120, x : x + 160] = _patch(seed)
    return img


def test_an_overlay_does_not_change_the_context_but_wants_a_confirmation(tmp_path):
    pack = load_pack(choice_pack(tmp_path, {"choice": ["A"]}))
    assert pack.states.names == ["explore", "bag"] and pack.overlays.names == ["choice"]
    source, pad, log = (
        FakeScreen("explore"),
        DummyController(),
        RunLog("t", tmp_path / "r"),
    )
    source.picture = with_choice("explore")
    guard = InputGuard(pack, source, pad, log)
    assert guard.context() == "explore"
    with pytest.raises(NeedsConfirmation, match="confirm"):
        guard.press("A")
    assert pad.log == []
    assert guard.press("B") == "explore"
    sent_before = len(pad.log)
    assert guard.press("A", confirm="沒什麼") == "explore"
    assert len(pad.log) > sent_before
    events = [
        json.loads(line)
        for line in (log.dir / "events.jsonl").read_text("utf-8").splitlines()
    ]
    a_events = [e for e in events if e["kind"] == "input" and e["button"] == "A"]
    assert (
        a_events[0]["outcome"] == "refused" and a_events[0]["needs_confirm"] == "choice"
    )
    assert a_events[1]["outcome"] == "sent" and a_events[1]["confirm"] == "沒什麼"
    # Without the overlay on screen nothing is asked.
    source.picture = screen("explore")
    assert guard.press("A") == "explore"


def test_a_combo_cannot_confirm_and_a_routine_must_say_what_it_read(tmp_path):
    pack = load_pack(choice_pack(tmp_path, {"choice": ["A"]}))
    source, pad = FakeScreen("explore"), DummyController()
    source.picture = with_choice("explore")
    guard = InputGuard(pack, source, pad)
    with pytest.raises(ForbiddenInput):
        guard.combo("ZL", 100, button="A")
    hands = Hands(pack, guard, pad, source)
    with pytest.raises(NeedsConfirmation):
        hands.press("A")
    assert pad.log == []
    assert hands.press("A", confirm="yes (end it?)") == "explore"
    assert pad.log != []


def test_confirm_may_name_a_context_and_must_name_something_known(tmp_path):
    pack = load_pack(write_pack(tmp_path / "a", confirm={"bag": ["A"]}))
    guard = InputGuard(pack, FakeScreen("bag"), DummyController())
    with pytest.raises(NeedsConfirmation):
        guard.press("A")
    with pytest.raises(PackError, match="neither a state nor an overlay"):
        load_pack(write_pack(tmp_path / "b", confirm={"nowhere": ["A"]}))
    with pytest.raises(PackError):
        load_pack(write_pack(tmp_path / "c", confirm={"bag": ["NOPE"]}))
