from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from autoplay.cli import main
from autoplay.core.capture.base import CaptureStats
from autoplay.core.tools import capture_view
from autoplay.core.tools.capture_view import (
    is_black,
    label_for_key,
    overlay_lines,
    parse_device_source,
    parse_labels,
    screenshot_path,
)


def test_is_black_threshold_is_mean_luma_12():
    assert is_black(np.full((54, 96, 3), 11, np.uint8))
    assert not is_black(np.full((54, 96, 3), 12, np.uint8))


def test_is_black_uses_mean_not_max():
    img = np.zeros((100, 100, 3), np.uint8)
    img[:5] = 255
    assert not is_black(img)
    img[:4] = 0
    assert is_black(img)


@pytest.mark.parametrize(("spec", "index"), [("device:0", 0), ("device:3", 3)])
def test_parse_device_source(spec, index):
    assert parse_device_source(spec) == index


@pytest.mark.parametrize(
    "spec", ["file:clip.mp4", "device:", "device:-1", "device:x", "0"]
)
def test_parse_device_source_rejects_other_forms(spec):
    with pytest.raises(ValueError, match="device:N"):
        parse_device_source(spec)


def test_parse_labels():
    assert parse_labels("menu,in_game, dialog") == ["menu", "in_game", "dialog"]


@pytest.mark.parametrize(
    "text", ["", "a,,b", "../x", "a/b", "a b", ",".join("abcdefghij")]
)
def test_parse_labels_rejects_unsafe_or_too_many(text):
    with pytest.raises(ValueError):
        parse_labels(text)


def test_label_for_key():
    labels = ["menu", "dialog"]
    assert label_for_key(ord("1"), labels) == "menu"
    assert label_for_key(ord("2"), labels) == "dialog"
    assert label_for_key(ord("3"), labels) is None
    assert label_for_key(ord("0"), labels) is None
    assert label_for_key(-1, labels) is None


def test_screenshot_path():
    now = datetime(2026, 11, 11, 9, 5, 7, 42_000)
    path = screenshot_path(Path("fixtures/screens"), "menu", now)
    assert path == Path("fixtures/screens/menu/20261111-090507-042.png")


def _no_device(*args, **kwargs):
    raise AssertionError("must not open a device")


def test_capture_rejects_unsupported_source_without_opening(monkeypatch, capsys):
    monkeypatch.setattr(capture_view, "DeviceSource", _no_device)
    assert main(["capture", "--source", "file:clip.mp4"]) == 2
    assert "device:N" in capsys.readouterr().err


def test_capture_help(capsys):
    assert main(["capture", "--help"]) == 0
    assert "--labels" in capsys.readouterr().out


def _stats(stale):
    return CaptureStats(
        width=1920,
        height=1080,
        fourcc="YUY2",
        fps=0.0 if stale else 59.9,
        frames=10,
        stale=stale,
    )


def test_overlay_marks_stale_stream():
    lines = overlay_lines(_stats(stale=True), black=False, label="menu")
    assert any("STALE" in line for line in lines)
    assert any("0.0 fps" in line for line in lines)


def test_overlay_without_stale_marker_when_live():
    lines = overlay_lines(_stats(stale=False), black=True, label="menu")
    assert not any("STALE" in line for line in lines)
    assert any("59.9 fps" in line and "1920x1080" in line for line in lines)
    assert any("black: yes" in line and "menu" in line for line in lines)
