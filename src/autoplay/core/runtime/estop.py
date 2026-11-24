import sys
import threading
from collections.abc import Callable

VK_F12 = 0x7B
_POLL_S = 0.02
_JOIN_TIMEOUT_S = 1.0


class EmergencyStop(RuntimeError):
    """The emergency stop was tripped; the run must end."""


def _f12_down() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_F12) & 0x8000)


class EStop:
    """The emergency stop. Tripping it is one-way: `tripped` stays set, and
    each callback runs exactly once.

    With `hotkey` on Windows, F12 trips it whichever window has the focus.
    Elsewhere there is no key: call `trip()`."""

    def __init__(
        self,
        on_trip: Callable[[], None] | None = None,
        hotkey: bool = True,
        *,
        key_down: Callable[[], bool] | None = None,
    ) -> None:
        self.tripped = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = [on_trip] if on_trip else []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if key_down is None and hotkey and sys.platform == "win32":
            key_down = _f12_down
        if key_down is not None:
            # The key state is polled: the system's hot-key registration
            # reserves F12 for debuggers and may refuse or ignore it.
            self._thread = threading.Thread(
                target=self._watch, args=(key_down,), name="estop-key", daemon=True
            )
            self._thread.start()

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Run `callback` when the stop trips, or now if it already has."""
        with self._lock:
            if not self.tripped.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def trip(self) -> None:
        with self._lock:
            if self.tripped.is_set():
                return
            # Set first: anything that checks the stop refuses from here on,
            # while the callbacks are still releasing.
            self.tripped.set()
            callbacks = list(self._callbacks)
        for callback in callbacks:
            callback()

    def close(self) -> None:
        """Stop watching the key. The stop itself stays as it is."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(_JOIN_TIMEOUT_S)

    def _watch(self, key_down: Callable[[], bool]) -> None:
        while not self._stop.wait(_POLL_S):
            if key_down():
                self.trip()
                return
