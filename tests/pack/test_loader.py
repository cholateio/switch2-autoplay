import pytest
import yaml
from synth_pack import screen, write_pack

from autoplay.core.pack.loader import PackError, load_pack


def edit_manifest(root, change):
    path = root / "manifest.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data), "utf-8")


def test_loads_a_pack(tmp_path):
    pack = load_pack(write_pack(tmp_path / "p"))
    assert pack.name == "synth"
    assert pack.home_context == "explore"
    assert pack.core_guide == "CORE GUIDE TEXT\n"
    assert pack.chapter_summaries == {"bag": "the bag screen"}
    assert pack.forbidden["bag"] == frozenset({"PLUS"})
    assert "BAG CHAPTER TEXT" in pack.read_chapter("bag")
    assert pack.states.classify(screen("bag")) == "bag"
    assert pack.states.classify(screen()) is None


def test_forbidden_in_a_context_and_in_an_unknown_one(tmp_path):
    root = write_pack(
        tmp_path / "p", forbidden={"*": ["HOME"], "bag": ["PLUS"], "explore": ["MINUS"]}
    )
    pack = load_pack(root)
    assert pack.forbidden_in("bag") == frozenset({"PLUS", "HOME"})
    assert pack.forbidden_in("explore") == frozenset({"MINUS", "HOME"})
    assert pack.forbidden_in(None) == frozenset({"PLUS", "MINUS", "HOME"})


def test_a_context_without_an_entry_forbids_only_the_global_buttons(tmp_path):
    pack = load_pack(write_pack(tmp_path / "p", forbidden={"*": ["HOME"]}))
    assert pack.forbidden_in("bag") == frozenset({"HOME"})


def test_missing_forbidden_section_forbids_nothing(tmp_path):
    pack = load_pack(write_pack(tmp_path / "p", forbidden=None))
    assert pack.forbidden_in(None) == frozenset()


def test_unknown_chapter_lists_the_ones_there_are(tmp_path):
    pack = load_pack(write_pack(tmp_path / "p"))
    with pytest.raises(PackError, match="bag"):
        pack.read_chapter("nope")


def test_forbidden_button_that_does_not_exist(tmp_path):
    root = write_pack(tmp_path / "p", forbidden={"bag": ["START"]})
    with pytest.raises(PackError, match="START"):
        load_pack(root)


@pytest.mark.parametrize(
    "overrides",
    [{"forbidden": {"shop": ["PLUS"]}}, {"home_context": "shop"}],
)
def test_context_the_states_file_does_not_define(tmp_path, overrides):
    root = write_pack(tmp_path / "p", **overrides)
    with pytest.raises(PackError, match="shop"):
        load_pack(root)


def test_missing_template_says_where_it_belongs(tmp_path):
    root = write_pack(tmp_path / "p")
    (root / "templates" / "bag.png").unlink()
    with pytest.raises(PackError) as info:
        load_pack(root)
    message = str(info.value)
    assert str(root / "templates" / "bag.png") in message
    assert "templates are local only" in message


def test_missing_core_guide(tmp_path):
    root = write_pack(tmp_path / "p")
    (root / "guide" / "core.md").unlink()
    with pytest.raises(PackError, match="core.md"):
        load_pack(root)


def test_missing_chapter_file(tmp_path):
    root = write_pack(tmp_path / "p")
    (root / "guide" / "bag.md").unlink()
    with pytest.raises(PackError, match="bag.md"):
        load_pack(root)


def test_duplicate_chapter_name(tmp_path):
    root = write_pack(tmp_path / "p")
    edit_manifest(
        root, lambda m: m["guide"]["chapters"].append(dict(m["guide"]["chapters"][0]))
    )
    with pytest.raises(PackError, match="bag"):
        load_pack(root)


def test_manifest_that_is_not_a_mapping(tmp_path):
    root = write_pack(tmp_path / "p")
    (root / "manifest.yaml").write_text("- a\n- b\n", "utf-8")
    with pytest.raises(PackError, match="mapping"):
        load_pack(root)


def test_missing_manifest(tmp_path):
    with pytest.raises(PackError, match="manifest.yaml"):
        load_pack(tmp_path)


def test_unknown_manifest_key(tmp_path):
    root = write_pack(tmp_path / "p", forbiden={"bag": ["PLUS"]})
    with pytest.raises(PackError, match="forbiden"):
        load_pack(root)


def test_chapter_file_outside_the_pack(tmp_path):
    root = write_pack(tmp_path / "p")
    (tmp_path / "secret.md").write_text("no", "utf-8")
    edit_manifest(root, lambda m: m["guide"]["chapters"][0].update(file="../secret.md"))
    with pytest.raises(PackError, match="outside"):
        load_pack(root)


def test_bad_states_file_is_a_pack_error(tmp_path):
    root = write_pack(tmp_path / "p")
    (root / "states.yaml").write_text("states: 3\n", "utf-8")
    with pytest.raises(PackError, match="states.yaml"):
        load_pack(root)


def test_template_outside_the_pack(tmp_path):
    root = write_pack(tmp_path / "p")
    (root / "templates" / "bag.png").rename(tmp_path / "private.png")
    states = yaml.safe_load((root / "states.yaml").read_text("utf-8"))
    states["states"][1]["template"] = "../private.png"
    (root / "states.yaml").write_text(yaml.safe_dump(states), "utf-8")
    with pytest.raises(PackError, match="outside the pack"):
        load_pack(root)
