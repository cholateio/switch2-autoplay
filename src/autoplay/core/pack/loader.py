from dataclasses import dataclass
from pathlib import Path

import yaml

from autoplay.core.controller.base import BUTTON_NAMES
from autoplay.core.perception.states import (
    StateRegistry,
    StatesFileError,
    load_states_file,
    read_states_entries,
)

MANIFEST = "manifest.yaml"
# In `forbidden`, the buttons refused in every context.
EVERYWHERE = "*"
_KEYS = {"name", "home_context", "guide", "states_file", "forbidden"}


class PackError(ValueError):
    """A game pack is malformed or incomplete."""


@dataclass(frozen=True)
class GamePack:
    """Everything the core knows about one game.

    `forbidden` maps each context that has an entry to the buttons refused
    there, the ones refused everywhere included; ask `forbidden_in`."""

    name: str
    root: Path
    states: StateRegistry
    core_guide: str
    chapters: dict[str, Path]
    chapter_summaries: dict[str, str]
    forbidden: dict[str, frozenset[str]]
    home_context: str
    forbidden_everywhere: frozenset[str] = frozenset()

    def forbidden_in(self, context: str | None) -> frozenset[str]:
        """The buttons refused in `context`. None is a picture no context was
        recognised in: there, every button refused anywhere is refused."""
        if context is None:
            return self.forbidden_everywhere.union(*self.forbidden.values())
        return self.forbidden.get(context, self.forbidden_everywhere)

    def read_chapter(self, name: str) -> str:
        """Return a guide chapter; raise PackError listing the chapters there
        are when `name` is not one of them."""
        path = self.chapters.get(name)
        if path is None:
            known = ", ".join(self.chapters) or "(none)"
            raise PackError(f"no chapter {name!r}; the chapters are: {known}")
        return _read(path)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackError(f"cannot read {path}: {exc}") from exc


def _inside(root: Path, relative: object, what: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise PackError(f"{root / MANIFEST}: {what} must be a file path")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise PackError(f"{root / MANIFEST}: {what} {relative!r} is outside the pack")
    return path


def _buttons(source: Path, context: str, raw: object) -> frozenset[str]:
    if not isinstance(raw, list) or not all(isinstance(b, str) for b in raw):
        raise PackError(f"{source}: forbidden.{context} must be a list of buttons")
    for button in raw:
        if button not in BUTTON_NAMES:
            raise PackError(
                f"{source}: forbidden.{context} names unknown button {button!r}; "
                f"expected one of {', '.join(BUTTON_NAMES)}"
            )
    return frozenset(raw)


def _load_states(path: Path) -> StateRegistry:
    try:
        for entry in read_states_entries(path):
            template = entry.get("template")
            if isinstance(template, str) and not (path.parent / template).is_file():
                raise PackError(
                    f"{path}: state {entry['name']!r} needs {path.parent / template}, "
                    "which is missing; templates are local only (they are cut from "
                    "game screenshots and never committed). Rebuild them with "
                    "`ap template`, see docs/DEVELOPMENT.md."
                )
        return load_states_file(path)
    except StatesFileError as exc:
        raise PackError(str(exc)) from exc


def load_pack(path: Path) -> GamePack:
    """Load the pack in directory `path`, described by its `manifest.yaml`:

        name: <pack name>
        home_context: <the context a run returns to>
        guide:
          core: <file>                    # sent with every model call
          chapters:
            - {name, file, summary}       # read on demand
        states_file: <file>               # one state per context
        forbidden:
          "*": [BUTTON, ...]              # refused everywhere
          <context>: [BUTTON, ...]

    Paths are relative to the pack directory and must stay inside it; template
    paths are relative to the states file. Raises PackError saying what is
    wrong, and for a missing template where it belongs."""
    root = Path(path)
    source = root / MANIFEST
    try:
        data = yaml.safe_load(_read(source))
    except yaml.YAMLError as exc:
        raise PackError(f"cannot read {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackError(f"{source}: expected a mapping")
    unknown = sorted(str(k) for k in data.keys() - _KEYS)
    missing = sorted(_KEYS - {"forbidden"} - data.keys())
    if unknown or missing:
        raise PackError(f"{source}: missing keys {missing}, unknown keys {unknown}")
    name, home = data["name"], data["home_context"]
    if (
        not isinstance(name, str)
        or not name.replace("-", "").replace("_", "").isalnum()
    ):
        raise PackError(f"{source}: name must be letters, digits, '_' or '-'")

    guide = data["guide"]
    if not isinstance(guide, dict) or guide.keys() - {"core", "chapters"}:
        raise PackError(f"{source}: guide must hold `core` and `chapters`")
    core_guide = _read(_inside(root, guide.get("core"), "guide.core"))
    chapters: dict[str, Path] = {}
    summaries: dict[str, str] = {}
    listed = guide.get("chapters") or []
    if not isinstance(listed, list):
        raise PackError(f"{source}: guide.chapters must be a list")
    for entry in listed:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("summary"), str)
        ):
            raise PackError(
                f"{source}: each chapter needs a name, a file and a summary"
            )
        chapter = entry["name"]
        if chapter in chapters:
            raise PackError(f"{source}: chapter {chapter!r} is listed twice")
        chapter_path = _inside(root, entry.get("file"), f"chapter {chapter!r} file")
        if not chapter_path.is_file():
            raise PackError(
                f"{source}: chapter {chapter!r} file {chapter_path} is missing"
            )
        chapters[chapter] = chapter_path
        summaries[chapter] = entry["summary"]

    states = _load_states(_inside(root, data["states_file"], "states_file"))
    contexts = states.names
    if home not in contexts:
        raise PackError(
            f"{source}: home_context {home!r} is not a state; the states are {contexts}"
        )
    raw_forbidden = data.get("forbidden") or {}
    if not isinstance(raw_forbidden, dict):
        raise PackError(f"{source}: forbidden must map contexts to button lists")
    everywhere = _buttons(source, EVERYWHERE, raw_forbidden.get(EVERYWHERE, []))
    forbidden: dict[str, frozenset[str]] = {}
    for context, raw in raw_forbidden.items():
        if context == EVERYWHERE:
            continue
        if context not in contexts:
            raise PackError(
                f"{source}: forbidden names context {context!r}, which is not a "
                f"state; the states are {contexts}"
            )
        forbidden[context] = _buttons(source, context, raw) | everywhere
    return GamePack(
        name=name,
        root=root,
        states=states,
        core_guide=core_guide,
        chapters=chapters,
        chapter_summaries=summaries,
        forbidden=forbidden,
        home_context=home,
        forbidden_everywhere=everywhere,
    )
