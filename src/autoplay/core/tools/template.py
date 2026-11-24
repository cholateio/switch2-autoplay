import argparse
import os
import re
import sys
from pathlib import Path

import cv2
import numpy as np

from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.offline import read_image
from autoplay.core.perception.geometry import BASE_H, BASE_W, ROI, parse_roi
from autoplay.core.perception.ops import check_template
from autoplay.core.perception.states import (
    DEFAULT_THRESHOLD,
    StatesFileError,
    read_states_entries,
    save_state_entry,
)

_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
DEFAULT_MARGIN = 16
_PICK_SIZE = (1280, 720)
_WINDOW = "ap template: drag a box, Enter confirms, c cancels"


def to_base(bgr: np.ndarray) -> np.ndarray:
    """Scale a picture to the base size templates are authored at."""
    if bgr.shape[1] == BASE_W and bgr.shape[0] == BASE_H:
        return bgr
    shrink = bgr.shape[1] > BASE_W
    return cv2.resize(
        bgr,
        (BASE_W, BASE_H),
        interpolation=cv2.INTER_AREA if shrink else cv2.INTER_LINEAR,
    )


def search_area(box: ROI, margin: int) -> ROI:
    """The box grown by `margin` on every side, kept inside the base picture."""
    x0, y0 = max(0, box.x - margin), max(0, box.y - margin)
    x1, y1 = min(BASE_W, box.x + box.w + margin), min(BASE_H, box.y + box.h + margin)
    return ROI(x0, y0, x1 - x0, y1 - y0)


def add_template_state(
    states_file: Path,
    name: str,
    image_bgr: np.ndarray,
    box: ROI,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    margin: int = DEFAULT_MARGIN,
) -> Path:
    """Cut `box` (base coordinates) out of the picture, save it as
    `templates/<name>.png` beside the states file and add or replace the
    state's entry. Returns the template's path. Raises ValueError if the box
    holds only a flat colour. If the states file cannot be read or written,
    a template that already existed under this name is left as it was."""
    base = to_base(image_bgr)
    template = base[box.y : box.y + box.h, box.x : box.x + box.w]
    check_template(template)
    if states_file.exists():
        read_states_entries(states_file)
    path = states_file.parent / "templates" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, png = cv2.imencode(".png", template)
    if not ok:
        raise OSError(f"PNG encoding failed for {path}")
    # An existing entry of this name keeps pointing at this path, so the old
    # picture is put back if the entry cannot be rewritten.
    previous = path.read_bytes() if path.exists() else None
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    staged.write_bytes(png.tobytes())
    os.replace(staged, path)
    try:
        save_state_entry(
            states_file,
            {
                "name": name,
                "kind": "template",
                "template": f"templates/{name}.png",
                "roi": search_area(box, margin).as_list(),
                "threshold": threshold,
            },
        )
    except BaseException:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)
        raise
    return path


def _pick_box(base: np.ndarray) -> ROI | None:
    view = cv2.resize(base, _PICK_SIZE, interpolation=cv2.INTER_AREA)
    try:
        x, y, w, h = cv2.selectROI(_WINDOW, view, showCrosshair=False)
    finally:
        cv2.destroyAllWindows()
    if w == 0 or h == 0:
        return None
    sx, sy = BASE_W / _PICK_SIZE[0], BASE_H / _PICK_SIZE[1]
    return ROI(round(x * sx), round(y * sy), round(w * sx), round(h * sy))


def _handle(args: argparse.Namespace) -> int:
    try:
        if not _NAME_RE.fullmatch(args.name):
            raise ValueError(
                f"invalid --name {args.name!r}; use letters, digits, '_' or '-'"
            )
        if not 0 < args.threshold <= 1:
            raise ValueError("--threshold must be above 0 and at most 1")
        if args.margin < 0:
            raise ValueError("--margin must not be negative")
        box = parse_roi(args.roi) if args.roi else None
        if args.kind == "template" and args.image is None:
            raise ValueError("--image is required for a template state")
    except ValueError as exc:
        print(f"ap template: {exc}", file=sys.stderr)
        return 2
    states_file = Path(args.states_file)
    try:
        if args.kind == "black":
            entry = {"name": args.name, "kind": "black"}
            if box is not None:
                entry["roi"] = box.as_list()
            save_state_entry(states_file, entry)
            print(f"{args.name}: black state written to {states_file}")
            return 0
        image = read_image(Path(args.image))
        if box is None:
            box = _pick_box(to_base(image))
            if box is None:
                print("ap template: no box selected", file=sys.stderr)
                return 1
        path = add_template_state(
            states_file,
            args.name,
            image,
            box,
            threshold=args.threshold,
            margin=args.margin,
        )
    except (CaptureError, StatesFileError, ValueError, OSError) as exc:
        print(f"ap template: {exc}", file=sys.stderr)
        return 1
    print(
        f"{args.name}: template {box.w}x{box.h} at {box.x},{box.y} saved to {path}; "
        f"entry written to {states_file}"
    )
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "template",
        help="cut a template out of a screenshot and add the state to a states file",
        description=(
            "Saves the box as templates/<name>.png beside the states file and adds or "
            "replaces the state's entry. Without --roi a window opens to drag the box "
            "with the mouse. Coordinates refer to a 1920x1080 picture."
        ),
    )
    p.add_argument("--image", help="screenshot to cut the template from")
    p.add_argument("--name", required=True, help="state name")
    p.add_argument("--states-file", required=True, help="states YAML file")
    p.add_argument("--roi", help="the box x,y,w,h; for --kind black, the area to test")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument(
        "--margin",
        type=int,
        default=DEFAULT_MARGIN,
        help="how far around the box the template is searched for",
    )
    p.add_argument(
        "--kind",
        choices=("template", "black"),
        default="template",
        help="black: a state that is a black screen, with no template",
    )
    p.set_defaults(func=_handle)
