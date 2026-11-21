import argparse
import sys
from pathlib import Path

from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.offline import IMAGE_SUFFIXES, read_image
from autoplay.core.perception.states import StatesFileError, load_states_file


def collect_images(paths: list[Path]) -> list[Path]:
    """Expand each directory to the image files directly inside it, in name
    order; raise ValueError for a path that is neither an image nor a
    directory holding one."""
    images: list[Path] = []
    for path in paths:
        if path.is_dir():
            found = sorted(
                (p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES),
                key=lambda p: p.name,
            )
            if not found:
                raise ValueError(f"no image files in {path}")
            images.extend(found)
        elif path.is_file():
            images.append(path)
        else:
            raise ValueError(f"not found: {path}")
    return images


def _handle(args: argparse.Namespace) -> int:
    try:
        registry = load_states_file(Path(args.states_file))
        images = collect_images([Path(p) for p in args.paths])
    except (StatesFileError, ValueError) as exc:
        print(f"ap classify: {exc}", file=sys.stderr)
        return 1
    correct = 0
    for path in images:
        try:
            bgr = read_image(path)
        except CaptureError as exc:
            print(f"ap classify: {exc}", file=sys.stderr)
            return 1
        scores = registry.scores(bgr)
        state = registry.classify(bgr)
        detail = " ".join(f"{name}={score:.2f}" for name, score in scores.items())
        line = f"{path} -> {state} ({detail})"
        if args.expect_from_dirname:
            # A directory that is not a registered state holds pictures that
            # must match no state.
            folder = path.parent.name
            expected = folder if folder in registry.names else None
            ok = state == expected
            correct += ok
            line += "" if ok else f"  WRONG, expected {expected}"
        print(line)
    if not args.expect_from_dirname:
        return 0
    print(f"correct={correct}/{len(images)}")
    return 0 if correct == len(images) else 1


def register(sub) -> None:
    p = sub.add_parser(
        "classify",
        help="print the state a states file gives to each image",
        description=(
            "Prints the state and every detector's score for each image. With "
            "--expect-from-dirname the name of the folder an image is in is the right "
            "answer (a folder that is not a state name means: no state), and the exit "
            "code is 1 unless every image is right."
        ),
    )
    p.add_argument("--states-file", required=True, help="states YAML file")
    p.add_argument("paths", nargs="+", help="image files or folders of images")
    p.add_argument("--expect-from-dirname", action="store_true")
    p.set_defaults(func=_handle)
