import pytest

from autoplay.cli import main
from autoplay.core.controller.base import ControllerTimeout
from autoplay.core.controller.dummy import DummyController
from autoplay.core.tools import pad


def run(*argv: str) -> int:
    return main(["pad", "--port", "dummy", *argv])


def test_press_on_the_dummy_prints_both_states(capsys):
    assert run("press", "A") == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert "Button.A" in out[0] and out[0].endswith("hold_ms=80")
    assert out[1].endswith("hold_ms=50")


def test_press_takes_ms(capsys):
    assert run("press", "DPAD_LEFT", "--ms", "200") == 0
    assert "hat=LEFT" in capsys.readouterr().out.splitlines()[0]


def test_stick_accepts_negative_values(capsys):
    assert run("stick", "L", "-1", "-1", "--ms", "500") == 0
    assert "L=(0,255)" in capsys.readouterr().out.splitlines()[0]


def test_hold_on_the_dummy_returns_without_waiting(capsys):
    assert run("hold", "ZR") == 0
    assert "Button.ZR" in capsys.readouterr().out


def test_release_all_and_info(capsys):
    assert run("release-all") == 0
    assert "release_all x1" in capsys.readouterr().out
    assert run("info") == 0
    assert "dummy" in capsys.readouterr().out


def test_unknown_button_is_a_usage_error(capsys):
    assert run("press", "TURBO") == 2
    captured = capsys.readouterr()
    assert "unknown button 'TURBO'" in captured.err
    assert "DPAD_UP" in captured.err


def test_out_of_range_stick_is_a_usage_error(capsys):
    assert run("stick", "L", "2", "0") == 2
    assert "outside [-1, 1]" in capsys.readouterr().err


def test_selftest_refuses_the_dummy(capsys):
    assert run("selftest") == 2
    assert "real port" in capsys.readouterr().err


def test_a_port_that_does_not_answer_is_reported(capsys, monkeypatch):
    def refuse(port):
        raise ControllerTimeout(f"no answer to HELLO from {port}")

    monkeypatch.setattr(pad, "open_controller", refuse)
    assert main(["pad", "--port", "COM9", "press", "A"]) == 1
    assert "no answer to HELLO from COM9" in capsys.readouterr().err


class CountingPad(DummyController):
    def __init__(self):
        super().__init__()
        self.heartbeats = 0

    def heartbeat(self):
        self.heartbeats += 1


def interrupt_after(n):
    calls = []

    def sleep(seconds):
        calls.append(seconds)
        if len(calls) > n:
            raise KeyboardInterrupt

    return sleep, calls


def test_keep_alive_renews_until_interrupted():
    counting = CountingPad()
    sleep, calls = interrupt_after(5)
    pad.keep_alive(counting, stall=False, sleep=sleep)
    assert counting.heartbeats == 5
    assert calls[0] == pytest.approx(0.1)


def test_keep_alive_with_stall_never_renews():
    counting = CountingPad()
    sleep, _ = interrupt_after(5)
    pad.keep_alive(counting, stall=True, sleep=sleep)
    assert counting.heartbeats == 0
