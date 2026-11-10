import argparse
import math
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.device import DeviceSource, fourcc_str, open_capture
from autoplay.core.tools.pattern import decode_stamp, encode_stamp, stamp_age_ms

PROBE_BACKENDS = ("dshow", "msmf")
PROBE_FOURCCS = ("YUY2", "NV12", "MJPG")
PROBE_SECONDS = 5.0
LOOPBACK_SECONDS = 10.0
# The first frames after the window opens still show the previous screen.
LOOPBACK_WARMUP_SECONDS = 1.0
MIN_DECODE_RATE = 0.9
_READ_FAIL_BACKOFF_S = 0.005


@dataclass(frozen=True)
class ReadTiming:
    fps: float
    p50_ms: float
    p99_ms: float
    read_ms: float


@dataclass(frozen=True)
class LoopbackSummary:
    p50_ms: float
    p99_ms: float
    decode_rate: float
    valid: bool


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


def interval_percentiles_ms(times_s: Sequence[float]) -> tuple[float, float]:
    """Return p50 and p99 of consecutive intervals in ms; NaN with fewer than two times."""
    if len(times_s) < 2:
        return math.nan, math.nan
    gaps = np.diff(np.asarray(times_s, dtype=np.float64)) * 1000
    p50, p99 = np.percentile(gaps, [50, 99])
    return float(p50), float(p99)


def summarize_reads(
    read_ends: Sequence[float], read_durations: Sequence[float], elapsed: float
) -> ReadTiming:
    """Summarize successful `read()` calls: completion times and durations in seconds."""
    p50, p99 = interval_percentiles_ms(read_ends)
    read_ms = float(np.mean(read_durations)) * 1000 if len(read_durations) else math.nan
    return ReadTiming(len(read_ends) / elapsed, p50, p99, read_ms)


def summarize_loopback(ages_ms: Sequence[int], attempts: int) -> LoopbackSummary:
    """Summarize decoded frame ages; the run is valid only at a decode rate >= 90%."""
    rate = len(ages_ms) / attempts if attempts else 0.0
    if ages_ms:
        p50, p99 = (float(v) for v in np.percentile(ages_ms, [50, 99]))
    else:
        p50 = p99 = math.nan
    return LoopbackSummary(p50, p99, rate, rate >= MIN_DECODE_RATE)


def format_monitors(monitors: Sequence[Monitor]) -> str:
    lines = ["monitor  device          position     resolution  refresh"]
    for m in monitors:
        lines.append(
            f"{m.index:<8} {m.name:<15} {f'{m.x},{m.y}':<12} {f'{m.width}x{m.height}':<11} "
            f"{m.refresh_hz} Hz{'  (primary)' if m.primary else ''}"
        )
    return "\n".join(lines)


def _list_monitors() -> list[Monitor]:
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


def _probe_one(index: int, backend: str, fourcc: str) -> str:
    label = f"{backend:<7} {fourcc:<6}"
    try:
        cap = open_capture(
            index, backend=backend, width=1920, height=1080, fps=60, fourcc=fourcc
        )
    except CaptureError:
        return f"{label} unavailable"
    try:
        cap.read()
        ends: list[float] = []
        durations: list[float] = []
        shape = None
        start = time.perf_counter()
        deadline = start + PROBE_SECONDS
        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            ok, img = cap.read()
            t1 = time.perf_counter()
            if not ok or img is None:
                time.sleep(_READ_FAIL_BACKOFF_S)
                continue
            ends.append(t1)
            durations.append(t1 - t0)
            shape = img.shape
        actual = fourcc_str(cap.get(cv2.CAP_PROP_FOURCC))
    finally:
        cap.release()
    if shape is None:
        return f"{label} unavailable"
    t = summarize_reads(ends, durations, time.perf_counter() - start)
    size = f"{shape[1]}x{shape[0]}"
    return (
        f"{label} {size:<10} {actual:<6} {t.fps:>6.2f} {t.p50_ms:>7.2f} "
        f"{t.p99_ms:>7.2f} {t.read_ms:>8.2f}"
    )


def _run_table(index: int) -> int:
    print("backend fourcc size       actual    fps  p50 ms  p99 ms  read ms")
    for backend in PROBE_BACKENDS:
        for fourcc in PROBE_FOURCCS:
            print(_probe_one(index, backend, fourcc), flush=True)
    return 0


def _measure_loopback(src: DeviceSource, monitor: Monitor) -> tuple[list[int], int]:
    window = "ap probe loopback"
    ages: list[int] = []
    attempts = 0
    measure_from = time.monotonic() + LOOPBACK_WARMUP_SECONDS
    done = threading.Event()

    def consume() -> None:
        nonlocal attempts
        seq = 0
        while not done.is_set():
            frame = src.wait_next(seq, 0.1)
            if frame is None:
                continue
            seq = frame.seq
            if frame.ts < measure_from:
                continue
            attempts += 1
            stamp = decode_stamp(frame.bgr)
            if stamp is not None:
                # Same clock as the drawn stamp: both come from time.monotonic() in this process.
                ages.append(stamp_age_ms(int(frame.ts * 1000), stamp))

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.moveWindow(window, monitor.x, monitor.y)
    cv2.setWindowProperty(window, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    consumer = threading.Thread(target=consume, name="loopback-decode", daemon=True)
    consumer.start()
    try:
        while time.monotonic() < measure_from + LOOPBACK_SECONDS:
            stamp_ms = int(time.monotonic() * 1000)
            cv2.imshow(window, encode_stamp(stamp_ms, (monitor.width, monitor.height)))
            cv2.waitKey(1)
    finally:
        done.set()
        consumer.join()
        cv2.destroyWindow(window)
    return ages, attempts


def _run_loopback(index: int, monitor_index: int | None) -> int:
    try:
        monitors = _list_monitors()
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(format_monitors(monitors))
    if monitor_index is None:
        print(
            "error: pass --monitor N for the output wired to the capture card",
            file=sys.stderr,
        )
        return 2
    if not 0 <= monitor_index < len(monitors):
        print(
            f"error: no monitor {monitor_index}; choose one listed above",
            file=sys.stderr,
        )
        return 2
    monitor = monitors[monitor_index]
    src = DeviceSource(index)
    try:
        src.start()
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with src:
        ages, attempts = _measure_loopback(src, monitor)
    summary = summarize_loopback(ages, attempts)
    print(
        f"monitor {monitor.index} refresh {monitor.refresh_hz} Hz; "
        f"decoded {len(ages)}/{attempts} frames ({summary.decode_rate:.1%})"
    )
    if not summary.valid:
        print(
            f"measurement invalid: decode rate below {MIN_DECODE_RATE:.0%}",
            file=sys.stderr,
        )
        return 1
    print(
        f"frame age p50 {summary.p50_ms:.1f} ms, p99 {summary.p99_ms:.1f} ms "
        "(upper bound: includes draw-to-output delay)"
    )
    return 0


def _handle(args: argparse.Namespace) -> int:
    if args.loopback:
        return _run_loopback(args.index, args.monitor)
    if args.monitor is not None:
        print("error: --monitor only applies with --loopback", file=sys.stderr)
        return 2
    return _run_table(args.index)


def register(sub) -> None:
    p = sub.add_parser(
        "probe",
        help="measure what a capture device delivers",
        description=(
            "Without --loopback: open every backend x pixel format for "
            f"{PROBE_SECONDS:.0f} s and print size, format, fps and frame timing. "
            "With --loopback: show a time-stamp pattern full screen on --monitor N "
            "and measure how old captured frames are."
        ),
    )
    p.add_argument(
        "--index", type=int, default=0, help="capture device index (default 0)"
    )
    p.add_argument("--loopback", action="store_true", help="measure captured-frame age")
    p.add_argument(
        "--monitor", type=int, help="monitor wired to the capture card (loopback)"
    )
    p.set_defaults(func=_handle)
