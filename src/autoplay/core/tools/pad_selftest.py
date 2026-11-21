"""`ap pad selftest`: drive the firmware over serial and read back, over USB,
the reports it really sends. Both of the board's ports are plugged into the PC."""

import dataclasses
import threading
import time
from collections.abc import Callable
from typing import Protocol

from autoplay.core import clock
from autoplay.core.actions.timing import Timing, load_timing
from autoplay.core.controller.base import ControllerError
from autoplay.core.controller.serial_backend import SerialController, Transport
from autoplay.core.controller.types import Button, Hat, PadState

# USB identity of each firmware backend id (INFO `backend`).
BACKEND_USB_IDS = {1: (0x0F0D, 0x0092)}
REPORT_LEN = 8
# docs/protocol.md, "Device guarantees".
WATCHDOG_MS = 300
WATCHDOG_TOLERANCE_MS = 60
# Report timestamps are taken on arrival at the PC, one poll apart.
MEASURE_TOLERANCE_MS = 1.5
_SETTLE_S = 0.06
_HAT_CASES = (
    (("DPAD_UP",), Hat.UP),
    (("DPAD_UP", "DPAD_RIGHT"), Hat.UP_RIGHT),
    (("DPAD_RIGHT",), Hat.RIGHT),
    (("DPAD_DOWN", "DPAD_RIGHT"), Hat.DOWN_RIGHT),
    (("DPAD_DOWN",), Hat.DOWN),
    (("DPAD_DOWN", "DPAD_LEFT"), Hat.DOWN_LEFT),
    (("DPAD_LEFT",), Hat.LEFT),
    (("DPAD_UP", "DPAD_LEFT"), Hat.UP_LEFT),
)

ReadReport = Callable[[int], bytes | None]


class HidHandle(Protocol):
    def read(self, timeout_ms: int) -> bytes | None: ...

    def close(self) -> None: ...


def decode_report(data: bytes) -> PadState:
    """Decode the 8-byte input report of the Pokken identity."""
    if len(data) != REPORT_LEN:
        raise ValueError(f"input report is {len(data)} bytes; expected {REPORT_LEN}")
    return PadState(
        Button(int.from_bytes(data[0:2], "little")),
        Hat(data[2] & 0x0F),
        data[3],
        data[4],
        data[5],
        data[6],
    )


class ReportLog:
    """Reads input reports on a thread and keeps each with its arrival time."""

    def __init__(self, hid: HidHandle) -> None:
        self._hid = hid
        self._lock = threading.Lock()
        self._rows: list[tuple[float, PadState]] = []
        self._stop = threading.Event()
        self.error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run, name="selftest-hid", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._hid.read(50)
                if data:
                    row = (clock.now(), decode_report(bytes(data)))
                    with self._lock:
                        self._rows.append(row)
            except Exception as exc:  # noqa: BLE001 - thread boundary: reported by the selftest
                self.error = exc
                return

    def since(self, t: float) -> list[tuple[float, PadState]]:
        with self._lock:
            return [row for row in self._rows if row[0] >= t]

    def latest(self) -> PadState | None:
        with self._lock:
            return self._rows[-1][1] if self._rows else None

    def close(self) -> None:
        self._stop.set()
        self._thread.join(1.0)
        self._hid.close()


def shown_ms(
    rows: list[tuple[float, PadState]], wanted: Callable[[PadState], bool]
) -> float | None:
    """How long the first unbroken run of reports matching `wanted` lasted:
    from its first report to the first report after it. None if no such run
    both started and ended within `rows`."""
    start = None
    for t, state in rows:
        if wanted(state):
            if start is None:
                start = t
        elif start is not None:
            return (t - start) * 1000
    return None


def first_time(
    rows: list[tuple[float, PadState]], wanted: Callable[[PadState], bool]
) -> float | None:
    return next((t for t, state in rows if wanted(state)), None)


class Selftest:
    def __init__(
        self,
        open_pad: Callable[[Timing], SerialController],
        open_hid: Callable[[int], HidHandle],
        out: Callable[[str], None] = print,
    ) -> None:
        self._open_pad = open_pad
        self._open_hid = open_hid
        self._out = out
        self.failures = 0

    def _result(self, ok: bool, label: str, detail: str = "") -> None:
        self.failures += not ok
        self._out(
            f"{'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}"
        )

    def run(self) -> int:
        """Run every check, print one PASS/FAIL line each and return the
        number of failures."""
        timing = load_timing()
        pad = self._open_pad(timing)
        try:
            log = ReportLog(self._open_hid(pad.info().backend))
        except BaseException:
            pad.close()
            raise
        try:
            self._buttons_and_hats(pad, log)
            self._minimum_hold(pad, log, timing)
            self._requested_hold(pad, log, timing)
        finally:
            pad.close()
        # A lease of 1 ms: the heartbeat stops as soon as a call returns.
        pad = self._open_pad(dataclasses.replace(timing, lease_ms=1))
        try:
            self._watchdog(pad, log)
        finally:
            pad.close()
            log.close()
        if log.error is not None:
            self._result(False, "reading input reports", str(log.error))
        return self.failures

    def _buttons_and_hats(self, pad: SerialController, log: ReportLog) -> None:
        cases = [((b.name,), PadState(buttons=b)) for b in Button]
        cases += [(names, PadState(hat=hat)) for names, hat in _HAT_CASES]
        for names, expected in cases:
            for name in names:
                pad.hold(name)
            time.sleep(_SETTLE_S)
            seen = log.latest()
            self._result(
                seen == expected,
                "+".join(names),
                "" if seen == expected else f"report shows {seen}",
            )
            pad.release_all()
            time.sleep(_SETTLE_S)

    def _minimum_hold(
        self, pad: SerialController, log: ReportLog, timing: Timing
    ) -> None:
        start = clock.now()
        pad.press("A", ms=0)
        time.sleep(_SETTLE_S)
        ms = shown_ms(log.since(start), lambda s: bool(s.buttons & Button.A))
        ok = ms is not None and ms >= timing.min_state_ms - MEASURE_TOLERANCE_MS
        self._result(
            ok,
            f"press A --ms 0 is shown at least {timing.min_state_ms} ms",
            self._ms(ms),
        )

    def _requested_hold(
        self, pad: SerialController, log: ReportLog, timing: Timing
    ) -> None:
        start = clock.now()
        pad.press("A")
        time.sleep(_SETTLE_S)
        ms = shown_ms(log.since(start), lambda s: bool(s.buttons & Button.A))
        ok = ms is not None and ms >= timing.press_ms - MEASURE_TOLERANCE_MS
        self._result(
            ok, f"press A is shown at least {timing.press_ms} ms", self._ms(ms)
        )

    def _watchdog(self, pad: SerialController, log: ReportLog) -> None:
        start = clock.now()
        pad.stick("L", 0, 1)
        silent_from = pad.last_tx
        time.sleep((WATCHDOG_MS + 2 * WATCHDOG_TOLERANCE_MS) / 1000)
        rows = log.since(start)
        deflected = first_time(rows, lambda s: s.ly == 0)
        centred = None
        if deflected is not None:
            centred = first_time(
                [r for r in rows if r[0] > deflected], lambda s: s == PadState()
            )
        ms = None if centred is None else (centred - silent_from) * 1000
        ok = ms is not None and abs(ms - WATCHDOG_MS) <= WATCHDOG_TOLERANCE_MS
        self._result(
            ok,
            f"stick released {WATCHDOG_MS} ms (+/- {WATCHDOG_TOLERANCE_MS}) after the last frame",
            self._ms(ms),
        )

    @staticmethod
    def _ms(ms: float | None) -> str:
        return "no such report sequence seen" if ms is None else f"measured {ms:.1f} ms"


def open_hid(backend: int) -> HidHandle:
    """Open the gamepad's USB side; raise ControllerError saying how to make
    it visible if it cannot be opened."""
    if backend not in BACKEND_USB_IDS:
        raise ControllerError(f"no USB identity known for firmware backend {backend}")
    vid, pid = BACKEND_USB_IDS[backend]
    try:
        import hid
    except ImportError as exc:
        raise ControllerError(
            "the hidapi package is missing; run with `scripts/win --group hw ap pad selftest`"
        ) from exc
    device = hid.device()
    try:
        device.open(vid, pid)
    except OSError as exc:
        raise ControllerError(
            f"cannot open USB gamepad {vid:04X}:{pid:04X}; plug the board's OTG port into this PC ({exc})"
        ) from exc
    return device


def run(
    port: str,
    *,
    transport: Transport | None = None,
    hid_opener: Callable[[int], HidHandle] = open_hid,
) -> int:
    """Run the selftest against `port` and return the process exit code."""
    test = Selftest(
        lambda timing: SerialController(port, timing=timing, transport=transport),
        hid_opener,
    )
    failures = test.run()
    print(
        f"selftest: {'all checks passed' if failures == 0 else f'{failures} check(s) FAILED'}"
    )
    return 0 if failures == 0 else 1
