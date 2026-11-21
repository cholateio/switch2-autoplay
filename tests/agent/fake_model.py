"""A scripted stand-in for the model, and a session built on the synthetic pack."""

import copy

from synth_pack import FakeConsole, FakeScreen, write_pack

from autoplay.core.agent.budget import Budget, Usage
from autoplay.core.agent.config import load_agent_config
from autoplay.core.agent.loop import ModelReply, ToolCall
from autoplay.core.agent.session import Session
from autoplay.core.memory.lessons import LessonStore
from autoplay.core.memory.notes import ProgressNotes
from autoplay.core.pack.guard import InputGuard
from autoplay.core.pack.loader import load_pack
from autoplay.core.runtime.estop import EStop
from autoplay.core.runtime.runlog import RunLog


class FakeModel:
    """Replies from a script. Each entry is one turn: a list of
    (tool name, input) calls, a string for a turn of plain text, or a ready
    ModelReply. `sent` keeps a copy of the conversation as each call saw it."""

    def __init__(
        self, script, usage: Usage | None = None, prompt_tokens: int = 1000
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.counted = 0
        self.script = list(script)
        self.usage = usage or Usage(1000, 100, 0, 0)
        self.sent: list[list[dict]] = []
        self.systems: list[str] = []
        self.tools: list[list[dict]] = []

    def count_tokens(self, system, tools, messages) -> int:
        self.counted += 1
        return self.prompt_tokens

    def send(self, system, tools, messages) -> ModelReply:
        self.systems.append(system)
        self.tools.append(tools)
        self.sent.append(copy.deepcopy(messages))
        turn = self.script.pop(0)
        if isinstance(turn, ModelReply):
            return turn
        if isinstance(turn, str):
            return ModelReply(
                [{"type": "text", "text": turn}], [], turn, "end_turn", self.usage
            )
        number = len(self.sent)
        calls = [
            ToolCall(f"call_{number}_{i}", name, args)
            for i, (name, args) in enumerate(turn)
        ]
        content = [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
            for c in calls
        ]
        return ModelReply(content, calls, "", "tool_use", self.usage)

    def results(self, call_number: int) -> list[dict]:
        """The tool results the model was sent as input to its call number
        `call_number` (counted from 1): the answers to the turn before."""
        return self.sent[call_number - 1][-1]["content"]


def make_session(
    tmp_path,
    *,
    showing=("explore",),
    moves=None,
    max_usd=2.0,
    max_calls=150,
    pad_class=FakeConsole,
) -> Session:
    pack = load_pack(write_pack(tmp_path / "pack"))
    source = FakeScreen(*showing)
    pad = pad_class(source, moves or {})
    config = load_agent_config()
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
        budget=Budget(config.model, max_usd, max_calls, config.max_tokens),
        config=config,
        log=log,
        estop=estop,
    )
