import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from autoplay.core.memory.notes import write_atomic

_SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
MAX_SLUG = 60
MAX_WHEN = 200
MAX_FIELD = 1500
_TEXT_FIELDS = ("when", "steps", "failure", "verify")


@dataclass
class Lesson:
    """A draft of something a run learned: `when` it applies, the `steps`,
    what to do on `failure`, and how to `verify` it worked. `uses` and
    `successes` count how often a run applied it and how often that worked."""

    slug: str
    when: str
    steps: str
    failure: str
    verify: str
    uses: int = 0
    successes: int = 0


def check_slug(slug: object) -> str:
    """Return `slug`; raise ValueError unless it is lower-case letters and
    digits in groups joined by single hyphens, at most MAX_SLUG long."""
    if (
        not isinstance(slug, str)
        or len(slug) > MAX_SLUG
        or not _SLUG_RE.fullmatch(slug)
    ):
        raise ValueError(
            f"invalid lesson slug {slug!r}; use lower-case letters, digits and "
            f"single hyphens, at most {MAX_SLUG} characters"
        )
    return slug


class LessonStore:
    """Lesson drafts, one file each under `root`. Drafts are written by a run
    and are not trusted guide text until a person promotes them."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, slug: str) -> Path:
        return self.root / f"{check_slug(slug)}.md"

    def _write(self, lesson: Lesson) -> Path:
        path = self._path(lesson.slug)
        body = yaml.safe_dump(asdict(lesson), sort_keys=False, allow_unicode=True)
        write_atomic(path, f"---\n{body}---\n")
        return path

    def add(self, lesson: Lesson) -> Path:
        """Store a new draft and return its file. Raises ValueError for a bad
        or already used slug, or a text field that is empty or too long."""
        path = self._path(lesson.slug)
        if path.exists():
            raise ValueError(f"a lesson named {lesson.slug!r} already exists")
        for name in _TEXT_FIELDS:
            value = getattr(lesson, name)
            limit = MAX_WHEN if name == "when" else MAX_FIELD
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"lesson field {name!r} must not be empty")
            if len(value) > limit:
                raise ValueError(
                    f"lesson field {name!r} is {len(value)} characters; "
                    f"the limit is {limit}"
                )
        return self._write(lesson)

    def get(self, slug: str) -> Lesson:
        """Raises KeyError when there is no such draft, ValueError when its
        file cannot be understood."""
        path = self._path(slug)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise KeyError(f"no lesson named {slug!r}") from None
        parts = text.split("---\n")
        try:
            data = yaml.safe_load(parts[1])
            return Lesson(**data)
        except (IndexError, TypeError, yaml.YAMLError) as exc:
            raise ValueError(f"cannot read lesson {path}: {exc}") from exc

    def list(self) -> list[Lesson]:
        """Every readable draft, in slug order."""
        if not self.root.is_dir():
            return []
        lessons = []
        for path in sorted(self.root.glob("*.md")):
            try:
                lessons.append(self.get(path.stem))
            except (KeyError, ValueError):
                continue
        return lessons

    def record_use(self, slug: str, success: bool) -> Lesson:
        lesson = self.get(slug)
        lesson.uses += 1
        lesson.successes += bool(success)
        self._write(lesson)
        return lesson

    def drop(self, slug: str) -> None:
        """Raises KeyError when there is no such draft."""
        try:
            self._path(slug).unlink()
        except FileNotFoundError:
            raise KeyError(f"no lesson named {slug!r}") from None
