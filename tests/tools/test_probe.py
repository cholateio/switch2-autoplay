import math

import pytest

from autoplay.cli import main
from autoplay.core.tools import probe
from autoplay.core.tools.probe import (
    Monitor,
    format_monitors,
    interval_percentiles_ms,
    summarize_loopback,
    summarize_reads,
)

MONITORS = [
    Monitor(
        index=0,
        name=r"\\.\DISPLAY1",
        x=0,
        y=0,
        width=2560,
        height=1440,
        refresh_hz=144,
        primary=True,
    ),
    Monitor(
        index=1,
        name=r"\\.\DISPLAY2",
        x=2560,
        y=0,
        width=1920,
        height=1080,
        refresh_hz=60,
        primary=False,
    ),
]


def test_interval_percentiles_ms():
    times = [i / 60 for i in range(61)]
    p50, p99 = interval_percentiles_ms(times)
    assert p50 == pytest.approx(1000 / 60)
    assert p99 == pytest.approx(1000 / 60)


def test_interval_percentiles_ms_catches_a_stall():
    times = [i * 0.01 for i in range(99)]
    times += [times[-1] + 0.5, times[-1] + 1.0]
    p50, p99 = interval_percentiles_ms(times)
    assert p50 == pytest.approx(10)
    assert p99 == pytest.approx(500)


def test_interval_percentiles_ms_needs_two_frames():
    assert all(math.isnan(v) for v in interval_percentiles_ms([1.0]))


def test_summarize_reads():
    ends = [i / 50 for i in range(251)]
    timing = summarize_reads(ends, [0.002] * 251, elapsed=5.0)
    assert timing.fps == pytest.approx(50.2)
    assert timing.p50_ms == pytest.approx(20)
    assert timing.read_ms == pytest.approx(2)


def test_summarize_loopback_valid_at_90_percent():
    summary = summarize_loopback([10] * 45 + [30] * 45, attempts=100)
    assert summary.decode_rate == pytest.approx(0.9)
    assert summary.valid
    assert summary.p50_ms == pytest.approx(20)
    assert summary.p99_ms == pytest.approx(30)


def test_summarize_loopback_invalid_below_90_percent():
    summary = summarize_loopback([10] * 89, attempts=100)
    assert not summary.valid


def test_summarize_loopback_invalid_without_frames():
    summary = summarize_loopback([], attempts=0)
    assert summary.decode_rate == 0.0
    assert not summary.valid


def test_format_monitors_lists_index_resolution_and_refresh():
    text = format_monitors(MONITORS)
    assert "1" in text and "1920x1080" in text and "60 Hz" in text
    assert "2560x1440" in text and "144 Hz" in text


def _no_device(*args, **kwargs):
    raise AssertionError("loopback must not open a device here")


def test_loopback_without_monitor_lists_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_list_monitors", lambda: MONITORS)
    monkeypatch.setattr(probe, "DeviceSource", _no_device)
    assert main(["probe", "--loopback"]) == 2
    out = capsys.readouterr()
    assert "1920x1080" in out.out
    assert "--monitor" in out.err


def test_loopback_rejects_unknown_monitor(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_list_monitors", lambda: MONITORS)
    monkeypatch.setattr(probe, "DeviceSource", _no_device)
    assert main(["probe", "--loopback", "--monitor", "2"]) == 2
    assert "monitor 2" in capsys.readouterr().err


def test_monitor_without_loopback_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_list_monitors", lambda: MONITORS)
    monkeypatch.setattr(probe, "open_capture", _no_device)
    assert main(["probe", "--monitor", "1"]) == 2
    assert "--loopback" in capsys.readouterr().err


def test_probe_help(capsys):
    assert main(["probe", "--help"]) == 0
    assert "--loopback" in capsys.readouterr().out
