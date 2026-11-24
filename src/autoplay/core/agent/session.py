from dataclasses import dataclass

from autoplay.core.agent.budget import Budget
from autoplay.core.agent.config import AgentConfig
from autoplay.core.capture.base import FrameSource
from autoplay.core.controller.base import Controller
from autoplay.core.memory.lessons import LessonStore
from autoplay.core.memory.notes import ProgressNotes
from autoplay.core.pack.guard import InputGuard
from autoplay.core.pack.loader import GamePack
from autoplay.core.runtime.estop import EStop
from autoplay.core.runtime.runlog import RunLog


@dataclass
class Session:
    """Everything one run works with. Tools send inputs only through `guard`;
    `pad` is for the heartbeat and for releasing everything when the run ends."""

    pack: GamePack
    pad: Controller
    source: FrameSource
    guard: InputGuard
    notes: ProgressNotes
    lessons: LessonStore
    budget: Budget
    config: AgentConfig
    log: RunLog
    estop: EStop
