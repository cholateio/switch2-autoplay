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
        facing: <text>            # optional
        confidence: ...           # optional
        by: human                 # optional; the only value until a piece builder exists
    source: <text>                # optional
    unsure: [<text>, ...]         # optional
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
ITEMS_FILE = Path(__file__).with_name("items.yaml")
_TOP = {"name", "size", "axes", "palette", "layers", "pieces", "source", "unsure"}
_LAYER = {"rows", "confidence", "note", "frames"}
_PIECE = {"item", "kind", "layer", "cells", "facing", "confidence", "by", "note"}
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


def _parse_pieces(
    data: dict, width: int, depth: int, count: int, errors: list[str]
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
        if not isinstance(layer, int) or isinstance(layer, bool) or layer < 0:
            errors.append(f"{where}: layer must be a layer index")
            layer = None
        elif layer >= count:
            errors.append(f"{where}: layer {layer} is above the top layer {count - 1}")
        cells = raw.get("cells")
        if isinstance(cells, list):
            for cell in cells:
                inside = (
                    isinstance(cell, list)
                    and len(cell) == 2
                    and all(isinstance(v, int) for v in cell)
                    and 0 <= cell[0] < max(depth, 1)
                    and 0 <= cell[1] < max(width, 1)
                )
                if not inside:
                    errors.append(f"{where}: cell {cell} is not a [row, col] inside")
        elif not isinstance(cells, str) or not cells:
            errors.append(f"{where}: needs `cells`, a list of [row, col]")
            cells = ""
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
    size = data.get("size") if isinstance(data.get("size"), dict) else {}
    dims = {}
    for key in ("width", "depth", "layers"):
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
    pieces = _parse_pieces(data, dims["width"], dims["depth"], len(layers), errors)
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
