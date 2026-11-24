import dataclasses
import json

import pytest
from fake_model import PINNED, FakeModel
from synth_pack import FakeConsole, FakeScreen, write_pack

from autoplay.core.agent.budget import Budget, Usage
from autoplay.core.agent.config import load_agent_config
from autoplay.core.agent.loop import DROPPED, run_agent, trim_pictures
from autoplay.core.agent.session import Session
from autoplay.core.agent.tools import build_tools
from autoplay.core.controller.types import PadState
from autoplay.core.memory.lessons import LessonStore
from autoplay.core.memory.notes import ProgressNotes
from autoplay.core.pack.guard import InputGuard
from autoplay.core.pack.loader import PackError, load_pack
from autoplay.core.runtime.estop import EStop
from autoplay.core.runtime.runlog import RunLog

ROUTINES = """
from autoplay.core.pack.routines import Param, RoutineStopped, routine


@routine("Open the bag and say so.", times=Param(int, 1, 3))
def open_bag(hands, times):
    for _ in range(times):
        hands.press("X")
    return f"pressed X {times} times, now {hands.context()}"


@routine("Stop at the second step.")
def stops(hands):
    hands.press("B")
    raise RoutineStopped("no outline seen", step="break 2")


@routine("Press what the bag forbids.")
def forbidden(hands):
    hands.press("X")
    hands.press("PLUS")
    return "never"


@routine("Hold a button and crash.")
def crashes(hands):
    hands._pad.hold("B")
    raise RuntimeError("boom")


@routine("Close the bag if it is open.")
def leave(hands):
    if hands.context() == "bag":
        hands.press("B")
        return "closed the bag"
    return "nothing to do"
"""


def session_with(
    tmp_path, *, showing=("explore",), max_usd=2.0, code=ROUTINES, **manifest
):
    root = write_pack(tmp_path / "pack", routines="routines.py", **manifest)
    (root / "routines.py").write_text(code, "utf-8")
    pack = load_pack(root)
    source = FakeScreen(*showing)
    pad = FakeConsole(source, {"X": ("bag",), "B": ("explore",)})
    config = dataclasses.replace(load_agent_config(), **PINNED)
    log = RunLog("test", tmp_path / "runs")
    estop = EStop(hotkey=False)
    pad.attach_estop(estop)
    return Session(
        pack=pack,
        pad=pad,
        source=source,
        guard=InputGuard(pack, source, pad, log),
        notes=ProgressNotes(tmp_path / "memory" / "notes.md", config.notes_limit),
        lessons=LessonStore(tmp_path / "memory" / "lessons"),
        budget=Budget(config.model, max_usd, 150, config.max_tokens),
        config=config,
        log=log,
        estop=estop,
    )


def events(session, kind):
    lines = (session.log.dir / "events.jsonl").read_text("utf-8").splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == kind]


def payload(result):
    return json.loads(result["content"][0]["text"])


def pictures(result):
    return [part for part in result["content"] if part["type"] == "image"]


def test_a_routine_is_a_tool_with_its_arguments_as_schema(tmp_path):
    tools = {tool.name: tool for tool in build_tools(session_with(tmp_path))}
    assert {"look", "press", "open_bag", "stops", "leave"} <= tools.keys()
    definition = tools["open_bag"].definition()
    assert definition["description"].startswith("Routine of this game: Open the bag")
    assert definition["input_schema"]["properties"] == {
        "times": {"type": "integer", "minimum": 1, "maximum": 3}
    }
    assert definition["input_schema"]["required"] == ["times"]


def test_a_routine_reports_text_and_one_picture(tmp_path):
    session = session_with(tmp_path)
    model = FakeModel([[("open_bag", {"times": 1})], [("finish", {"summary": "ok"})]])
    assert run_agent(session, "goal", model).reason == "done"
    result = model.results(2)[0]
    assert not result.get("is_error")
    assert payload(result) == {
        "routine": "open_bag",
        "result": "pressed X 1 times, now bag",
        "context_after": "bag",
        "settled": True,
    }
    assert len(pictures(result)) == 1
    assert [e["button"] for e in events(session, "input")] == ["X"]


def test_arguments_out_of_range_send_nothing(tmp_path):
    session = session_with(tmp_path)
    model = FakeModel([[("open_bag", {"times": 9})], [("finish", {"summary": "ok"})]])
    run_agent(session, "goal", model)
    result = model.results(2)[0]
    assert result["is_error"] and "times" in result["content"][0]["text"]
    assert events(session, "input") == []


def test_a_stopped_routine_names_its_step(tmp_path):
    session = session_with(tmp_path, showing=("bag",))
    model = FakeModel([[("stops", {})], [("finish", {"summary": "ok"})]])
    assert run_agent(session, "goal", model).reason == "done"
    result = model.results(2)[0]
    assert result["is_error"]
    said = payload(result)
    assert (said["stopped_at"], said["reason"]) == ("break 2", "no outline seen")
    # An error result holds text only: the API refuses anything else in one.
    assert said["context_after"] == "explore" and pictures(result) == []
    assert all(part["type"] == "text" for part in result["content"])


def test_a_refused_input_inside_a_routine_stops_it(tmp_path):
    session = session_with(tmp_path)
    model = FakeModel([[("forbidden", {})], [("finish", {"summary": "ok"})]])
    assert run_agent(session, "goal", model).reason == "done"
    result = model.results(2)[0]
    assert result["is_error"] and "PLUS is forbidden" in payload(result)["reason"]
    assert [(e["button"], e["outcome"]) for e in events(session, "input")] == [
        ("X", "sent"),
        ("PLUS", "refused"),
    ]


def test_a_crashing_routine_ends_the_run_with_the_pad_released(tmp_path):
    session = session_with(tmp_path)
    result = run_agent(session, "goal", FakeModel([[("crashes", {})]]))
    assert result.reason == "error" and "boom" in result.summary
    assert session.pad.state == PadState()


def test_a_routine_may_not_take_a_built_in_name(tmp_path):
    code = (
        "from autoplay.core.pack.routines import routine\n"
        "@routine('x')\ndef press(hands):\n    return ''\n"
    )
    with pytest.raises(PackError, match="built-in"):
        build_tools(session_with(tmp_path, code=code))


def test_on_stop_runs_once_when_the_budget_ends_the_run(tmp_path):
    session = session_with(tmp_path, max_usd=0.17, on_stop="leave")
    model = FakeModel([[("press", {"button": "X"})]] * 5, usage=Usage(0, 3000, 0, 0))
    result = run_agent(session, "goal", model)
    assert result.reason == "budget"
    (final,) = events(session, "run_result")
    assert (final["context"], final["on_stop"]) == ("explore", "closed the bag")
    assert [e["button"] for e in events(session, "input")].count("B") == 1


def test_on_stop_failing_does_not_change_how_the_run_ended(tmp_path):
    code = ROUTINES.replace('return "nothing to do"', 'raise RuntimeError("stuck")')
    session = session_with(tmp_path, on_stop="leave", code=code)
    result = run_agent(session, "goal", FakeModel([[("finish", {"summary": "ok"})]]))
    assert (result.reason, result.summary) == ("done", "ok")
    (final,) = events(session, "run_result")
    assert final["on_stop"] == "failed: RuntimeError: stuck"


def test_on_stop_is_not_run_after_an_emergency_stop(tmp_path):
    session = session_with(tmp_path, showing=("bag",), on_stop="leave")
    session.estop.trip()
    result = run_agent(session, "goal", FakeModel([[("look", {})]]))
    assert result.reason == "estop"
    (final,) = events(session, "run_result")
    assert final["on_stop"] is None and events(session, "input") == []


def image():
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "x"},
    }


def history(count):
    messages = [{"role": "user", "content": "goal"}]
    for i in range(count):
        use = {"type": "tool_use", "id": f"c{i}", "name": "look", "input": {}}
        messages.append({"role": "assistant", "content": [use]})
        result = {
            "type": "tool_result",
            "tool_use_id": f"c{i}",
            "content": [{"type": "text", "text": f"t{i}"}, image()],
        }
        messages.append({"role": "user", "content": [result]})
    return messages


def kept(messages):
    return [
        part["type"]
        for message in messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for part in message["content"][0]["content"]
        if part.get("text") != DROPPED and part["type"] == "image"
    ]


def test_trimming_waits_until_twice_the_number_kept(tmp_path):
    messages = history(8)
    assert trim_pictures(messages, 4) == 0 and len(kept(messages)) == 8
    messages = history(9)
    assistants = [m["content"] for m in messages if m["role"] == "assistant"]
    assert trim_pictures(messages, 4) == 5
    assert len(kept(messages)) == 4
    # The newest four keep their picture; the older ones a line of text in its place.
    results = [m["content"][0]["content"] for m in messages[2::2]]
    assert [r[1]["type"] for r in results] == ["text"] * 5 + ["image"] * 4
    assert all(r[1] == {"type": "text", "text": DROPPED} for r in results[:5])
    assert all(r[0]["text"] == f"t{i}" for i, r in enumerate(results))
    after = [m["content"] for m in messages if m["role"] == "assistant"]
    assert all(a is b for a, b in zip(assistants, after, strict=True))


def test_the_loop_never_sends_more_than_twice_the_pictures_kept(tmp_path):
    session = session_with(tmp_path)
    turns = [[("look", {})] for _ in range(40)] + [[("finish", {"summary": "ok"})]]
    model = FakeModel(turns)
    assert run_agent(session, "goal", model).reason == "done"
    keep = session.config.keep_screenshots
    counts = [
        sum(
            part["type"] == "image"
            for message in sent
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
            for part in block["content"]
        )
        for sent in model.sent
    ]
    assert max(counts) == 2 * keep and counts[-1] <= 2 * keep
    # The picture of the newest result is always there.
    assert all(
        sent[-1]["content"][0]["content"][-1]["type"] == "image"
        for sent in model.sent[1:]
    )


def test_each_call_is_sent_with_the_reply_length_the_budget_reserved(tmp_path):
    session = session_with(tmp_path)
    model = FakeModel(
        [[("look", {})], [("look", {})], [("finish", {"summary": "ok"})]],
        usage=Usage(1000, 600, 0, 0),
    )
    run_agent(session, "goal", model)
    assert model.limits == [3000, 2400, 2400]


def test_after_a_failed_call_the_rest_of_the_turn_is_not_run(tmp_path):
    session = session_with(tmp_path)
    turn = [("press", {"button": "MINUS"}), ("press", {"button": "X"}), ("look", {})]
    model = FakeModel([turn, [("finish", {"summary": "ok"})]])
    assert run_agent(session, "goal", model).reason == "done"
    first, second, third = model.results(2)
    assert first["is_error"] and "Refused" in first["content"][0]["text"]
    assert second["is_error"] and "Not run" in second["content"][0]["text"]
    # Looking is harmless and still happens.
    assert not third.get("is_error") and payload(third) == {"context": "explore"}
    assert [(e["button"], e["outcome"]) for e in events(session, "input")] == [
        ("MINUS", "refused")
    ]


def test_calls_of_one_turn_all_run_when_none_fails(tmp_path):
    session = session_with(tmp_path)
    turn = [("press", {"button": "X"}), ("press", {"button": "B"}), ("look", {})]
    model = FakeModel([turn, [("finish", {"summary": "ok"})]])
    run_agent(session, "goal", model)
    assert not any(result.get("is_error") for result in model.results(2))
    assert [e["button"] for e in events(session, "input")] == ["X", "B"]


def test_an_overrun_is_logged(tmp_path):
    session = session_with(tmp_path, max_usd=0.20)
    model = FakeModel([[("look", {})]] * 3, usage=Usage(60_000, 100, 0, 0))
    assert run_agent(session, "goal", model).reason == "budget"
    (overrun,) = events(session, "budget_overrun")
    assert overrun["usd"] > 0


def test_trimming_tells_the_budget_that_the_cache_is_gone(tmp_path):
    session = session_with(tmp_path)
    edits = []
    session.budget.history_edited = lambda: edits.append(len(edits))
    turns = [[("look", {})] for _ in range(12)] + [[("finish", {"summary": "ok"})]]
    run_agent(session, "goal", FakeModel(turns))
    assert len(edits) >= 1


def test_the_model_cannot_press_a_checked_button_itself(tmp_path):
    session = session_with(tmp_path, checked={"explore": ["Y"]})
    turn = [("press", {"button": "Y"})]
    combo = [("combo", {"hold": "ZL", "button": "Y", "ms": 100})]
    model = FakeModel([turn, combo, [("finish", {"summary": "ok"})]])
    assert run_agent(session, "goal", model).reason == "done"
    assert (
        model.results(2)[0]["is_error"]
        and "routine" in model.results(2)[0]["content"][0]["text"]
    )
    assert model.results(3)[0]["is_error"]
    assert [e["outcome"] for e in events(session, "input")] == ["refused", "refused"]
