import contextlib
from dataclasses import dataclass
from typing import Protocol

from autoplay.core import clock
from autoplay.core.agent.budget import BudgetExceeded, Usage
from autoplay.core.agent.session import Session
from autoplay.core.agent.tools import FINISH, UNKNOWN, Tool, ToolOutput, build_tools
from autoplay.core.controller.base import BUTTON_NAMES
from autoplay.core.pack.hands import Hands
from autoplay.core.runtime.estop import EmergencyStop

DONE, BUDGET, ESTOP, ERROR = "done", "budget", "estop", "error"
_LOGGED_TEXT = 2000

__all__ = [
    "ModelClient",
    "ModelError",
    "ModelReply",
    "RunResult",
    "Session",
    "ToolCall",
    "run_agent",
]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ModelReply:
    """One model turn. `content` is what goes back into the history, exactly
    as the model returned it."""

    content: list
    tool_calls: list[ToolCall]
    text: str
    stop_reason: str
    usage: Usage
    refusal_category: str | None = None
    # Thinking blocks of earlier turns the API left out of this call.
    dropped_thinking: int = 0


class ModelError(RuntimeError):
    """The model could not be reached or rejected the request."""


class ModelClient(Protocol):
    def count_tokens(self, system: str, tools: list[dict], messages: list[dict]) -> int:
        """Return the size in tokens of the prompt `send` would send; raise
        ModelError when it cannot be counted."""
        ...

    def send(
        self,
        system: str,
        tools: list[dict],
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> ModelReply:
        """Send the whole conversation and return the next turn; raise
        ModelError when no turn comes back."""
        ...


@dataclass(frozen=True)
class RunResult:
    """How a run ended: `reason` is "done", "budget", "estop" or "error"."""

    reason: str
    summary: str
    model_calls: int
    usd: float


def build_system(session: Session) -> str:
    """The system prompt: fixed for the whole run, so it is cached."""
    pack = session.pack
    chapters = "\n".join(
        f"- {name}: {summary}" for name, summary in pack.chapter_summaries.items()
    )
    drafts = "\n".join(
        f"- {lesson.slug} (used {lesson.uses}, worked {lesson.successes}): "
        f"{lesson.when}"
        for lesson in session.lessons.list()
    )
    return f"""\
You are playing a video game on a real console. A capture card is your eyes and \
a gamepad is your hands; you act only through the tools.

How to work:
- Look before you act. Every input tool tells you the context before and after \
and shows the screen: check that what you meant to happen did happen before the \
next step. If it did not, look again rather than repeating the input blindly.
- The game guide below says what each button does in each context. Read the \
chapter for a context with `read_guide` before acting there for the first time. \
Where the guide says something is not known, do not guess with an input that \
could lose progress or items.
- Button names are: {", ".join(BUTTON_NAMES)}.
- Tools described as "Routine of this game" are fixed sequences that check the \
picture at each step. Prefer one over doing the same thing input by input; when \
one stops, look, fix what it reports, and go on from there.
- Some buttons are refused in some contexts, and in a picture that is not \
recognised. A refused input was not sent. That is a safety rule of this setup: \
do not look for another way to reach the same effect.
- Play offline, single-player content only. Never open a shop that costs real \
money, online play, or system menus outside the game.
- The progress notes carry over between runs and hold at most \
{session.notes.limit} characters. `notes_write` replaces all of them, so write \
the whole text you want kept.
- Lesson drafts are short how-tos from earlier runs. They are unverified: read \
one with `lesson_read` when it fits, and report with `lesson_used` whether it \
worked. Add one with `lesson_add` when you worked out something reusable.
- When the goal is reached, or cannot be reached, bring the game back to the \
`{pack.home_context}` context if you can and call `finish`.

# Game guide: {pack.name}

{pack.core_guide.strip()}

# Guide chapters

{chapters or "(none)"}

# Lesson drafts

{drafts or "(none)"}
"""


def _first_message(session: Session, goal: str) -> str:
    notes = session.notes.read().strip()
    return f"Goal of this run: {goal}\n\nProgress notes:\n{notes or '(none yet)'}"


def _check_stop(session: Session) -> None:
    if session.estop.tripped.is_set():
        raise EmergencyStop("the emergency stop was tripped")


def _result_block(call: ToolCall, output: ToolOutput) -> dict:
    block = {"type": "tool_result", "tool_use_id": call.id, "content": output.content}
    if output.is_error:
        block["is_error"] = True
    return block


DROPPED = "[screenshot dropped]"


def trim_pictures(messages: list[dict], keep: int) -> int:
    """Once more than `2 * keep` screenshots sit in the tool results, replace
    all but the newest `keep` with a line of text, in place; return how many
    were replaced. Assistant turns are never touched. This is still an edit of
    what the model was sent: the client asks the API to leave out the thinking
    blocks made before it (see client.THINKING), so it is done in batches."""
    spots = [
        (block["content"], index)
        for message in messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result" and isinstance(block.get("content"), list)
        for index, part in enumerate(block["content"])
        if part.get("type") == "image"
    ]
    if len(spots) <= 2 * keep:
        return 0
    for content, index in spots[:-keep]:
        content[index] = {"type": "text", "text": DROPPED}
    return len(spots) - keep


def _converse(
    session: Session, goal: str, client: ModelClient, tools: dict[str, Tool]
) -> tuple[str, str]:
    system = build_system(session)
    definitions = [tool.definition() for tool in tools.values()]
    # Append only: the model's earlier turns must come back unchanged.
    messages: list[dict] = [{"role": "user", "content": _first_message(session, goal)}]
    while True:
        _check_stop(session)
        trim_pictures(messages, session.config.keep_screenshots)
        # The whole prompt is counted, so the reserve is a bound and not a guess.
        session.budget.reserve(client.count_tokens(system, definitions, messages))
        started = clock.now()
        # Sent with the reply length the reserve assumed, or it would not bound it.
        reply = client.send(
            system, definitions, messages, max_tokens=session.budget.reply_limit()
        )
        usd = session.budget.add(reply.usage)
        session.log.event(
            "model_call",
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cache_read_tokens=reply.usage.cache_read_tokens,
            cache_write_tokens=reply.usage.cache_write_tokens,
            usd=round(usd, 6),
            seconds=round(clock.now() - started, 3),
            stop_reason=reply.stop_reason,
            dropped_thinking=reply.dropped_thinking,
            text=reply.text[:_LOGGED_TEXT],
        )
        messages.append({"role": "assistant", "content": reply.content})
        if reply.stop_reason == "refusal":
            return ERROR, f"the model refused (category: {reply.refusal_category})"
        if reply.stop_reason == "end_turn" and not reply.tool_calls:
            return DONE, reply.text
        if reply.stop_reason != "tool_use" or not reply.tool_calls:
            # max_tokens, a full context window, a pause: the turn is not whole.
            return ERROR, f"the model's turn ended early ({reply.stop_reason})"
        results = []
        for call in reply.tool_calls:
            _check_stop(session)
            tool = tools.get(call.name)
            if tool is None:
                output = ToolOutput(
                    [{"type": "text", "text": f"there is no tool {call.name!r}"}], True
                )
            else:
                output = tool.run(call.input)
            session.log.event(
                "tool",
                name=call.name,
                input=call.input,
                is_error=output.is_error,
                result=output.text[:_LOGGED_TEXT],
            )
            if call.name == FINISH and not output.is_error:
                return DONE, call.input["summary"]
            results.append(_result_block(call, output))
        messages.append({"role": "user", "content": results})


def _log(session: Session, kind: str, **fields) -> None:
    """Log without letting a failing log change how the run ends."""
    with contextlib.suppress(Exception, KeyboardInterrupt):
        session.log.event(kind, **fields)


def _wind_down(session: Session, reason: str) -> tuple[str, str | None]:
    """Release the gamepad and read the context the game was left in. Returns
    the reason, changed to "estop" if the stop tripped meanwhile. Nothing in
    here raises: a failure to release or to look must not hide the outcome."""
    if session.estop.tripped.is_set():
        # release_all() refuses after a trip; the stop's own callback has
        # released, or with the link gone the firmware watchdog does.
        reason = ESTOP
    else:
        try:
            session.pad.release_all()
        except EmergencyStop:
            reason = ESTOP
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
            _log(session, "release_failed", message=f"{type(exc).__name__}: {exc}")
    try:
        context = session.guard.context() or UNKNOWN
    except (Exception, KeyboardInterrupt):  # noqa: BLE001
        context = None
    return reason, context


def _on_stop(session: Session) -> str | None:
    """Run the pack's `on_stop` routine, which brings the game to a state it
    can be left in, and return what it reported. Nothing in here raises."""
    name = session.pack.on_stop
    if name is None:
        return None
    try:
        hands = Hands(
            session.pack, session.guard, session.pad, session.source, session.log
        )
        return str(session.pack.routines[name].run(hands))
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        with contextlib.suppress(Exception, KeyboardInterrupt):
            session.pad.release_all()
        return f"failed: {type(exc).__name__}: {exc}"


def run_agent(
    session: Session, goal: str, client: ModelClient | None = None
) -> RunResult:
    """Let the model pursue `goal` with the session's tools until it finishes,
    a limit is reached, the emergency stop trips or something breaks. However
    the run ends, Ctrl+C included, the gamepad is released and a `run_result`
    event is logged; nothing is raised."""
    reason, summary = ERROR, ""
    try:
        if client is None:
            from autoplay.core.agent.client import AnthropicModel

            client = AnthropicModel(session.config)
        tools = {tool.name: tool for tool in build_tools(session)}
        session.log.event("run_start", goal=goal, model=session.config.model)
        reason, summary = _converse(session, goal, client, tools)
    except EmergencyStop:
        reason, summary = ESTOP, "the emergency stop was tripped"
    except BudgetExceeded as exc:
        reason, summary = BUDGET, str(exc)
    except KeyboardInterrupt:
        reason, summary = ERROR, "interrupted from the keyboard"
    except Exception as exc:  # noqa: BLE001 - the run boundary: report, never raise
        reason, summary = ERROR, f"{type(exc).__name__}: {exc}"
    finally:
        reason, context = _wind_down(session, reason)
        left = None
        if reason != ESTOP:
            left = _on_stop(session)
            if left is not None:
                # The routine moved the game; report where it is now.
                reason, context = _wind_down(session, reason)
    result = RunResult(reason, summary, session.budget.calls, session.budget.spent_usd)
    _log(
        session,
        "run_result",
        reason=reason,
        goal=goal,
        context=context,
        on_stop=left,
        model_calls=result.model_calls,
        usd=round(result.usd, 6),
        summary=summary[:_LOGGED_TEXT],
    )
    return result
