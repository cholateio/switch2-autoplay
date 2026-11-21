import json

from fake_model import FakeModel, make_session
from synth_pack import FakeConsole

from autoplay.core.agent.budget import Usage
from autoplay.core.agent.loop import ModelError, ModelReply, run_agent
from autoplay.core.controller.base import ControllerError
from autoplay.core.controller.types import Button, PadState
from autoplay.core.memory.lessons import Lesson


def events(session, kind=None) -> list[dict]:
    lines = (session.log.dir / "events.jsonl").read_text("utf-8").splitlines()
    found = [json.loads(line) for line in lines]
    return [e for e in found if kind is None or e["kind"] == kind]


def payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_look_press_finish(tmp_path):
    session = make_session(tmp_path, moves={"X": ("bag",)})
    model = FakeModel(
        [[("look", {})], [("press", {"button": "X"})], [("finish", {"summary": "ok"})]]
    )
    result = run_agent(session, "open the bag", model)
    assert (result.reason, result.summary, result.model_calls) == ("done", "ok", 3)
    assert result.usd > 0
    calls = events(session, "model_call")
    assert len(calls) == 3
    assert all(c["usd"] > 0 and c["seconds"] >= 0 for c in calls)
    assert calls[0]["input_tokens"] == 1000 and calls[0]["stop_reason"] == "tool_use"
    looked = model.results(2)[0]
    assert payload(looked) == {"context": "explore"}
    assert looked["content"][1]["source"]["media_type"] == "image/jpeg"
    pressed = payload(model.results(3)[0])
    assert pressed == {
        "pressed": "X",
        "context_before": "explore",
        "context_after": "bag",
        "picture_changed": True,
        "settled": True,
    }
    (final,) = events(session, "run_result")
    assert (final["reason"], final["goal"], final["context"]) == (
        "done",
        "open the bag",
        "bag",
    )
    assert final["model_calls"] == 3


def test_history_is_only_ever_appended_to(tmp_path):
    session = make_session(tmp_path)
    model = FakeModel(
        [[("look", {})], [("press", {"button": "A"})], [("finish", {"summary": "ok"})]]
    )
    run_agent(session, "goal", model)
    for earlier, later in zip(model.sent, model.sent[1:], strict=False):
        assert later[: len(earlier)] == earlier
        assert len(later) == len(earlier) + 2
    assert len(set(model.systems)) == 1
    assert all(tools == model.tools[0] for tools in model.tools)


def test_system_prompt_and_first_message(tmp_path):
    session = make_session(tmp_path)
    session.notes.write("NOTES FROM BEFORE")
    session.lessons.add(Lesson("open-bag", "WHEN TEXT", "STEPS", "f", "v"))
    model = FakeModel(["nothing to do"])
    result = run_agent(session, "THE GOAL", model)
    assert (result.reason, result.summary) == ("done", "nothing to do")
    system = model.systems[0]
    assert "CORE GUIDE TEXT" in system and "- bag: the bag screen" in system
    assert "open-bag" in system and "WHEN TEXT" in system and "STEPS" not in system
    assert "NOTES FROM BEFORE" not in system
    first = model.sent[0][0]["content"]
    assert "THE GOAL" in first and "NOTES FROM BEFORE" in first


def test_forbidden_button_is_an_error_result_and_the_run_goes_on(tmp_path):
    session = make_session(tmp_path, showing=("bag",))
    model = FakeModel(
        [[("press", {"button": "PLUS"})], [("finish", {"summary": "ok"})]]
    )
    result = run_agent(session, "goal", model)
    assert result.reason == "done"
    assert not session.pad.pressed("PLUS")
    refused = model.results(2)[0]
    assert refused["is_error"] is True
    assert "PLUS" in refused["content"][0]["text"]
    (event,) = events(session, "forbidden_input")
    assert (event["button"], event["context"]) == ("PLUS", "bag")


def test_second_action_of_one_turn_is_judged_on_the_new_picture(tmp_path):
    session = make_session(tmp_path, moves={"X": ("bag",)})
    model = FakeModel(
        [
            [("press", {"button": "X"}), ("press", {"button": "PLUS"})],
            [("finish", {"summary": "ok"})],
        ]
    )
    run_agent(session, "goal", model)
    first, second = model.results(2)
    assert payload(first)["context_after"] == "bag"
    assert second["is_error"] is True
    assert session.pad.pressed("X") and not session.pad.pressed("PLUS")


def test_budget_stop_releases_what_was_held(tmp_path):
    session = make_session(tmp_path, max_usd=0.40)
    session.pad.hold("B")
    session.pad.stick("L", 1.0, 0.0)
    assert session.pad.state != PadState()
    # 6500 output tokens cost 0.13: after two calls a full reply no longer fits.
    model = FakeModel([[("look", {})]] * 5, usage=Usage(0, 6500, 0, 0))
    result = run_agent(session, "THE GOAL", model)
    assert (result.reason, result.model_calls) == ("budget", 2)
    assert len(model.sent) == 2
    assert session.pad.release_all_calls == 1
    assert session.pad.state == PadState()
    (final,) = events(session, "run_result")
    assert (final["reason"], final["goal"], final["context"]) == (
        "budget",
        "THE GOAL",
        "explore",
    )
    assert final["usd"] <= 0.40


def test_call_limit_stops_the_run(tmp_path):
    session = make_session(tmp_path, max_calls=1)
    result = run_agent(session, "goal", FakeModel([[("look", {})]] * 3))
    assert (result.reason, result.model_calls) == ("budget", 1)


class TrippingConsole(FakeConsole):
    """Trips the emergency stop while the Y button is going down."""

    release_error: Exception | None = None

    def _apply(self, state, hold_ms):
        super()._apply(state, hold_ms)
        if state.buttons & Button.Y:
            self._estop.trip()

    def _release_all(self):
        super()._release_all()
        if self.release_error is not None:
            raise self.release_error


def test_emergency_stop_during_an_action_ends_the_run(tmp_path):
    session = make_session(tmp_path, pad_class=TrippingConsole)
    model = FakeModel(
        [
            [("press", {"button": "Y"}), ("press", {"button": "A"})],
            [("press", {"button": "A"})],
        ]
    )
    result = run_agent(session, "goal", model)
    assert result.reason == "estop"
    assert session.pad.release_all_calls == 1
    assert len(model.sent) == 1
    assert not session.pad.pressed("A")
    assert [e["name"] for e in events(session, "tool")] == []
    assert events(session, "run_result")[0]["reason"] == "estop"


def test_emergency_stop_with_a_dead_link_still_ends_cleanly(tmp_path):
    session = make_session(tmp_path, pad_class=TrippingConsole)
    session.pad.release_error = ControllerError("the port is gone")
    result = run_agent(session, "goal", FakeModel([[("press", {"button": "Y"})]]))
    assert result.reason == "estop"
    assert session.pad.release_all_calls == 1


def test_stop_tripped_before_the_run_makes_no_model_call(tmp_path):
    session = make_session(tmp_path)
    session.estop.trip()
    model = FakeModel([[("look", {})]])
    assert run_agent(session, "goal", model).reason == "estop"
    assert model.sent == []


def test_oversized_notes_are_an_error_result(tmp_path):
    session = make_session(tmp_path)
    session.notes.write("kept")
    model = FakeModel(
        [
            [("notes_write", {"text": "x" * 2001})],
            [("notes_write", {"text": "new notes"})],
            [("finish", {"summary": "ok"})],
        ]
    )
    assert run_agent(session, "goal", model).reason == "done"
    refused = model.results(2)[0]
    assert refused["is_error"] is True and "2000" in refused["content"][0]["text"]
    assert session.notes.read() == "new notes"


def test_unknown_chapter_lists_the_chapters(tmp_path):
    session = make_session(tmp_path)
    model = FakeModel(
        [
            [("read_guide", {"chapter": "nope"}), ("read_guide", {"chapter": "bag"})],
            [("finish", {"summary": "ok"})],
        ]
    )
    run_agent(session, "goal", model)
    missing, found = model.results(2)
    assert missing["is_error"] is True and "bag" in missing["content"][0]["text"]
    assert "BAG CHAPTER TEXT" in found["content"][0]["text"]


def test_press_that_changes_nothing_says_so(tmp_path):
    session = make_session(tmp_path)
    model = FakeModel([[("press", {"button": "A"})], [("finish", {"summary": "ok"})]])
    run_agent(session, "goal", model)
    assert payload(model.results(2)[0])["picture_changed"] is False


def test_unknown_tool_and_bad_arguments_are_error_results(tmp_path):
    session = make_session(tmp_path)
    model = FakeModel(
        [
            [
                ("teleport", {}),
                ("press", {"button": "START"}),
                ("press", {"button": "A", "ms": 99999}),
                ("press", {}),
                ("hold_stick", {"which": "L", "x": 2, "y": 0, "ms": 100}),
                ("wait", {"ms": 60000}),
                ("finish", {}),
            ],
            [("finish", {"summary": "ok"})],
        ]
    )
    assert run_agent(session, "goal", model).reason == "done"
    assert all(r.get("is_error") for r in model.results(2))
    assert session.pad.log == []


def test_lessons_through_the_tools(tmp_path):
    session = make_session(tmp_path)
    lesson = {
        "slug": "open-bag",
        "when": "w",
        "steps": "s",
        "failure": "f",
        "verify": "v",
    }
    model = FakeModel(
        [
            [("lesson_add", lesson), ("lesson_add", lesson)],
            [("lesson_used", {"slug": "open-bag", "success": True})],
            [("lesson_read", {"slug": "open-bag"}), ("lesson_read", {"slug": "nope"})],
            [("finish", {"summary": "ok"})],
        ]
    )
    run_agent(session, "goal", model)
    added, duplicate = model.results(2)
    assert "is_error" not in added and duplicate["is_error"] is True
    read, missing = model.results(4)
    assert payload(read)["steps"] == "s" and payload(read)["successes"] == 1
    assert missing["is_error"] is True
    assert session.lessons.get("open-bag").uses == 1


def test_stick_and_wait(tmp_path):
    session = make_session(tmp_path)
    model = FakeModel(
        [
            [("hold_stick", {"which": "L", "x": 1, "y": 0.0, "ms": 100})],
            [("wait", {"ms": 20})],
            [("finish", {"summary": "ok"})],
        ]
    )
    run_agent(session, "goal", model)
    assert session.pad.log[0][0].lx == 255
    assert payload(model.results(2)[0])["context_after"] == "explore"
    assert payload(model.results(3)[0]) == {"waited_ms": 20, "context": "explore"}


def test_refusal_and_cut_off_reply_end_the_run_as_errors(tmp_path):
    for stop_reason, expected in (("refusal", "cyber"), ("max_tokens", "cut off")):
        session = make_session(tmp_path / stop_reason)
        reply = ModelReply([], [], "", stop_reason, Usage(10, 10), "cyber")
        result = run_agent(session, "goal", FakeModel([reply]))
        assert result.reason == "error" and expected in result.summary


def test_model_and_hardware_failures_end_the_run_as_errors(tmp_path):
    class Broken(FakeModel):
        def send(self, system, tools, messages):
            raise ModelError("RateLimitError: slow down")

    session = make_session(tmp_path / "a")
    result = run_agent(session, "goal", Broken([]))
    assert (result.reason, result.model_calls) == ("error", 0)
    assert "RateLimitError" in result.summary
    assert session.pad.release_all_calls == 1

    session = make_session(tmp_path / "b")
    session.source.stalled = True
    result = run_agent(session, "goal", FakeModel([[("look", {})]]))
    assert result.reason == "error" and "CaptureError" in result.summary
    assert events(session, "run_result")[0]["context"] is None


def test_the_api_key_never_reaches_the_log(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET-VALUE")
    session = make_session(tmp_path, moves={"X": ("bag",)})
    model = FakeModel(
        [[("look", {})], [("press", {"button": "X"})], [("finish", {"summary": "ok"})]]
    )
    run_agent(session, "goal", model)
    assert "SECRET" not in (session.log.dir / "events.jsonl").read_text("utf-8")
