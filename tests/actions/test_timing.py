import pytest

from autoplay.core.actions.timing import load_timing


def test_packaged_timing_matches_the_project_constraints():
    timing = load_timing()
    assert (timing.min_state_ms, timing.press_ms, timing.release_gap_ms) == (16, 80, 50)
    # The heartbeat must beat the firmware's 300 ms watchdog with room for a lost PING.
    assert timing.heartbeat_ms * 2 < 300


def test_load_from_a_file(tmp_path):
    text = "\n".join(f"{name}: {i + 1}" for i, name in enumerate(vars(load_timing())))
    path = tmp_path / "timing.yaml"
    path.write_text(text)
    assert load_timing(path).min_state_ms == 1


@pytest.mark.parametrize(
    "edit, message",
    [
        (lambda d: d.pop("press_ms"), "missing keys ['press_ms']"),
        (lambda d: d.update(extra_ms=1), "unknown keys ['extra_ms']"),
        (lambda d: d.update(press_ms=0), "press_ms must be a positive integer"),
        (lambda d: d.update(press_ms="80"), "press_ms must be a positive integer"),
        (lambda d: d.update(press_ms=True), "press_ms must be a positive integer"),
    ],
)
def test_bad_files_are_rejected_with_the_reason(tmp_path, edit, message):
    data = dict(vars(load_timing()))
    edit(data)
    path = tmp_path / "timing.yaml"
    path.write_text("\n".join(f"{k}: {v!r}" for k, v in data.items()))
    with pytest.raises(ValueError) as err:
        load_timing(path)
    assert message in str(err.value)
    assert str(path) in str(err.value)


def test_a_file_that_is_not_a_mapping_is_rejected(tmp_path):
    path = tmp_path / "timing.yaml"
    path.write_text("- 1\n- 2\n")
    with pytest.raises(ValueError):
        load_timing(path)
