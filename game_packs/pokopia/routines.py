"""Fixed sequences for Pokopia, offered to the model as tools. Every recipe
and number comes from guide/ and knowledge/aim-drills.md; each step that
changes the world is checked on the picture, and a routine that cannot
confirm a step stops and says where."""

import importlib.util
from pathlib import Path

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
# Share of changed pixels in front of the character: placed 0.09 to 0.15, not placed at most 0.03.
PLACED = 0.04
STANDING = 130
CONFIRM_ROI = (380, 880, 760, 120)
NOTE_PP_ROI = (700, 440, 540, 240)
BERRY_ROI = (438, 195, 90, 90)


def _pitch(hands, value: int) -> None:
    hands.stick("R", 0.0, -1.0, 1500)
    hands.stick("R", 0.0, 1.0, value)
    hands.wait(400)


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
    for _ in range(2):
        if hands.context() == "bag":
            return
        hands.press("X")
        hands.wait(1300)
    if hands.context() != "bag":
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
    "the mid view. Each block is confirmed on the picture and retried once. "
    "Called again at the end of a line, in the other direction, it puts the "
    "next layer on top (up to layer 2 from the ground; rise first for more).",
    direction=Param(str, choices=("e", "w")),
    count=Param(int, 1, 9),
)
def place_line(hands, direction, count):
    _pitch(hands, PITCH["mid"])
    placed = _line(hands, direction, count)
    return f"placed {placed} going {direction}; now in front of the last one"


def _line(hands, direction: str, count: int) -> int:
    dx = 1.0 if direction == "e" else -1.0
    placed = 0
    for block in range(1, count + 1):
        if block > 1:
            hands.combo("ZL", STEP_MS, stick=("L", dx, 0.0))
            hands.wait(500)
        before = hands.frame()
        hand = _in_hand(hands, before)
        if hand != "one" and hand != "human":
            why = (
                "the quantity is not 1: press DPAD_UP until the prompt reads 1"
                if hand == "many"
                else "nothing is in hand"
            )
            raise RoutineStopped(f"{why}; {placed} placed", step=f"block {block}")
        change = 0.0
        for attempt in (1, 2):
            hands.press("A")
            hands.wait(1300)
            change = checks.changed_fraction(before, hands.frame())
            hands.log(
                routine="place_line",
                block=block,
                attempt=attempt,
                change=round(change, 3),
            )
            if change >= PLACED:
                break
        else:
            raise RoutineStopped(
                f"no block appeared after two presses (picture change {change:.3f}); "
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
    done = 0
    for layer in range(1, layers + 1):
        if layer > 2:
            hands.press("B", RISE_MS)
            hands.wait(500)
        try:
            done += _line(hands, "e" if layer % 2 else "w", width)
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
        hands.press("ZR")
        for _ in range(8):
            hands.wait(500)
            if hands.context() == "placing":
                return "transformed; nothing in hand"
    raise RoutineStopped(
        "the transformation did not start (PP may be empty)", step="ZR"
    )


def _untransform(hands) -> str:
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
    hands.press("R")
    hands.wait(900)
    confirm = hands.template("confirm_untransform.png")
    if checks.label_score(hands.frame(), confirm, CONFIRM_ROI) < SEEN:
        raise RoutineStopped("the confirmation box did not appear", step="R")
    hands.press("A")
    # The change back takes between 1.5 and 3 s.
    for _ in range(10):
        hands.wait(500)
        if hands.context() == "explore":
            return "human form again"
    raise RoutineStopped("did not return to explore", step="confirm")


@routine(
    "End Magnet Rise: empties the hand if needed, R, confirms, ends in explore. "
    "The character may face another way afterwards: call face_away before the "
    "next placing or breaking."
)
def magnet_off(hands):
    return _untransform(hands)


@routine("Magnet Rise: descend until the character is at ground level.")
def ground(hands):
    for _ in range(4):
        hands.press("L", RISE_MS)
    hands.wait(300)
    return "at ground level"


@routine(
    "Magnet Rise: rise by `steps` (about 0.9 layer each). Count from ground "
    "level: call `ground` first when the height matters.",
    steps=Param(int, 1, 8),
)
def rise(hands, steps):
    for _ in range(steps):
        hands.press("B", RISE_MS)
        hands.wait(200)
    hands.wait(300)
    return f"rose {steps} steps"


def _gate(hands) -> tuple[bool, str]:
    """Whether Y may be pressed: the break prompt and the outline of a block
    that stands (not a flat ground cell, not half hidden) are both seen."""
    frame = hands.frame()
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
    if box[3] < STANDING:
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


def _break(hands, step: str) -> None:
    # A note from the break before lingers about 3 s and would pass for this one.
    for _ in range(30):
        if not _note(hands):
            break
        hands.wait(200)
    else:
        raise RoutineStopped("the note of the last pick-up does not go away", step=step)
    ok, why = _gate(hands)
    hands.log(routine="break", step=step, gate=ok, why=why)
    if not ok:
        raise RoutineStopped(f"Y not pressed: {why}", step=step)
    hands.press("Y")
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
    "Y is pressed only when the break prompt and a standing block's outline are "
    "seen, and the pick-up note must follow. It cannot tell your block from one "
    "that was there before: look first. Use the side view for layer 1."
)
def break_front(hands):
    _break(hands, "break")
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
    # From behind, the character hides the lower half of the outline.
    hands.stick("R", 1.0, 0.0, YAW_MS)
    hands.wait(300)
    try:
        for block in range(1, count + 1):
            for _ in range(7):
                if _gate(hands)[0]:
                    break
                hands.press("L", 150)
                hands.wait(500)
            try:
                _break(hands, f"block {block}")
            except RoutineStopped as stop:
                raise RoutineStopped(
                    f"{stop}; {block - 1} broken", step=stop.step
                ) from None
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
        for _ in range(4):
            hands.press("L", RISE_MS)
        for _ in range(layers + 1):
            hands.press("B", RISE_MS)
            hands.wait(200)
        hands.wait(300)
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
        hands.press("A")
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
    if hands.context() == "bag":
        hands.press("B")
        hands.wait(900)
        done.append("closed the bag")
    if hands.context() == "placing":
        done.append(_untransform(hands))
    if hands.context() == "explore" and _prompt(hands, hands.frame(), "spit"):
        hands.press("R")
        hands.wait(700)
        done.append("put the item away")
    return "; ".join(done) or "nothing to do"
