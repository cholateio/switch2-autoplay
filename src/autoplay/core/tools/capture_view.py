import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from autoplay.core.capture.base import CaptureError, CaptureStats
from autoplay.core.capture.device import BACKENDS, DeviceSource

PREVIEW_SIZE = (960, 540)
BLACK_MEAN_LUMA = 12
SCREENS_DIR = Path("fixtures/screens")
_LABEL_RE = re.compile(r"[A-Za-z0-9_-]+")
_MAX_LABELS = 9
_WINDOW = "ap capture"


def parse_device_source(spec: str) -> int:
    """Return N for a `device:N` source spec; raise ValueError for any other form."""
    match = re.fullmatch(r"device:(\d+)", spec)
    if match is None:
        raise ValueError(f"unsupported --source {spec!r}; only device:N is supported")
    return int(match.group(1))


def parse_labels(text: str) -> list[str]:
    """Split a comma-separated label list; labels become directory names, so only
    [A-Za-z0-9_-] is allowed, and at most 9 (one per digit key)."""
    labels = [part.strip() for part in text.split(",")]
    for label in labels:
        if not _LABEL_RE.fullmatch(label):
            raise ValueError(
                f"invalid label {label!r}; use letters, digits, '_' or '-'"
            )
    if len(labels) > _MAX_LABELS:
        raise ValueError(f"at most {_MAX_LABELS} labels (digit keys 1-9)")
    return labels


def label_for_key(key: int, labels: list[str]) -> str | None:
    """Map digit key 1-9 to the matching label, else None."""
    if ord("1") <= key <= ord("9") and key - ord("1") < len(labels):
        return labels[key - ord("1")]
    return None


def is_black(bgr: np.ndarray) -> bool:
    """True when the mean luma is below 12."""
    return float(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).mean()) < BLACK_MEAN_LUMA


def screenshot_path(root: Path, label: str, now: datetime) -> Path:
    """Return `root/<label>/<YYYYmmdd-HHMMSS-mmm>.png`."""
    stamp = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"
    return root / label / f"{stamp}.png"


def overlay_lines(stats: CaptureStats, black: bool, label: str) -> list[str]:
    lines = [
        f"{stats.fps:.1f} fps  {stats.width}x{stats.height}  {stats.fourcc}",
        f"black: {'yes' if black else 'no'}   label: {label}",
    ]
    if stats.stale:
        lines.append("STALE: no new frame")
    return lines


def _draw_overlay(img: np.ndarray, lines: list[str]) -> None:
    for i, line in enumerate(lines):
        org = (10, 28 + 26 * i)
        cv2.putText(
            img, line, org, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA
        )
        cv2.putText(
            img,
            line,
            org,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


def _save(bgr: np.ndarray, label: str) -> Path:
    path = screenshot_path(SCREENS_DIR, label, datetime.now())
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, png = cv2.imencode(".png", bgr)
    if not ok:
        raise OSError(f"PNG encoding failed for {path}")
    path.write_bytes(png.tobytes())
    return path


def _preview(src: DeviceSource, labels: list[str]) -> None:
    label = labels[0]
    frame = None
    cv2.namedWindow(_WINDOW, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            newer = src.wait_next(frame.seq if frame else 0, 0.1)
            if newer is not None:
                frame = newer
                view = cv2.resize(frame.bgr, PREVIEW_SIZE, interpolation=cv2.INTER_AREA)
                _draw_overlay(view, overlay_lines(src.stats(), is_black(view), label))
                cv2.imshow(_WINDOW, view)
            key = cv2.waitKey(1)
            if (
                key == ord("q")
                or cv2.getWindowProperty(_WINDOW, cv2.WND_PROP_VISIBLE) < 1
            ):
                return
            if key == ord("s") and frame is not None:
                print(f"saved {_save(frame.bgr, label)}", flush=True)
            label = label_for_key(key, labels) or label
    finally:
        cv2.destroyWindow(_WINDOW)


def _handle(args: argparse.Namespace) -> int:
    try:
        index = parse_device_source(args.source)
        labels = parse_labels(args.labels)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    src = DeviceSource(index, backend=args.backend)
    try:
        src.start()
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with src:
        _preview(src, labels)
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "capture",
        help="preview a capture device and save labelled screenshots",
        description=(
            "Preview at 960x540 with fps, resolution and black-screen detection. "
            "Keys: 1-9 pick a label, s saves a full-resolution PNG to "
            "fixtures/screens/<label>/, q quits."
        ),
    )
    p.add_argument(
        "--source", default="device:0", help="frame source, device:N (default device:0)"
    )
    p.add_argument("--backend", choices=sorted(BACKENDS), default="dshow")
    p.add_argument(
        "--labels",
        default="unlabeled",
        help="comma-separated screenshot labels (default: unlabeled)",
    )
    p.set_defaults(func=_handle)
