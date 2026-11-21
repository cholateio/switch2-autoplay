import pytest

from autoplay.core.memory.lessons import Lesson, LessonStore


def lesson(slug="open-bag", **changes) -> Lesson:
    fields = {
        "when": "the bag is closed",
        "steps": "press X\nwait for the list",
        "failure": "press B twice",
        "verify": "the context is bag: yes",
    }
    return Lesson(slug, **{**fields, **changes})


def test_add_then_list_and_get(tmp_path):
    store = LessonStore(tmp_path / "lessons")
    assert store.list() == []
    path = store.add(lesson())
    assert path == tmp_path / "lessons" / "open-bag.md"
    assert store.list() == [lesson()]
    assert store.get("open-bag") == lesson()


def test_uses_survive_a_new_store(tmp_path):
    store = LessonStore(tmp_path)
    store.add(lesson())
    store.record_use("open-bag", True)
    store.record_use("open-bag", True)
    store.record_use("open-bag", False)
    again = LessonStore(tmp_path).get("open-bag")
    assert (again.uses, again.successes) == (3, 2)


def test_non_ascii_text_round_trips(tmp_path):
    store = LessonStore(tmp_path)
    store.add(lesson(when="背包關著的時候", steps="按 X：等清單出現"))
    assert store.get("open-bag").steps == "按 X：等清單出現"


@pytest.mark.parametrize(
    "slug", ["a/b", "..", "../x", "Open", "a b", "", "-a", "a" * 61]
)
def test_bad_slug(tmp_path, slug):
    store = LessonStore(tmp_path)
    with pytest.raises(ValueError):
        store.add(lesson(slug))
    with pytest.raises(ValueError):
        store.get(slug)
    assert list(tmp_path.iterdir()) == []


def test_duplicate_slug(tmp_path):
    store = LessonStore(tmp_path)
    store.add(lesson())
    with pytest.raises(ValueError, match="already exists"):
        store.add(lesson(when="other"))
    assert store.get("open-bag").when == "the bag is closed"


@pytest.mark.parametrize(
    "changes", [{"when": " "}, {"when": "x" * 201}, {"steps": "x" * 1501}]
)
def test_empty_or_oversized_field(tmp_path, changes):
    with pytest.raises(ValueError):
        LessonStore(tmp_path).add(lesson(**changes))


def test_unknown_slug(tmp_path):
    store = LessonStore(tmp_path)
    for call in (store.get, store.drop, lambda s: store.record_use(s, True)):
        with pytest.raises(KeyError):
            call("nope")


def test_drop_removes_the_draft(tmp_path):
    store = LessonStore(tmp_path)
    store.add(lesson())
    store.drop("open-bag")
    assert store.list() == []


def test_unreadable_draft_is_skipped_in_the_list(tmp_path):
    store = LessonStore(tmp_path)
    store.add(lesson())
    (tmp_path / "broken.md").write_text("not a lesson", "utf-8")
    assert [item.slug for item in store.list()] == ["open-bag"]
