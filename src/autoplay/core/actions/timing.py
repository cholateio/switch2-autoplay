import dataclasses
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Timing:
    """The timing constants, in milliseconds. The values live only in
    `config/timing.yaml`; get an instance from `load_timing()`."""

    min_state_ms: int
    press_ms: int
    release_gap_ms: int
    menu_step_ms: int
    hello_window_ms: int
    ack_timeout_ms: int
    ack_retries: int
    heartbeat_ms: int
    done_slack_ms: int
    done_queries: int
    lease_ms: int


def load_timing(path: Path | None = None) -> Timing:
    """Load timing constants from `path`, or from the packaged
    `config/timing.yaml`. Raise ValueError naming the file if a key is missing,
    unknown, or not a positive integer."""
    if path is None:
        source = "autoplay/config/timing.yaml"
        text = (
            resources.files("autoplay.config")
            .joinpath("timing.yaml")
            .read_text(encoding="utf-8")
        )
    else:
        source = str(path)
        text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        # A wrong file, not a wrong argument type: the same error as any other bad content.
        raise ValueError(f"{source}: expected a mapping of timing constants")  # noqa: TRY004
    names = {field.name for field in dataclasses.fields(Timing)}
    missing, unknown = sorted(names - data.keys()), sorted(data.keys() - names)
    if missing or unknown:
        raise ValueError(f"{source}: missing keys {missing}, unknown keys {unknown}")
    for name, value in data.items():
        if type(value) is not int or value <= 0:
            raise ValueError(
                f"{source}: {name} must be a positive integer, got {value!r}"
            )
    return Timing(**data)
