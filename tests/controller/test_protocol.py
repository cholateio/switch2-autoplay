import random

import pytest

from autoplay.core.controller.protocol import (
    MAX_PAYLOAD,
    FrameParser,
    Msg,
    MsgType,
    crc8,
    encode,
    pack_set_state,
    unpack_set_state,
)
from autoplay.core.controller.types import Button, Hat, PadState

PING_1 = bytes.fromhex("a5 00 01 01 12")
A_FOR_80_MS = bytes.fromhex("a5 09 02 02 04 00 08 80 80 80 80 50 00 11")


def frames(*msgs: Msg) -> bytes:
    return b"".join(encode(m) for m in msgs)


def test_crc8_check_value():
    assert crc8(b"123456789") == 0xF4


def test_encode_ping():
    assert encode(Msg(MsgType.PING, 1)) == PING_1


def test_encode_set_state():
    payload = pack_set_state(PadState(buttons=Button.A), 80)
    assert encode(Msg(MsgType.SET_STATE, 2, payload)) == A_FOR_80_MS


def test_encode_ack_done_pong():
    assert encode(Msg(MsgType.ACK, 2)).hex(" ") == "a5 00 81 02 ad"
    assert encode(Msg(MsgType.DONE, 2)).hex(" ") == "a5 00 82 02 92"
    assert (
        encode(Msg(MsgType.PONG, 1, bytes([1, 8]))).hex(" ") == "a5 02 84 01 01 08 eb"
    )


def test_encode_rejects_oversized_payload_and_bad_fields():
    with pytest.raises(ValueError):
        encode(Msg(MsgType.PING, 1, bytes(MAX_PAYLOAD + 1)))
    with pytest.raises(ValueError):
        encode(Msg(MsgType.PING, 256))
    with pytest.raises(ValueError):
        encode(Msg(256, 0))


def test_parser_decodes_a_frame():
    parser = FrameParser()
    assert parser.feed(A_FOR_80_MS) == [
        Msg(MsgType.SET_STATE, 2, pack_set_state(PadState(buttons=Button.A), 80))
    ]
    assert parser.crc_errors == 0


def test_parser_handles_split_and_concatenated_frames():
    msgs = [
        Msg(MsgType.PING, 1),
        Msg(MsgType.SET_STATE, 2, bytes(range(9))),
        Msg(MsgType.HELLO, 3),
    ]
    stream = frames(*msgs)

    assert FrameParser().feed(stream) == msgs

    parser = FrameParser()
    out = []
    for byte in stream:
        out += parser.feed(bytes([byte]))
    assert out == msgs
    assert parser.crc_errors == 0


def test_parser_resyncs_after_garbage_and_bad_crc():
    bad_crc = bytearray(PING_1)
    bad_crc[-1] ^= 0xFF
    parser = FrameParser()
    out = parser.feed(b"\x00\xff\xa5ESP-ROM:esp32s3\r\n" + bytes(bad_crc) + PING_1)
    assert out == [Msg(MsgType.PING, 1)]
    assert parser.crc_errors >= 1


def test_parser_rejects_len_over_16():
    parser = FrameParser()
    # A frame that would be valid if a 17-byte payload were allowed.
    body = bytes([17, MsgType.PING, 1]) + bytes(17)
    oversized = b"\xa5" + body + bytes([crc8(body)])
    assert parser.feed(oversized) == []
    assert parser.feed(PING_1) == [Msg(MsgType.PING, 1)]
    assert parser.crc_errors >= 1


def test_corrupted_len_does_not_swallow_next_frame():
    corrupted = bytearray(PING_1)
    corrupted[1] = 9
    parser = FrameParser()
    out = parser.feed(
        bytes(corrupted) + encode(Msg(MsgType.PING, 2)) + encode(Msg(MsgType.PING, 3))
    )
    assert out == [Msg(MsgType.PING, 2), Msg(MsgType.PING, 3)]


def test_truncated_frame_then_valid_frame():
    parser = FrameParser()
    assert parser.feed(A_FOR_80_MS[:7]) == []
    later = Msg(MsgType.SET_STATE, 3, pack_set_state(PadState(hat=Hat.LEFT), 16))
    assert parser.feed(encode(later)) == [later]


def test_inserted_and_deleted_byte_recovery():
    first, middle, last = (
        Msg(MsgType.SET_STATE, seq, bytes(range(seq, seq + 9))) for seq in (1, 2, 3)
    )
    raw = encode(middle)

    inserted = raw[:5] + b"\x77" + raw[5:]
    assert FrameParser().feed(encode(first) + inserted + encode(last)) == [first, last]

    deleted = raw[:5] + raw[6:]
    assert FrameParser().feed(encode(first) + deleted + encode(last)) == [first, last]


def test_parser_never_holds_more_than_one_frame_of_bytes():
    parser = FrameParser()
    # Only frame starts that all claim the longest payload: nothing can be decided
    # until a whole frame's worth has arrived, and nothing beyond that is kept.
    parser.feed(bytes([0xA5, MAX_PAYLOAD]) * 100)
    assert parser.pending <= MAX_PAYLOAD + 5


def test_random_stream_with_garbage_recovers_every_intact_frame():
    rng = random.Random(1234)
    msgs = [
        Msg(
            rng.choice(list(MsgType)),
            rng.randrange(256),
            rng.randbytes(rng.randrange(MAX_PAYLOAD + 1)),
        )
        for _ in range(200)
    ]
    stream = bytearray()
    for msg in msgs:
        # Garbage without the magic byte cannot start a false frame.
        stream += bytes(b for b in rng.randbytes(rng.randrange(4)) if b != 0xA5)
        stream += encode(msg)
    assert FrameParser().feed(bytes(stream)) == msgs


def test_set_state_roundtrip():
    state = PadState(
        buttons=Button.ZL | Button.HOME, hat=Hat.DOWN_LEFT, lx=0, ly=255, rx=1, ry=200
    )
    payload = pack_set_state(state, 65535)
    assert len(payload) == 9
    assert unpack_set_state(payload) == (state, 65535)


def test_pack_set_state_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        pack_set_state(PadState(), -1)
    with pytest.raises(ValueError):
        pack_set_state(PadState(), 65536)
    with pytest.raises(ValueError):
        pack_set_state(PadState(lx=256), 0)


def test_unpack_set_state_rejects_bad_payloads():
    good = pack_set_state(PadState(), 0)
    with pytest.raises(ValueError):
        unpack_set_state(good[:-1])
    with pytest.raises(ValueError):
        unpack_set_state(good[:2] + b"\x09" + good[3:])
