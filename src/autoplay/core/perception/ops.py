import math
from dataclasses import dataclass

import cv2
import numpy as np

from autoplay.core.perception.geometry import BASE_H, BASE_W, ROI, crop

MIN_TEMPLATE_STD = 1.0


@dataclass(frozen=True)
class Match:
    """Best template position: `score` is the normalised correlation (1 is a
    perfect match), `x`, `y` the top-left corner in base coordinates."""

    score: float
    x: int
    y: int


def check_template(template_bgr: np.ndarray) -> None:
    """Raise ValueError for a template with no detail: correlation is undefined
    for it, and OpenCV then reports a perfect match everywhere."""
    if float(template_bgr.std()) < MIN_TEMPLATE_STD:
        raise ValueError("template is a flat colour; pick an area with detail")


def match_template(
    bgr: np.ndarray, template_bgr: np.ndarray, roi: ROI | None = None
) -> Match:
    """Find `template_bgr`, cut from a base-size (1920x1080) picture, in the
    frame, searching only inside `roi` when one is given. A frame of another
    size is scaled to the base size first. Raises ValueError if the search
    area is smaller than the template, or the template is a flat colour."""
    check_template(template_bgr)
    area = roi or ROI(0, 0, BASE_W, BASE_H)
    search = crop(bgr, area)
    if search.shape[1] != area.w or search.shape[0] != area.h:
        shrink = search.shape[1] > area.w
        search = cv2.resize(
            search,
            (area.w, area.h),
            interpolation=cv2.INTER_AREA if shrink else cv2.INTER_LINEAR,
        )
    th, tw = template_bgr.shape[:2]
    if tw > area.w or th > area.h:
        raise ValueError(
            f"template {tw}x{th} is larger than the search area {area.w}x{area.h}"
        )
    result = cv2.matchTemplate(search, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(result)
    # A flat search area has no variance to correlate.
    if not math.isfinite(score):
        score = 0.0
    return Match(score=float(score), x=area.x + x, y=area.y + y)


def roi_color(bgr: np.ndarray, roi: ROI | None = None) -> tuple[float, float, float]:
    """Mean blue, green and red of the area."""
    b, g, r = crop(bgr, roi).reshape(-1, 3).mean(axis=0)
    return float(b), float(g), float(r)


def frame_diff(a: np.ndarray, b: np.ndarray, roi: ROI | None = None) -> float:
    """Mean absolute difference of two frames of the same size, 0 to 255."""
    if a.shape != b.shape:
        raise ValueError(f"frames differ in size: {a.shape} and {b.shape}")
    return float(cv2.absdiff(crop(a, roi), crop(b, roi)).mean())


def is_black(bgr: np.ndarray, roi: ROI | None = None, max_mean: float = 12.0) -> bool:
    """True when the mean luma of the area is below `max_mean`."""
    grey = cv2.cvtColor(np.ascontiguousarray(crop(bgr, roi)), cv2.COLOR_BGR2GRAY)
    return float(grey.mean()) < max_mean
