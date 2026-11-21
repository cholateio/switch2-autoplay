"""The C codec in firmware/components/padcore and the Python codec must agree
on every byte stream. Runs where a C compiler and make exist (WSL)."""

import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from autoplay.core.controller.protocol import MAX_PAYLOAD, FrameParser, Msg, encode

TEST_HOST = Path(__file__).resolve().parents[2] / "firmware" / "test_host"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32"
    or shutil.which("cc") is None
    or shutil.which("make") is None,
    reason="needs cc and make to build the firmware's host tools",
)


@pytest.fixture(scope="module")
def cli(tmp_path_factory) -> Path:
    build = tmp_path_factory.mktemp("proto_cli")
    subprocess.run(
        ["make", "-C", str(TEST_HOST), f"BUILD={build}", "cli"],
        check=True,
        capture_output=True,
        text=True,
    )
    return build / "proto_cli"


def random_msgs(rng: random.Random, count: int) -> list[Msg]:
    return [
        Msg(
            rng.randrange(256),
            rng.randrange(256),
            rng.randbytes(rng.randrange(MAX_PAYLOAD + 1)),
        )
        for _ in range(count)
    ]


def c_decode(cli: Path, stream: bytes) -> tuple[list[Msg], int]:
    out = subprocess.run(
        [str(cli)], input=stream, check=True, capture_output=True
    ).stdout.decode()
    *frames, tail = out.splitlines()
    msgs = []
    for line in frames:
        kind, seq, *payload = line.split(" ")
        msgs.append(
            Msg(int(kind), int(seq), bytes.fromhex(payload[0]) if payload else b"")
        )
    return msgs, int(tail.removeprefix("crc_errors="))


def test_c_decodes_what_python_encodes(cli):
    msgs = random_msgs(random.Random(20261121), 200)
    decoded, errors = c_decode(cli, b"".join(encode(m) for m in msgs))
    assert decoded == msgs
    assert errors == 0


def test_python_decodes_what_c_encodes(cli):
    msgs = random_msgs(random.Random(7), 200)
    stream = bytearray()
    for m in msgs:
        out = subprocess.run(
            [str(cli), "--encode", str(m.type), str(m.seq), m.payload.hex()],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        frame = bytes.fromhex(out.strip())
        assert frame == encode(m)
        stream += frame
    assert FrameParser().feed(bytes(stream)) == msgs


@pytest.mark.parametrize("seed", range(8))
def test_both_decode_a_damaged_stream_alike(cli, seed):
    rng = random.Random(seed)
    stream = bytearray()
    for m in random_msgs(rng, 200):
        frame = bytearray(encode(m))
        damage = rng.randrange(8)
        if damage == 0:
            frame[rng.randrange(len(frame))] ^= 1 << rng.randrange(8)
        elif damage == 1:
            del frame[rng.randrange(len(frame))]
        elif damage == 2:
            frame.insert(rng.randrange(len(frame) + 1), rng.randrange(256))
        elif damage == 3:
            del frame[rng.randrange(1, len(frame)) :]
        # Garbage that is rich in magic bytes, so false frame starts are common.
        stream += bytes(
            rng.choice((0xA5, rng.randrange(256))) for _ in range(rng.randrange(6))
        )
        stream += frame

    parser = FrameParser()
    expected = parser.feed(bytes(stream))
    decoded, errors = c_decode(cli, bytes(stream))

    assert len(expected) > 50
    assert decoded == expected
    assert errors == parser.crc_errors
