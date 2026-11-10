import cv2
import numpy as np

# Layout: a horizontal band of CELL_COUNT equal cells on a gray background.
# Cell 0 is always white and cell 1 always black (framing); cells 2..25 hold the
# 24-bit stamp MSB first; cell 26 is even parity over the data bits. The band
# spans x in [5%, 95%] and y in [25%, 75%] of the image, so the layout scales
# with resolution and tolerates a few percent of overscan.
DATA_BITS = 24
WHITE_CELL = 0
BLACK_CELL = 1
FIRST_DATA_CELL = 2
PARITY_CELL = FIRST_DATA_CELL + DATA_BITS
CELL_COUNT = PARITY_CELL + 1
STAMP_MASK = (1 << DATA_BITS) - 1

_X_MARGIN = 0.05
_Y_BAND = (0.25, 0.75)
_BACKGROUND = 128
# A cell counts only if it is clearly black or clearly white and uniform; this
# rejects arbitrary images and frames torn between two patterns.
_BLACK_MAX = 64
_WHITE_MIN = 192
_STD_MAX = 40.0


def cell_span(index: int, width: int) -> tuple[int, int]:
    """Return the [x0, x1) pixel columns of cell `index` in an image `width` wide."""
    left = round(width * _X_MARGIN)
    band = width - 2 * left
    return left + index * band // CELL_COUNT, left + (index + 1) * band // CELL_COUNT


def _cell_values(ms: int) -> list[int]:
    stamp = ms & STAMP_MASK
    bits = [(stamp >> (DATA_BITS - 1 - i)) & 1 for i in range(DATA_BITS)]
    return [1, 0, *bits, sum(bits) & 1]


def encode_stamp(ms: int, size: tuple[int, int]) -> np.ndarray:
    """Draw the low 24 bits of `ms` as a BGR uint8 pattern; `size` is (width, height)."""
    width, height = size
    img = np.full((height, width, 3), _BACKGROUND, dtype=np.uint8)
    y0, y1 = round(height * _Y_BAND[0]), round(height * _Y_BAND[1])
    for i, value in enumerate(_cell_values(ms)):
        x0, x1 = cell_span(i, width)
        img[y0:y1, x0:x1] = 255 if value else 0
    return img


def decode_stamp(bgr: np.ndarray) -> int | None:
    """Return the stamp drawn by `encode_stamp`, or None if `bgr` is not a valid
    pattern (unclear cell, wrong framing cells or parity error)."""
    if bgr is None or bgr.ndim != 3 or bgr.shape[2] != 3:
        return None
    height, width = bgr.shape[:2]
    if width < CELL_COUNT * 8 or height < 16:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    band_y0, band_y1 = height * _Y_BAND[0], height * _Y_BAND[1]
    quarter = (band_y1 - band_y0) / 4
    rows = gray[round(band_y0 + quarter) : round(band_y1 - quarter)]
    values = []
    for i in range(CELL_COUNT):
        x0, x1 = cell_span(i, width)
        q = (x1 - x0) // 4
        cell = rows[:, x0 + q : x1 - q]
        mean = float(cell.mean())
        if float(cell.std()) > _STD_MAX:
            return None
        if mean <= _BLACK_MAX:
            values.append(0)
        elif mean >= _WHITE_MIN:
            values.append(1)
        else:
            return None
    if values[WHITE_CELL] != 1 or values[BLACK_CELL] != 0:
        return None
    bits = values[FIRST_DATA_CELL:PARITY_CELL]
    if sum(bits) & 1 != values[PARITY_CELL]:
        return None
    stamp = 0
    for bit in bits:
        stamp = (stamp << 1) | bit
    return stamp


def stamp_age_ms(now_ms: int, stamp: int) -> int:
    """Return how many ms before `now_ms` the 24-bit `stamp` was drawn, modulo 2**24."""
    return (now_ms - stamp) & STAMP_MASK
