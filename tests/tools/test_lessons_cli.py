from datetime import date

import pytest
from synth_pack import write_pack

from autoplay.cli import main
from autoplay.core.memory.lessons import Lesson, LessonStore


@pytest.fixture
def setup(tmp_path):
    pack = write_pack(tmp_path / "pack")
    memory = tmp_path / "memory"
    store = LessonStore(memory / "synth" / "lessons")
    store.add(Lesson("open-bag", "WHEN TEXT", "STEPS", "FAILURE", "VERIFY"))
    return pack, memory, store


def run(pack, memory, *argv):
    action, *rest = argv
    return main(
        ["lessons", action, *rest, "--pack", str(pack), "--memory-root", str(memory)]
    )


def test_list_prints_slug_and_counts(setup, capsys):
    pack, memory, store = setup
    store.record_use("open-bag", True)
    assert run(pack, memory, "list") == 0
    out = capsys.readouterr().out
    assert "open-bag" in out and "uses=1" in out and "successes=1" in out


def test_promote_appends_to_the_chapter_and_removes_the_draft(setup):
    pack, memory, store = setup
    store.record_use("open-bag", False)
    store.record_use("open-bag", True)
    assert run(pack, memory, "promote", "open-bag", "--chapter", "bag") == 0
    text = (pack / "guide" / "bag.md").read_text("utf-8")
    assert text.startswith("# Bag\n\nBAG CHAPTER TEXT\n")
    tail = text.split("## 經驗")[1]
    assert "WHEN TEXT" in tail and date.today().isoformat() in tail
    assert "使用 2 次、成功 1 次" in tail
    assert "未經實際使用驗證" not in tail
    assert store.list() == []


def test_second_promotion_reuses_the_heading(setup):
    pack, memory, store = setup
    store.add(Lesson("close-bag", "OTHER WHEN", "s", "f", "v"))
    for slug in ("open-bag", "close-bag"):
        store.record_use(slug, True)
        assert run(pack, memory, "promote", slug, "--chapter", "bag") == 0
    text = (pack / "guide" / "bag.md").read_text("utf-8")
    assert text.count("## 經驗") == 1
    assert text.index("WHEN TEXT") < text.index("OTHER WHEN")


def test_promote_into_unknown_chapter_is_a_usage_error(setup, capsys):
    pack, memory, store = setup
    store.record_use("open-bag", True)
    assert run(pack, memory, "promote", "open-bag", "--chapter", "nope") == 2
    assert "bag" in capsys.readouterr().err
    assert [item.slug for item in store.list()] == ["open-bag"]


@pytest.mark.parametrize("uses", [[], [False, False]])
def test_lesson_that_never_worked_is_not_promoted(setup, capsys, uses):
    pack, memory, store = setup
    for success in uses:
        store.record_use("open-bag", success)
    before = (pack / "guide" / "bag.md").read_text("utf-8")
    assert run(pack, memory, "promote", "open-bag", "--chapter", "bag") == 1
    assert "--unverified" in capsys.readouterr().err
    assert (pack / "guide" / "bag.md").read_text("utf-8") == before
    assert [item.slug for item in store.list()] == ["open-bag"]
    assert (
        run(pack, memory, "promote", "open-bag", "--chapter", "bag", "--unverified")
        == 0
    )
    assert "未經實際使用驗證" in (pack / "guide" / "bag.md").read_text("utf-8")
    assert store.list() == []


def test_unverified_flag_does_not_mark_a_lesson_that_worked(setup):
    pack, memory, store = setup
    store.record_use("open-bag", True)
    run(pack, memory, "promote", "open-bag", "--chapter", "bag", "--unverified")
    assert "未經實際使用驗證" not in (pack / "guide" / "bag.md").read_text("utf-8")


def test_unknown_slug(setup, capsys):
    pack, memory, _ = setup
    assert run(pack, memory, "drop", "nope") == 1
    assert run(pack, memory, "promote", "nope", "--chapter", "bag") == 1
    assert "nope" in capsys.readouterr().err


def test_drop(setup):
    pack, memory, store = setup
    assert run(pack, memory, "drop", "open-bag") == 0
    assert store.list() == []


def test_bad_pack(tmp_path, capsys):
    assert run(tmp_path, tmp_path, "list") == 1
    assert "manifest.yaml" in capsys.readouterr().err
