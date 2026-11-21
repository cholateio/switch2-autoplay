import os
from pathlib import Path


class NotesFull(ValueError):
    """The text is longer than the notes may be."""


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` with `text` in one step, so a failure leaves the old
    file whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    staged.write_text(text, encoding="utf-8")
    os.replace(staged, path)


class ProgressNotes:
    """A short text a run reads at its start and may replace: where things
    stand. It is capped so that it stays cheap to send with every run."""

    def __init__(self, path: Path, limit: int = 2000) -> None:
        self.path = Path(path)
        self.limit = limit

    def read(self) -> str:
        """Return the notes, or an empty string when there are none yet."""
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def write(self, text: str) -> None:
        """Replace the notes with `text`. Raises NotesFull, leaving the notes
        as they were, when `text` is longer than `limit` characters."""
        if len(text) > self.limit:
            raise NotesFull(
                f"the notes would be {len(text)} characters; the limit is "
                f"{self.limit}. Nothing was written: shorten them and write again."
            )
        write_atomic(self.path, text)
