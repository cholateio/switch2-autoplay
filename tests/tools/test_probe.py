import math
import sys
import threading
import time

import cv2
import numpy as np
import pytest

from autoplay.cli import main
from autoplay.core.tools import probe
from autoplay.core.tools.monitors import Monitor
from autoplay.core.tools.probe import (
    MAX_AGE_MS,
    AgeCollector,
    LoopbackRun,
    ProbeRow,
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
        width=1920,
        height=1080,
        refresh_hz=60,
        primary=True,
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


def test_summarize_reads_fps_comes_from_read_timestamps():
    ends = [100.0 + i / 60 for i in range(301)]
    timing = summarize_reads(ends, [0.002] * 301)
    assert timing.fps == pytest.approx(60.0)
    assert timing.p50_ms == pytest.approx(1000 / 60)
    assert timing.read_ms == pytest.approx(2)


def test_summarize_reads_needs_two_reads_for_fps():
    assert math.isnan(summarize_reads([1.0], [0.01]).fps)


def test_loopback_invalid_when_too_few_frames():
    summary = summarize_loopback([10] * 100, received=100, source_fps=60, seconds=10)
    assert summary.min_frames == 300
    assert summary.decode_rate == pytest.approx(1.0)
    assert not summary.valid
    assert "frames" in summary.reason


def test_loopback_invalid_when_decode_rate_low():
    summary = summarize_loopback([10] * 500, received=600, source_fps=60, seconds=10)
    assert not summary.valid
    assert "decode rate" in summary.reason


def test_loopback_valid_at_90_percent():
    ages = [10] * 270 + [30] * 270
    summary = summarize_loopback(ages, received=600, source_fps=60, seconds=10)
    assert summary.decode_rate == pytest.approx(0.9)
    assert summary.valid
    assert summary.reason is None
    assert summary.p50_ms == pytest.approx(20)
    assert summary.p99_ms == pytest.approx(30)


def test_loopback_invalid_without_frames():
    summary = summarize_loopback([], received=0, source_fps=0, seconds=10)
    assert summary.decode_rate == 0.0
    assert not summary.valid


def test_loopback_counts_implausible_age_as_decode_failure():
    corrupted = (1 << 24) - 50
    ok = summarize_loopback(
        [10] * 570 + [corrupted] * 30, received=600, source_fps=60, seconds=10
    )
    assert ok.decoded == 570
    assert ok.valid
    assert ok.p99_ms == pytest.approx(10)
    bad = summarize_loopback(
        [10] * 510 + [MAX_AGE_MS + 1] * 90, received=600, source_fps=60, seconds=10
    )
    assert bad.decoded == 510
    assert not bad.valid
    edge = summarize_loopback(
        [MAX_AGE_MS] * 600, received=600, source_fps=60, seconds=10
    )
    assert edge.decoded == 600


def test_loopback_invalid_when_source_is_far_below_the_requested_rate():
    summary = summarize_loopback([10] * 10, received=10, source_fps=1.0, seconds=10)
    assert summary.decode_rate == pytest.approx(1.0)
    assert not summary.valid
    assert "1.0 fps" in summary.reason


class _RaisingSource:
    def wait_next(self, after_seq, timeout):
        raise RuntimeError("decoder exploded")


def test_age_collector_records_consumer_exception():
    collector = AgeCollector(_RaisingSource(), measure_from=0.0, measure_until=1e9)
    thread = threading.Thread(target=collector.run, args=(threading.Event(),))
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert "decoder exploded" in collector.error


class _FakeSource:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _no_device(*args, **kwargs):
    raise AssertionError("must not open a device here")


def _loopback(monkeypatch, run):
    monkeypatch.setattr(probe, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(probe, "DeviceSource", _FakeSource)
    monkeypatch.setattr(probe, "_measure_loopback", lambda src, monitor: run)
    return main(["probe", "--loopback", "--monitor", "0"])


def test_loopback_consumer_error_fails_the_run(monkeypatch, capsys):
    run = LoopbackRun([10] * 600, 600, 60.0, "RuntimeError: decoder exploded")
    assert _loopback(monkeypatch, run) == 1
    out = capsys.readouterr()
    assert "decoder exploded" in out.err
    assert "p50" not in out.out


def test_loopback_invalid_run_gives_no_verdict(monkeypatch, capsys):
    assert _loopback(monkeypatch, LoopbackRun([10] * 50, 600, 60.0, None)) == 1
    out = capsys.readouterr()
    assert "invalid" in out.err
    assert "p50" not in out.out


def test_loopback_valid_run_prints_percentiles(monkeypatch, capsys):
    assert _loopback(monkeypatch, LoopbackRun([10] * 600, 600, 60.0, None)) == 0
    assert "p50 10.0 ms" in capsys.readouterr().out


def test_loopback_without_monitor_lists_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(probe, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(probe, "DeviceSource", _no_device)
    assert main(["probe", "--loopback"]) == 2
    out = capsys.readouterr()
    assert "1920x1080" in out.out
    assert "--monitor" in out.err


def test_loopback_rejects_unknown_monitor(monkeypatch, capsys):
    monkeypatch.setattr(probe, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(probe, "DeviceSource", _no_device)
    assert main(["probe", "--loopback", "--monitor", "1"]) == 2
    assert "monitor 1" in capsys.readouterr().err


def test_monitor_without_loopback_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(probe, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(probe, "open_capture", _no_device)
    assert main(["probe", "--monitor", "1"]) == 2
    assert "--loopback" in capsys.readouterr().err


# Delivers 8x8 frames, but read() raises cv2.error once NV12 has been requested.
class _TableCapture:
    def __init__(self):
        self.props = {}

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.props[prop] = value
        return True

    def get(self, prop):
        return self.props.get(prop, 0.0)

    def read(self):
        if self.props.get(cv2.CAP_PROP_FOURCC) == cv2.VideoWriter.fourcc(*"NV12"):
            raise cv2.error("simulated driver failure")
        time.sleep(0.002)
        return True, np.zeros((8, 8, 3), np.uint8)

    def release(self):
        pass


def _table_opener(index, api):
    if api == cv2.CAP_MSMF:
        raise RuntimeError("msmf exploded")
    return _TableCapture()


def _in_process(index, backend, fourcc):
    return probe._probe_one(index, backend, fourcc, _table_opener, 0.05)


def test_probe_table_continues_past_failing_combinations(capsys):
    assert probe._run_table(0, run_row=_in_process) == 0
    rows = capsys.readouterr().out.strip().splitlines()[1:]
    assert len(rows) == 6
    by_key = {tuple(r.split()[:2]): r for r in rows}
    assert "8x8" in by_key[("dshow", "YUY2")]
    assert "8x8" in by_key[("dshow", "MJPG")]
    assert "unavailable" in by_key[("dshow", "NV12")]
    assert "simulated driver failure" in by_key[("dshow", "NV12")]
    for fourcc in ("YUY2", "NV12", "MJPG"):
        assert "msmf exploded" in by_key[("msmf", fourcc)]


def test_probe_table_fails_when_nothing_was_measured(capsys):
    def nothing(index, backend, fourcc):
        return ProbeRow(f"{backend} {fourcc} unavailable (no device)", False)

    assert probe._run_table(0, run_row=nothing) == 1
    out = capsys.readouterr()
    assert len(out.out.strip().splitlines()) == 7
    assert "no backend" in out.err


# Delivers a single frame, then fails every read.
class _OneFrameCapture(_TableCapture):
    def __init__(self):
        super().__init__()
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.reads > 2:
            time.sleep(0.002)
            return False, None
        return super().read()


def test_one_frame_is_not_a_measurement():
    row = probe._probe_one(0, "dshow", "YUY2", lambda i, a: _OneFrameCapture(), 0.05)
    assert not row.measured
    assert "unavailable" in row.text
    assert "nan" not in row.text


def test_row_command_runs_one_combination_in_a_child_interpreter():
    assert probe._row_command(3, "msmf", "NV12") == [
        sys.executable,
        "-m",
        "autoplay",
        "probe",
        "--index",
        "3",
        "--only",
        "msmf:NV12",
    ]


def test_spawn_row_kills_a_hung_probe():
    hung = [sys.executable, "-c", "import time; time.sleep(60)"]
    t0 = time.monotonic()
    row = probe._spawn_row(0, "dshow", "YUY2", timeout=0.5, command=hung)
    assert time.monotonic() - t0 < 20
    assert not row.measured
    assert row.text.split()[:2] == ["dshow", "YUY2"]
    assert "timed out" in row.text


def test_spawn_row_relays_the_child_row_and_status():
    fine = [sys.executable, "-c", "print('dshow   YUY2   8x8 row')"]
    row = probe._spawn_row(0, "dshow", "YUY2", timeout=60, command=fine)
    assert row == ProbeRow("dshow   YUY2   8x8 row", True)
    failed = [
        sys.executable,
        "-c",
        "import sys; print('dshow   YUY2   unavailable (x)'); sys.exit(1)",
    ]
    row = probe._spawn_row(0, "dshow", "YUY2", timeout=60, command=failed)
    assert row == ProbeRow("dshow   YUY2   unavailable (x)", False)


def test_spawn_row_reports_a_probe_that_died_without_a_row():
    crashed = [sys.executable, "-c", "import sys; sys.exit(3)"]
    row = probe._spawn_row(0, "dshow", "YUY2", timeout=60, command=crashed)
    assert not row.measured
    assert "exited with code 3" in row.text


# The combination is rejected before any device is opened, so this is safe on
# a machine with real capture hardware.
def test_spawn_row_reaches_the_cli_in_a_child_interpreter():
    row = probe._spawn_row(0, "bogus", "XXXX", timeout=120)
    assert not row.measured
    assert "exited with code 2" in row.text


def test_only_measures_one_combination(capsys):
    assert probe._run_only(0, "dshow:YUY2", opener=_table_opener, seconds=0.05) == 0
    rows = capsys.readouterr().out.strip().splitlines()
    assert len(rows) == 1
    assert "8x8" in rows[0]


def test_only_exits_nonzero_when_unavailable(capsys):
    assert probe._run_only(0, "msmf:YUY2", opener=_table_opener, seconds=0.05) == 1
    assert "msmf exploded" in capsys.readouterr().out


def test_only_rejects_unknown_combinations(monkeypatch, capsys):
    monkeypatch.setattr(probe, "open_capture", _no_device)
    monkeypatch.setattr(probe, "DeviceSource", _no_device)
    assert main(["probe", "--only", "v4l2:YUY2"]) == 2
    assert main(["probe", "--only", "dshow"]) == 2
    assert main(["probe", "--loopback", "--only", "dshow:YUY2"]) == 2
    assert "--only" in capsys.readouterr().err


def test_probe_help(capsys):
    assert main(["probe", "--help"]) == 0
    assert "--loopback" in capsys.readouterr().out


def test_loopback_invalid_when_source_reported_no_fps():
    summary = summarize_loopback([10], received=1, source_fps=0.0, seconds=10)
    assert summary.decode_rate == pytest.approx(1.0)
    assert not summary.valid
    assert "0.0 fps" in summary.reason
