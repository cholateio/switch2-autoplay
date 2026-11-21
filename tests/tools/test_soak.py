import json

import numpy as np
import pytest

from autoplay.cli import main
from autoplay.core.capture.base import CaptureError, Frame
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.types import Hat
from autoplay.core.runtime.runlog import RunLog
from autoplay.core.tools.soak import (
    SoakResult,
    difference,
    fresh_frame,
    parse_buttons,
    parse_roi,
    run_soak,
    signature,
)


class FakeConsole(DummyController):
    """A menu with a cursor: D-pad right and left move a bright tile. It is
    both the gamepad and the frame source. `ignore` lists the presses (counted
    from 0) the console does not react to."""

    def __init__(self, ignore=(), size=(1080, 1920)):
        super().__init__()
        self.position = 3
        self.ignore = set(ignore)
        self.presses = 0
        self.size = size
        self.seq = 0
        self.stalled = False

    def _apply(self, state, hold_ms):
        super()._apply(state, hold_ms)
        if state.hat in (Hat.RIGHT, Hat.LEFT):
            if self.presses not in self.ignore:
                self.position += 1 if state.hat == Hat.RIGHT else -1
            self.presses += 1

    def _frame(self) -> Frame:
        height, width = self.size
        image = np.full((height, width, 3), 30, np.uint8)
        tile = width // 12
        image[
            height // 3 : 2 * height // 3,
            self.position * tile : (self.position + 1) * tile,
        ] = 240
        return Frame(image, float(self.seq), self.seq)

    def latest(self):
        return self._frame() if self.seq else None

    def wait_next(self, after_seq, timeout):
        if self.stalled:
            return None
        self.seq = max(self.seq, after_seq) + 1
        return self._frame()


def soak(console, tmp_path, cycles=5, buttons=("DPAD_RIGHT", "DPAD_LEFT"), **kwargs):
    log = RunLog("soak", root=tmp_path)
    try:
        result = run_soak(
            console,
            console,
            list(buttons),
            cycles,
            log,
            settle_s=0.4,
            sleep=lambda s: None,
            **kwargs,
        )
    finally:
        log.close()
    events = [
        json.loads(x) for x in (log.dir / "events.jsonl").read_text().splitlines()
    ]
    return result, log.dir, events


def test_a_console_that_follows_every_press_passes(tmp_path):
    result, run_dir, events = soak(FakeConsole(), tmp_path)
    assert result == SoakResult(cycles=5, step_failures=0, cycle_failures=0)
    assert result.ok
    assert str(result) == "cycles=5 step_failures=0 cycle_failures=0"
    assert [e["kind"] for e in events].count("step") == 10
    assert all(e["ok"] for e in events)
    assert list(run_dir.glob("*.png")) == []


def test_an_ignored_press_is_a_step_failure_and_a_cycle_failure(tmp_path):
    # Press 2 is the first press of cycle 1: the cursor stays, then LEFT moves it away.
    result, run_dir, events = soak(FakeConsole(ignore={2}), tmp_path, cycles=3)
    assert result.step_failures == 1
    assert result.cycle_failures == 1
    assert not result.ok
    failed = [e for e in events if not e["ok"]]
    assert [(e["kind"], e["cycle"]) for e in failed] == [("step", 1), ("cycle", 1)]
    names = sorted(p.name for p in run_dir.glob("*.png"))
    assert names == [
        "0001-c1-s0-DPAD_RIGHT-before.png",
        "0002-c1-s0-DPAD_RIGHT-after.png",
        "0003-c1-start.png",
        "0004-c1-end.png",
    ]


def test_a_sequence_that_does_not_return_is_a_cycle_failure_only(tmp_path):
    result, _, _ = soak(FakeConsole(), tmp_path, cycles=2, buttons=("DPAD_RIGHT",))
    assert result.step_failures == 0
    assert result.cycle_failures == 2


def test_a_smaller_capture_resolution_works(tmp_path):
    result, _, _ = soak(FakeConsole(size=(720, 1280)), tmp_path)
    assert result.ok


def test_roi_limits_what_is_compared(tmp_path):
    # The tile moves in the middle third; the top strip never changes.
    result, _, _ = soak(FakeConsole(), tmp_path, cycles=1, roi=(0, 0, 1920, 200))
    assert result.step_failures == 2


def test_a_stalled_capture_is_an_error(tmp_path):
    console = FakeConsole()
    console.stalled = True
    with pytest.raises(CaptureError, match="stalled"):
        soak(console, tmp_path)


def test_fresh_frame_is_newer_than_the_latest():
    console = FakeConsole()
    first = fresh_frame(console)
    assert fresh_frame(console).seq > first.seq


def test_difference_of_equal_frames_is_zero_and_grows_with_change():
    console = FakeConsole()
    a = fresh_frame(console)
    assert difference(a, fresh_frame(console), None) == 0
    console.position += 1
    assert difference(a, fresh_frame(console), None) > 5
    assert signature(a.bgr, None).shape == (180, 320)


def test_parse_roi():
    assert parse_roi("0,0,1920,1080") == (0, 0, 1920, 1080)
    assert parse_roi("100,200,300,400") == (100, 200, 300, 400)
    for bad in ("1,2,3", "a,b,c,d", "0,0,0,10", "1900,0,100,10", "-1,0,10,10"):
        with pytest.raises(ValueError):
            parse_roi(bad)


def test_parse_buttons():
    assert parse_buttons("A, B") == ["A", "B"]
    with pytest.raises(ValueError, match="TURBO"):
        parse_buttons("A,TURBO")


@pytest.mark.parametrize(
    "argv",
    [
        ["--seq", "A,TURBO"],
        ["--seq", "A", "--roi", "1,2,3"],
        ["--seq", "A", "--source", "file:x.mp4"],
        ["--seq", "A", "--cycles", "0"],
    ],
)
def test_bad_arguments_are_usage_errors_before_any_device_is_opened(argv, capsys):
    assert main(["soak", "--port", "COM99", *argv]) == 2
    assert "ap soak:" in capsys.readouterr().err
