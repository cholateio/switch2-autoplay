import dataclasses
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from autoplay.core.agent.budget import PRICES

EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class AgentConfig:
    """Settings of the model loop; the values live in `config/agent.yaml`."""

    model: str
    effort: str
    max_tokens: int
    max_usd_per_run: float
    max_model_calls: int
    screenshot_width: int
    jpeg_quality: int
    notes_limit: int


def load_agent_config(path: Path | None = None) -> AgentConfig:
    """Load the settings from `path`, or from the packaged `config/agent.yaml`.
    Raises ValueError naming the file for a missing or unknown key, a value of
    the wrong type or not above zero, an unknown effort, or a model without a
    known price."""
    if path is None:
        source = "autoplay/config/agent.yaml"
        text = (
            resources.files("autoplay.config")
            .joinpath("agent.yaml")
            .read_text(encoding="utf-8")
        )
    else:
        source = str(path)
        text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        # A wrong file, not a wrong argument type.
        raise ValueError(f"{source}: expected a mapping of settings")  # noqa: TRY004
    fields = {field.name: field.type for field in dataclasses.fields(AgentConfig)}
    missing, unknown = (
        sorted(fields.keys() - data.keys()),
        sorted(str(key) for key in data.keys() - fields.keys()),
    )
    if missing or unknown:
        raise ValueError(f"{source}: missing keys {missing}, unknown keys {unknown}")
    for name, kind in fields.items():
        value = data[name]
        if kind is str:
            if not isinstance(value, str) or not value:
                raise ValueError(f"{source}: {name} must be text, got {value!r}")
            continue
        accepted = (int, float) if kind is float else (int,)
        if isinstance(value, bool) or not isinstance(value, accepted) or value <= 0:
            raise ValueError(
                f"{source}: {name} must be a positive number, got {value!r}"
            )
    if data["model"] not in PRICES:
        raise ValueError(
            f"{source}: no price is known for model {data['model']!r}; "
            f"known models: {', '.join(PRICES)}"
        )
    if data["effort"] not in EFFORTS:
        raise ValueError(
            f"{source}: effort must be one of {', '.join(EFFORTS)}, "
            f"got {data['effort']!r}"
        )
    if data["jpeg_quality"] > 100:
        raise ValueError(f"{source}: jpeg_quality must be at most 100")
    data["max_usd_per_run"] = float(data["max_usd_per_run"])
    return AgentConfig(**data)
