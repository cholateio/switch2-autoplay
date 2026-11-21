"""Check the serial link to the firmware: scripts/win python scripts/hello.py COM4

Sends HELLO, prints the INFO fields, exchanges PINGs and reports how long each
took and whether the device saw any damaged frame."""

import argparse
import statistics
import sys
import time

import serial

from autoplay.core.controller.protocol import (
    FrameParser,
    Msg,
    MsgType,
    encode,
    unpack_info,
    unpack_pong,
)

BAUD = 921600
HELLO_WINDOW_S = 2.0
REPLY_TIMEOUT_S = 0.05


def open_port(name: str) -> serial.Serial:
    port = serial.Serial()
    port.port = name
    port.baudrate = BAUD
    port.timeout = 0.005
    # Raising DTR/RTS on open would reset the board through its auto-program circuit.
    port.dtr = False
    port.rts = False
    port.open()
    return port


def request(
    port: serial.Serial, parser: FrameParser, msg: Msg, want: MsgType, timeout: float
) -> Msg | None:
    port.write(encode(msg))
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        # Asking for one byte returns as soon as it arrives; asking for more
        # waits out the timeout, which Windows rounds up to a 15.6 ms tick.
        data = port.read(1)
        if data:
            data += port.read(port.in_waiting)
        for reply in parser.feed(data):
            if reply.type == want and reply.seq == msg.seq:
                return reply
    return None


def hello(port: serial.Serial, parser: FrameParser, seq: int):
    deadline = time.perf_counter() + HELLO_WINDOW_S
    while time.perf_counter() < deadline:
        reply = request(
            port, parser, Msg(MsgType.HELLO, seq), MsgType.INFO, REPLY_TIMEOUT_S
        )
        if reply is not None:
            return unpack_info(reply.payload)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port")
    ap.add_argument("--pings", type=int, default=100)
    args = ap.parse_args()

    port = open_port(args.port)
    try:
        port.reset_input_buffer()
        parser = FrameParser()
        info = hello(port, parser, 1)
        if info is None:
            print(f"no INFO from {args.port} within {HELLO_WINDOW_S} s")
            return 1
        print(
            f"proto_ver={info.proto_ver} backend={info.backend} flags={info.flags:#04x} "
            f"queue_free={info.queue_free} uptime_ms={info.uptime_ms} "
            f"rx_crc_errors={info.rx_crc_errors} wd_trips={info.wd_trips}"
        )

        times, lost = [], 0
        for i in range(args.pings):
            seq = (2 + i) % 256
            start = time.perf_counter()
            reply = request(
                port, parser, Msg(MsgType.PING, seq), MsgType.PONG, REPLY_TIMEOUT_S
            )
            if reply is None:
                lost += 1
                continue
            times.append((time.perf_counter() - start) * 1000)
            unpack_pong(reply.payload)
        if times:
            print(
                f"pings={args.pings} lost={lost} round trip ms: "
                f"p50 {statistics.median(times):.2f} max {max(times):.2f}"
            )
        else:
            print(f"pings={args.pings} lost={lost}")

        after = hello(port, parser, 200)
        if after is None:
            print("no INFO after the pings")
            return 1
        print(
            f"after pings: rx_crc_errors={after.rx_crc_errors} wd_trips={after.wd_trips} host crc_errors={parser.crc_errors}"
        )
        return 0 if lost == 0 and after.rx_crc_errors == info.rx_crc_errors else 1
    finally:
        port.close()


if __name__ == "__main__":
    sys.exit(main())
