import argparse
import contextlib
import dataclasses
import os
import sys
from pathlib import Path

from autoplay.core.agent.budget import PRICES, Budget
from autoplay.core.agent.config import AgentConfig, load_agent_config
from autoplay.core.agent.loop import ModelClient, ModelError, RunResult, run_agent
from autoplay.core.agent.session import Session
from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.factory import SOURCE_FORMS, open_source
from autoplay.core.controller.base import ControllerError
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.serial_backend import SerialController
from autoplay.core.memory.lessons import LessonStore
from autoplay.core.memory.notes import ProgressNotes
from autoplay.core.pack.guard import InputGuard
from autoplay.core.pack.loader import PackError, load_pack
from autoplay.core.runtime.estop import EStop
from autoplay.core.runtime.runlog import RunLog
from autoplay.core.tools.lessons_cli import MEMORY_ROOT, memory_dir

KEY_NAME = "ANTHROPIC_API_KEY"
ENV_FILE = Path(".env")
EXIT_CODES = {"done": 0, "error": 1, "estop": 3, "budget": 4}


def read_env_file(path: Path) -> dict[str, str]:
    """Return the KEY=VALUE pairs of an env file; a missing file is empty.
    Blank lines, `#` comments, a leading `export` and quotes around a value
    are allowed."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    pairs = {}
    for line in lines:
        line = line.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        pairs[key.strip()] = value
    return pairs


def find_api_key(env_file: Path = ENV_FILE) -> str | None:
    """The API key from the environment, else from the env file, else None.
    The value is never printed or logged."""
    return os.environ.get(KEY_NAME) or read_env_file(env_file).get(KEY_NAME) or None


def make_model(config: AgentConfig, api_key: str) -> ModelClient:
    from autoplay.core.agent.client import AnthropicModel

    return AnthropicModel(config, api_key=api_key)


def _handle(args: argparse.Namespace) -> int:
    def fail(message: object, code: int) -> int:
        print(f"ap play: {message}", file=sys.stderr)
        return code

    if bool(args.port) == args.dummy:
        return fail("give exactly one of --port and --dummy", 2)
    try:
        config = load_agent_config()
        if args.model is not None:
            if args.model not in PRICES:
                raise ValueError(
                    f"no price is known for model {args.model!r}; "
                    f"known models: {', '.join(PRICES)}"
                )
            config = dataclasses.replace(config, model=args.model)
        if args.max_usd is not None:
            if args.max_usd <= 0:
                raise ValueError("--max-usd must be positive")
            config = dataclasses.replace(config, max_usd_per_run=args.max_usd)
        if not args.goal.strip():
            raise ValueError("--goal must not be empty")
        source = open_source(args.source)
    except ValueError as exc:
        return fail(exc, 2)
    try:
        pack = load_pack(Path(args.pack))
    except PackError as exc:
        return fail(exc, 1)
    api_key = find_api_key()
    if api_key is None:
        return fail(
            f"no API key: set {KEY_NAME} in the environment or in {ENV_FILE} "
            "(see .env.example)",
            1,
        )
    memory = memory_dir(pack.name, args.memory_root)
    # Each resource is registered for closing as soon as it exists, so a
    # failure while opening a later one still releases the earlier ones.
    with contextlib.ExitStack() as stack:
        try:
            model = make_model(config, api_key)
            stack.callback(source.close)
            source.start()
            pad = DummyController() if args.dummy else SerialController(args.port)
            stack.callback(pad.close)
            estop = EStop()
            stack.callback(estop.close)
            # Before anything can press a button.
            pad.attach_estop(estop)
            log = RunLog("play")
            stack.callback(log.close)
        except (CaptureError, ControllerError, ModelError, OSError) as exc:
            return fail(exc, 1)
        session = Session(
            pack=pack,
            pad=pad,
            source=source,
            guard=InputGuard(pack, source, pad, log),
            notes=ProgressNotes(memory / "notes.md", config.notes_limit),
            lessons=LessonStore(memory / "lessons"),
            budget=Budget(
                config.model,
                config.max_usd_per_run,
                config.max_model_calls,
                config.max_tokens,
            ),
            config=config,
            log=log,
            estop=estop,
        )
        print(f"ap play: logging to {log.dir}; F12 stops", file=sys.stderr)
        result: RunResult = run_agent(session, args.goal, model)
        log_dir = log.dir
    if result.summary:
        print(result.summary)
    print(
        f"reason={result.reason} model_calls={result.model_calls} "
        f"usd={result.usd:.4f} log={log_dir}"
    )
    return EXIT_CODES[result.reason]


def register(sub) -> None:
    p = sub.add_parser(
        "play",
        help="let the model pursue a goal in a game, through a game pack",
        description=(
            "Runs the model loop on a game pack until the model finishes, a limit "
            "is reached or F12 (the emergency stop) is pressed. Exit code: 0 done, "
            "1 error, 3 emergency stop, 4 budget."
        ),
    )
    p.add_argument("--pack", required=True, help="game pack directory")
    p.add_argument("--goal", required=True, help="what the model should achieve")
    p.add_argument("--port", help="serial port of the firmware, such as COM4")
    p.add_argument(
        "--dummy",
        action="store_true",
        help="no gamepad: inputs are only recorded (for a video or image source)",
    )
    p.add_argument(
        "--source",
        default="device:0",
        help=f"frame source: {SOURCE_FORMS} (default device:0)",
    )
    p.add_argument("--max-usd", type=float, help="spending limit of this run")
    p.add_argument("--model", help=f"one of {', '.join(PRICES)}")
    p.add_argument(
        "--memory-root",
        type=Path,
        default=MEMORY_ROOT,
        help="where notes and lesson drafts are kept (default memory/)",
    )
    p.set_defaults(func=_handle)
