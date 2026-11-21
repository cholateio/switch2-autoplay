"""Codec for the serial protocol in docs/protocol.md. The firmware's copy is
firmware/components/padcore/proto.c; both must decode any byte stream alike."""

import enum
import struct
from dataclasses import dataclass

from autoplay.core.controller.types import Button, Hat, PadState

MAGIC = 0xA5
MAX_PAYLOAD = 16
# magic, len, type, seq before the payload and the CRC after it.
_HEADER = 4
_OVERHEAD = _HEADER + 1
_SET_STATE = struct.Struct("<HBBBBBH")


class MsgType(enum.IntEnum):
    PING = 0x01
    SET_STATE = 0x02
    RELEASE_ALL = 0x03
    HELLO = 0x04
    ACK = 0x81
    DONE = 0x82
    BUSY = 0x83
    PONG = 0x84
    INFO = 0x85
    NAK = 0x86


@dataclass(frozen=True)
class Msg:
    type: int
    seq: int
    payload: bytes = b""


def _crc8_table() -> bytes:
    table = bytearray(256)
    for value in range(256):
        crc = value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table[value] = crc
    return bytes(table)


_CRC8 = _crc8_table()


def crc8(data: bytes) -> int:
    """CRC-8 with polynomial 0x07, initial value 0, no reflection."""
    crc = 0
    for byte in data:
        crc = _CRC8[crc ^ byte]
    return crc


def encode(msg: Msg) -> bytes:
    """Return the frame for `msg`; raise ValueError if it cannot be framed."""
    if len(msg.payload) > MAX_PAYLOAD:
        raise ValueError(
            f"payload is {len(msg.payload)} bytes; the limit is {MAX_PAYLOAD}"
        )
    if not 0 <= msg.type <= 0xFF or not 0 <= msg.seq <= 0xFF:
        raise ValueError(
            f"type and seq must fit in one byte, got type={msg.type} seq={msg.seq}"
        )
    body = bytes([len(msg.payload), msg.type, msg.seq]) + msg.payload
    return bytes([MAGIC]) + body + bytes([crc8(body)])


def pack_set_state(state: PadState, hold_ms: int) -> bytes:
    """Return the 9-byte SET_STATE payload; raise ValueError for a value that
    does not fit its field."""
    try:
        return _SET_STATE.pack(
            int(state.buttons),
            int(state.hat),
            state.lx,
            state.ly,
            state.rx,
            state.ry,
            hold_ms,
        )
    except struct.error as exc:
        raise ValueError(f"cannot pack {state} with hold_ms={hold_ms}: {exc}") from exc


def unpack_set_state(payload: bytes) -> tuple[PadState, int]:
    """Return `(state, hold_ms)` from a SET_STATE payload; raise ValueError if
    its length or hat value is invalid."""
    if len(payload) != _SET_STATE.size:
        raise ValueError(
            f"SET_STATE payload is {len(payload)} bytes; expected {_SET_STATE.size}"
        )
    buttons, hat, lx, ly, rx, ry, hold_ms = _SET_STATE.unpack(payload)
    return PadState(Button(buttons), Hat(hat), lx, ly, rx, ry), hold_ms


class FrameParser:
    """Incremental frame decoder that resynchronises after corruption.

    `crc_errors` counts the frame starts it discarded: a length above the
    limit or a CRC mismatch."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0

    @property
    def pending(self) -> int:
        """Bytes received that do not yet form a decided frame."""
        return len(self._buf)

    def feed(self, data: bytes) -> list[Msg]:
        """Add received bytes and return the frames completed by them."""
        self._buf += data
        out = []
        while True:
            start = self._buf.find(MAGIC)
            if start < 0:
                self._buf.clear()
                break
            del self._buf[:start]
            if len(self._buf) < 2:
                break
            length = self._buf[1]
            if length <= MAX_PAYLOAD:
                total = _OVERHEAD + length
                if len(self._buf) < total:
                    break
                if crc8(self._buf[1 : total - 1]) == self._buf[total - 1]:
                    out.append(
                        Msg(
                            self._buf[2],
                            self._buf[3],
                            bytes(self._buf[_HEADER : total - 1]),
                        )
                    )
                    del self._buf[:total]
                    continue
            # Not a frame: drop only this magic byte, so a real frame that
            # starts inside the bytes it claimed is still found.
            self.crc_errors += 1
            del self._buf[0]
        return out
