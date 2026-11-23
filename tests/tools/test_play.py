import pytest
from fake_model import FakeModel
from synth_pack import FakeScreen, write_pack

from autoplay.cli import main
from autoplay.core.controller.base import ControllerError
from autoplay.core.controller.dummy import DummyController
from autoplay.core.runtime.estop import EStop
from autoplay.core.tools import play as play_module
from autoplay.core.tools.play import find_api_key, read_env_file


class Recorder:
    """Stands in for every resource `ap play` opens and notes the order of
    what happens to them."""

    def __init__(self, monkeypatch, tmp_path, script=None, fail=None):
        self.events: list[str] = []
        self.ports: list[str] = []
        self.model = FakeModel(script or [[("finish", {"summary": "all done"})]])
        self.keys: list[str] = []
        recorder = self

        class Source(FakeScreen):
            def __init__(self):
                self._ready = False
                super().__init__("explore")

            def start(self):
                if self._ready:
                    recorder.events.append("source.start")
                self._ready = True
                super().start()

            def close(self):
                recorder.events.append("source.close")

        class Pad(DummyController):
            def attach_estop(self, estop):
                recorder.events.append("pad.attach_estop")
                super().attach_estop(estop)

            def _apply(self, state, hold_ms):
                recorder.events.append("pad.apply")
                super()._apply(state, hold_ms)

            def close(self):
                recorder.events.append("pad.close")

        class Stop(EStop):
            def __init__(self):
                super().__init__(hotkey=False)

            def close(self):
                recorder.events.append("estop.close")
                super().close()

        def serial(port):
            if fail == "port":
                raise ControllerError("cannot open COM9")
            recorder.ports.append(port)
            return Pad()

        def run_log(name):
            if fail == "log":
                raise OSError("disk full")
            return play_module_run_log(name, tmp_path / "runs")

        def model(config, api_key):
            recorder.keys.append(api_key)
            recorder.config = config
            return recorder.model

        play_module_run_log = play_module.RunLog
        monkeypatch.setattr(play_module, "open_source", lambda spec: Source())
        monkeypatch.setattr(play_module, "SerialController", serial)
        monkeypatch.setattr(play_module, "DummyController", Pad)
        monkeypatch.setattr(play_module, "EStop", Stop)
        monkeypatch.setattr(play_module, "RunLog", run_log)
        monkeypatch.setattr(play_module, "make_model", model)


@pytest.fixture
def pack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    return write_pack(tmp_path / "pack")


def play(pack, *argv, tmp_path):
    return main(
        ["play", "--pack", str(pack), "--memory-root", str(tmp_path / "memory"), *argv]
    )


def test_a_run_to_the_end(pack, tmp_path, monkeypatch, capsys):
    script = [[("press", {"button": "A"})], [("finish", {"summary": "all done"})]]
    recorder = Recorder(monkeypatch, tmp_path, script)
    assert play(pack, "--goal", "g", "--port", "COM7", tmp_path=tmp_path) == 0
    out = capsys.readouterr().out
    assert "all done" in out and "reason=done model_calls=2 usd=" in out
    assert recorder.ports == ["COM7"] and recorder.keys == ["sk-test-key"]
    assert "sk-test-key" not in out
    events = recorder.events
    assert events.index("pad.attach_estop") < events.index("pad.apply")
    assert events[-3:] == ["estop.close", "pad.close", "source.close"]


def test_dummy_pad_opens_no_port(pack, tmp_path, monkeypatch):
    recorder = Recorder(monkeypatch, tmp_path)
    assert play(pack, "--goal", "g", "--dummy", tmp_path=tmp_path) == 0
    assert recorder.ports == []


def test_overrides(pack, tmp_path, monkeypatch):
    recorder = Recorder(monkeypatch, tmp_path)
    argv = [
        "--goal",
        "g",
        "--dummy",
        "--model",
        "claude-sonnet-5-5",
        "--max-usd",
        "0.5",
    ]
    assert play(pack, *argv, tmp_path=tmp_path) == 0
    assert recorder.config.model == "claude-sonnet-5-5"
    assert recorder.config.max_usd_per_run == 0.5


@pytest.mark.parametrize(
    "argv",
    [
        ["--goal", "g"],
        ["--goal", "g", "--port", "COM7", "--dummy"],
        ["--port", "COM7"],
        ["--goal", " ", "--dummy"],
        ["--goal", "g", "--dummy", "--max-usd", "0"],
        ["--goal", "g", "--dummy", "--model", "gpt-x"],
    ],
)
def test_usage_errors_open_nothing(pack, tmp_path, monkeypatch, argv):
    recorder = Recorder(monkeypatch, tmp_path)
    assert play(pack, *argv, tmp_path=tmp_path) == 2
    assert recorder.events == [] and recorder.ports == [] and recorder.keys == []


def test_missing_pack_argument_is_a_usage_error(tmp_path, monkeypatch):
    Recorder(monkeypatch, tmp_path)
    assert main(["play", "--goal", "g", "--dummy"]) == 2


def test_pack_that_does_not_load(pack, tmp_path, monkeypatch, capsys):
    recorder = Recorder(monkeypatch, tmp_path)
    (pack / "templates" / "bag.png").unlink()
    assert play(pack, "--goal", "g", "--port", "COM7", tmp_path=tmp_path) == 1
    assert "templates are local only" in capsys.readouterr().err
    assert recorder.events == [] and recorder.ports == []


def test_no_api_key_stops_before_any_device(pack, tmp_path, monkeypatch, capsys):
    recorder = Recorder(monkeypatch, tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert play(pack, "--goal", "g", "--port", "COM7", tmp_path=tmp_path) == 1
    assert ".env" in capsys.readouterr().err
    assert recorder.events == [] and recorder.ports == []


def test_key_from_the_env_file(pack, tmp_path, monkeypatch, capsys):
    recorder = Recorder(monkeypatch, tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-from-file\n", "utf-8")
    assert play(pack, "--goal", "g", "--dummy", tmp_path=tmp_path) == 0
    assert recorder.keys == ["sk-from-file"]
    captured = capsys.readouterr()
    assert "sk-from-file" not in captured.out + captured.err


def test_everything_opened_is_closed_when_the_log_cannot_be_made(
    pack, tmp_path, monkeypatch, capsys
):
    recorder = Recorder(monkeypatch, tmp_path, fail="log")
    assert play(pack, "--goal", "g", "--port", "COM7", tmp_path=tmp_path) == 1
    assert "disk full" in capsys.readouterr().err
    assert recorder.events == [
        "source.start",
        "pad.attach_estop",
        "estop.close",
        "pad.close",
        "source.close",
    ]
    assert recorder.model.sent == []


def test_the_capture_is_closed_when_the_port_cannot_be_opened(
    pack, tmp_path, monkeypatch
):
    recorder = Recorder(monkeypatch, tmp_path, fail="port")
    assert play(pack, "--goal", "g", "--port", "COM9", tmp_path=tmp_path) == 1
    assert recorder.events == ["source.start", "source.close"]


@pytest.mark.parametrize(
    "script, max_usd, code",
    [
        ([[("look", {})]] * 3, "0.065", 4),
        (["sorry"], "2", 0),
    ],
)
def test_exit_codes(pack, tmp_path, monkeypatch, capsys, script, max_usd, code):
    Recorder(monkeypatch, tmp_path, script)
    # The first cap is sized for this model's prices.
    model = ["--model", "claude-opus-5-5"]
    argv = ["--goal", "g", "--dummy", "--max-usd", max_usd, *model]
    assert play(pack, *argv, tmp_path=tmp_path) == code


def test_emergency_stop_exit_code(pack, tmp_path, monkeypatch, capsys):
    recorder = Recorder(monkeypatch, tmp_path, [[("look", {})]] * 3)
    original = recorder.model.send

    def send_then_stop(system, tools, messages, max_tokens=None):
        reply = original(system, tools, messages)
        stops[0].trip()
        return reply

    stops = []
    stop_class = play_module.EStop

    def make_stop():
        stops.append(stop_class())
        return stops[0]

    monkeypatch.setattr(play_module, "EStop", make_stop)
    recorder.model.send = send_then_stop
    assert play(pack, "--goal", "g", "--dummy", tmp_path=tmp_path) == 3
    assert "reason=estop model_calls=1" in capsys.readouterr().out


def test_env_file_parsing(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "﻿# comment\n\nexport A=1\nB = \"two words\"\nC='x=y'\nnot a pair\nD=\n",
        "utf-8",
    )
    assert read_env_file(path) == {"A": "1", "B": "two words", "C": "x=y", "D": ""}
    assert read_env_file(tmp_path / "missing") == {}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert find_api_key(path) is None
    path.write_text("ANTHROPIC_API_KEY=\n", "utf-8")
    assert find_api_key(path) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert find_api_key(path) == "from-env"
