import json

import numpy as np
import pytest

from autoplay.cli import main
from autoplay.core.capture.base import CaptureError, CaptureStats, Frame
from autoplay.core.controller.base import ControllerError
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.types import Hat
from autoplay.core.runtime.estop import EmergencyStop, EStop
from autoplay.core.runtime.runlog import RunLog
from autoplay.core.tools import latency as latency_module
from autoplay.core.tools.latency import LatencyResult, percentile, run_latency

FPS = 60.0


class FakeConsole(DummyController):
    """A menu cursor that follows the D-pad `delay` frames late. It is the
    gamepad, the frame source and the clock: time is the frame count."""

    def __init__(self, delay=4, ignore=()):
        super().__init__()
        self.delay = delay
        self.ignore = set(ignore)
        self.presses = 0
        self.seq = 1
        self.moves: list[tuple[int, int]] = []  # (visible from seq, step)
        self.stalled = False
        self.held_at_release: list[Hat] = []

    def now(self) -> float:
        return self.seq / FPS

    def _apply(self, state, hold_ms):
        previous = self.log[-1][0].hat if self.log else Hat.NEUTRAL
        super()._apply(state, hold_ms)
        if state.hat in (Hat.RIGHT, Hat.LEFT) and previous != state.hat:
            if self.presses not in self.ignore:
                step = 1 if state.hat == Hat.RIGHT else -1
                self.moves.append((self.seq + self.delay, step))
            self.presses += 1

    def _frame(self) -> Frame:
        position = 3 + sum(step for seq, step in self.moves if seq <= self.seq)
        image = np.full((90, 160, 3), 30, np.uint8)
        image[30:60, position * 12 : (position + 1) * 12] = 240
        return Frame(image, self.seq / FPS, self.seq)

    def start(self):
        pass

    def latest(self):
        return self._frame()

    def wait_next(self, after_seq, timeout):
        if self.stalled:
            return None
        self.seq = max(self.seq, after_seq) + 1
        return self._frame()

    def ended(self):
        return False

    def stats(self):
        return CaptureStats(160, 90, "YUY2", FPS, self.seq, stale=self.stalled)


def measure(console, tmp_path, n=5, **kwargs):
    log = RunLog("latency", root=tmp_path)
    try:
        result = run_latency(
            console,
            console,
            "DPAD_RIGHT",
            "DPAD_LEFT",
            n,
            log,
            settle_s=0.4,
            now=console.now,
            sleep=lambda s: [console.wait_next(0, 0) for _ in range(round(s * FPS))],
            **kwargs,
        )
    finally:
        log.close()
    lines = (log.dir / "events.jsonl").read_text().splitlines()
    return result, log.dir, [json.loads(line) for line in lines]


def test_latency_is_the_time_from_the_press_to_the_changed_frame(tmp_path):
    result, run_dir, events = measure(FakeConsole(delay=4), tmp_path)
    assert result.missed == 0
    assert result.drifted == 0
    assert result.ok
    assert result.samples_ms == pytest.approx([4 / FPS * 1000] * 5)
    assert [e["kind"] for e in events] == ["sample"] * 5
    assert list(run_dir.glob("*.png")) == []


def test_a_slower_console_measures_longer(tmp_path):
    result, _, _ = measure(FakeConsole(delay=9), tmp_path, n=2)
    assert result.samples_ms == pytest.approx([150.0, 150.0])


def test_the_button_is_released_and_undone_after_every_sample(tmp_path):
    console = FakeConsole()
    measure(console, tmp_path, n=3)
    hats = [state.hat for state, _ in console.log]
    assert hats == [Hat.RIGHT, Hat.NEUTRAL, Hat.LEFT, Hat.NEUTRAL] * 3


def test_an_ignored_press_is_missed_not_pressed_again(tmp_path):
    # Press 2 is the timed press of the second sample.
    console = FakeConsole(ignore={2})
    result, run_dir, events = measure(console, tmp_path, n=3, timeout=0.5)
    assert len(result.samples_ms) == 2
    assert result.missed == 1
    assert not result.ok
    # The undo press then moves the cursor away from where the sample began.
    assert result.drifted == 1
    assert [e["kind"] for e in events] == [
        "sample",
        "action_failed",
        "missed",
        "drift",
        "sample",
    ]
    assert console.presses == 6
    assert len(list(run_dir.glob("*sample1-*.png"))) == 6


def test_a_stalled_capture_ends_the_run_and_releases_the_button(tmp_path):
    console = FakeConsole()
    original = console.hold

    def hold_then_stall(button):
        original(button)
        console.stalled = True

    console.hold = hold_then_stall
    with pytest.raises(CaptureError, match="capture stale"):
        measure(console, tmp_path)
    assert console.log[-1][0].hat == Hat.NEUTRAL


def test_an_emergency_stop_during_the_wait_ends_the_run(tmp_path):
    console = FakeConsole(delay=30)
    estop = EStop(hotkey=False)
    console.attach_estop(estop)
    original = console.hold

    def hold_then_stop(button):
        original(button)
        estop.trip()

    console.hold = hold_then_stop
    with pytest.raises(EmergencyStop):
        measure(console, tmp_path)
    assert console.release_all_calls == 1


def test_percentile_is_nearest_rank():
    values = [float(v) for v in range(1, 11)]
    assert percentile(values, 0.5) == 5.0
    assert percentile(values, 0.9) == 9.0
    assert percentile(values, 1.0) == 10.0
    assert percentile([7.0], 0.9) == 7.0
    assert percentile([3.0, 1.0, 2.0], 0.5) == 2.0


def test_result_text():
    result = LatencyResult((50.0, 70.0, 60.0), missed=1, drifted=0)
    assert str(result) == (
        "n=4 measured=3 missed=1 drifted=0 p50=60.0 p90=70.0 max=70.0 min=50.0 ms"
    )
    assert str(LatencyResult((), 2, 0)) == "n=2 measured=0 missed=2 drifted=0"
    assert not LatencyResult((), 0, 0).ok


@pytest.mark.parametrize(
    "argv",
    [
        ["--button", "TURBO", "--undo", "B"],
        ["--button", "A", "--undo", "TURBO"],
        ["--button", "A", "--undo", "B", "--roi", "1,2,3"],
        ["--button", "A", "--undo", "B", "--source", "file:x.mp4"],
        ["--button", "A", "--undo", "B", "--n", "0"],
        ["--button", "A", "--undo", "B", "--source", "video:clip.avi"],
        ["--button", "A", "--undo", "B", "--source", "dir:frames"],
    ],
)
def test_bad_arguments_are_usage_errors_before_any_device_is_opened(argv, capsys):
    assert main(["latency", "--port", "COM99", *argv]) == 2
    assert "ap latency:" in capsys.readouterr().err


def test_the_capture_is_closed_when_the_port_cannot_be_opened(monkeypatch, capsys):
    closed = []

    class Source:
        def start(self):
            pass

        def close(self):
            closed.append("source")

    def no_port(port):
        raise ControllerError(f"cannot open serial port {port}")

    monkeypatch.setattr(latency_module, "DeviceSource", Source)
    monkeypatch.setattr(latency_module, "open_source", lambda spec: Source())
    monkeypatch.setattr(latency_module, "SerialController", no_port)
    args = ["latency", "--port", "COM9", "--button", "A", "--undo", "B"]
    assert main(args) == 1
    assert "cannot open serial port COM9" in capsys.readouterr().err
    assert closed == ["source"]
