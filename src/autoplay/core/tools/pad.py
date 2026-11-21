import argparse
import sys
import time
from collections.abc import Callable

from autoplay.core.controller.base import BUTTON_NAMES, Controller, ControllerError
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.serial_backend import SerialController

DUMMY_PORT = "dummy"


def open_controller(port: str) -> Controller:
    """Open the controller on `port`; the port name `dummy` gives a
    DummyController."""
    if port == DUMMY_PORT:
        return DummyController()
    return SerialController(port)


def keep_alive(
    pad: Controller, *, stall: bool, sleep: Callable[[float], None] = time.sleep
) -> None:
    """Stay in the foreground so a held state stays held, until Ctrl-C. With
    `stall` the process stays alive but stops renewing the lease, as a hung
    control loop would."""
    try:
        while True:
            sleep(pad.timing.heartbeat_ms / 1000)
            if not stall:
                pad.heartbeat()
    except KeyboardInterrupt:
        pass


def _print_dummy_log(pad: DummyController) -> None:
    for state, hold_ms in pad.log:
        print(
            f"buttons={state.buttons!r} hat={state.hat.name} "
            f"L=({state.lx},{state.ly}) R=({state.rx},{state.ry}) hold_ms={hold_ms}"
        )
    if pad.release_all_calls:
        print(f"release_all x{pad.release_all_calls}")


def _run_action(pad: Controller, args: argparse.Namespace) -> None:
    held = False
    if args.action == "press":
        pad.press(args.button, args.ms)
    elif args.action == "hold":
        pad.hold(args.button)
        held = True
    elif args.action == "stick":
        pad.stick(args.which, args.x, args.y, args.ms)
        held = args.ms is None
    elif args.action == "release-all":
        pad.release_all()
    elif args.action == "info":
        if isinstance(pad, SerialController):
            info = pad.info()
            print(
                f"proto_ver={info.proto_ver} backend={info.backend} flags={info.flags:#04x} "
                f"queue_free={info.queue_free} uptime_ms={info.uptime_ms} "
                f"rx_crc_errors={info.rx_crc_errors} wd_trips={info.wd_trips}"
            )
        else:
            print("dummy controller: no device")
    # A dummy has nothing to keep alive; its log is the result.
    if held and not isinstance(pad, DummyController):
        print("holding; press Ctrl-C to release", file=sys.stderr)
        keep_alive(pad, stall=args.stall)


def _handle(args: argparse.Namespace) -> int:
    if args.action == "selftest":
        if args.port == DUMMY_PORT:
            print("ap pad: selftest needs a real port", file=sys.stderr)
            return 2
        # Imported here: it needs hidapi, which only the selftest uses.
        from autoplay.core.tools import pad_selftest

        try:
            return pad_selftest.run(args.port)
        except ControllerError as exc:
            print(f"ap pad: {exc}", file=sys.stderr)
            return 1
    try:
        pad = open_controller(args.port)
    except ControllerError as exc:
        print(f"ap pad: {exc}", file=sys.stderr)
        return 1
    try:
        _run_action(pad, args)
    except ControllerError as exc:
        print(f"ap pad: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ap pad: {exc}", file=sys.stderr)
        return 2
    finally:
        try:
            pad.close()
        except ControllerError as exc:
            print(f"ap pad: {exc}", file=sys.stderr)
    if isinstance(pad, DummyController):
        _print_dummy_log(pad)
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "pad",
        help="drive the gamepad by hand",
        description=(
            "Send one gamepad action through the firmware on a serial port. "
            f"Buttons: {', '.join(BUTTON_NAMES)}. "
            "`hold` and `stick` without --ms stay in the foreground and keep the state alive until Ctrl-C."
        ),
    )
    p.add_argument(
        "--port",
        required=True,
        help=f"serial port such as COM4, or '{DUMMY_PORT}' to print what would be sent",
    )
    actions = p.add_subparsers(dest="action", metavar="action", required=True)

    press = actions.add_parser("press", help="press a button, then release it")
    press.add_argument("button")
    press.add_argument(
        "--ms", type=int, help="how long to press (default: press_ms from timing.yaml)"
    )

    hold = actions.add_parser("hold", help="hold a button until Ctrl-C")
    hold.add_argument("button")

    stick = actions.add_parser(
        "stick", help="deflect a stick; x and y in [-1, 1], +y is up"
    )
    stick.add_argument("which", choices=("L", "R"))
    stick.add_argument("x", type=float)
    stick.add_argument("y", type=float)
    stick.add_argument(
        "--ms",
        type=int,
        help="deflect this long, then centre; without it, until Ctrl-C",
    )

    for sub_parser in (hold, stick):
        sub_parser.add_argument(
            "--stall",
            action="store_true",
            help="keep the process alive but stop renewing the lease, as a hung control loop would",
        )

    actions.add_parser("release-all", help="release every button and centre the sticks")
    actions.add_parser("info", help="print the firmware's INFO")
    actions.add_parser(
        "selftest",
        help="check the firmware's reports and timing (both board ports plugged into this PC)",
    )
    p.set_defaults(func=_handle, stall=False)
