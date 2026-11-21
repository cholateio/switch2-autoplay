"""An in-memory device that speaks docs/protocol.md, for SerialController
tests. It runs in real time and stands in for the serial transport."""

import queue
import threading
import time
from collections import OrderedDict

from autoplay.core import clock
from autoplay.core.controller.protocol import (
    FLAG_USB_MOUNTED,
    FLAG_WD_TRIPPED,
    FrameParser,
    Msg,
    MsgType,
    encode,
    unpack_set_state,
)
from autoplay.core.controller.types import PadState

DEPTH = 8
HISTORY = 16
MIN_HOLD_S = 0.016
WATCHDOG_S = 0.3
PENDING, DONE, DROPPED = "pending", "done", "dropped"


class FakeDevice:
    """Faults are switched on by the keyword arguments; counters and logs are
    public attributes the tests read."""

    def __init__(
        self,
        *,
        boot_garbage: bytes = b"",
        mute_s: float = 0.0,
        drop_acks: int = 0,
        drop_dones: int = 0,
        busy_first: int = 0,
        mounted: bool = True,
        proto_ver: int = 1,
        auto_deliver: bool = True,
    ) -> None:
        self.drop_acks = drop_acks
        self.drop_dones = drop_dones
        self.busy_first = busy_first
        self.mounted = mounted
        self.proto_ver = proto_ver
        # False: a state's hold clock starts only when take_report() is called,
        # as the firmware's does when the USB host takes a report.
        self.auto_deliver = auto_deliver
        self.deaf = False

        self.applied: list[tuple[PadState, int]] = []
        self.seqs: dict[int, list[int]] = {}
        self.state = PadState()
        self.wd_trips = 0
        self.pings = 0
        self.writes = 0
        self.written_types: list[int | None] = []
        self.releases = 0
        self.closed = False

        self._lock = threading.Lock()
        self._parser = FrameParser()
        self._out: queue.Queue[bytes] = queue.Queue()
        self._mute_until = clock.now() + mute_s
        self._queue: list[dict] = []
        self._seen: OrderedDict[int, str] = OrderedDict()
        self._wd_armed = False
        self._wd_fed = 0.0
        self._wd_flag = False
        if boot_garbage:
            self._out.put(boot_garbage)
        self._thread = threading.Thread(
            target=self._run, name="fake-device", daemon=True
        )
        self._thread.start()

    # Transport side -------------------------------------------------------

    def write(self, data: bytes) -> None:
        with self._lock:
            if self.closed:
                raise OSError("port is closed")
            self.writes += 1
            # Frame types as written, including those a deaf device never parses.
            self.written_types.append(data[2] if len(data) > 2 else None)
            if self.deaf or clock.now() < self._mute_until:
                return
            for msg in self._parser.feed(data):
                self._handle(msg)

    def read(self) -> bytes:
        try:
            return self._out.get(timeout=0.01)
        except queue.Empty:
            return b""

    def reset_input(self) -> None:
        pass

    def close(self) -> None:
        # Closing the port does not switch the device off: it lives on until
        # its watchdog has had the chance to notice the silence.
        self.closed = True

    # Test controls --------------------------------------------------------

    def trip_watchdog(self) -> None:
        with self._lock:
            self._trip()

    def take_report(self) -> PadState:
        """The USB host polls: return the state shown and, like the firmware,
        start the hold clock of a state taken for the first time."""
        with self._lock:
            if (
                self._queue
                and self._queue[0]["shown"]
                and self._queue[0]["start"] is None
            ):
                self._queue[0]["start"] = clock.now()
            return self.state

    # Device side ----------------------------------------------------------

    def _send(self, kind: int, seq: int, payload: bytes = b"") -> None:
        self._out.put(encode(Msg(kind, seq, payload)))

    def _flags(self, clear: bool) -> int:
        flags = (FLAG_USB_MOUNTED if self.mounted else 0) | (
            FLAG_WD_TRIPPED if self._wd_flag else 0
        )
        if clear:
            self._wd_flag = False
        return flags

    def _handle(self, msg: Msg) -> None:
        self._wd_armed, self._wd_fed = True, clock.now()
        self.seqs.setdefault(msg.type, []).append(msg.seq)
        if msg.type == MsgType.PING:
            self.pings += 1
            self._send(
                MsgType.PONG,
                msg.seq,
                bytes([self._flags(True), DEPTH - len(self._queue)]),
            )
        elif msg.type == MsgType.HELLO:
            self._drop_all()
            self._seen.clear()
            info = (
                bytes([self.proto_ver, 1, self._flags(False), DEPTH])
                + bytes(4)
                + bytes(2)
            )
            self._send(
                MsgType.INFO,
                msg.seq,
                info + min(self.wd_trips, 0xFFFF).to_bytes(2, "little"),
            )
        elif msg.type == MsgType.RELEASE_ALL:
            self.releases += 1
            self._drop_all()
            self._send(MsgType.ACK, msg.seq)
        elif msg.type == MsgType.SET_STATE:
            self._set_state(msg)
        else:
            self._send(MsgType.NAK, msg.seq, b"\x02")

    def _set_state(self, msg: Msg) -> None:
        status = self._seen.get(msg.seq)
        if status == DONE:
            self._send(MsgType.DONE, msg.seq)
        elif status == DROPPED:
            self._send(MsgType.NAK, msg.seq, b"\x04")
        elif status is None and (self.busy_first > 0 or len(self._queue) == DEPTH):
            self.busy_first = max(0, self.busy_first - 1)
            self._send(MsgType.BUSY, msg.seq)
        else:
            if status is None:
                state, hold_ms = unpack_set_state(msg.payload)
                self.applied.append((state, hold_ms))
                self._queue.append(
                    {
                        "seq": msg.seq,
                        "state": state,
                        "hold_s": hold_ms / 1000,
                        "shown": False,
                        "start": None,
                    }
                )
                self._seen[msg.seq] = PENDING
                while len(self._seen) > HISTORY:
                    self._seen.popitem(last=False)
            if self.drop_acks > 0:
                self.drop_acks -= 1
            else:
                self._send(MsgType.ACK, msg.seq)

    def _drop_all(self) -> None:
        for entry in self._queue:
            self._seen[entry["seq"]] = DROPPED
        self._queue.clear()
        self.state = PadState()

    def _trip(self) -> None:
        self._drop_all()
        self._wd_armed = False
        self._wd_flag = True
        self.wd_trips += 1

    def _tick(self) -> None:
        now = clock.now()
        if self._wd_armed and now - self._wd_fed >= WATCHDOG_S:
            self._trip()
        if not self._queue:
            return
        entry = self._queue[0]
        if entry["start"] is None:
            self.state = entry["state"]
            entry["shown"] = True
            # A console that is not polling never takes the report.
            if self.mounted and self.auto_deliver:
                entry["start"] = now
            return
        if now - entry["start"] >= max(entry["hold_s"], MIN_HOLD_S):
            self._queue.pop(0)
            self._seen[entry["seq"]] = DONE
            if self.drop_dones > 0:
                self.drop_dones -= 1
            else:
                self._send(MsgType.DONE, entry["seq"])

    def _run(self) -> None:
        while not (self.closed and not self._wd_armed):
            time.sleep(0.001)
            with self._lock:
                self._tick()
