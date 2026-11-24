"""Fixed sequences for Pokopia, offered to the model as tools. Every recipe
and number comes from guide/ and knowledge/aim-drills.md; each step that
changes the world is checked on the picture, and a routine that cannot
confirm a step stops and says where."""

import importlib.util
from pathlib import Path

from autoplay.core.pack.guard import UnexpectedPicture
from autoplay.core.pack.routines import Param, RoutineStopped, routine

_spec = importlib.util.spec_from_file_location(
    "pokopia_checks", Path(__file__).with_name("checks.py")
)
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)

PITCH = {"build": 350, "mid": 650, "level": 1000}
YAW_MS = 950
STEP_MS = 200
TURN_MS = 60
RISE_MS = 300
# Scores of the white lettering: the same prompt 0.99 to 1.0, another one at most 0.67.
SEEN = 0.85
STANDING = 130
# A wall decoration seen from one cell back was outlined 92 px high.
LOW = 60
# The middle of the screen, where a change of height shows. One rise step
# changed 0.07 to 0.19 of it (11 steps, two pitches); hovering in place 0.01.
HEIGHT_ROI = (560, 140, 800, 800)
RISEN = 0.04
CONFIRM_ROI = (380, 880, 760, 120)
NOTE_PP_ROI = (700, 440, 540, 240)
BERRY_ROI = (438, 195, 90, 90)


def _pitch(hands, value: int) -> None:
    hands.stick("R", 0.0, -1.0, 1500)
    hands.stick("R", 0.0, 1.0, value)
    hands.wait(400)


def _press_if(
    hands, button: str, require, step: str, why: str, confirm: str | None = None
) -> None:
    """Press only if `require` holds on the pictures the guard reads right
    before sending; a check made a moment earlier can already be stale."""
    try:
        hands.press(button, confirm=confirm, require=require)
    except UnexpectedPicture:
        raise RoutineStopped(f"{button} not pressed: {why}", step=step) from None


def _prompt(hands, frame, name: str) -> bool:
    return checks.prompt_score(frame, hands.template(f"prompt_{name}.png")) >= SEEN


@routine(
    "Set the camera to a fixed view. build: steep, for work on the ground. mid: "
    "for walls up to 4 layers and for breaking. level: for anything higher. "
    "side: turn the camera 90 degrees to the right, pitch unchanged, to see "
    "what the character hides. back: undo `side`.",
    name=Param(str, choices=("build", "mid", "level", "side", "back")),
)
def view(hands, name):
    if name == "side":
        hands.stick("R", 1.0, 0.0, YAW_MS)
    elif name == "back":
        hands.stick("R", -1.0, 0.0, YAW_MS)
    else:
        _pitch(hands, PITCH[name])
    hands.wait(300)
    return f"view {name} set"


@routine(
    "Turn the character to face away from the camera (screen up). Do it once "
    "before a job; doing it twice walks forward."
)
def face_away(hands):
    hands.stick("L", 0.0, 1.0, TURN_MS)
    hands.wait(400)
    return "facing away from the camera"


def _open_bag(hands) -> None:
    start = hands.context()
    if start == "bag":
        return
    if start not in ("explore", "placing"):
        raise RoutineStopped(
            f"the bag is opened from explore or placing, not from {start}",
            step="open the bag",
        )
    for attempt in range(4):
        if attempt == 2 and start == "explore" and hands.context() == "explore":
            # Left alone for a few minutes the human form dozes off and takes
            # no button (seen 2026-11-23); a push of the stick wakes it.
            hands.stick("L", 0.0, 1.0, TURN_MS)
            hands.wait(1500)
        if hands.context() != start:
            break
        hands.press("X")
        hands.wait(1300)
        if hands.context() == "bag":
            return
    raise RoutineStopped("the bag did not open", step="open the bag")


def _goto(hands, page: int, row: int, column: int) -> None:
    _open_bag(hands)
    shown = checks.bag_page(hands.frame())
    if shown is None:
        raise RoutineStopped(
            "cannot read which page the bag shows", step="read the page"
        )
    for _ in range((page - shown) % 4):
        hands.press("R")
        hands.wait(700)
    frame = hands.frame()
    if checks.bag_page(frame) != page:
        raise RoutineStopped(f"the bag is not on page {page}", step="turn the page")
    at = checks.bag_cursor(frame)
    if at is None:
        raise RoutineStopped("cannot find the bag cursor", step="find the cursor")
    for button, steps in (
        ("DPAD_DOWN", row - at[0]),
        ("DPAD_UP", at[0] - row),
        ("DPAD_RIGHT", column - at[1]),
        ("DPAD_LEFT", at[1] - column),
    ):
        for _ in range(max(0, steps)):
            hands.press(button)
            hands.wait(250)
    hands.wait(300)
    if checks.bag_cursor(hands.frame()) != (row, column):
        raise RoutineStopped(
            f"the cursor did not reach row {row} column {column}; the cell may be empty",
            step="move the cursor",
        )


@routine(
    "Open the bag and put the cursor on a cell (page 1-4, row 1-3, column 1-10). "
    "It does not take the item: read the name in the screenshot, then press A to "
    "take it or B to close.",
    page=Param(int, 1, 4),
    row=Param(int, 1, 3),
    column=Param(int, 1, 10),
)
def bag_goto(hands, page, row, column):
    _goto(hands, page, row, column)
    return f"bag open, cursor on page {page} row {row} column {column}"


def _in_hand(hands, frame) -> str | None:
    """one: a single block in Magnet Rise. many: a quantity above 1. human: an
    item in the human form's hand. None: nothing."""
    count = hands.template("prompt_count1.png")
    if checks.prompt_score(frame, count) >= SEEN:
        return "one" if checks.prompt_tail_score(frame, count, 30) >= SEEN else "many"
    return "human" if _prompt(hands, frame, "spit") else None


@routine(
    "Place a line of blocks: one in the cell in front, step one cell sideways, "
    "the next, and so on. direction e is screen right, w screen left. Needs an "
    "item in hand, the character facing away from the camera, quantity 1, and "
    "the camera square to the grid (a row of blocks looks level on screen). Sets "
    "the mid view. After every press it opens the bag to see whether the "
    "item's count went down, and repeats a press only when it did not, once. "
    "Called again at the end of a line, in the other direction, it puts the "
    "next layer on top (up to layer 2 from the ground; rise first for more).",
    direction=Param(str, choices=("e", "w")),
    count=Param(int, 1, 9),
)
def place_line(hands, direction, count):
    _pitch(hands, PITCH["mid"])
    placed = _line(hands, direction, count, _start_count(hands))
    return f"placed {placed} going {direction}; now in front of the last one"


class _InHand:
    """Where the item in hand sits in the bag, what it looks like there, and
    the picture of its count as last seen."""

    def __init__(self, page, cell, icon, mark) -> None:
        self.page, self.cell, self.icon, self.mark = page, cell, icon, mark


def _bag_cell(hands, step: str):
    _open_bag(hands)
    frame = hands.frame()
    page, cell = checks.bag_page(frame), checks.bag_cursor(frame)
    if page is None or cell is None:
        raise RoutineStopped("cannot read the bag page or cursor", step=step)
    return frame, page, cell


def _start_count(hands) -> _InHand:
    """With an item in hand: open the bag (its cursor is on that item),
    remember the cell, the item's picture and its count, and take it again."""
    if _in_hand(hands, hands.frame()) is None:
        raise RoutineStopped("nothing is in hand", step="start")
    frame, page, cell = _bag_cell(hands, "start")
    item = _InHand(
        page, cell, checks.cell_icon(frame, cell), checks.count_mark(frame, cell)
    )
    _retake(hands, "start")
    return item


def _retake(hands, step: str) -> None:
    hands.press("A")
    hands.wait(1000)
    if hands.context() == "bag":
        raise RoutineStopped("the item was not taken out again", step=step)


def _count_changed(hands, item: _InHand, step: str) -> bool:
    """Open the bag and say whether the item's count differs from the last
    look; then take the item again. A is pressed in the bag only on the same
    cell showing the same item: with the stack used up, the cursor could be
    on something else."""
    frame, page, cell = _bag_cell(hands, step)
    if (page, cell) != (item.page, item.cell) or not checks.same_icon(
        checks.cell_icon(frame, cell), item.icon
    ):
        raise RoutineStopped(
            "the item is no longer in its bag cell (the stack may be used up); "
            "the bag is left open, nothing was taken",
            step=step,
        )
    mark = checks.count_mark(frame, cell)
    changed = not checks.same_count(mark, item.mark)
    item.mark = mark
    _retake(hands, step)
    return changed


def _still(hands):
    """A frame taken once the picture has stopped moving: after a step the
    camera follows for a moment, and a change measured across that is not the
    block's."""
    last = hands.frame()
    for _ in range(8):
        hands.wait(250)
        now = hands.frame()
        if checks.changed_fraction(last, now) < 0.01:
            return now
        last = now
    return last


def _line(hands, direction: str, count: int, item: _InHand) -> int:
    """Place `count` blocks. Whether a press placed one is decided by the
    item's count in the bag, looked at after every press: the picture in front
    of the character misses blocks when the character is off the middle of the
    screen (it read 0.005 to 0.028 for blocks that were placed)."""
    dx = 1.0 if direction == "e" else -1.0
    placed = 0
    for block in range(1, count + 1):
        if block > 1:
            hands.combo("ZL", STEP_MS, stick=("L", dx, 0.0))
            hands.wait(500)
        for attempt in (1, 2):
            before = _still(hands)
            hand = _in_hand(hands, before)
            if hand != "one" and hand != "human":
                why = (
                    "the quantity is not 1: press DPAD_UP until the prompt reads 1"
                    if hand == "many"
                    else "nothing is in hand"
                )
                raise RoutineStopped(f"{why}; {placed} placed", step=f"block {block}")
            hands.press("A")
            hands.wait(1300)
            change = checks.changed_fraction(before, hands.frame())
            counted = _count_changed(hands, item, f"block {block}")
            hands.log(
                routine="place_line",
                block=block,
                attempt=attempt,
                change=round(change, 3),
                counted=counted,
            )
            if counted:
                break
            # The count did not move: nothing was placed, a second press is safe.
        else:
            raise RoutineStopped(
                f"two presses placed nothing (the count in the bag did not change); "
                f"{placed} placed in this line. The cell may be out of reach at this height",
                step=f"block {block}",
            )
        placed += 1
    return placed


@routine(
    "Build a wall `width` wide and `layers` high in front of you, going east "
    "(screen right) for the first layer and back for the next, and so on. Same "
    "needs as place_line. Layers 1 and 2 work in human form and right after "
    "magnet_on; layers 3 and 4 need Magnet Rise and are placed after rising a "
    "step each (do not call `ground` between magnet_on and this). Ends in front "
    "of the west end for an even number of layers, the east end for an odd one.",
    width=Param(int, 1, 6),
    layers=Param(int, 1, 4),
)
def place_wall(hands, width, layers):
    if layers > 2 and hands.context() != "placing":
        raise RoutineStopped(
            "more than 2 layers needs Magnet Rise: magnet_on, take the block, call again",
            step="start",
        )
    _pitch(hands, PITCH["mid"])
    item = _start_count(hands)
    done = 0
    for layer in range(1, layers + 1):
        if layer > 2:
            _ascend(hands, 1)
        try:
            done += _line(hands, "e" if layer % 2 else "w", width, item)
        except RoutineStopped as stop:
            raise RoutineStopped(
                f"{stop}; {done} placed in the layers before",
                step=f"layer {layer}, {stop.step}",
            ) from None
    return f"wall {width} wide, {layers} high: {done} blocks placed"


@routine(
    "Transform with Magnet Rise: selects the move if needed and presses ZR. Ends "
    "in the placing context with nothing in hand."
)
def magnet_on(hands):
    context = hands.context()
    if context == "placing":
        return "already transformed"
    if context != "explore":
        raise RoutineStopped(f"needs the explore context, not {context}", step="start")
    if _prompt(hands, hands.frame(), "spit"):
        hands.press("R")
        hands.wait(700)
    label = hands.template("move_magnet.png")
    for _ in range(9):
        if checks.label_score(hands.frame(), label, checks.MOVE_LABEL) >= SEEN:
            break
        hands.press("DPAD_RIGHT")
        hands.wait(600)
    else:
        raise RoutineStopped(
            "Magnet Rise is not among the moves", step="select the move"
        )
    # A press right after another program left the gamepad is sometimes lost.
    for _ in range(2):
        # ZR uses whichever move is selected, and other moves break things.
        _press_if(
            hands,
            "ZR",
            lambda frame: checks.label_score(frame, label, checks.MOVE_LABEL) >= SEEN,
            "ZR",
            "the selected move was no longer Magnet Rise",
        )
        for _ in range(8):
            hands.wait(500)
            if hands.context() == "placing":
                # The picture changes before the game takes inputs again.
                hands.wait(1500)
                return "transformed; nothing in hand"
    raise RoutineStopped(
        "the transformation did not start (PP may be empty)", step="ZR"
    )


def _confirm_open(hands) -> bool:
    confirm = hands.template("confirm_untransform.png")
    return checks.label_score(hands.frame(), confirm, CONFIRM_ROI) >= SEEN


def _answer_confirm(hands) -> str:
    # The question was matched and the cursor starts on the first option.
    _press_if(
        hands,
        "A",
        lambda frame: (
            checks.label_score(
                frame, hands.template("confirm_untransform.png"), CONFIRM_ROI
            )
            >= SEEN
        ),
        "confirm",
        "the question about ending the transformation was no longer on screen",
        confirm="是 (要結束變身，恢復原來的樣子嗎？)",
    )
    # The change back takes between 1.5 and 3 s.
    for _ in range(10):
        hands.wait(500)
        if hands.context() == "explore":
            return "human form again"
    raise RoutineStopped("did not return to explore", step="confirm")


def _untransform(hands) -> str:
    if _confirm_open(hands):
        # Left open by an R that was not followed up, as when a run is stopped.
        return _answer_confirm(hands)
    context = hands.context()
    if context == "bag":
        hands.press("B")
        hands.wait(900)
        context = hands.context()
    if context != "placing":
        return f"not transformed (context {context})"
    if not _prompt(hands, hands.frame(), "untransform"):
        # Something is in hand: opening and closing the bag empties it.
        _open_bag(hands)
        hands.press("B")
        hands.wait(900)
        if not _prompt(hands, hands.frame(), "untransform"):
            raise RoutineStopped(
                "the prompt row does not offer to end the transformation; the "
                "character may overlap a block: step back one cell",
                step="R",
            )
    for _ in range(2):
        hands.press("R")
        hands.wait(900)
        if _confirm_open(hands):
            return _answer_confirm(hands)
    raise RoutineStopped("the confirmation box did not appear", step="R")


@routine(
    "End Magnet Rise: empties the hand if needed, R, confirms, ends in explore. "
    "The character may face another way afterwards: call face_away before the "
    "next placing or breaking."
)
def magnet_off(hands):
    return _untransform(hands)


def _in_placing(hands, step: str) -> None:
    context = hands.context()
    if context != "placing":
        raise RoutineStopped(
            f"needs Magnet Rise (the placing context), not {context}", step=step
        )


def _descend(hands) -> None:
    for press in range(1, 5):
        _in_placing(hands, f"descend {press}")
        hands.press("L", RISE_MS)
    hands.wait(300)


def _ascend(hands, steps: int) -> None:
    """Rise `steps` times, each one seen on the picture: a press that is lost,
    or a ceiling, would shift every layer aimed at afterwards."""
    for step in range(1, steps + 1):
        _in_placing(hands, f"rise {step}")
        before = _still(hands)
        hands.press("B", RISE_MS)
        hands.wait(400)
        change = checks.changed_fraction(before, hands.frame(), HEIGHT_ROI)
        hands.log(routine="rise", step=step, change=round(change, 3))
        if change < RISEN:
            raise RoutineStopped(
                f"the picture did not move when rising (change {change:.3f}); "
                f"{step - 1} steps risen. The height is not what was asked for",
                step=f"rise {step}",
            )
    hands.wait(300)


@routine("Magnet Rise: descend until the character is at ground level.")
def ground(hands):
    _descend(hands)
    return "at ground level"


@routine(
    "Magnet Rise: rise by `steps` (about 0.9 layer each), each step checked on "
    "the picture. Count from ground level: call `ground` first when the height "
    "matters.",
    steps=Param(int, 1, 8),
)
def rise(hands, steps):
    _ascend(hands, steps)
    return f"rose {steps} steps"


def _gate(hands, frame=None, tall: int = STANDING) -> tuple[bool, str]:
    """Whether Y may be pressed: the break prompt and an outline at least
    `tall` high are both seen. The default is a block that stands; a flat
    ground cell and a half-hidden outline are lower."""
    frame = hands.frame() if frame is None else frame
    if not _prompt(hands, frame, "break"):
        return False, (
            "no 'Y 破壞' prompt: nothing is targeted. Usual causes: the character "
            "does not face the block (the facing changes when Magnet Rise ends: "
            "call face_away), it is not in front of the block, the height is "
            "wrong, or a Pokémon beside you has the target ('A 交談' on screen)"
        )
    box = checks.outline(frame)
    if box is None:
        return (
            False,
            "the prompt is there but no outline is visible (hidden by the character?)",
        )
    if box[3] < tall:
        return (
            False,
            f"the outline is only {box[3]} px tall: a ground cell, or half hidden",
        )
    return True, f"outline {box}"


def _note(hands) -> bool:
    """Whether the pick-up note ('<item> x N', right edge) is on screen. Only
    its 'x' is matched: the name, the count and the scene behind it vary."""
    score = checks.label_score(
        hands.frame(), hands.template("note_x.png"), checks.NOTE_ROI
    )
    return score >= 0.7


def _break(hands, step: str, tall: int = STANDING) -> None:
    # A note from the break before lingers about 3 s and would pass for this one.
    for _ in range(30):
        if not _note(hands):
            break
        hands.wait(200)
    else:
        raise RoutineStopped("the note of the last pick-up does not go away", step=step)
    for attempt in (1, 2):
        ok, why = _gate(hands, tall=tall)
        hands.log(routine="break", step=step, gate=ok, why=why)
        if not ok:
            raise RoutineStopped(f"Y not pressed: {why}", step=step)
        try:
            _press_if(
                hands,
                "Y",
                lambda frame: _gate(hands, frame, tall)[0],
                step,
                "the outline or the prompt was gone when it was about to be sent",
            )
            break
        except RoutineStopped:
            # Nothing was sent. A flicker (a puff of smoke, the follower
            # passing) can hide the outline for a frame: judge once more.
            if attempt == 2:
                raise
            hands.wait(500)
    for _ in range(23):
        hands.wait(150)
        if _note(hands):
            return
    raise RoutineStopped(
        "Y was pressed but no pick-up note followed (with PP at zero the game "
        "refuses and shows a 'PP' note instead)",
        step=step,
    )


@routine(
    "Magnet Rise, nothing in hand: break the one thing the dashed outline is on. "
    "This and break_column / break_wall are the only way to press Y while "
    "transformed. Y is sent only when the break prompt and the outline are "
    "seen, and the pick-up note must follow. shape block: the outline of a "
    "standing block (use the side view for layer 1). shape low: also a low "
    "outline, for a wall decoration seen from one cell back; a flat ground "
    "cell has a low outline too, so look first and be sure the outline is on "
    "the decoration. It cannot tell your block from one that was there before.",
    shape=Param(str, choices=("block", "low")),
)
def break_front(hands, shape):
    _in_placing(hands, "start")
    _break(hands, "break", STANDING if shape == "block" else LOW)
    return "broke 1 and picked it up"


@routine(
    "Magnet Rise, nothing in hand: break `count` blocks of the column in front, "
    "top down. Rise above the top first. It turns the camera to the side view "
    "(only the column nearest the camera, the leftmost, shows its outline: work "
    "left to right), descends until an outline shows, breaks, descends a layer, "
    "repeats, and turns the camera back. Same checks as break_front.",
    count=Param(int, 1, 8),
)
def break_column(hands, count):
    _in_placing(hands, "start")
    # From behind, the character hides the lower half of the outline.
    hands.stick("R", 1.0, 0.0, YAW_MS)
    hands.wait(300)
    try:
        for block in range(1, count + 1):
            for _ in range(7):
                if _gate(hands)[0]:
                    break
                _in_placing(hands, f"block {block}")
                hands.press("L", 150)
                hands.wait(500)
            try:
                _break(hands, f"block {block}")
            except RoutineStopped as stop:
                raise RoutineStopped(
                    f"{stop}; {block - 1} broken", step=stop.step
                ) from None
            _in_placing(hands, f"block {block}")
            hands.press("L", RISE_MS)
            hands.wait(500)
    finally:
        hands.stick("R", -1.0, 0.0, YAW_MS)
    return f"broke {count}, top down"


@routine(
    "Magnet Rise, nothing in hand: take down a wall `width` wide and `layers` "
    "high. Start in front of its west (screen left) end, facing it; it goes "
    "column by column to the east with break_column and ends in front of the "
    "east end. If it stops, it says how many columns were finished.",
    width=Param(int, 1, 6),
    layers=Param(int, 1, 4),
)
def break_wall(hands, width, layers):
    for column in range(1, width + 1):
        if column > 1:
            hands.combo("ZL", STEP_MS, stick=("L", 1.0, 0.0))
            hands.wait(500)
        _descend(hands)
        _ascend(hands, layers + 1)
        try:
            break_column(hands, layers)
        except RoutineStopped as stop:
            raise RoutineStopped(
                f"{stop}; {column - 1} columns finished",
                step=f"column {column}, {stop.step}",
            ) from None
    return f"wall taken down: {width * layers} blocks"


@routine(
    "Human form: eat 蘋野果 from the bag (page 1, first cell) until the game "
    "says PP is full, at most `most`. Reports how many times it ate.",
    most=Param(int, 1, 10),
)
def eat_berries(hands, most):
    if hands.context() not in ("explore", "bag"):
        raise RoutineStopped("needs the human form in explore", step="start")
    _goto(hands, 1, 1, 1)
    icon = hands.template("berry.png")
    x, y, w, h = BERRY_ROI
    cell = hands.frame()[y : y + h, x : x + w]
    if checks.cv2.matchTemplate(cell, icon, checks.cv2.TM_CCOEFF_NORMED).max() < 0.8:
        raise RoutineStopped(
            "the first cell of page 1 is not 蘋野果", step="find the berries"
        )
    hands.press("A")
    hands.wait(1000)
    if not _prompt(hands, hands.frame(), "eat"):
        raise RoutineStopped(
            "the prompt does not offer to eat", step="take the berries"
        )
    full = hands.template("note_pp_full.png")
    eaten, is_full = 0, False
    while eaten < most and not is_full:
        # Checked before every press: with the berries gone, A would act on
        # whatever is in front of the character.
        if not _prompt(hands, hands.frame(), "eat"):
            raise RoutineStopped(
                f"the prompt no longer offers to eat; ate {eaten} times. The "
                "berries may have run out",
                step=f"berry {eaten + 1}",
            )
        _press_if(
            hands,
            "A",
            lambda frame: _prompt(hands, frame, "eat"),
            f"berry {eaten + 1}",
            f"the eat prompt was gone when it was about to be sent; ate {eaten} times",
        )
        eaten += 1
        for _ in range(11):
            hands.wait(150)
            if checks.label_score(hands.frame(), full, NOTE_PP_ROI) >= SEEN:
                is_full = True
        hands.wait(200)
    hands.press("R")
    hands.wait(800)
    return f"ate {eaten} times; PP full: {'yes' if is_full else 'not seen'}"


@routine(
    "Bring the game to a state it can be left in: closes the bag, ends Magnet "
    "Rise, puts away what the human form holds. Run by the program when a run ends."
)
def leave(hands):
    done = []
    for _ in range(3):
        if hands.context() != "bag":
            break
        hands.press("B")
        hands.wait(900)
        done.append("closed the bag")
    if hands.context() == "bag":
        raise RoutineStopped("the bag does not close", step="close the bag")
    if hands.context() == "placing" or _confirm_open(hands):
        done.append(_untransform(hands))
    if hands.context() == "explore" and _prompt(hands, hands.frame(), "spit"):
        hands.press("R")
        hands.wait(700)
        done.append("put the item away")
    context = hands.context()
    if context != "explore":
        raise RoutineStopped(
            f"could not bring the game back to explore; it is in {context}",
            step="check",
        )
    return "; ".join(dict.fromkeys(done)) or "nothing to do"
