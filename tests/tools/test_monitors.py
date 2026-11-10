import sys

import pytest

from autoplay.core.tools.monitors import Monitor, format_monitors, list_monitors

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


def test_format_monitors_lists_index_resolution_and_refresh():
    lines = format_monitors(MONITORS).splitlines()
    assert (
        lines[1].split()[0] == "0" and "2560x1440" in lines[1] and "144 Hz" in lines[1]
    )
    assert "(primary)" in lines[1]
    assert (
        lines[2].split()[0] == "1" and "1920x1080" in lines[2] and "60 Hz" in lines[2]
    )


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows guard")
def test_list_monitors_needs_windows():
    with pytest.raises(RuntimeError, match="Windows"):
        list_monitors()
