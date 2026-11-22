import pytest
import yaml

from autoplay.core.agent.config import AgentConfig, load_agent_config

GOOD = {
    "model": "claude-sonnet-5-5",
    "effort": "low",
    "max_tokens": 4000,
    "max_usd_per_run": 1,
    "max_model_calls": 20,
    "screenshot_width": 960,
    "jpeg_quality": 70,
    "notes_limit": 500,
}


def write(tmp_path, **changes):
    data = {**GOOD, **changes}
    path = tmp_path / "agent.yaml"
    path.write_text(
        yaml.safe_dump({k: v for k, v in data.items() if v is not None}), "utf-8"
    )
    return path


def test_packaged_defaults():
    config = load_agent_config()
    assert config == AgentConfig(
        model="claude-opus-5-5",
        effort="medium",
        max_tokens=3000,
        max_usd_per_run=2.0,
        max_model_calls=150,
        screenshot_width=1280,
        jpeg_quality=80,
        notes_limit=2000,
    )


def test_loads_a_file(tmp_path):
    config = load_agent_config(write(tmp_path))
    assert config.model == "claude-sonnet-5-5"
    assert config.max_usd_per_run == 1.0
    assert isinstance(config.max_usd_per_run, float)


def test_unknown_model_lists_the_known_ones(tmp_path):
    with pytest.raises(ValueError, match="claude-opus-5-5, claude-sonnet-5-5"):
        load_agent_config(write(tmp_path, model="claude-9"))


@pytest.mark.parametrize(
    "changes",
    [
        {"fallbacks": "default"},
        {"max_tokens": None},
        {"max_tokens": 0},
        {"max_tokens": "8000"},
        {"max_tokens": 1.5},
        {"max_tokens": True},
        {"max_usd_per_run": -1},
        {"effort": "extreme"},
        {"model": 5},
        {"jpeg_quality": 101},
    ],
)
def test_bad_settings(tmp_path, changes):
    with pytest.raises(ValueError, match="agent.yaml"):
        load_agent_config(write(tmp_path, **changes))


def test_file_that_is_not_a_mapping(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("- 1\n", "utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_agent_config(path)
