import sys
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    index: int
    name: str
    x: int
    y: int
    width: int
    height: int
    refresh_hz: int
    primary: bool


def format_monitors(monitors: Sequence[Monitor]) -> str:
    lines = ["monitor  device          position     resolution  refresh"]
    for m in monitors:
        lines.append(
            f"{m.index:<8} {m.name:<15} {f'{m.x},{m.y}':<12} {f'{m.width}x{m.height}':<11} "
            f"{m.refresh_hz} Hz{'  (primary)' if m.primary else ''}"
        )
    return "\n".join(lines)


def list_monitors() -> list[Monitor]:
    """List the connected monitors in physical pixels; Windows only (raises
    RuntimeError elsewhere). Makes the process per-monitor DPI aware."""
    if sys.platform != "win32":
        raise RuntimeError("monitor listing needs Windows; run through scripts/win")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    # Without per-monitor DPI awareness Windows reports scaled rectangles, and the
    # full-screen pattern would be drawn at the wrong size.
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    # Display variant of DEVMODEW; sizeof must be 220.
    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32),
            ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("dmPositionX", wintypes.LONG),
            ("dmPositionY", wintypes.LONG),
            ("dmDisplayOrientation", wintypes.DWORD),
            ("dmDisplayFixedOutput", wintypes.DWORD),
            ("dmColor", wintypes.SHORT),
            ("dmDuplex", wintypes.SHORT),
            ("dmYResolution", wintypes.SHORT),
            ("dmTTOption", wintypes.SHORT),
            ("dmCollate", wintypes.SHORT),
            ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD),
            ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD),
            ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD),
            ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD),
            ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD),
            ("dmPanningHeight", wintypes.DWORD),
        ]

    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        enum_proc,
        wintypes.LPARAM,
    ]
    user32.GetMonitorInfoW.argtypes = [
        wintypes.HMONITOR,
        ctypes.POINTER(MONITORINFOEXW),
    ]
    user32.EnumDisplaySettingsW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(DEVMODEW),
    ]
    enum_current_settings = 0xFFFFFFFF
    monitor_info_primary = 1
    monitors: list[Monitor] = []

    def callback(hmonitor, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return True
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        ok = user32.EnumDisplaySettingsW(
            info.szDevice, enum_current_settings, ctypes.byref(mode)
        )
        r = info.rcMonitor
        monitors.append(
            Monitor(
                index=len(monitors),
                name=info.szDevice,
                x=r.left,
                y=r.top,
                width=r.right - r.left,
                height=r.bottom - r.top,
                refresh_hz=int(mode.dmDisplayFrequency) if ok else 0,
                primary=bool(info.dwFlags & monitor_info_primary),
            )
        )
        return True

    if not user32.EnumDisplayMonitors(None, None, enum_proc(callback), 0):
        raise RuntimeError("EnumDisplayMonitors failed")
    return monitors
