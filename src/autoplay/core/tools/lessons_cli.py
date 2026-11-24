import argparse
import sys
from datetime import date
from pathlib import Path

from autoplay.core.memory.lessons import Lesson, LessonStore
from autoplay.core.memory.notes import write_atomic
from autoplay.core.pack.loader import PackError, load_pack

MEMORY_ROOT = Path("memory")
HEADING = "## 經驗"
UNVERIFIED = "未經實際使用驗證"


def memory_dir(pack_name: str, root: Path = MEMORY_ROOT) -> Path:
    """Where the notes and lesson drafts of one pack live on this machine."""
    return Path(root) / pack_name


def promoted_text(lesson: Lesson, today: date, unverified: bool) -> str:
    counts = f"使用 {lesson.uses} 次、成功 {lesson.successes} 次"
    mark = f"；{UNVERIFIED}" if unverified else ""
    return (
        f"### {lesson.slug}（{today.isoformat()} 升級；{counts}{mark}）\n\n"
        f"- 何時用：{lesson.when}\n"
        f"- 步驟：{lesson.steps}\n"
        f"- 失敗時：{lesson.failure}\n"
        f"- 怎麼確認：{lesson.verify}\n"
    )


def promote(
    store: LessonStore, slug: str, chapter: Path, *, unverified: bool, today: date
) -> None:
    """Append the draft to the end of the chapter file and drop the draft.
    Raises KeyError for an unknown slug, and PermissionError when the lesson
    never succeeded in use and `unverified` is not set."""
    lesson = store.get(slug)
    if lesson.successes == 0 and not unverified:
        raise PermissionError(
            f"lesson {slug!r} has {lesson.uses} use(s) and no success; only a "
            "lesson that worked at least once is promoted. Pass --unverified to "
            "promote it anyway, marked as such."
        )
    text = chapter.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    if HEADING not in text.splitlines():
        text += f"\n{HEADING}\n"
    text += "\n" + promoted_text(lesson, today, lesson.successes == 0)
    write_atomic(chapter, text)
    store.drop(slug)


def _handle(args: argparse.Namespace) -> int:
    try:
        pack = load_pack(Path(args.pack))
    except PackError as exc:
        print(f"ap lessons: {exc}", file=sys.stderr)
        return 1
    store = LessonStore(memory_dir(pack.name, args.memory_root) / "lessons")
    try:
        if args.action == "list":
            for lesson in store.list():
                print(
                    f"{lesson.slug}  uses={lesson.uses} successes={lesson.successes}  "
                    f"{lesson.when}"
                )
        elif args.action == "drop":
            store.drop(args.slug)
        else:
            chapter = pack.chapters.get(args.chapter)
            if chapter is None:
                known = ", ".join(pack.chapters) or "(none)"
                print(
                    f"ap lessons: no chapter {args.chapter!r}; the chapters are: {known}",
                    file=sys.stderr,
                )
                return 2
            promote(
                store,
                args.slug,
                chapter,
                unverified=args.unverified,
                today=date.today(),
            )
            print(f"promoted {args.slug} into {chapter}")
    except (KeyError, ValueError, PermissionError, OSError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) else exc
        print(f"ap lessons: {message}", file=sys.stderr)
        return 1
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "lessons",
        help="list, promote or drop the lesson drafts a run wrote",
        description=(
            "A run writes what it learned as drafts under memory/<pack>/lessons/. "
            "A draft becomes guide text only when a person promotes it here."
        ),
    )
    actions = p.add_subparsers(dest="action", metavar="action", required=True)

    def common(parser) -> None:
        parser.add_argument("--pack", required=True, help="game pack directory")
        parser.add_argument(
            "--memory-root",
            type=Path,
            default=MEMORY_ROOT,
            help="where memories are kept (default memory/)",
        )
        parser.set_defaults(func=_handle)

    common(actions.add_parser("list", help="print every draft with its counts"))
    promote_p = actions.add_parser(
        "promote", help="append a draft to a guide chapter and remove the draft"
    )
    promote_p.add_argument("slug")
    promote_p.add_argument("--chapter", required=True, help="chapter name")
    promote_p.add_argument(
        "--unverified",
        action="store_true",
        help="promote a lesson that never succeeded in use, marked as unverified",
    )
    common(promote_p)
    drop_p = actions.add_parser("drop", help="delete a draft")
    drop_p.add_argument("slug")
    common(drop_p)
