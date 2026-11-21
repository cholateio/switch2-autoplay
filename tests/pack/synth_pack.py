"""A synthetic game pack and a scripted console for the pack, memory and
agent tests: two contexts, each marked by its own noise patch in a corner."""

from pathlib import Path

import cv2
import numpy as np
import yaml

from autoplay.core.capture.base import CaptureStats, Frame, FrameSource
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.types import Button

WIDTH, HEIGHT = 1920, 1080
PATCH_W, PATCH_H = 160, 120
SPOTS = {"explore": (100, 100, 7), "bag": (1600, 800, 9)}
FPS = 60.0


def _patch(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (PATCH_H // 20, PATCH_W // 20, 3), dtype=np.uint8)
    return cv2.resize(small, (PATCH_W, PATCH_H), interpolation=cv2.INTER_NEAREST)


def screen(*contexts: str, shade: int = 40) -> np.ndarray:
    """A frame showing the marker of every context named; none: a frame no
    state matches. `shade` changes the background without changing the context."""
    img = np.full((HEIGHT, WIDTH, 3), shade, np.uint8)
    for name in contexts:
        x, y, seed = SPOTS[name]
        img[y : y + PATCH_H, x : x + PATCH_W] = _patch(seed)
    return img


def write_pack(root: Path, **overrides) -> Path:
    """Write a loadable pack under `root` and return `root`. Keyword arguments
    replace top-level manifest keys; a value of None removes the key."""
    (root / "templates").mkdir(parents=True)
    (root / "guide").mkdir()
    states = []
    for name, (x, y, seed) in SPOTS.items():
        assert cv2.imwrite(str(root / "templates" / f"{name}.png"), _patch(seed))
        states.append(
            {
                "name": name,
                "kind": "template",
                "template": f"templates/{name}.png",
                "roi": [x - 20, y - 20, PATCH_W + 40, PATCH_H + 40],
            }
        )
    (root / "states.yaml").write_text(yaml.safe_dump({"states": states}), "utf-8")
    (root / "guide" / "core.md").write_text("CORE GUIDE TEXT\n", "utf-8")
    (root / "guide" / "bag.md").write_text("# Bag\n\nBAG CHAPTER TEXT\n", "utf-8")
    manifest = {
        "name": "synth",
        "home_context": "explore",
        "guide": {
            "core": "guide/core.md",
            "chapters": [
                {"name": "bag", "file": "guide/bag.md", "summary": "the bag screen"}
            ],
        },
        "states_file": "states.yaml",
        "forbidden": {"bag": ["PLUS"], "explore": ["MINUS"]},
    }
    for key, value in overrides.items():
        if value is None:
            manifest.pop(key, None)
        else:
            manifest[key] = value
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), "utf-8")
    return root


class FakeScreen(FrameSource):
    """A source that shows whatever it is told to. Every wait delivers one new
    frame: the next queued picture if there is one, else the current one."""

    def __init__(self, *contexts: str) -> None:
        self.picture = screen(*contexts)
        self.queued: list[np.ndarray] = []
        self.stalled = False
        self._seq = 0
        self._latest: Frame | None = None
        self.start()

    def show(self, *contexts: str, shade: int = 40) -> None:
        self.picture = screen(*contexts, shade=shade)

    def start(self) -> None:
        self._emit()

    def _emit(self) -> Frame:
        if self.queued:
            self.picture = self.queued.pop(0)
        self._seq += 1
        self._latest = Frame(self.picture, self._seq / FPS, self._seq)
        return self._latest

    def latest(self) -> Frame | None:
        return self._latest

    def wait_next(self, after_seq: int, timeout: float) -> Frame | None:
        if self.stalled:
            return None
        return self._emit()

    def stats(self) -> CaptureStats:
        return CaptureStats(WIDTH, HEIGHT, "FAKE", FPS, self._seq, self.stalled)

    def close(self) -> None:
        pass


class FakeConsole(DummyController):
    """A gamepad wired to a FakeScreen: `moves` maps a button to the contexts
    shown once that button goes down."""

    def __init__(self, source: FakeScreen, moves: dict[str, tuple[str, ...]]) -> None:
        super().__init__()
        self.source = source
        self.moves = moves

    def _apply(self, state, hold_ms):
        pressed = state.buttons & ~self.state.buttons
        super()._apply(state, hold_ms)
        for name, contexts in self.moves.items():
            if pressed & Button[name]:
                self.source.show(*contexts)

    def pressed(self, name: str) -> bool:
        return any(state.buttons & Button[name] for state, _ in self.log)
