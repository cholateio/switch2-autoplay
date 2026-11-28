"""Blueprints for Pokopia: the text format, its checks, the material list and
a local preview page. `ap blueprint` calls `check` and `preview`.

A blueprint is a YAML mapping:

    name: <text>
    size: {width: W, depth: D, layers: N}
    axes: {row0: back, col0: left, layer0: bottom}   # the only convention
    palette: {<one character>: <item name from items.yaml>}
    layers:                       # bottom first
      - rows: [<W characters>, ... D rows]           # "." is an empty cell
        confidence: seen | inferred | guess          # optional
        note: <text>                                 # optional
        frames: [<video second>, ...]                # optional
    pieces:                       # optional: whatever does not fill a cell
      - item: <text>
        kind: panel | door | furniture | plant | decoration | beam | other
        layer: <index>
        cells: [[row, col], ...]  # or a sentence, in a draft
        area: {x: [c0, c1], y: [r0, r1], ring: true}   # instead of cells: a
                                  # rectangle of cells, or only its outline
        facing: <text>            # optional
        confidence: ...           # optional
        by: human                 # optional; the only value until a piece builder exists
    source: <text>                # optional
    unsure: [<text>, ...]         # optional

Instead of `size` and `layers` a blueprint may give construction operations,
which are expanded into the same grid; later operations overwrite earlier ones:

    ops:
      - {op: fill,  item: S, x: [x0, x1], y: [y0, y1], z: [z0, z1]}   # solid box
      - {op: walls, item: S, x: ..., y: ..., z: ...}    # the four sides of a box
      - {op: clear, x: ..., y: ..., z: ...}             # openings
      - {op: roof,  item: S, z: z0, x: ..., y: ..., style: hip, overhang: 0}
      - {op: roof,  item: S, z: z0, overhang: 1, cap: 3,
         parts: [{x: ..., y: ..., style: gable_x}, {x: ..., y: ...}]}

x is the column, y the row and z the layer; ranges are inclusive, a single
number is a range of one, and coordinates may be negative (an overhang): the
grid is the bounding box of the result, and pieces of such a blueprint are
given in the same coordinates. There are at most 100 operations, and a range is
at most 64 long (32 for z). A roof is one block per column, rising one layer
per cell inwards from the edges of its rectangle: from all four (`hip`), from
the two y edges (`gable_x`, the ridge runs along x), from the two x edges
(`gable_y`) or not at all (`flat`). `z` is the layer of its lowest row, `cap`
flattens it above that many layers, and several `parts` join into one roof with
valleys. `overhang` tells how far the rectangle reaches past the walls; a wall
that ends right under the lowest roof row is raised to meet the roof above it.
"""

from collections import Counter
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import yaml

EMPTY = "."
AXES = {"row0": "back", "col0": "left", "layer0": "bottom"}
CONFIDENCE = ("seen", "inferred", "guess")
KINDS = (
    "panel",
    "door",
    "furniture",
    "plant",
    "decoration",
    "beam",
    "other",
)
BUILDERS = ("human",)
# A draft that fails the checks is still drawn, so the drawing must stay bounded.
MAX_SIDE = 64
MAX_LAYERS = 32
MAX_OPS = 100
MAX_CELLS = 2 * MAX_SIDE * MAX_SIDE * MAX_LAYERS
MAX_ROOF_PARTS = 8
ROOF_STYLES = ("hip", "gable_x", "gable_y", "flat")
_BOX = {"op", "item", "x", "y", "z"}
_OPS = {
    "fill": _BOX,
    "walls": _BOX,
    "clear": _BOX - {"item"},
    "roof": _BOX | {"style", "overhang", "cap", "parts"},
}
_PART = {"x", "y", "style"}
ITEMS_FILE = Path(__file__).with_name("items.yaml")
_TOP = {
    "name",
    "size",
    "axes",
    "palette",
    "layers",
    "ops",
    "pieces",
    "source",
    "unsure",
}
_LAYER = {"rows", "confidence", "note", "frames"}
_PIECE = {
    "item",
    "kind",
    "layer",
    "cells",
    "area",
    "facing",
    "confidence",
    "by",
    "note",
}
_FALLBACK = (
    "#e57373",
    "#64b5f6",
    "#ffb74d",
    "#ba68c8",
    "#4db6ac",
    "#a1887f",
    "#dce775",
    "#90a4ae",
    "#f06292",
    "#7986cb",
)


@dataclass
class Layer:
    rows: list[str]
    confidence: str | None = None
    note: str = ""
    frames: list = field(default_factory=list)


@dataclass
class Piece:
    item: str
    kind: str
    layer: int | None
    cells: list | str
    facing: str = ""
    confidence: str | None = None
    by: str = "human"
    note: str = ""


@dataclass
class Blueprint:
    name: str
    width: int
    depth: int
    palette: dict[str, str]
    layers: list[Layer]
    pieces: list[Piece]
    source: str = ""
    unsure: list[str] = field(default_factory=list)
    # Of a blueprint given as operations: how many there were, and the x, y, z
    # of the operations that grid cell [row 0, col 0] of layer 0 stands for.
    ops: int = 0
    origin: tuple[int, int, int] = (0, 0, 0)


@dataclass
class Report:
    """What `check` found. `summary` is the size and the material list."""

    errors: list[str]
    warnings: list[str]
    summary: list[str]


def block_colours() -> dict[str, str]:
    """The blocks a blueprint may name, each with the colour it is drawn in."""
    data = yaml.safe_load(ITEMS_FILE.read_text(encoding="utf-8"))
    return {str(name): str(colour) for name, colour in data["blocks"].items()}


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        # ValueError is the one error `ap blueprint` reports for an unreadable file.
        raise ValueError(f"{path}: expected a mapping")  # noqa: TRY004
    return data


def _parse_layers(data: dict, width: int, depth: int, errors: list[str]) -> list[Layer]:
    raw_layers = data.get("layers")
    if not isinstance(raw_layers, list) or not raw_layers:
        errors.append("layers: the list is empty")
        return []
    if len(raw_layers) > MAX_LAYERS:
        errors.append(f"layers: {len(raw_layers)} is above the limit of {MAX_LAYERS}")
    layers: list[Layer] = []
    for index, raw in enumerate(raw_layers[:MAX_LAYERS]):
        where = f"layer {index}"
        if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
            errors.append(f"{where}: needs `rows`, a list of strings")
            layers.append(Layer(rows=[]))
            continue
        if raw.keys() - _LAYER:
            errors.append(
                f"{where}: unknown keys {sorted(str(k) for k in raw.keys() - _LAYER)}"
            )
        given = raw["rows"]
        if depth and len(given) != depth:
            errors.append(f"{where}: has {len(given)} rows, size.depth is {depth}")
        rows = []
        for number, row in enumerate(given[:MAX_SIDE]):
            row = str(row)
            if width and len(row) != width:
                errors.append(
                    f"{where} row {number}: is {len(row)} long, size.width is {width}"
                )
            rows.append(row[:MAX_SIDE])
        confidence = raw.get("confidence")
        if confidence is not None and confidence not in CONFIDENCE:
            errors.append(f"{where}: confidence must be one of {list(CONFIDENCE)}")
            confidence = None
        frames = raw.get("frames") or []
        layers.append(
            Layer(
                rows=rows,
                confidence=confidence,
                note=str(raw.get("note") or ""),
                frames=frames if isinstance(frames, list) else [frames],
            )
        )
    return layers


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _span(value: object, limit: int, where: str, errors: list[str]) -> range | None:
    pair = [value, value] if _whole(value) else value
    if not (isinstance(pair, list) and len(pair) == 2 and all(map(_whole, pair))):
        errors.append(f"{where}: must be a whole number or [low, high]")
        return None
    low, high = sorted(pair)
    # Bounded so that no operation can cost more than one full grid of work.
    if high - low >= limit:
        errors.append(f"{where}: [{low}, {high}] spans more than {limit}")
        return None
    return range(low, high + 1)


def _roof(op: dict, where: str, cells: dict, errors: list[str]) -> None:
    base = op.get("z")
    if not _whole(base):
        errors.append(f"{where}: z must be the layer of the lowest roof row")
        return
    overhang = op.get("overhang", 0)
    cap = op.get("cap")
    if not _whole(overhang) or not 0 <= overhang < MAX_SIDE:
        errors.append(f"{where}: overhang must be a whole number, 0 or more")
        return
    if cap is not None and (not _whole(cap) or cap < 0):
        errors.append(f"{where}: cap must be a whole number, 0 or more")
        return
    raw_parts = [op]
    if "parts" in op:
        raw_parts = op["parts"]
        if "x" in op or "y" in op:
            errors.append(f"{where}: give x and y on the roof or on its parts")
            return
        if not isinstance(raw_parts, list) or not raw_parts:
            errors.append(f"{where}: parts must be a list of {{x, y}}")
            return
        if len(raw_parts) > MAX_ROOF_PARTS:
            errors.append(f"{where}: more than {MAX_ROOF_PARTS} parts")
            return
    parts = []
    for number, raw in enumerate(raw_parts):
        spot = where if raw is op else f"{where} part {number}"
        if not isinstance(raw, dict):
            errors.append(f"{spot}: must be a mapping with x and y")
            return
        if raw is not op and raw.keys() - _PART:
            unknown = sorted(str(k) for k in raw.keys() - _PART)
            errors.append(f"{spot}: unknown keys {unknown}")
        style = raw.get("style", op.get("style", "hip"))
        if style not in ROOF_STYLES:
            errors.append(f"{spot}: style must be one of {list(ROOF_STYLES)}")
            return
        xs = _span(raw.get("x"), MAX_SIDE, f"{spot} x", errors)
        ys = _span(raw.get("y"), MAX_SIDE, f"{spot} y", errors)
        if xs is None or ys is None:
            return
        parts.append((xs, ys, style))
    rise: dict[tuple[int, int], int] = {}
    for xs, ys, style in parts:
        for x in xs:
            for y in ys:
                from_x = min(x - xs[0], xs[-1] - x)
                from_y = min(y - ys[0], ys[-1] - y)
                height = {
                    "hip": min(from_x, from_y),
                    "gable_x": from_y,
                    "gable_y": from_x,
                    "flat": 0,
                }[style]
                if cap is not None:
                    height = min(height, cap)
                rise[(x, y)] = max(rise.get((x, y), 0), height)
    for (x, y), height in rise.items():
        cells[(x, y, base + height)] = op["item"]
    for xs, ys, _ in parts:
        wall_x = range(xs[0] + overhang, xs[-1] - overhang + 1)
        wall_y = range(ys[0] + overhang, ys[-1] - overhang + 1)
        for x in wall_x:
            for y in wall_y:
                if x not in (wall_x[0], wall_x[-1]) and y not in (
                    wall_y[0],
                    wall_y[-1],
                ):
                    continue
                top = base + rise[(x, y)]
                # Only a wall that reaches the eaves is raised; a floor far
                # below the roof must not grow into a pillar.
                for z in range(top - 1, base - 2, -1):
                    if (x, y, z) in cells:
                        for gap in range(z + 1, top):
                            cells[(x, y, gap)] = cells[(x, y, z)]
                        break


def _expand(data: dict, palette: dict[str, str], errors: list[str]) -> tuple[dict, int]:
    """The cells the operations leave, keyed by (x, y, z), and how many
    operations were read."""
    raw_ops = data.get("ops")
    if not isinstance(raw_ops, list) or not raw_ops:
        errors.append("ops: the list is empty")
        return {}, 0
    if len(raw_ops) > MAX_OPS:
        errors.append(f"ops: {len(raw_ops)} is above the limit of {MAX_OPS}")
        raw_ops = raw_ops[:MAX_OPS]
    cells: dict[tuple[int, int, int], str] = {}
    for index, op in enumerate(raw_ops):
        where = f"op {index}"
        # No valid result holds more cells than one grid; twice that leaves
        # room for what a later `clear` removes and keeps a faulty draft small.
        if len(cells) > MAX_CELLS:
            errors.append(
                f"{where}: more than {MAX_CELLS} blocks before it; this and the "
                "later operations are dropped"
            )
            break
        kind = op.get("op") if isinstance(op, dict) else None
        if not isinstance(kind, str) or kind not in _OPS:
            errors.append(f"{where}: op must be one of {list(_OPS)}")
            continue
        if op.keys() - _OPS[kind]:
            unknown = sorted(str(k) for k in op.keys() - _OPS[kind])
            errors.append(f"{where}: unknown keys {unknown}")
        if kind != "clear":
            op = {**op, "item": str(op.get("item"))}
            if op["item"] not in palette:
                errors.append(f"{where}: item {op['item']!r} is not in the palette")
                continue
        if kind == "roof":
            _roof(op, where, cells, errors)
            continue
        xs = _span(op.get("x"), MAX_SIDE, f"{where} x", errors)
        ys = _span(op.get("y"), MAX_SIDE, f"{where} y", errors)
        zs = _span(op.get("z"), MAX_LAYERS, f"{where} z", errors)
        if xs is None or ys is None or zs is None:
            continue
        for z in zs:
            for y in ys:
                for x in xs:
                    if kind == "clear":
                        cells.pop((x, y, z), None)
                    elif kind == "fill" or x in (xs[0], xs[-1]) or y in (ys[0], ys[-1]):
                        cells[(x, y, z)] = op["item"]
    if not cells:
        errors.append("ops: leave no blocks")
    return cells, len(raw_ops)


def _grid(
    cells: dict, errors: list[str]
) -> tuple[int, int, list[Layer], tuple[int, int, int]]:
    if not cells:
        return 0, 0, [], (0, 0, 0)
    low = [min(cell[axis] for cell in cells) for axis in range(3)]
    spans = [max(cell[axis] for cell in cells) - low[axis] + 1 for axis in range(3)]
    for name, span, limit in zip(
        ("wide", "deep", "layers"), spans, (MAX_SIDE, MAX_SIDE, MAX_LAYERS), strict=True
    ):
        if span > limit:
            errors.append(f"ops: the result is {span} {name}, the limit is {limit}")
    width, depth, count = (
        min(spans[0], MAX_SIDE),
        min(spans[1], MAX_SIDE),
        min(spans[2], MAX_LAYERS),
    )
    layers = [
        Layer(
            rows=[
                "".join(
                    cells.get((col + low[0], row + low[1], level + low[2]), EMPTY)
                    for col in range(width)
                )
                for row in range(depth)
            ]
        )
        for level in range(count)
    ]
    return width, depth, layers, (low[0], low[1], low[2])


def _piece_cells(
    raw: dict, where: str, errors: list[str]
) -> list[list[int]] | str | None:
    """The cells a piece names, in the blueprint's own coordinates."""
    if "area" in raw:
        area = raw["area"]
        if (
            "cells" in raw
            or not isinstance(area, dict)
            or area.keys()
            - {
                "x",
                "y",
                "ring",
            }
        ):
            errors.append(f"{where}: area is {{x, y, ring}} and replaces cells")
            return None
        xs = _span(area.get("x"), MAX_SIDE, f"{where} area x", errors)
        ys = _span(area.get("y"), MAX_SIDE, f"{where} area y", errors)
        if xs is None or ys is None:
            return None
        ring = area.get("ring", False)
        if not isinstance(ring, bool):
            errors.append(f"{where}: area ring must be true or false")
            return None
        return [
            [y, x]
            for y in ys
            for x in xs
            if not ring or x in (xs[0], xs[-1]) or y in (ys[0], ys[-1])
        ]
    cells = raw.get("cells")
    if isinstance(cells, list) or (isinstance(cells, str) and cells):
        return cells
    errors.append(f"{where}: needs `cells`, a list of [row, col]")
    return None


def _parse_pieces(
    data: dict,
    width: int,
    depth: int,
    count: int,
    errors: list[str],
    origin: tuple[int, int, int] = (0, 0, 0),
) -> list[Piece]:
    raw_pieces = data.get("pieces") or []
    if not isinstance(raw_pieces, list):
        errors.append("pieces: must be a list")
        return []
    pieces: list[Piece] = []
    for index, raw in enumerate(raw_pieces):
        where = f"piece {index}"
        if not isinstance(raw, dict) or not isinstance(raw.get("item"), str):
            errors.append(f"{where}: needs `item`, a name")
            continue
        if raw.keys() - _PIECE:
            errors.append(
                f"{where}: unknown keys {sorted(str(k) for k in raw.keys() - _PIECE)}"
            )
        kind = raw.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}: kind must be one of {list(KINDS)}")
            kind = "other"
        layer = raw.get("layer")
        if not _whole(layer) or layer < origin[2]:
            errors.append(f"{where}: layer must be a layer index")
            layer = None
        elif layer - origin[2] >= count:
            errors.append(
                f"{where}: layer {layer} is above the top layer {count - 1 + origin[2]}"
            )
        else:
            layer -= origin[2]
        named = _piece_cells(raw, where, errors)
        cells: list | str = ""
        if isinstance(named, str):
            cells = named
        elif named is not None:
            cells = []
            outside = []
            for cell in named[: MAX_SIDE * MAX_SIDE]:
                if not (
                    isinstance(cell, list) and len(cell) == 2 and all(map(_whole, cell))
                ):
                    outside.append(cell)
                    continue
                row, col = cell[0] - origin[1], cell[1] - origin[0]
                if 0 <= row < max(depth, 1) and 0 <= col < max(width, 1):
                    cells.append([row, col])
                else:
                    outside.append(cell)
            for cell in outside[:5]:
                errors.append(f"{where}: cell {cell} is not a [row, col] inside")
            if len(outside) > 5:
                errors.append(f"{where}: {len(outside) - 5} more cell(s) not inside")
        by = raw.get("by", "human")
        if by not in BUILDERS:
            errors.append(f"{where}: by must be one of {list(BUILDERS)}")
            by = "human"
        confidence = raw.get("confidence")
        if confidence is not None and confidence not in CONFIDENCE:
            errors.append(f"{where}: confidence must be one of {list(CONFIDENCE)}")
            confidence = None
        pieces.append(
            Piece(
                item=raw["item"],
                kind=kind,
                layer=layer,
                cells=cells,
                facing=str(raw.get("facing") or ""),
                confidence=confidence,
                by=by,
                note=str(raw.get("note") or ""),
            )
        )
    return pieces


def parse(data: dict) -> tuple[Blueprint, list[str]]:
    """Read a blueprint mapping leniently: the errors are returned beside
    whatever could be read, so that a faulty draft can still be drawn."""
    errors: list[str] = []
    if data.keys() - _TOP:
        errors.append(f"unknown keys {sorted(str(k) for k in data.keys() - _TOP)}")
    from_ops = "ops" in data
    size = data.get("size") if isinstance(data.get("size"), dict) else {}
    dims = {"width": 0, "depth": 0, "layers": 0}
    for key in ("size", "layers") if from_ops else ():
        if key in data:
            errors.append(f"{key}: not allowed beside ops, which set it")
    for key in () if from_ops else tuple(dims):
        value = size.get(key)
        if not _positive(value):
            errors.append(f"size.{key}: must be a whole number above zero")
            value = 0
        limit = MAX_LAYERS if key == "layers" else MAX_SIDE
        if value > limit:
            errors.append(f"size.{key}: {value} is above the limit of {limit}")
            value = limit
        dims[key] = value
    if data.get("axes") != AXES:
        errors.append(f"axes: must be exactly {AXES}")
    palette: dict[str, str] = {}
    raw_palette = data.get("palette")
    if not isinstance(raw_palette, dict) or not raw_palette:
        errors.append("palette: must map symbols to item names")
        raw_palette = {}
    known = block_colours()
    for symbol, item in raw_palette.items():
        symbol = str(symbol)
        if len(symbol) != 1 or symbol == EMPTY or symbol.isspace():
            errors.append(f"palette: {symbol!r} must be one character, not {EMPTY!r}")
            continue
        palette[symbol] = str(item)
        if str(item) not in known:
            errors.append(
                f"palette: {symbol!r} names {item!r}, which is not in the item list"
            )
    origin, op_count = (0, 0, 0), 0
    if from_ops:
        cells, op_count = _expand(data, palette, errors)
        dims["width"], dims["depth"], layers, origin = _grid(cells, errors)
    else:
        layers = _parse_layers(data, dims["width"], dims["depth"], errors)
    if layers and dims["layers"] and len(layers) != dims["layers"]:
        errors.append(
            f"layers: there are {len(layers)}, size.layers is {dims['layers']}"
        )
    for index, layer in enumerate(layers):
        for number, row in enumerate(layer.rows):
            for symbol in sorted(set(row) - set(palette) - {EMPTY}):
                errors.append(
                    f"layer {index} row {number}: symbol {symbol!r} is not in "
                    "the palette"
                )
    pieces = _parse_pieces(
        data, dims["width"], dims["depth"], len(layers), errors, origin
    )
    unsure = data.get("unsure") or []
    blueprint = Blueprint(
        name=str(data.get("name") or ""),
        width=dims["width"],
        depth=dims["depth"],
        palette=palette,
        layers=layers,
        pieces=pieces,
        source=str(data.get("source") or ""),
        unsure=[str(line) for line in unsure] if isinstance(unsure, list) else [],
        ops=op_count,
        origin=origin,
    )
    if not blueprint.name:
        errors.append("name: is missing")
    return blueprint, errors


def _cell(blueprint: Blueprint, layer: int, row: int, col: int) -> str:
    rows = blueprint.layers[layer].rows
    if 0 <= row < len(rows) and 0 <= col < len(rows[row]):
        return rows[row][col]
    return EMPTY


def materials(blueprint: Blueprint) -> tuple[Counter, list[Counter]]:
    """Blocks per item over the whole blueprint, and layer by layer."""
    per_layer = []
    for layer in blueprint.layers:
        counts: Counter = Counter()
        for row in layer.rows:
            for symbol in row:
                if symbol != EMPTY:
                    counts[blueprint.palette.get(symbol, f"?{symbol}")] += 1
        per_layer.append(counts)
    total: Counter = Counter()
    for counts in per_layer:
        total.update(counts)
    return total, per_layer


def warnings_of(blueprint: Blueprint) -> list[str]:
    """What a person should look at but that does not make a blueprint wrong."""
    found: list[str] = []
    for index, layer in enumerate(blueprint.layers):
        cells = [
            (r, c)
            for r, row in enumerate(layer.rows)
            for c, symbol in enumerate(row)
            if symbol != EMPTY
        ]
        if not cells:
            found.append(f"layer {index}: has no blocks")
        floating = [
            cell
            for cell in cells
            if index and _cell(blueprint, index - 1, *cell) == EMPTY
        ]
        if floating:
            shown = ", ".join(f"[{r}, {c}]" for r, c in floating[:5])
            more = " ..." if len(floating) > 5 else ""
            found.append(
                f"layer {index}: {len(floating)} block(s) with nothing beneath: "
                f"{shown}{more}"
            )
    by_human = sum(1 for piece in blueprint.pieces if piece.by == "human")
    if by_human:
        found.append(f"pieces: {by_human} left to a human; the machine skips them")
    loose = sum(1 for piece in blueprint.pieces if isinstance(piece.cells, str))
    if loose:
        found.append(f"pieces: {loose} placed by a sentence, not by cells")
    return found


def _summary(blueprint: Blueprint) -> list[str]:
    total, per_layer = materials(blueprint)
    head = (
        f"{blueprint.name}: {blueprint.width} wide, {blueprint.depth} deep, "
        f"{len(blueprint.layers)} layer(s), {sum(total.values())} block(s), "
        f"{len(blueprint.pieces)} piece(s)"
    )
    lines = [head]
    if blueprint.ops:
        x, y, z = blueprint.origin
        lines.append(
            f"  from {blueprint.ops} operation(s); row 0, col 0 of layer 0 is "
            f"their x={x}, y={y}, z={z}"
        )
    lines += [f"  {item}: {count}" for item, count in total.items()]
    for index, counts in enumerate(per_layer):
        detail = ", ".join(f"{item} {count}" for item, count in counts.items())
        lines.append(f"  layer {index}: {sum(counts.values())} ({detail or 'empty'})")
    return lines


def check(path: Path) -> Report:
    """Check the blueprint file at `path`. Raises ValueError when it cannot be
    read as a mapping at all."""
    blueprint, errors = parse(_read(path))
    return Report(errors, warnings_of(blueprint), _summary(blueprint))


def _colours(blueprint: Blueprint) -> dict[str, str]:
    known = block_colours()
    spare = iter(_FALLBACK * (len(blueprint.palette) // len(_FALLBACK) + 1))
    symbols = dict(blueprint.palette)
    for layer in blueprint.layers:
        for row in layer.rows:
            for symbol in row:
                if symbol != EMPTY:
                    symbols.setdefault(symbol, f"?{symbol}")
    return {symbol: known.get(item) or next(spare) for symbol, item in symbols.items()}


def _shade(colour: str, factor: float) -> str:
    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{min(255, int(v * factor)):02x}" for v in (red, green, blue))


def _isometric(blueprint: Blueprint, colours: dict[str, str]) -> str:
    half, rise = 14, 14
    cubes = [
        (col, row, level, symbol)
        for level, layer in enumerate(blueprint.layers)
        for row, line in enumerate(layer.rows)
        for col, symbol in enumerate(line)
        if symbol != EMPTY
    ]
    if not cubes:
        return "<p>No blocks to draw.</p>"
    count = len(blueprint.layers)
    taken = {(u, v, z) for u, v, z, _ in cubes}
    # The view direction is (1, 1, 1): a cube further along it hides the others.
    cubes.sort(key=lambda cube: (cube[0] + cube[1] + cube[2], cube[2]))
    span_v = max(v for _, v, _, _ in cubes) + 1
    span_u = max(u for u, _, _, _ in cubes) + 1
    left = span_v * half + 4
    top = count * rise + 4
    width = (span_u + span_v) * half + 8
    height = (span_u + span_v) * half // 2 + count * rise + 12
    shapes = []
    for u, v, z, symbol in cubes:
        x = left + (u - v) * half
        y = top + (u + v) * half // 2 - z * rise
        colour = colours[symbol]
        h = half // 2
        if (u, v, z + 1) not in taken:
            points = f"{x},{y - rise} {x + half},{y - rise + h} {x},{y - rise + half} {x - half},{y - rise + h}"
            shapes.append(f'<polygon points="{points}" fill="{_shade(colour, 1.12)}"/>')
        if (u, v + 1, z) not in taken:
            points = f"{x - half},{y - rise + h} {x},{y - rise + half} {x},{y + half} {x - half},{y + h}"
            shapes.append(f'<polygon points="{points}" fill="{_shade(colour, 0.85)}"/>')
        if (u + 1, v, z) not in taken:
            points = f"{x},{y - rise + half} {x + half},{y - rise + h} {x + half},{y + h} {x},{y + half}"
            shapes.append(f'<polygon points="{points}" fill="{_shade(colour, 0.68)}"/>')
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{min(width * 2, 900)}" '
        'stroke="#3338" stroke-width="0.6" stroke-linejoin="round">'
        + "".join(shapes)
        + "</svg>"
    )


def _plan(blueprint: Blueprint, index: int, colours: dict[str, str]) -> str:
    layer = blueprint.layers[index]
    width = max([blueprint.width, *(len(row) for row in layer.rows)])
    head = "".join(f"<th>{col}</th>" for col in range(width))
    body = []
    for number, row in enumerate(layer.rows):
        cells = []
        for col in range(width):
            symbol = row[col] if col < len(row) else None
            if symbol is None:
                cells.append('<td class="gap"></td>')
            elif symbol == EMPTY:
                cells.append("<td></td>")
            else:
                colour = colours.get(symbol, "#ffffff")
                cells.append(f'<td style="background:{colour}">{escape(symbol)}</td>')
        body.append(f"<tr><th>{number}</th>{''.join(cells)}</tr>")
    badge = layer.confidence or "no confidence given"
    frames = (
        f" · frames {escape(', '.join(str(f) for f in layer.frames))}"
        if layer.frames
        else ""
    )
    return (
        f'<section class="layer"><h3>Layer {index} '
        f'<span class="badge {escape(layer.confidence or "none")}">{escape(badge)}'
        f"</span></h3><p>{escape(layer.note)}{frames}</p>"
        f'<table class="plan"><tr><th></th>{head}</tr>{"".join(body)}</table>'
        "</section>"
    )


_STYLE = """
body{font:15px/1.5 system-ui,sans-serif;margin:24px;color:#222;background:#fafafa}
h1{margin:0 0 4px}h2{margin-top:28px;border-bottom:1px solid #ccc}
table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:2px 8px}
.plan td{width:22px;height:22px;padding:0;text-align:center;font:12px monospace}
.plan th{border:none;color:#888;font:11px monospace;padding:0 4px}
.plan td.gap{background:repeating-linear-gradient(45deg,#f88,#f88 3px,#fff 3px,#fff 6px)}
.layers{display:flex;flex-wrap:wrap;gap:20px}.layer h3{margin:0}.layer p{margin:2px 0 6px;max-width:420px;color:#555}
.badge{font-size:12px;padding:1px 8px;border-radius:10px;background:#ddd}
.badge.seen{background:#c8e6c9}.badge.inferred{background:#fff3c4}.badge.guess{background:#ffcdd2}
.errors{background:#ffebee;border:1px solid #e57373;padding:8px 16px}
.warnings{background:#fff8e1;border:1px solid #ffca28;padding:8px 16px}
.swatch{display:inline-block;width:14px;height:14px;border:1px solid #666;vertical-align:middle}
"""


def _list(title: str, css: str, lines: list[str]) -> str:
    if not lines:
        return ""
    items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    return f'<div class="{css}"><b>{title} ({len(lines)})</b><ul>{items}</ul></div>'


def preview(path: Path) -> str:
    """One self-contained HTML page for the blueprint at `path`, with its
    errors and warnings at the top; it needs nothing from the network. Raises
    ValueError when the file cannot be read as a mapping at all."""
    blueprint, errors = parse(_read(path))
    colours = _colours(blueprint)
    total, per_layer = materials(blueprint)
    symbol_of = {item: symbol for symbol, item in blueprint.palette.items()}
    material_rows = "".join(
        f'<tr><td><span class="swatch" style="background:'
        f'{colours.get(symbol_of.get(item, ""), "#fff")}"></span> '
        f"{escape(symbol_of.get(item, '?'))}</td><td>{escape(item)}</td>"
        f"<td>{count}</td>"
        + "".join(f"<td>{counts.get(item, 0) or ''}</td>" for counts in per_layer)
        + "</tr>"
        for item, count in total.items()
    )
    layer_heads = "".join(f"<th>L{i}</th>" for i in range(len(per_layer)))
    piece_rows = "".join(
        f"<tr><td>{escape(p.item)}</td><td>{escape(p.kind)}</td>"
        f"<td>{'' if p.layer is None else p.layer}</td>"
        f"<td>{escape(p.cells if isinstance(p.cells, str) else str(p.cells))}</td>"
        f"<td>{escape(p.facing)}</td><td>{escape(p.confidence or '')}</td>"
        f"<td>{escape(p.by)}</td></tr>"
        for p in blueprint.pieces
    )
    pieces = (
        "<h2>Pieces</h2><table><tr><th>item</th><th>kind</th><th>layer</th>"
        f"<th>cells</th><th>facing</th><th>confidence</th><th>by</th></tr>{piece_rows}"
        "</table>"
        if blueprint.pieces
        else ""
    )
    plans = "".join(_plan(blueprint, i, colours) for i in range(len(blueprint.layers)))
    title = escape(blueprint.name or Path(path).name)
    return (
        f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        f"<title>{title}</title><style>{_STYLE}</style></head><body>"
        f"<h1>{title}</h1><p>{blueprint.width} wide × {blueprint.depth} deep × "
        f"{len(blueprint.layers)} layers · row 0 is the back, column 0 the left, "
        f"layer 0 the bottom. {escape(blueprint.source)}</p>"
        + _list("Errors", "errors", errors)
        + _list("Warnings", "warnings", warnings_of(blueprint))
        + f"<h2>Shape</h2>{_isometric(blueprint, colours)}"
        + "<h2>Materials</h2><table><tr><th></th><th>item</th><th>total</th>"
        + f"{layer_heads}</tr>{material_rows}</table>"
        + f'<h2>Layers, bottom first</h2><div class="layers">{plans}</div>'
        + pieces
        + _list("Not sure about", "warnings", blueprint.unsure)
        + "</body></html>"
    )
