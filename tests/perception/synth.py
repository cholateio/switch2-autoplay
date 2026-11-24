"""Synthetic pictures for the perception tests: blocky noise that survives
being scaled, with one known patch pasted in."""

import cv2
import numpy as np

from autoplay.core.perception.geometry import BASE_H, BASE_W

PATCH_W, PATCH_H = 160, 120


def blocks(rng: np.random.Generator, width: int, height: int, cell: int) -> np.ndarray:
    small = rng.integers(0, 256, (height // cell, width // cell, 3), dtype=np.uint8)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)


def patch(seed: int = 7) -> np.ndarray:
    return blocks(np.random.default_rng(seed), PATCH_W, PATCH_H, 20)


def scene(at: tuple[int, int] | None, seed: int = 1) -> np.ndarray:
    """A 1920x1080 background, with the patch at `at` (x, y) unless None."""
    img = blocks(np.random.default_rng(seed), BASE_W, BASE_H, 40)
    if at is not None:
        x, y = at
        img[y : y + PATCH_H, x : x + PATCH_W] = patch()
    return img
