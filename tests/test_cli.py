from autoplay.cli import main


def test_no_args_prints_help_and_returns_2(capsys):
    assert main([]) == 2
    assert "usage: ap" in capsys.readouterr().err


def test_version(capsys):
    assert main(["--version"]) == 0
