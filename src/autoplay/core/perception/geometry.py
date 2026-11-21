from dataclasses import dataclass

import numpy as np

# Every coordinate and every template in this project refers to a picture of
# this size; a frame of another size is scaled to it.
BASE_W, BASE_H = 1920, 1080


@dataclass(frozen=True)
class ROI:
    """A rectangle in base (1920x1080) coordinates. Raises ValueError unless it
    has a positive size and lies inside the base picture."""

    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        if (
            self.w <= 0
            or self.h <= 0
            or self.x < 0
            or self.y < 0
            or self.x + self.w > BASE_W
            or self.y + self.h > BASE_H
        ):
            raise ValueError(
                f"ROI {self.x},{self.y},{self.w},{self.h} is not inside {BASE_W}x{BASE_H}"
            )

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]


def parse_roi(text: str) -> ROI:
    """Parse `x,y,w,h` in base coordinates; raise ValueError if it is malformed
    or not inside the base picture."""
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise ValueError(f"invalid ROI {text!r}; expected x,y,w,h") from None
    return ROI(x, y, w, h)


def crop(bgr: np.ndarray, roi: ROI | None) -> np.ndarray:
    """Return the part of `bgr` that `roi` covers, scaling the base coordinates
    to the frame's real size; the whole frame when `roi` is None. The result
    is a view, not a copy."""
    if roi is None:
        return bgr
    height, width = bgr.shape[:2]
    sx, sy = width / BASE_W, height / BASE_H
    x0, x1 = round(roi.x * sx), round((roi.x + roi.w) * sx)
    y0, y1 = round(roi.y * sy), round((roi.y + roi.h) * sy)
    # A very small frame must still give at least one pixel.
    return bgr[y0 : max(y1, y0 + 1), x0 : max(x1, x0 + 1)]
