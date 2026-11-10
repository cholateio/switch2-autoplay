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
