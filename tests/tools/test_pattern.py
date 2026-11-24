import itertools

import cv2
import numpy as np
import pytest

from autoplay.core.tools.pattern import (
    CELL_COUNT,
    PARITY_CELL,
    cell_span,
    decode_stamp,
    encode_stamp,
    paint_stamp,
    stamp_age_ms,
)

SIZE = (1920, 1080)


def test_pattern_roundtrip_all_bits():
    values = [0, 0xFFFFFF, 0xA5A5A5, 0x5A5A5A] + [1 << i for i in range(24)]
    for ms in values:
        img = encode_stamp(ms, SIZE)
        assert img.shape == (1080, 1920, 3)
        assert img.dtype == np.uint8
        assert decode_stamp(img) == ms


def test_paint_stamp_repaints_in_place():
    img = encode_stamp(0x000000, SIZE)
    buffer = img.ctypes.data
    paint_stamp(img, 0xABCDEF)
    assert img.ctypes.data == buffer
    assert np.array_equal(img, encode_stamp(0xABCDEF, SIZE))
    assert decode_stamp(img) == 0xABCDEF


def test_encode_keeps_low_24_bits():
    ms = (7 << 24) | 0x123456
    assert decode_stamp(encode_stamp(ms, SIZE)) == 0x123456


def test_pattern_survives_resize_to_720p():
    rng = np.random.default_rng(1)
    for ms in [0, 0xFFFFFF, *rng.integers(0, 1 << 24, size=20).tolist()]:
        img = cv2.resize(
            encode_stamp(ms, SIZE), (1280, 720), interpolation=cv2.INTER_AREA
        )
        assert decode_stamp(img) == ms


def test_decode_tolerates_limited_range_and_noise():
    rng = np.random.default_rng(3)
    img = encode_stamp(0xC0FFEE, SIZE).astype(np.float32) * (219 / 255) + 16
    img += rng.normal(0, 6, size=img.shape)
    assert decode_stamp(np.clip(img, 0, 255).astype(np.uint8)) == 0xC0FFEE


def test_decode_returns_none_on_non_pattern():
    rng = np.random.default_rng(2)
    gradient = np.tile(np.linspace(0, 255, 1920, dtype=np.uint8), (1080, 1))
    candidates = [
        np.zeros((1080, 1920, 3), np.uint8),
        np.full((1080, 1920, 3), 255, np.uint8),
        np.full((1080, 1920, 3), 128, np.uint8),
        np.dstack([gradient] * 3),
        rng.integers(0, 256, size=(1080, 1920, 3), dtype=np.uint8),
        np.zeros((8, 8, 3), np.uint8),
        np.zeros((1080, 1920), np.uint8),
    ]
    for img in candidates:
        assert decode_stamp(img) is None


def test_decode_returns_none_when_a_cell_is_gray():
    img = encode_stamp(0x123456, SIZE)
    x0, x1 = cell_span(5, SIZE[0])
    img[:, x0:x1] = 128
    assert decode_stamp(img) is None


def test_decode_returns_none_on_parity_error():
    img = encode_stamp(0x123456, SIZE)
    x0, x1 = cell_span(PARITY_CELL, SIZE[0])
    img[:, x0:x1] = 255 - img[:, x0:x1]
    assert decode_stamp(img) is None


def test_cell_spans_tile_the_band():
    spans = [cell_span(i, SIZE[0]) for i in range(CELL_COUNT)]
    assert all(a[1] == b[0] for a, b in itertools.pairwise(spans))
    assert spans[0][0] > 0 and spans[-1][1] < SIZE[0]


@pytest.mark.parametrize(
    ("now_ms", "stamp", "age"),
    [
        (1000, 900, 100),
        (5, (1 << 24) - 10, 15),
        ((3 << 24) + 5, (1 << 24) - 10, 15),
        ((3 << 24) + 500, 400, 100),
    ],
)
def test_age_handles_24bit_wraparound(now_ms, stamp, age):
    assert stamp_age_ms(now_ms, stamp) == age
