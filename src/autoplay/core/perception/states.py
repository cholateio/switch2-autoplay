from collections.abc import Callable
from pathlib import Path

import numpy as np
import yaml

from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.offline import read_image
from autoplay.core.perception.geometry import ROI
from autoplay.core.perception.ops import check_template, is_black, match_template

# Returns a confidence from 0 to 1 that a frame shows the state.
Detector = Callable[[np.ndarray], float]
DEFAULT_THRESHOLD = 0.8
KINDS = ("template", "black")


class StatesFileError(ValueError):
    """A states file is malformed or names a template that cannot be read."""


class StateRegistry:
    """Named screen states, each recognised by one detector."""

    def __init__(self) -> None:
        self._states: dict[str, tuple[Detector, float]] = {}

    @property
    def names(self) -> list[str]:
        return list(self._states)

    def register(
        self, name: str, detector: Detector, threshold: float = DEFAULT_THRESHOLD
    ) -> None:
        if name in self._states:
            raise ValueError(f"state {name!r} is already registered")
        self._states[name] = (detector, threshold)

    def scores(self, bgr: np.ndarray) -> dict[str, float]:
        return {name: detector(bgr) for name, (detector, _) in self._states.items()}

    def classify(self, bgr: np.ndarray) -> str | None:
        """Return the state with the highest score among those that reach
        their own threshold, or None when no state does."""
        best, best_score = None, -1.0
        for name, score in self.scores(bgr).items():
            if score >= self._states[name][1] and score > best_score:
                best, best_score = name, score
        return best


def template_detector(template_bgr: np.ndarray, roi: ROI | None) -> Detector:
    """Raises ValueError for a template that can never be matched: a flat
    colour, or one larger than `roi`."""
    check_template(template_bgr)
    height, width = template_bgr.shape[:2]
    if roi is not None and (width > roi.w or height > roi.h):
        raise ValueError(f"template {width}x{height} is larger than its roi")

    def detect(bgr: np.ndarray) -> float:
        return min(1.0, max(0.0, match_template(bgr, template_bgr, roi).score))

    return detect


def black_detector(roi: ROI | None = None) -> Detector:
    def detect(bgr: np.ndarray) -> float:
        return 1.0 if is_black(bgr, roi) else 0.0

    return detect


def read_states_entries(path: Path) -> list[dict]:
    """Return the entries of a states file as written; raise StatesFileError if
    the file is not of the form `states: [{name: ...}, ...]`."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise StatesFileError(f"cannot read states file {path}: {exc}") from exc
    entries = data.get("states") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(
        isinstance(e, dict) and isinstance(e.get("name"), str) for e in entries
    ):
        raise StatesFileError(
            f"{path}: expected `states:` holding a list of entries with a name"
        )
    return entries


def _roi_of(path: Path, entry: dict) -> ROI | None:
    raw = entry.get("roi")
    if raw is None:
        return None
    try:
        x, y, w, h = raw
        return ROI(int(x), int(y), int(w), int(h))
    except (TypeError, ValueError) as exc:
        raise StatesFileError(
            f"{path}: state {entry['name']!r} has a bad roi: {exc}"
        ) from exc


def load_states_file(path: Path) -> StateRegistry:
    """Build a registry from a states file:

        states:
          - {name, kind: template, template: <path relative to the file>,
             roi: [x, y, w, h], threshold: 0.8}
          - {name, kind: black, roi: [x, y, w, h]}

    `roi` (base coordinates) and `threshold` are optional. Raises
    StatesFileError naming the file, and the template path when that is what
    cannot be read."""
    path = Path(path)
    registry = StateRegistry()
    for entry in read_states_entries(path):
        name, kind = entry["name"], entry.get("kind")
        roi = _roi_of(path, entry)
        try:
            threshold = float(entry.get("threshold", DEFAULT_THRESHOLD))
        except (TypeError, ValueError):
            raise StatesFileError(
                f"{path}: state {name!r} has a bad threshold"
            ) from None
        if kind == "template":
            if not isinstance(entry.get("template"), str):
                raise StatesFileError(f"{path}: state {name!r} names no template")
            template_path = path.parent / entry["template"]
            try:
                detector = template_detector(read_image(template_path), roi)
            except (CaptureError, ValueError) as exc:
                raise StatesFileError(f"{path}: state {name!r}: {exc}") from exc
        elif kind == "black":
            detector = black_detector(roi)
        else:
            raise StatesFileError(
                f"{path}: state {name!r} has kind {kind!r}; expected one of {KINDS}"
            )
        try:
            registry.register(name, detector, threshold)
        except ValueError as exc:
            raise StatesFileError(f"{path}: {exc}") from exc
    return registry
