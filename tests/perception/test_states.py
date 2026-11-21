import cv2
import numpy as np
import pytest
from synth import PATCH_H, PATCH_W, patch, scene

from autoplay.core.perception.states import (
    StateRegistry,
    StatesFileError,
    black_detector,
    load_states_file,
    template_detector,
)

AT = (700, 420)
BLACK = np.zeros((1080, 1920, 3), np.uint8)


def _const(score):
    return lambda bgr: score


def test_classify_returns_highest_above_threshold_else_none():
    registry = StateRegistry()
    registry.register("low", _const(0.85))
    registry.register("high", _const(0.9))
    registry.register("strict", _const(0.95), threshold=0.99)
    assert registry.scores(BLACK) == {"low": 0.85, "high": 0.9, "strict": 0.95}
    assert registry.classify(BLACK) == "high"

    none = StateRegistry()
    none.register("a", _const(0.79))
    none.register("b", _const(0.5), threshold=0.6)
    assert none.classify(BLACK) is None
    assert StateRegistry().classify(BLACK) is None


def test_a_state_at_exactly_its_threshold_counts():
    registry = StateRegistry()
    registry.register("edge", _const(0.8))
    assert registry.classify(BLACK) == "edge"


@pytest.mark.parametrize("threshold", [0, -0.1, 1.01, float("nan"), True, "0.8"])
def test_register_refuses_a_threshold_outside_zero_to_one(threshold):
    with pytest.raises(ValueError, match="threshold"):
        StateRegistry().register("a", _const(1.0), threshold=threshold)


def test_registering_a_name_twice_is_an_error():
    registry = StateRegistry()
    registry.register("a", _const(1.0))
    with pytest.raises(ValueError, match="already registered"):
        registry.register("a", _const(1.0))


def test_detectors_stay_within_zero_and_one():
    assert template_detector(patch(), None)(scene(AT)) == pytest.approx(1.0, abs=0.05)
    assert template_detector(patch(), None)(BLACK) == 0.0
    assert black_detector()(BLACK) == 1.0
    assert black_detector()(scene(None)) == 0.0


def _write_states(tmp_path, text, with_template=True):
    if with_template:
        (tmp_path / "templates").mkdir()
        assert cv2.imwrite(str(tmp_path / "templates" / "badge.png"), patch())
    path = tmp_path / "states.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_states_file_builds_template_and_black_detectors(tmp_path):
    path = _write_states(
        tmp_path,
        f"""
states:
  - name: badge
    kind: template
    template: templates/badge.png
    roi: [{AT[0] - 20}, {AT[1] - 20}, {PATCH_W + 40}, {PATCH_H + 40}]
    threshold: 0.9
  - name: loading
    kind: black
""",
    )
    registry = load_states_file(path)
    assert registry.names == ["badge", "loading"]
    assert registry.classify(scene(AT)) == "badge"
    assert registry.classify(BLACK) == "loading"
    assert registry.classify(scene(None)) is None
    # The roi limits the search: the patch elsewhere is not this state.
    assert registry.classify(scene((100, 100))) is None


def test_states_file_missing_template_raises_with_path(tmp_path):
    path = _write_states(
        tmp_path,
        "states:\n  - {name: badge, kind: template, template: templates/none.png}\n",
        with_template=False,
    )
    with pytest.raises(StatesFileError) as info:
        load_states_file(path)
    assert "none.png" in str(info.value)
    assert "badge" in str(info.value)
    assert str(path) in str(info.value)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("states: {}", "expected `states:`"),
        ("- a\n- b\n", "expected `states:`"),
        ("states:\n  - {kind: black}\n", "expected `states:`"),
        ("states:\n  - {name: a, kind: ocr}\n", "kind 'ocr'"),
        ("states:\n  - {name: a, kind: template}\n", "names no template"),
        ("states:\n  - {name: a, kind: black, roi: [1, 2, 3]}\n", "bad roi"),
        ("states:\n  - {name: a, kind: black, roi: [0, 0, 5000, 10]}\n", "bad roi"),
        ("states:\n  - {name: a, kind: black, threshold: high}\n", "bad threshold"),
        ("states:\n  - {name: a, kind: black, threshold: -1}\n", "bad threshold"),
        ("states:\n  - {name: a, kind: black, threshold: 0}\n", "bad threshold"),
        ("states:\n  - {name: a, kind: black, threshold: 1.5}\n", "bad threshold"),
        ("states:\n  - {name: a, kind: black, threshold: .nan}\n", "bad threshold"),
        ("states:\n  - {name: a, kind: black, threshold: .inf}\n", "bad threshold"),
        ("states:\n  - {name: a, kind: black, threshold: true}\n", "bad threshold"),
        (
            "states:\n  - {name: a, kind: black}\n  - {name: a, kind: black}\n",
            "already registered",
        ),
        ("states: [", "cannot read states file"),
    ],
)
def test_malformed_states_files_are_reported(tmp_path, text, message):
    path = _write_states(tmp_path, text, with_template=False)
    with pytest.raises(StatesFileError, match=message):
        load_states_file(path)


@pytest.mark.parametrize(
    ("template", "roi", "message"),
    [
        (np.full((40, 40, 3), 90, np.uint8), "[0, 0, 100, 100]", "flat colour"),
        (patch(), "[0, 0, 100, 100]", "larger than its roi"),
    ],
)
def test_a_template_that_cannot_match_is_reported_at_load(
    tmp_path, template, roi, message
):
    assert cv2.imwrite(str(tmp_path / "t.png"), template)
    path = tmp_path / "states.yaml"
    path.write_text(
        f"states:\n  - {{name: a, kind: template, template: t.png, roi: {roi}}}\n"
    )
    with pytest.raises(StatesFileError, match=message):
        load_states_file(path)


def test_a_missing_states_file_is_reported(tmp_path):
    with pytest.raises(StatesFileError, match="cannot read states file"):
        load_states_file(tmp_path / "none.yaml")
