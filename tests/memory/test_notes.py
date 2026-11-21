import pytest

from autoplay.core.memory.notes import NotesFull, ProgressNotes


def test_missing_notes_read_as_empty(tmp_path):
    assert ProgressNotes(tmp_path / "m" / "notes.md").read() == ""


def test_write_replaces_the_notes(tmp_path):
    notes = ProgressNotes(tmp_path / "m" / "notes.md")
    notes.write("first")
    notes.write("第二版")
    assert notes.read() == "第二版"
    assert ProgressNotes(notes.path).read() == "第二版"
    assert [p.name for p in notes.path.parent.iterdir()] == ["notes.md"]


def test_exactly_the_limit_is_accepted(tmp_path):
    notes = ProgressNotes(tmp_path / "notes.md")
    notes.write("字" * 2000)
    assert len(notes.read()) == 2000


def test_over_the_limit_is_refused_and_nothing_changes(tmp_path):
    notes = ProgressNotes(tmp_path / "notes.md")
    notes.write("kept")
    with pytest.raises(NotesFull) as info:
        notes.write("x" * 2001)
    assert "2001" in str(info.value) and "2000" in str(info.value)
    assert notes.read() == "kept"


def test_limit_is_configurable(tmp_path):
    notes = ProgressNotes(tmp_path / "notes.md", limit=5)
    with pytest.raises(NotesFull):
        notes.write("123456")
