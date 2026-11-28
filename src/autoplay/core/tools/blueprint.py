import argparse
import importlib.util
import itertools
import sys
from pathlib import Path

from autoplay.core.pack.loader import MANIFEST

# The file in a game pack that knows the game's blueprint format. It exposes
# `check(path)`, returning an object with `errors`, `warnings` and `summary`
# (lists of lines), and `preview(path)`, returning one HTML page as text. Both
# raise ValueError for a file that cannot be read.
PACK_MODULE = "blueprint.py"
_ids = itertools.count()


class BlueprintToolError(Exception):
    """The pack or its blueprint module cannot be used."""


def find_pack(blueprint: Path) -> Path:
    """The pack directory a blueprint file lies in: its nearest ancestor that
    holds a manifest."""
    for parent in Path(blueprint).resolve().parents:
        if (parent / MANIFEST).is_file():
            return parent
    raise BlueprintToolError(
        f"{blueprint} is not inside a game pack; name the pack with --pack"
    )


def load_module(pack: Path):
    """Import the pack's blueprint module."""
    path = Path(pack) / PACK_MODULE
    if not path.is_file():
        raise BlueprintToolError(f"the pack {pack} has no {PACK_MODULE}")
    spec = importlib.util.spec_from_file_location(f"_pack_blueprint_{next(_ids)}", path)
    if spec is None or spec.loader is None:
        raise BlueprintToolError(f"{path}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while it is executed.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[spec.name]
        raise BlueprintToolError(f"{path}: {type(exc).__name__}: {exc}") from exc
    for name in ("check", "preview"):
        if not callable(getattr(module, name, None)):
            raise BlueprintToolError(f"{path}: has no function {name}")
    return module


def _handle(args: argparse.Namespace) -> int:
    try:
        module = load_module(Path(args.pack) if args.pack else find_pack(args.file))
        if args.action == "preview":
            page = module.preview(args.file)
            out = args.out or args.file.with_suffix(".preview.html")
            out.write_text(page, encoding="utf-8")
            print(f"wrote {out}")
            return 0
        report = module.check(args.file)
    except (BlueprintToolError, ValueError, OSError) as exc:
        print(f"ap blueprint: {exc}", file=sys.stderr)
        return 1
    for line in report.summary:
        print(line)
    for line in report.warnings:
        print(f"warning: {line}")
    for line in report.errors:
        print(f"error: {line}")
    if report.errors:
        print(f"FAILED: {len(report.errors)} error(s)")
        return 1
    print(f"OK: {len(report.warnings)} warning(s)")
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "blueprint",
        help="check or draw a blueprint file of a game pack",
        description=(
            "A blueprint describes a structure to build, in a format its game "
            "pack defines. `check` prints the size, the materials and what is "
            "wrong; `preview` writes one local HTML page."
        ),
    )
    actions = p.add_subparsers(dest="action", metavar="action", required=True)

    def common(parser) -> None:
        parser.add_argument("file", type=Path, help="the blueprint file")
        parser.add_argument(
            "--pack",
            help="game pack directory (default: the pack the file lies in)",
        )
        parser.set_defaults(func=_handle)

    common(actions.add_parser("check", help="print the materials and the errors"))
    preview_p = actions.add_parser("preview", help="write a local HTML preview")
    common(preview_p)
    preview_p.add_argument(
        "-o",
        "--out",
        type=Path,
        help="where to write the page (default: FILE with .preview.html)",
    )
