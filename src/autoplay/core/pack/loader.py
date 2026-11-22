from dataclasses import dataclass, field
from pathlib import Path

import yaml

from autoplay.core.controller.base import BUTTON_NAMES
from autoplay.core.pack.routines import Routine, RoutineError, load_routines
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
_OPTIONAL = {"forbidden", "confirm", "routines", "on_stop"}


class PackError(ValueError):
    """A game pack is malformed or incomplete."""


@dataclass(frozen=True)
class GamePack:
    """Everything the core knows about one game.

    `forbidden` maps each context that has an entry to the buttons refused
    there, the ones refused everywhere included; ask `forbidden_in`.
    `confirm` maps a context or an overlay to the buttons that are sent there
    only with a stated confirmation. `on_stop` names the routine run once when
    a run ends."""

    name: str
    root: Path
    states: StateRegistry
    core_guide: str
    chapters: dict[str, Path]
    chapter_summaries: dict[str, str]
    forbidden: dict[str, frozenset[str]]
    home_context: str
    forbidden_everywhere: frozenset[str] = frozenset()
    overlays: StateRegistry = field(default_factory=StateRegistry)
    confirm: dict[str, frozenset[str]] = field(default_factory=dict)
    routines: dict[str, Routine] = field(default_factory=dict)
    on_stop: str | None = None

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


def _load_states(root: Path, path: Path) -> tuple[StateRegistry, StateRegistry]:
    try:
        for entry in read_states_entries(path):
            template = entry.get("template")
            if not isinstance(template, str):
                continue
            target = path.parent / template
            if not target.resolve().is_relative_to(root.resolve()):
                raise PackError(
                    f"{path}: state {entry['name']!r} template {template!r} is "
                    "outside the pack"
                )
            if not target.is_file():
                raise PackError(
                    f"{path}: state {entry['name']!r} needs {target}, which is "
                    "missing; templates are local only (they are cut from game "
                    "screenshots and never committed). Rebuild them with "
                    "`ap template`, see docs/DEVELOPMENT.md."
                )
        return load_states_file(path), load_states_file(path, overlays=True)
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
        confirm:                          # optional
          <context or overlay>: [BUTTON, ...]
        routines: <file>                  # optional, Python, see routines.py
        on_stop: <routine name>           # optional, takes no arguments

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
    unknown = sorted(str(k) for k in data.keys() - _KEYS - _OPTIONAL)
    missing = sorted(_KEYS - _OPTIONAL - data.keys())
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

    states, overlays = _load_states(
        root, _inside(root, data["states_file"], "states_file")
    )
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
    raw_confirm = data.get("confirm") or {}
    if not isinstance(raw_confirm, dict):
        raise PackError(f"{source}: confirm must map contexts or overlays to buttons")
    confirm: dict[str, frozenset[str]] = {}
    for where, raw in raw_confirm.items():
        if where not in contexts and where not in overlays.names:
            raise PackError(
                f"{source}: confirm names {where!r}, which is neither a state nor "
                f"an overlay; states {contexts}, overlays {overlays.names}"
            )
        confirm[where] = _buttons(source, where, raw)

    routines: dict[str, Routine] = {}
    if data.get("routines") is not None:
        routines_path = _inside(root, data["routines"], "routines")
        if not routines_path.is_file():
            raise PackError(f"{source}: routines file {routines_path} is missing")
        try:
            routines = load_routines(routines_path)
        except RoutineError as exc:
            raise PackError(str(exc)) from exc
    on_stop = data.get("on_stop")
    if on_stop is not None and (on_stop not in routines or routines[on_stop].params):
        raise PackError(
            f"{source}: on_stop must name a routine without arguments; the "
            f"routines are {sorted(routines)}"
        )
    return GamePack(
        overlays=overlays,
        confirm=confirm,
        routines=routines,
        on_stop=on_stop,
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
