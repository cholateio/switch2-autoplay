"""The Pokopia pack's picture checks, on drawn frames only."""

import importlib.util
from pathlib import Path

import cv2
import numpy as np
import pytest

from autoplay.core.pack.routines import load_routines

PACK = Path(__file__).parents[2] / "game_packs" / "pokopia"
_spec = importlib.util.spec_from_file_location("pokopia_checks_t", PACK / "checks.py")
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)


def scene(seed: int = 3) -> np.ndarray:
    """A mid-grey frame with mild noise, like grass at night."""
    rng = np.random.default_rng(seed)
    return rng.integers(70, 95, (1080, 1920, 3), dtype=np.uint8)


def dashed_box(frame: np.ndarray, x: int, y: int, w: int, h: int) -> None:
    def dash_line(x1, y1, x2, y2):
        steps = int(np.hypot(x2 - x1, y2 - y1) // 12)
        for i in range(steps):
            a, b = i / steps, (i + 0.55) / steps
            start = (int(x1 + (x2 - x1) * a), int(y1 + (y2 - y1) * a))
            end = (int(x1 + (x2 - x1) * b), int(y1 + (y2 - y1) * b))
            cv2.line(frame, start, end, (235, 235, 235), 2)

    for x1, y1, x2, y2 in (
        (x, y, x + w, y),
        (x + w, y, x + w, y + h),
        (x + w, y + h, x, y + h),
        (x, y + h, x, y),
    ):
        dash_line(x1, y1, x2, y2)


def test_no_outline_in_a_plain_scene():
    assert checks.outline(scene()) is None


def test_a_standing_block_outline_is_found_tall():
    frame = scene()
    dashed_box(frame, 900, 400, 124, 162)
    box = checks.outline(frame)
    assert box is not None
    x, y, w, h = box
    assert abs(x - 900) <= 8 and abs(y - 400) <= 8 and abs(w - 124) <= 12
    assert h >= 130


def test_a_ground_cell_outline_is_found_flat():
    frame = scene()
    dashed_box(frame, 890, 450, 112, 88)
    box = checks.outline(frame)
    assert box is not None and box[3] < 130


def test_an_outline_far_from_the_centre_is_ignored():
    frame = scene()
    dashed_box(frame, 100, 300, 124, 162)
    assert checks.outline(frame) is None


def test_solid_lines_are_not_an_outline():
    frame = scene()
    cv2.rectangle(frame, (900, 400), (1024, 562), (235, 235, 235), 2)
    assert checks.outline(frame) is None


def lettering(text: str, size=(46, 150)) -> np.ndarray:
    patch = np.full((*size, 3), 90, np.uint8)
    cv2.putText(patch, text, (4, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    return patch


def with_prompt(text: str, x: int, shade: int = 80) -> np.ndarray:
    frame = np.full((1080, 1920, 3), shade, np.uint8)
    frame[1018 : 1018 + 46, x : x + 150] = lettering(text)[:, :, :] // 90 * shade
    mask = lettering(text).min(axis=2) >= 200
    frame[1018 : 1018 + 46, x : x + 150][mask] = 255
    return frame


def test_a_prompt_is_found_wherever_it_sits_and_whatever_is_behind_it():
    template = lettering("Y BREAK")
    assert checks.prompt_score(with_prompt("Y BREAK", 600), template) > 0.95
    assert checks.prompt_score(with_prompt("Y BREAK", 1100, shade=170), template) > 0.95
    assert checks.prompt_score(with_prompt("R AWAY", 600), template) < 0.7
    assert checks.prompt_score(scene(), template) == 0.0


def test_the_tail_tells_one_quantity_from_another():
    template = lettering("COUNT:1")
    assert checks.prompt_tail_score(with_prompt("COUNT:1", 700), template, 30) > 0.9
    assert checks.prompt_score(with_prompt("COUNT:4", 700), template) > 0.8
    assert checks.prompt_tail_score(with_prompt("COUNT:4", 700), template, 30) < 0.7
    assert checks.prompt_tail_score(scene(), template, 30) == 0.0


def test_changed_fraction_sees_a_block_appear_and_not_noise():
    before = scene(1)
    noisy = scene(2)
    assert checks.changed_fraction(before, noisy) < 0.03
    placed = before.copy()
    x, y, _w, _h = checks.FRONT_ROI
    placed[y + 60 : y + 220, x + 40 : x + 180] = (60, 140, 90)
    assert checks.changed_fraction(before, placed) > 0.09


def bag(page: int, cursor: tuple[int, int] | None) -> np.ndarray:
    frame = np.full((1080, 1920, 3), 235, np.uint8)
    for i, (x, y) in enumerate(checks.BAG_DOTS):
        colour = (40, 150, 245) if i + 1 == page else (200, 200, 200)
        cv2.circle(frame, (x, y), 9, colour, 4 if i + 1 == page else -1)
    if cursor is not None:
        row, column = cursor
        centre = (checks.BAG_COLUMNS[column - 1], checks.BAG_ROWS[row - 1])
        cv2.circle(frame, centre, 44, (40, 225, 250), -1)
        cv2.rectangle(
            frame,
            (centre[0] - 30, centre[1] - 30),
            (centre[0] + 30, centre[1] + 30),
            (90, 160, 110),
            -1,
        )
    return frame


@pytest.mark.parametrize("page", [1, 2, 3, 4])
def test_the_bag_page_is_the_orange_dot(page):
    assert checks.bag_page(bag(page, (1, 1))) == page


def test_no_page_outside_the_bag():
    assert checks.bag_page(scene()) is None and checks.bag_cursor(scene()) is None


@pytest.mark.parametrize("cursor", [(1, 1), (1, 4), (2, 10), (3, 1)])
def test_the_bag_cursor_is_the_cell_with_the_yellow_disc(cursor):
    assert checks.bag_cursor(bag(4, cursor)) == cursor


def test_the_routines_file_loads_and_names_what_the_guide_names():
    routines = load_routines(PACK / "routines.py")
    assert set(routines) == {
        "view",
        "face_away",
        "bag_goto",
        "place_line",
        "place_wall",
        "break_wall",
        "magnet_on",
        "magnet_off",
        "ground",
        "rise",
        "break_front",
        "break_column",
        "eat_berries",
        "leave",
    }
    assert routines["leave"].params == {}


def bag_with_count(text: str) -> np.ndarray:
    frame = bag(4, (1, 4))
    x, y = checks.BAG_COLUMNS[3], checks.BAG_ROWS[0]
    cv2.putText(
        frame, text, (x + 16, y + 52), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (90, 90, 90), 2
    )
    return frame


def test_the_count_beside_a_cell_tells_same_from_changed_without_reading_it():
    cell = (1, 4)
    ninety_nine = checks.count_mark(bag_with_count("99"), cell)
    again = checks.count_mark(bag_with_count("99"), cell)
    ninety_eight = checks.count_mark(bag_with_count("98"), cell)
    assert ninety_nine.any()
    assert checks.same_count(ninety_nine, again)
    assert not checks.same_count(ninety_nine, ninety_eight)
