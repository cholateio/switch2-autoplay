import cv2
import numpy as np
import pytest
import yaml

from autoplay.cli import main
from autoplay.core.perception.geometry import ROI
from autoplay.core.perception.states import load_states_file
from autoplay.core.tools import template as template_module
from autoplay.core.tools.template import search_area

BOX = (700, 420, 160, 120)


def _blocks(seed, width, height, cell):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (height // cell, width // cell, 3), dtype=np.uint8)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)


def _screen(seed=1, patch_seed=7):
    """A 1920x1080 picture with a distinct patch inside BOX."""
    img = _blocks(seed, 1920, 1080, 40)
    x, y, w, h = BOX
    img[y : y + h, x : x + w] = _blocks(patch_seed, w, h, 20)
    return img


def _save(path, img):
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), img)
    return path


def _template_args(tmp_path, image, name="badge", roi=BOX):
    return [
        "template",
        "--image",
        str(image),
        "--name",
        name,
        "--states-file",
        str(tmp_path / "states.yaml"),
        "--roi",
        ",".join(str(v) for v in roi),
    ]


def test_template_tool_writes_crop_and_yaml_entry(tmp_path):
    image = _save(tmp_path / "shot.png", _screen())
    assert main([*_template_args(tmp_path, image), "--threshold", "0.9"]) == 0

    x, y, w, h = BOX
    crop = cv2.imread(str(tmp_path / "templates" / "badge.png"))
    assert np.array_equal(crop, _screen()[y : y + h, x : x + w])
    data = yaml.safe_load((tmp_path / "states.yaml").read_text())
    assert data == {
        "states": [
            {
                "name": "badge",
                "kind": "template",
                "template": "templates/badge.png",
                "roi": [x - 16, y - 16, w + 32, h + 32],
                "threshold": 0.9,
            }
        ]
    }
    registry = load_states_file(tmp_path / "states.yaml")
    assert registry.classify(_screen(seed=2)) == "badge"
    assert registry.classify(_screen(seed=2, patch_seed=8)) is None


def test_template_tool_stores_base_coordinates_for_a_720p_image(tmp_path):
    small = cv2.resize(_screen(), (1280, 720), interpolation=cv2.INTER_AREA)
    image = _save(tmp_path / "shot.png", small)
    assert main(_template_args(tmp_path, image)) == 0

    crop = cv2.imread(str(tmp_path / "templates" / "badge.png"))
    assert crop.shape == (BOX[3], BOX[2], 3)
    registry = load_states_file(tmp_path / "states.yaml")
    assert registry.classify(_screen()) == "badge"
    assert registry.classify(small) == "badge"


def test_template_tool_overwrites_same_name(tmp_path):
    image = _save(tmp_path / "shot.png", _screen())
    other = (100, 100, 80, 80)
    assert main(_template_args(tmp_path, image, name="first")) == 0
    assert main(_template_args(tmp_path, image, name="second")) == 0
    assert main(_template_args(tmp_path, image, name="first", roi=other)) == 0

    states = yaml.safe_load((tmp_path / "states.yaml").read_text())["states"]
    assert [s["name"] for s in states] == ["first", "second"]
    assert states[0]["roi"] == [84, 84, 112, 112]
    assert cv2.imread(str(tmp_path / "templates" / "first.png")).shape == (80, 80, 3)


def test_template_tool_adds_a_black_state_without_an_image(tmp_path):
    args = ["template", "--name", "loading", "--kind", "black"]
    assert main([*args, "--states-file", str(tmp_path / "states.yaml")]) == 0
    registry = load_states_file(tmp_path / "states.yaml")
    assert registry.classify(np.zeros((1080, 1920, 3), np.uint8)) == "loading"


def test_a_failed_update_leaves_the_old_state_as_it_was(tmp_path, monkeypatch, capsys):
    image = _save(tmp_path / "shot.png", _screen())
    assert main(_template_args(tmp_path, image)) == 0
    template = tmp_path / "templates" / "badge.png"
    states = tmp_path / "states.yaml"
    old_png, old_yaml = template.read_bytes(), states.read_text()

    def fail(path, entry):
        raise OSError("states.yaml: read-only")

    monkeypatch.setattr(template_module, "save_state_entry", fail)
    assert main(_template_args(tmp_path, image, roi=(100, 100, 80, 80))) == 1
    assert "read-only" in capsys.readouterr().err
    assert template.read_bytes() == old_png
    assert states.read_text() == old_yaml
    # A state that was new leaves no template behind.
    assert main(_template_args(tmp_path, image, name="fresh")) == 1
    assert not (tmp_path / "templates" / "fresh.png").exists()


def test_a_malformed_states_file_stops_the_update_first(tmp_path, capsys):
    image = _save(tmp_path / "shot.png", _screen())
    assert main(_template_args(tmp_path, image)) == 0
    template = tmp_path / "templates" / "badge.png"
    old_png = template.read_bytes()
    (tmp_path / "states.yaml").write_text("states: [")
    assert main(_template_args(tmp_path, image, roi=(100, 100, 80, 80))) == 1
    assert "cannot read states file" in capsys.readouterr().err
    assert template.read_bytes() == old_png
    assert (tmp_path / "states.yaml").read_text() == "states: ["
    assert list(tmp_path.glob("**/*.tmp")) == []


def test_search_area_stays_inside_the_picture():
    assert search_area(ROI(0, 0, 100, 100), 16) == ROI(0, 0, 116, 116)
    assert search_area(ROI(1820, 980, 100, 100), 16) == ROI(1804, 964, 116, 116)
    assert search_area(ROI(50, 50, 10, 10), 0) == ROI(50, 50, 10, 10)


@pytest.mark.parametrize(
    ("extra", "code", "message"),
    [
        (["--name", "a/b"], 2, "invalid --name"),
        (["--roi", "1,2,3"], 2, "expected x,y,w,h"),
        (["--threshold", "0"], 2, "--threshold"),
        (["--margin", "-1"], 2, "--margin"),
        (["--image", "none.png"], 1, "none.png"),
    ],
)
def test_template_tool_reports_bad_arguments(tmp_path, capsys, extra, code, message):
    image = _save(tmp_path / "shot.png", _screen())
    assert main([*_template_args(tmp_path, image), *extra]) == code
    assert message in capsys.readouterr().err
    assert not (tmp_path / "states.yaml").exists()


def test_template_tool_refuses_a_flat_box(tmp_path, capsys):
    image = _save(tmp_path / "shot.png", np.full((1080, 1920, 3), 80, np.uint8))
    assert main(_template_args(tmp_path, image)) == 1
    assert "flat colour" in capsys.readouterr().err
    assert not (tmp_path / "states.yaml").exists()


def _classify_setup(tmp_path):
    image = _save(tmp_path / "shot.png", _screen())
    assert main(_template_args(tmp_path, image)) == 0
    states = str(tmp_path / "states.yaml")
    assert (
        main(
            ["template", "--name", "dark", "--kind", "black"]
            + ["--states-file", states]
        )
        == 0
    )
    screens = tmp_path / "screens"
    for i in range(3):
        _save(screens / "badge" / f"{i}.png", _screen(seed=10 + i))
    _save(screens / "dark" / "0.png", np.zeros((1080, 1920, 3), np.uint8))
    # A folder that is not a state: its pictures must match nothing.
    _save(screens / "other" / "0.png", _screen(seed=3, patch_seed=9))
    return states, screens


def test_classify_expect_from_dirname_exit_code(tmp_path, capsys):
    states, screens = _classify_setup(tmp_path)
    folders = [str(screens / name) for name in ("badge", "dark", "other")]
    args = ["classify", "--states-file", states, "--expect-from-dirname"]
    assert main([*args, *folders]) == 0
    out = capsys.readouterr().out
    assert "correct=5/5" in out
    assert "-> badge (badge=1.00 dark=0.00)" in out
    assert "-> None" in out

    # A black picture filed under "badge" is a wrong answer.
    _save(screens / "badge" / "9.png", np.zeros((1080, 1920, 3), np.uint8))
    assert main([*args, *folders]) == 1
    out = capsys.readouterr().out
    assert "correct=5/6" in out
    assert "WRONG, expected badge" in out


def test_classify_without_expectation_only_prints(tmp_path, capsys):
    states, screens = _classify_setup(tmp_path)
    one = str(screens / "dark" / "0.png")
    assert main(["classify", "--states-file", states, one]) == 0
    out = capsys.readouterr().out
    assert "-> dark" in out
    assert "correct=" not in out


def test_classify_reports_missing_inputs(tmp_path, capsys):
    states, screens = _classify_setup(tmp_path)
    assert main(["classify", "--states-file", states, str(tmp_path / "no")]) == 1
    assert "not found" in capsys.readouterr().err
    (tmp_path / "empty").mkdir()
    assert main(["classify", "--states-file", states, str(tmp_path / "empty")]) == 1
    assert "no image files" in capsys.readouterr().err
    assert (
        main(["classify", "--states-file", str(tmp_path / "x.yaml"), str(screens)]) == 1
    )
    assert "cannot read states file" in capsys.readouterr().err
