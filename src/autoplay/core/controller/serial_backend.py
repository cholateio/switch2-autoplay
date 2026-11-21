import threading
import time
from collections.abc import Callable
from typing import Protocol

from autoplay.core import clock
from autoplay.core.actions.timing import Timing
from autoplay.core.controller.base import Controller, ControllerError, ControllerTimeout
from autoplay.core.controller.protocol import (
    FLAG_USB_MOUNTED,
    DeviceInfo,
    FrameParser,
    Msg,
    MsgType,
    encode,
    pack_set_state,
    unpack_info,
    unpack_pong,
)
from autoplay.core.controller.types import PadState

BAUD = 921600
PROTO_VERSION = 1
_NAK_DROPPED = 4
_READ_POLL_S = 0.05
_JOIN_TIMEOUT_S = 1.0
_INBOX_LIMIT = 64
# docs/protocol.md, "Duplicates and lost replies": SET_STATE seq values and all
# other seq values come from separate ranges.
_SEQ_SPAN = 128
_OTHER_SEQ_BASE = 128


class Transport(Protocol):
    """A byte pipe to the device."""

    def write(self, data: bytes) -> None: ...

    def read(self) -> bytes:
        """Return the bytes that have arrived, or `b""` after a short wait."""

    def reset_input(self) -> None: ...

    def close(self) -> None: ...


class SerialTransport:
    """The board's serial port."""

    def __init__(self, port: str) -> None:
        import serial

        self._port = serial.Serial()
        self._port.port = port
        self._port.baudrate = BAUD
        self._port.timeout = _READ_POLL_S
        # Raising DTR/RTS on open would reset the board through its auto-program circuit.
        self._port.dtr = False
        self._port.rts = False
        try:
            self._port.open()
        except OSError as exc:
            raise ControllerError(f"cannot open serial port {port}: {exc}") from exc

    def write(self, data: bytes) -> None:
        self._port.write(data)

    def read(self) -> bytes:
        # Asking for one byte returns as soon as it arrives; asking for more
        # waits out the timeout, which Windows rounds up to a 15.6 ms tick.
        data = self._port.read(1)
        if data:
            data += self._port.read(self._port.in_waiting)
        return data

    def reset_input(self) -> None:
        self._port.reset_input_buffer()

    def close(self) -> None:
        self._port.close()


class SerialController(Controller):
    """The ESP32-S3 firmware over its serial port (docs/protocol.md).

    A background thread sends a heartbeat while the lease is valid. Every call
    renews the lease for `lease_ms`, and a call that blocks keeps renewing it;
    when calls stop, the heartbeat stops and the firmware's watchdog releases
    the gamepad.

    After a `ControllerTimeout` what the device shows is unknown: call
    `release_all()` before going on."""

    def __init__(
        self,
        port: str,
        *,
        timing: Timing | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(timing)
        self._transport: Transport = transport or SerialTransport(port)
        self._port_name = port
        self._parser = FrameParser()
        self._cond = threading.Condition()
        self._inbox: list[Msg] = []
        self._failure: Exception | None = None
        # One command at a time, each waiting for its immediate reply.
        self._tx_lock = threading.Lock()
        self._seq_lock = threading.Lock()
        self._state_count = 0
        self._other_count = 0
        self._flags = 0
        self._lease_until = 0.0
        # `clock.now()` of the last frame written; the firmware watchdog counts from it.
        self.last_tx = 0.0
        self._closed = False
        self._stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, name="pad-reader", daemon=True
        )
        self._heart = threading.Thread(
            target=self._heart_loop, name="pad-heartbeat", daemon=True
        )
        self._reader.start()
        try:
            self._info = self._hello()
        except BaseException:
            self._shutdown()
            raise
        self._renew()
        self._heart.start()

    def info(self) -> DeviceInfo:
        """The device's INFO as reported when the port was opened."""
        return self._info

    def heartbeat(self) -> None:
        self._check_open()
        self._renew()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.release_all()
        except ControllerError:
            pass
        self._closed = True
        self._shutdown()

    # Controller backend ---------------------------------------------------

    def _apply(self, state: PadState, hold_ms: int) -> None:
        self._check_open()
        self._renew()
        msg = Msg(
            MsgType.SET_STATE,
            self._next_seq(state_command=True),
            pack_set_state(state, hold_ms),
        )
        if self._submit(msg).type != MsgType.DONE:
            self._await_done(msg, hold_ms)

    def _release_all(self) -> None:
        self._check_open()
        self._renew()
        self._request(Msg(MsgType.RELEASE_ALL, self._next_seq()), (MsgType.ACK,))

    # Link -----------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise ControllerError(f"controller on {self._port_name} is closed")

    def _renew(self) -> None:
        self._lease_until = clock.now() + self.timing.lease_ms / 1000

    def _next_seq(self, *, state_command: bool = False) -> int:
        with self._seq_lock:
            if state_command:
                seq = self._state_count % _SEQ_SPAN
                self._state_count += 1
            else:
                seq = _OTHER_SEQ_BASE + self._other_count % _SEQ_SPAN
                self._other_count += 1
        # A reply left over from the last use of this seq must not answer the new command.
        with self._cond:
            self._inbox = [m for m in self._inbox if m.seq != seq]
        return seq

    def _take(self, wanted: Callable[[Msg], bool], timeout_s: float) -> Msg | None:
        deadline = clock.now() + timeout_s
        with self._cond:
            while True:
                if self._failure is not None:
                    raise ControllerError(
                        f"serial port {self._port_name} failed: {self._failure}"
                    ) from self._failure
                for i, msg in enumerate(self._inbox):
                    if wanted(msg):
                        return self._inbox.pop(i)
                remaining = deadline - clock.now()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def _request(
        self, msg: Msg, replies: tuple[int, ...], *, attempts: int | None = None
    ) -> Msg:
        frame = encode(msg)
        attempts = 1 + self.timing.ack_retries if attempts is None else attempts
        with self._tx_lock:
            for _ in range(attempts):
                try:
                    self._transport.write(frame)
                except Exception as exc:
                    raise ControllerError(
                        f"serial port {self._port_name} failed: {exc}"
                    ) from exc
                self.last_tx = clock.now()
                reply = self._take(
                    lambda m: m.seq == msg.seq and m.type in replies,
                    self.timing.ack_timeout_ms / 1000,
                )
                if reply is not None:
                    return reply
        name = MsgType(msg.type).name
        raise ControllerTimeout(
            f"no reply to {name} from {self._port_name} after {attempts} attempts"
        )

    def _hello(self) -> DeviceInfo:
        self._transport.reset_input()
        deadline = clock.now() + self.timing.hello_window_ms / 1000
        while True:
            try:
                reply = self._request(
                    Msg(MsgType.HELLO, self._next_seq()), (MsgType.INFO,), attempts=1
                )
            except ControllerTimeout:
                if clock.now() >= deadline:
                    raise ControllerTimeout(
                        f"no answer to HELLO from {self._port_name} within "
                        f"{self.timing.hello_window_ms} ms; is the firmware running?"
                    ) from None
                continue
            info = unpack_info(reply.payload)
            if info.proto_ver != PROTO_VERSION:
                raise ControllerError(
                    f"device on {self._port_name} speaks protocol version {info.proto_ver}; "
                    f"this program speaks {PROTO_VERSION}"
                )
            self._flags = info.flags
            return info

    def _submit(self, msg: Msg) -> Msg:
        """Get `msg` accepted; return the ACK, or the DONE of a state that has
        already finished."""
        give_up = clock.now() + self.timing.done_slack_ms / 1000
        while True:
            reply = self._request(
                msg, (MsgType.ACK, MsgType.DONE, MsgType.BUSY, MsgType.NAK)
            )
            if reply.type in (MsgType.ACK, MsgType.DONE):
                return reply
            if reply.type == MsgType.NAK:
                self._raise_nak(reply)
            if clock.now() >= give_up:
                raise ControllerTimeout(
                    f"device queue on {self._port_name} stayed full"
                )
            time.sleep(self.timing.min_state_ms / 1000)

    def _await_done(self, msg: Msg, hold_ms: int) -> None:
        window_s = (hold_ms + self.timing.done_slack_ms) / 1000
        for query in range(self.timing.done_queries + 1):
            deadline = clock.now() + window_s
            while (remaining := deadline - clock.now()) > 0:
                # A call that blocks is still the control loop making progress.
                self._renew()
                if self._take(
                    lambda m: m.type == MsgType.DONE and m.seq == msg.seq,
                    min(remaining, self.timing.heartbeat_ms / 1000),
                ):
                    return
            if query == self.timing.done_queries:
                break
            # Same frame, same seq: the device answers with what became of it
            # and never shows the state a second time.
            reply = self._request(
                msg, (MsgType.ACK, MsgType.DONE, MsgType.BUSY, MsgType.NAK)
            )
            if reply.type == MsgType.DONE:
                return
            if reply.type == MsgType.NAK:
                self._raise_nak(reply)
            window_s = self.timing.done_slack_ms / 1000
        usb = (
            ""
            if self._flags & FLAG_USB_MOUNTED
            else "; the console is not polling the gamepad over USB"
        )
        raise ControllerTimeout(f"state was accepted but never reported done{usb}")

    def _raise_nak(self, reply: Msg) -> None:
        reason = reply.payload[0] if reply.payload else 0
        if reason == _NAK_DROPPED:
            raise ControllerTimeout(
                "state was dropped by the device (watchdog or release) before it finished"
            )
        raise ControllerError(f"device refused the state: NAK reason {reason}")

    # Threads --------------------------------------------------------------

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._transport.read()
            except Exception as exc:  # noqa: BLE001 - thread boundary: any failure is handed to the callers
                with self._cond:
                    self._failure = exc
                    self._cond.notify_all()
                return
            msgs = self._parser.feed(data)
            if msgs:
                with self._cond:
                    self._inbox = (self._inbox + msgs)[-_INBOX_LIMIT:]
                    self._cond.notify_all()

    def _heart_loop(self) -> None:
        while not self._stop.wait(self.timing.heartbeat_ms / 1000):
            if clock.now() >= self._lease_until:
                continue
            try:
                reply = self._request(
                    Msg(MsgType.PING, self._next_seq()), (MsgType.PONG,), attempts=1
                )
                self._flags = unpack_pong(reply.payload)[0]
            except ControllerError:
                # The next command on the main thread reports a dead link.
                pass

    def _shutdown(self) -> None:
        self._stop.set()
        for thread in (self._heart, self._reader):
            if thread.is_alive():
                thread.join(_JOIN_TIMEOUT_S)
        self._transport.close()
