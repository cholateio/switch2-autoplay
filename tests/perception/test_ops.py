import cv2
import numpy as np
import pytest
from synth import PATCH_H, PATCH_W, patch, scene

from autoplay.core.perception.geometry import ROI, crop, parse_roi
from autoplay.core.perception.ops import (
    frame_diff,
    is_black,
    match_template,
    roi_color,
)

AT = (700, 420)


def test_crop_scales_roi_to_720p():
    frame = np.zeros((720, 1280, 3), np.uint8)
    assert crop(frame, ROI(960, 540, 192, 108)).shape == (72, 128, 3)


def test_crop_at_base_size_and_without_roi():
    frame = scene(None)
    assert crop(frame, None) is frame
    part = crop(frame, ROI(10, 20, 30, 40))
    assert part.shape == (40, 30, 3)
    assert np.array_equal(part, frame[20:60, 10:40])


@pytest.mark.parametrize("size", [(640, 360), (320, 240), (100, 7), (1280, 720)])
@pytest.mark.parametrize(
    "box", [(1919, 1079, 1, 1), (1919, 0, 1, 1080), (0, 1079, 1920, 1), (5, 5, 1, 1)]
)
def test_crop_gives_at_least_one_pixel_at_every_edge(size, box):
    frame = np.zeros((size[1], size[0], 3), np.uint8)
    part = crop(frame, ROI(*box))
    assert part.shape[0] >= 1
    assert part.shape[1] >= 1
    assert is_black(frame, ROI(*box))


@pytest.mark.parametrize(
    "box", [(0, 0, 0, 10), (0, 0, 10, 0), (-1, 0, 10, 10), (1900, 0, 100, 10)]
)
def test_roi_must_lie_inside_the_base_picture(box):
    with pytest.raises(ValueError, match="not inside 1920x1080"):
        ROI(*box)


def test_parse_roi_reports_a_malformed_text():
    assert parse_roi("1,2,3,4") == ROI(1, 2, 3, 4)
    with pytest.raises(ValueError, match="expected x,y,w,h"):
        parse_roi("1,2,3")


def test_match_template_finds_patch_at_base_coords():
    match = match_template(scene(AT), patch())
    assert match.score > 0.95
    assert abs(match.x - AT[0]) <= 2
    assert abs(match.y - AT[1]) <= 2


@pytest.mark.parametrize("size", [(1280, 720), (3840, 2160)])
def test_match_template_scales_with_frame_size(size):
    interpolation = cv2.INTER_AREA if size[0] < 1920 else cv2.INTER_LINEAR
    frame = cv2.resize(scene(AT), size, interpolation=interpolation)
    match = match_template(frame, patch())
    assert match.score > 0.85
    assert abs(match.x - AT[0]) <= 4
    assert abs(match.y - AT[1]) <= 4


def test_match_template_low_score_when_absent():
    assert match_template(scene(None), patch()).score < 0.5


def test_match_template_searches_only_inside_the_roi():
    frame = scene(AT)
    around = ROI(AT[0] - 30, AT[1] - 30, PATCH_W + 60, PATCH_H + 60)
    match = match_template(frame, patch(), around)
    assert match.score > 0.95
    assert (match.x, match.y) == AT
    assert match_template(frame, patch(), ROI(0, 0, 600, 400)).score < 0.5


def test_match_template_rejects_a_template_larger_than_the_search_area():
    with pytest.raises(ValueError, match="larger than the search area"):
        match_template(scene(AT), patch(), ROI(0, 0, PATCH_W - 1, PATCH_H))


def test_match_template_scores_zero_on_a_flat_picture():
    flat = np.full((1080, 1920, 3), 90, np.uint8)
    assert match_template(flat, patch()).score == 0.0


def test_match_template_rejects_a_flat_template():
    # OpenCV would report a perfect match everywhere.
    with pytest.raises(ValueError, match="flat colour"):
        match_template(scene(AT), np.full((40, 40, 3), 90, np.uint8))


def test_roi_color_is_the_mean_bgr():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:, :960] = (10, 20, 30)
    assert roi_color(frame, ROI(0, 0, 960, 1080)) == (10.0, 20.0, 30.0)
    assert roi_color(frame) == (5.0, 10.0, 15.0)


def test_frame_diff_zero_for_identical_and_positive_for_changed():
    a = scene(None)
    b = a.copy()
    assert frame_diff(a, b) == 0.0
    b[0:540, 0:960] = 255 - b[0:540, 0:960]
    assert frame_diff(a, b) > 10
    assert frame_diff(a, b, ROI(960, 540, 960, 540)) == 0.0
    assert frame_diff(a, b, ROI(0, 0, 960, 540)) > frame_diff(a, b)
    with pytest.raises(ValueError, match="differ in size"):
        frame_diff(a, a[:540])


def test_is_black():
    assert is_black(np.full((54, 96, 3), 11, np.uint8))
    assert not is_black(np.full((54, 96, 3), 12, np.uint8))
    # The mean decides, not the brightest pixel.
    img = np.zeros((100, 100, 3), np.uint8)
    img[:4] = 255
    assert is_black(img)
    img[:5] = 255
    assert not is_black(img)
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:, 960:] = 200
    assert is_black(frame, ROI(0, 0, 960, 1080))
    assert not is_black(frame)
    assert is_black(frame, max_mean=120.0)
