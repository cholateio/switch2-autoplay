"""Picture checks for the Pokopia routines. Pure functions on 1920x1080 BGR
frames; every number in here was measured on the console (see
knowledge/aim-drills.md)."""

import cv2
import numpy as np

# The row of button prompts at the bottom of the screen.
PROMPT_ROW = (250, 1005, 1300, 70)
# The pick-up note that slides in at the right edge after a break.
NOTE_ROI = (1560, 675, 360, 65)
# Mid view, character facing away from the camera: where a block placed in the
# cell in front shows up.
FRONT_ROI = (940, 260, 220, 380)
# The bag: icon centres, and the dots that show the page.
BAG_COLUMNS = tuple(483 + 106 * i for i in range(10))
BAG_ROWS = (240, 345, 448)
BAG_DOTS = ((912, 520), (944, 520), (976, 520), (1008, 520))
# The label of the selected move, bottom right.
MOVE_LABEL = (1640, 1012, 170, 50)


def _crop(bgr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = roi
    return bgr[y : y + h, x : x + w]


def text_mask(bgr: np.ndarray) -> np.ndarray:
    """White where the picture is the white of the on-screen lettering. The
    lettering sits on a see-through bar, so matching on colour would follow
    the scene behind it and the time of day."""
    return (bgr.min(axis=2) >= 200).astype(np.uint8) * 255


def prompt_score(bgr: np.ndarray, template: np.ndarray) -> float:
    """How well a prompt (a crop of the prompt row) is found anywhere in the
    row, 0 to 1. A prompt moves sideways when others come and go."""
    row = text_mask(_crop(bgr, PROMPT_ROW))
    wanted = text_mask(template)
    if wanted.max() == 0 or row.max() == 0:
        return 0.0
    return float(cv2.matchTemplate(row, wanted, cv2.TM_CCOEFF_NORMED).max())


def prompt_tail_score(bgr: np.ndarray, template: np.ndarray, tail: int) -> float:
    """Find the prompt as `prompt_score` does, then compare only its last
    `tail` columns at that place. For a prompt that ends in a number: the
    whole prompt matches whatever the number is, the tail does not."""
    row = text_mask(_crop(bgr, PROMPT_ROW))
    wanted = text_mask(template)
    if wanted.max() == 0 or row.max() == 0:
        return 0.0
    scores = cv2.matchTemplate(row, wanted, cv2.TM_CCOEFF_NORMED)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    if best < 0.8:
        return 0.0
    h, w = wanted.shape
    seen, want = row[y : y + h, x + w - tail : x + w], wanted[:, w - tail :]
    if seen.max() == 0 or seen.shape != want.shape:
        return 0.0
    return float(cv2.matchTemplate(seen, want, cv2.TM_CCOEFF_NORMED).max())


def label_score(
    bgr: np.ndarray, template: np.ndarray, roi: tuple[int, int, int, int]
) -> float:
    """The same lettering match inside `roi`."""
    area = text_mask(_crop(bgr, roi))
    wanted = text_mask(template)
    if wanted.max() == 0 or area.max() == 0:
        return 0.0
    if wanted.shape[0] > area.shape[0] or wanted.shape[1] > area.shape[1]:
        return 0.0
    return float(cv2.matchTemplate(area, wanted, cv2.TM_CCOEFF_NORMED).max())


def _dashes(bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    top = cv2.morphologyEx(
        gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    )
    mask = (top >= 28).astype(np.uint8)
    # The target is next to the character, which the camera keeps near the centre.
    mask[:140, :] = 0
    mask[940:, :] = 0
    mask[:, :560] = 0
    mask[:, 1360:] = 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros_like(mask)
    for i in range(1, count):
        _, _, w, h, area = stats[i]
        if 3 <= area <= 80 and max(w, h) <= 30 and min(w, h) <= 5:
            keep[labels == i] = 1
    return keep


def outline(bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """The bounding box (x, y, w, h) of the dashed outline around what Y would
    break, or None. Found as straight chains of evenly spaced short dashes. A
    block standing on the ground is outlined about 120 x 160; a ground cell is
    a flat 110 x 90; an outline half hidden by the character comes out shorter."""
    keep = _dashes(bgr)
    segments = cv2.HoughLinesP(
        keep * 255, 1, np.pi / 180, threshold=18, minLineLength=45, maxLineGap=12
    )
    if segments is None:
        return None
    found = []
    for x1, y1, x2, y2 in segments.reshape(-1, 4):
        n = int(np.hypot(x2 - x1, y2 - y1))
        along = keep[
            np.linspace(y1, y2, n).astype(int), np.linspace(x1, x2, n).astype(int)
        ]
        runs = int(((along[1:] == 1) & (along[:-1] == 0)).sum()) + int(along[0])
        if runs >= 4 and 0.25 <= along.mean() <= 0.85:
            found.append((x1, y1, x2, y2))
    total = sum(float(np.hypot(x2 - x1, y2 - y1)) for x1, y1, x2, y2 in found)
    if len(found) < 2 or total < 100:
        return None
    ends = np.array(found)
    xs = np.concatenate([ends[:, 0], ends[:, 2]])
    ys = np.concatenate([ends[:, 1], ends[:, 3]])
    return (
        int(xs.min()),
        int(ys.min()),
        int(xs.max() - xs.min()),
        int(ys.max() - ys.min()),
    )


def changed_fraction(
    before: np.ndarray, after: np.ndarray, roi: tuple[int, int, int, int] = FRONT_ROI
) -> float:
    """The share of pixels in `roi` that changed strongly. Measured in the mid
    view: a placed block 0.09 to 0.15, a press that placed nothing or just
    waiting at most 0.03."""
    diff = np.abs(_crop(before, roi).astype(np.int16) - _crop(after, roi)).max(axis=2)
    return float((diff > 40).mean())


def bag_page(bgr: np.ndarray) -> int | None:
    """The page the bag shows, 1 to 4: the dot drawn as an orange ring."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    scores = []
    for x, y in BAG_DOTS:
        patch = hsv[y - 12 : y + 12, x - 12 : x + 12]
        scores.append(
            float(
                (
                    (patch[..., 0] > 8)
                    & (patch[..., 0] < 28)
                    & (patch[..., 1] > 120)
                    & (patch[..., 2] > 180)
                ).mean()
            )
        )
    best = int(np.argmax(scores))
    return best + 1 if scores[best] > 0.08 else None


def bag_cursor(bgr: np.ndarray) -> tuple[int, int] | None:
    """The (row, column) of the selected cell, both from 1: the cell with the
    yellow disc behind its icon."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    best, where = 0.0, None
    for r, y in enumerate(BAG_ROWS):
        for c, x in enumerate(BAG_COLUMNS):
            patch = hsv[y - 45 : y + 45, x - 45 : x + 45]
            yellow = float(
                (
                    (patch[..., 0] > 24)
                    & (patch[..., 0] < 34)
                    & (patch[..., 1] > 150)
                    & (patch[..., 2] > 200)
                ).mean()
            )
            if yellow > best:
                best, where = yellow, (r + 1, c + 1)
    return where if best > 0.1 else None
