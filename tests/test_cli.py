import io
import subprocess
import sys

from autoplay.cli import main


def test_no_args_prints_help_and_returns_2(capsys):
    assert main([]) == 2
    assert "usage: ap" in capsys.readouterr().err


def test_version(capsys):
    assert main(["--version"]) == 0


def test_module_entry_point_returns_main_exit_code():
    result = subprocess.run(
        [sys.executable, "-m", "autoplay", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("ap ")


def test_text_the_console_cannot_encode_does_not_crash(monkeypatch):
    import io

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp950"))
    assert main(["--version"]) == 0
    print("caf\u00e9")
    sys.stdout.flush()
    assert b"caf\\xe9" in raw.getvalue()


def test_a_closed_or_plain_stream_does_not_stop_a_command(monkeypatch, capsys):
    import io

    closed = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    assert main(["--version"]) == 0
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert main(["--version"]) == 0


def test_a_closed_stream_is_replaced_so_that_writing_to_it_works(monkeypatch):
    closed = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    monkeypatch.setattr(sys, "stdout", None)
    # No command: argparse prints its usage to stderr and exits with 2.
    assert main([]) == 2
    assert not sys.stderr.closed and sys.stdout is not None


def test_a_missing_stream_is_replaced_too(monkeypatch):
    monkeypatch.delattr(sys, "stderr")
    assert main([]) == 2
    assert sys.stderr is not None
