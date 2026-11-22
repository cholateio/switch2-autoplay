import argparse
import contextlib
import sys
from importlib.metadata import version

from autoplay.core.tools import (
    capture_view,
    classify,
    latency,
    lessons_cli,
    pad,
    play,
    probe,
    soak,
    template,
)

# Each entry is a module-level `register(sub) -> None` that adds one subparser
# and sets `func=handler`, where `handler(args) -> int` is the exit code.
_REGISTRARS = (
    probe.register,
    capture_view.register,
    pad.register,
    soak.register,
    template.register,
    classify.register,
    latency.register,
    lessons_cli.register,
    play.register,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ap")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('switch2-autoplay')}"
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    for register in _REGISTRARS:
        register(sub)
    return parser


def _tolerant_output() -> None:
    # A Windows console uses a legacy code page (cp950 here); text it cannot
    # encode, such as a model's summary, must not crash the command.
    # A closed or replaced stream may refuse; the command must still run.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, TypeError, ValueError, OSError):
            stream.reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    """Run the `ap` CLI and return its exit code; argparse exits become return codes."""
    _tolerant_output()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)
