"""The Pokopia blueprint format: checks, material counts and the preview page."""

from collections import Counter
from pathlib import Path

import pytest
import yaml

from autoplay.cli import main
from autoplay.core.tools.blueprint import BlueprintToolError, find_pack, load_module

PACK = Path(__file__).parents[2] / "game_packs" / "pokopia"
SAMPLES = PACK / "blueprints"
bp = load_module(PACK)

GRASS, STONE = "草原的草皮", "石塊"


def corner_wall() -> dict:
    return yaml.safe_load((SAMPLES / "corner-wall.yaml").read_text(encoding="utf-8"))


def write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "bp.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "total", "per_layer"),
    [
        ("row", {GRASS: 5}, [{GRASS: 5}]),
        ("wall", {STONE: 12}, [{STONE: 4}, {STONE: 4}, {STONE: 4}]),
        (
            "corner-wall",
            {GRASS: 25, STONE: 10},
            [{GRASS: 25}, {STONE: 5}, {STONE: 5}],
        ),
    ],
)
def test_samples_pass_and_count_their_materials(name, total, per_layer):
    path = SAMPLES / f"{name}.yaml"
    report = bp.check(path)
    assert report.errors == []
    assert report.warnings == []
    blueprint, errors = bp.parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert errors == []
    counted, layers = bp.materials(blueprint)
    assert counted == Counter(total)
    assert layers == [Counter(layer) for layer in per_layer]


def _short_row(data):
    data["layers"][1]["rows"][3] = "...."


def _unknown_symbol(data):
    data["layers"][2]["rows"][0] = "SSXSS"


def _unknown_item(data):
    data["palette"]["S"] = "月亮石磚"


def _layer_count(data):
    data["size"]["layers"] = 4


def _no_layers(data):
    data["layers"] = []


def _zero_size(data):
    data["size"]["width"] = 0


@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        (_short_row, "layer 1 row 3: is 4 long, size.width is 5"),
        (_unknown_symbol, "layer 2 row 0: symbol 'X' is not in the palette"),
        (_unknown_item, "palette: 'S' names '月亮石磚', which is not in the item list"),
        (_layer_count, "layers: there are 3, size.layers is 4"),
        (_no_layers, "layers: the list is empty"),
        (_zero_size, "size.width: must be a whole number above zero"),
    ],
)
def test_a_wrong_blueprint_is_refused_with_the_place(tmp_path, damage, expected):
    data = corner_wall()
    damage(data)
    path = write(tmp_path, data)
    assert expected in bp.check(path).errors
    assert main(["blueprint", "check", str(path), "--pack", str(PACK)]) == 1


def test_wrong_row_count_names_the_layer(tmp_path):
    data = corner_wall()
    data["layers"][1]["rows"].pop()
    assert (
        "layer 1: has 4 rows, size.depth is 5" in bp.check(write(tmp_path, data)).errors
    )


def test_other_axes_are_refused(tmp_path):
    data = corner_wall()
    data["axes"]["row0"] = "front"
    assert any(e.startswith("axes:") for e in bp.check(write(tmp_path, data)).errors)


def test_a_floating_block_is_a_warning_not_an_error(tmp_path):
    data = corner_wall()
    data["layers"][2]["rows"][4] = "..S.."
    report = bp.check(write(tmp_path, data))
    assert report.errors == []
    assert report.warnings == ["layer 2: 1 block(s) with nothing beneath: [4, 2]"]


def test_pieces_default_to_a_human_and_are_reported(tmp_path):
    data = corner_wall()
    data["pieces"] = [
        {
            "item": "木門",
            "kind": "door",
            "layer": 1,
            "cells": [[4, 2]],
            "facing": "front",
        },
        {
            "item": "花盆",
            "kind": "plant",
            "layer": 1,
            "cells": [[2, 2]],
        },
    ]
    blueprint, errors = bp.parse(data)
    assert errors == []
    assert [piece.by for piece in blueprint.pieces] == ["human", "human"]
    report = bp.check(write(tmp_path, data))
    assert report.errors == []
    assert report.warnings == ["pieces: 2 left to a human; the machine skips them"]


@pytest.mark.parametrize(
    ("piece", "expected"),
    [
        ({"item": "門", "kind": "gate", "layer": 1, "cells": [[0, 0]]}, "kind must be"),
        (
            {"item": "門", "kind": "door", "layer": 3, "cells": [[0, 0]]},
            "above the top",
        ),
        ({"item": "門", "kind": "door", "layer": 1, "cells": [[5, 0]]}, "cell [5, 0]"),
        ({"item": "門", "kind": "door", "layer": 1}, "needs `cells`"),
        (
            {"item": "門", "kind": "door", "layer": 1, "cells": [[0, 0]], "by": "cat"},
            "by must be",
        ),
    ],
)
def test_a_wrong_piece_is_refused(tmp_path, piece, expected):
    data = corner_wall()
    data["pieces"] = [piece]
    errors = bp.check(write(tmp_path, data)).errors
    assert len(errors) == 1
    assert errors[0].startswith("piece 0:")
    assert expected in errors[0]


def test_preview_is_one_page_without_outside_requests():
    page = bp.preview(SAMPLES / "corner-wall.yaml")
    assert page.startswith("<!doctype html>")
    assert "http" not in page
    assert "<script" not in page
    assert "<svg" in page
    assert page.count('<section class="layer">') == 3
    assert GRASS in page
    assert STONE in page


def test_isometric_draws_only_uncovered_faces():
    data = yaml.safe_load((SAMPLES / "wall.yaml").read_text(encoding="utf-8"))
    blueprint, _ = bp.parse(data)
    drawing = bp._isometric(blueprint, bp._colours(blueprint))
    # A 4 by 1 wall of 3 layers: 4 tops, 12 front faces, 3 right-hand faces.
    assert drawing.count("<polygon") == 19


def test_preview_still_draws_a_draft_that_fails_the_checks(tmp_path):
    data = corner_wall()
    data["palette"]["S"] = "white wall <b>"
    data["layers"][1]["rows"][0] = "SSS"
    data["layers"][1]["confidence"] = "guess"
    data["pieces"] = [
        {"item": "glass door", "kind": "door", "layer": 1, "cells": "front middle"}
    ]
    data["unsure"] = ["roof material"]
    path = write(tmp_path, data)
    page = bp.preview(path)
    assert "Errors (2)" in page
    assert "which is not in the item list" in page
    assert "white wall &lt;b&gt;" in page
    assert "<b>'" not in page
    assert 'class="badge guess"' in page
    assert "front middle" in page
    assert "roof material" in page
    out = tmp_path / "page.html"
    assert (
        main(["blueprint", "preview", str(path), "--pack", str(PACK), "-o", str(out)])
        == 0
    )
    assert out.read_text(encoding="utf-8") == page


def test_unreadable_file_is_a_value_error(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a mapping"):
        bp.check(path)
    assert main(["blueprint", "check", str(path), "--pack", str(PACK)]) == 1
    assert (
        main(["blueprint", "check", str(tmp_path / "none.yaml"), "--pack", str(PACK)])
        == 1
    )


def test_cli_finds_the_pack_of_a_sample(capsys):
    assert find_pack(SAMPLES / "row.yaml") == PACK.resolve()
    assert main(["blueprint", "check", str(SAMPLES / "row.yaml")]) == 0
    out = capsys.readouterr().out
    assert "row: 5 wide, 1 deep, 1 layer(s), 5 block(s)" in out
    assert out.rstrip().endswith("OK: 0 warning(s)")


def test_cli_outside_a_pack_asks_for_one(tmp_path, capsys):
    path = write(tmp_path, corner_wall())
    with pytest.raises(BlueprintToolError):
        find_pack(path)
    assert main(["blueprint", "check", str(path)]) == 1
    assert "--pack" in capsys.readouterr().err


def test_the_shell_works_with_any_pack_module(tmp_path, capsys):
    pack = tmp_path / "other"
    pack.mkdir()
    (pack / "manifest.yaml").write_text("name: other\n", encoding="utf-8")
    (pack / "blueprint.py").write_text(
        "from types import SimpleNamespace\n"
        "def check(path):\n"
        "    return SimpleNamespace(errors=[], warnings=['w'], summary=['s'])\n"
        "def preview(path):\n"
        "    return '<p>page</p>'\n",
        encoding="utf-8",
    )
    plan = pack / "plan.txt"
    plan.write_text("anything", encoding="utf-8")
    assert main(["blueprint", "check", str(plan)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "s",
        "warning: w",
        "OK: 1 warning(s)",
    ]
    assert main(["blueprint", "preview", str(plan)]) == 0
    assert (pack / "plan.preview.html").read_text(encoding="utf-8") == "<p>page</p>"


def test_a_pack_without_a_blueprint_module_is_reported(tmp_path, capsys):
    (tmp_path / "manifest.yaml").write_text("name: bare\n", encoding="utf-8")
    plan = tmp_path / "plan.yaml"
    plan.write_text("a: 1\n", encoding="utf-8")
    assert main(["blueprint", "check", str(plan)]) == 1
    assert "has no blueprint.py" in capsys.readouterr().err


def test_a_piece_for_the_machine_is_refused(tmp_path):
    data = corner_wall()
    data["pieces"] = [
        {
            "item": "花盆",
            "kind": "plant",
            "layer": 1,
            "cells": [[2, 2]],
            "by": "machine",
        }
    ]
    assert bp.check(write(tmp_path, data)).errors == [
        "piece 0: by must be one of ['human']"
    ]


def test_stairs_are_not_a_piece_kind(tmp_path):
    data = corner_wall()
    data["pieces"] = [{"item": "石階", "kind": "stairs", "layer": 1, "cells": [[2, 2]]}]
    errors = bp.check(write(tmp_path, data)).errors
    assert len(errors) == 1
    assert errors[0].startswith("piece 0: kind must be one of")


def test_unknown_keys_of_mixed_types_are_reported_not_raised():
    data = corner_wall()
    data["layers"][0][7] = "x"
    data["layers"][0]["extra"] = "y"
    data["pieces"] = [
        {
            "item": "花盆",
            "kind": "plant",
            "layer": 1,
            "cells": [[2, 2]],
            3: "x",
            "z": "y",
        }
    ]
    _, errors = bp.parse(data)
    assert errors == [
        "layer 0: unknown keys ['7', 'extra']",
        "piece 0: unknown keys ['3', 'z']",
    ]


def test_an_oversized_draft_is_refused_and_drawn_bounded(tmp_path):
    data = corner_wall()
    data["size"]["width"] = 10_000_000
    path = write(tmp_path, data)
    assert "size.width: 10000000 is above the limit of 64" in bp.check(path).errors
    assert len(bp.preview(path)) < 2_000_000


def test_oversized_content_is_cut_for_the_drawing(tmp_path):
    data = corner_wall()
    data["size"] = {"width": 1000, "depth": 200, "layers": 40}
    data["layers"] = [{"rows": ["G" * 1000] * 200}] * 40
    blueprint, errors = bp.parse(data)
    assert "layers: 40 is above the limit of 32" in errors
    assert len(blueprint.layers) == 32
    assert all(len(layer.rows) == 64 for layer in blueprint.layers)
    assert all(len(row) == 64 for layer in blueprint.layers for row in layer.rows)
    assert len(bp.preview(write(tmp_path, data))) < 20_000_000


def from_ops(*ops, pieces=None) -> dict:
    data = {
        "name": "ops",
        "axes": {"row0": "back", "col0": "left", "layer0": "bottom"},
        "palette": {"G": GRASS, "S": STONE},
        "ops": list(ops),
    }
    if pieces is not None:
        data["pieces"] = pieces
    return data


def rows_of(data: dict) -> list[list[str]]:
    blueprint, errors = bp.parse(data)
    assert errors == []
    return [layer.rows for layer in blueprint.layers]


def test_the_ops_sample_expands_to_a_hut_with_a_closed_gable():
    path = SAMPLES / "hut.yaml"
    report = bp.check(path)
    assert report.errors == []
    assert (
        report.summary[1]
        == "  from 4 operation(s); row 0, col 0 of layer 0 is their x=-1, y=-1, z=0"
    )
    layers = rows_of(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert layers[1] == [
        ".......",
        ".SSSSS.",
        ".S...S.",
        ".S...S.",
        ".SS.SS.",
        ".......",
    ]
    # The eaves hang one cell out at the wall-top layer; the end walls are
    # raised under the slope so that the gable is closed.
    assert layers[3][0] == layers[3][5] == "SSSSSSS"
    assert layers[4] == [
        ".......",
        "SSSSSSS",
        ".S...S.",
        ".S...S.",
        "SSSSSSS",
        ".......",
    ]
    assert layers[5][2] == layers[5][3] == "SSSSSSS"
    assert "<svg" in bp.preview(path)


def test_fill_walls_and_clear_overwrite_in_order():
    layers = rows_of(
        from_ops(
            {"op": "fill", "item": "G", "x": [0, 3], "y": [0, 2], "z": 0},
            {"op": "walls", "item": "S", "x": [3, 0], "y": [0, 2], "z": [0, 1]},
            {"op": "clear", "x": 1, "y": 2, "z": [0, 1]},
        )
    )
    assert layers == [["SSSS", "SGGS", "S.SS"], ["SSSS", "S..S", "S.SS"]]


def test_a_hip_roof_steps_in_on_four_sides_and_a_cap_flattens_it():
    hip = {"op": "roof", "item": "S", "z": 0, "x": [0, 4], "y": [0, 4]}
    assert rows_of(from_ops(hip)) == [
        ["SSSSS", "S...S", "S...S", "S...S", "SSSSS"],
        [".....", ".SSS.", ".S.S.", ".SSS.", "....."],
        [".....", ".....", "..S..", ".....", "....."],
    ]
    assert rows_of(from_ops({**hip, "cap": 1}))[1:] == [
        [".....", ".SSS.", ".SSS.", ".SSS.", "....."],
    ]
    assert rows_of(from_ops({**hip, "style": "flat"})) == [["SSSSS"] * 5]


def test_roof_parts_join_into_an_l_with_a_valley():
    layers = rows_of(
        from_ops(
            {
                "op": "roof",
                "item": "S",
                "z": 0,
                "parts": [
                    {"x": [0, 5], "y": [0, 2], "style": "gable_x"},
                    {"x": [0, 2], "y": [0, 5], "style": "gable_y"},
                ],
            }
        )
    )
    # One block per column: the two ridges meet and neither wing cuts the other.
    assert layers == [
        ["S.SSSS", "......", "S.SSSS", "S.S...", "S.S...", "S.S..."],
        [".S....", "SSSSSS", ".S....", ".S....", ".S....", ".S...."],
    ]


def test_only_a_wall_that_reaches_the_eaves_is_raised():
    layers = rows_of(
        from_ops(
            {"op": "fill", "item": "G", "x": [0, 4], "y": [0, 2], "z": 0},
            {"op": "fill", "item": "G", "x": 0, "y": [0, 2], "z": [1, 2]},
            {"op": "roof", "item": "S", "z": 3, "x": [0, 4], "y": [0, 2]}
            | {"style": "gable_x"},
        )
    )
    # Column x=0 reaches layer 2 and grows to the ridge; x=4 stops at the floor.
    assert layers[3] == ["SSSSS", "G....", "SSSSS"]
    assert layers[4] == [".....", "SSSSS", "....."]
    assert layers[1] == layers[2] == ["G....", "G....", "G...."]


def test_pieces_of_an_ops_blueprint_use_the_ops_coordinates():
    data = from_ops(
        {"op": "fill", "item": "G", "x": [-1, 3], "y": [-2, 1], "z": [2, 3]},
        pieces=[
            {"item": "木門", "kind": "door", "layer": 3, "cells": [[-2, -1], [1, 3]]},
            {
                "item": "木柵欄",
                "kind": "panel",
                "layer": 3,
                "area": {"x": [-1, 1], "y": [-2, 0], "ring": True},
            },
        ],
    )
    blueprint, errors = bp.parse(data)
    assert errors == []
    assert blueprint.origin == (-1, -2, 2)
    assert [piece.layer for piece in blueprint.pieces] == [1, 1]
    assert blueprint.pieces[0].cells == [[0, 0], [3, 4]]
    assert blueprint.pieces[1].cells == [
        [0, 0],
        [0, 1],
        [0, 2],
        [1, 0],
        [1, 2],
        [2, 0],
        [2, 1],
        [2, 2],
    ]
    data["pieces"][0]["cells"] = [[2, 0]]
    data["pieces"][1]["layer"] = 1
    assert bp.parse(data)[1] == [
        "piece 0: cell [2, 0] is not a [row, col] inside",
        "piece 1: layer must be a layer index",
    ]


def test_an_area_names_a_rectangle_of_cells_in_a_layered_blueprint():
    data = corner_wall()
    data["pieces"] = [
        {"item": "小麥", "kind": "plant", "layer": 1, "area": {"x": [1, 3], "y": 2}}
    ]
    blueprint, errors = bp.parse(data)
    assert errors == []
    assert blueprint.pieces[0].cells == [[2, 1], [2, 2], [2, 3]]
    data["pieces"][0]["cells"] = [[0, 0]]
    assert bp.parse(data)[1] == ["piece 0: area is {x, y, ring} and replaces cells"]


BOX = {"op": "fill", "item": "S", "x": [0, 1], "y": [0, 1], "z": 0}


@pytest.mark.parametrize(
    ("ops", "expected"),
    [
        ([], "ops: the list is empty"),
        ([{**BOX, "op": "dig"}], "op 0: op must be one of"),
        ([BOX, {**BOX, "item": "X"}], "op 1: item 'X' is not in the palette"),
        ([{**BOX, "x": [0, "a"]}], "op 0 x: must be a whole number or [low, high]"),
        ([{**BOX, "x": [0, 64]}], "op 0 x: [0, 64] spans more than 64"),
        ([{**BOX, "z": [-1, 31]}], "op 0 z: [-1, 31] spans more than 32"),
        ([{**BOX, "depth": 2}], "op 0: unknown keys ['depth']"),
        ([{**BOX, "op": "clear", "item": "S"}], "op 0: unknown keys ['item']"),
        ([{"op": "clear", "x": 0, "y": 0, "z": 0}], "ops: leave no blocks"),
        ([{**BOX, "op": "roof", "style": "dome"}], "op 0: style must be one of"),
        ([{**BOX, "op": "roof", "z": [0, 1]}], "op 0: z must be the layer of"),
        (
            [{**BOX, "op": "roof", "parts": [{"x": 0, "y": 0}]}],
            "op 0: give x and y on the roof or on its parts",
        ),
        (
            [{"op": "roof", "item": "S", "z": 0, "parts": [{"x": 0}]}],
            "op 0 part 0 y: must be a whole number or [low, high]",
        ),
        ([BOX] * 101, "ops: 101 is above the limit of 100"),
        (
            [BOX, {**BOX, "x": [100, 101]}],
            "ops: the result is 102 wide, the limit is 64",
        ),
    ],
)
def test_a_wrong_operation_is_refused_with_the_place(tmp_path, ops, expected):
    errors = bp.check(write(tmp_path, from_ops(*ops))).errors
    assert any(error.startswith(expected) for error in errors), errors


def test_ops_replace_size_and_layers():
    data = from_ops(BOX)
    data["size"] = {"width": 2, "depth": 2, "layers": 1}
    data["layers"] = [{"rows": ["SS", "SS"]}]
    blueprint, errors = bp.parse(data)
    assert errors == [
        "size: not allowed beside ops, which set it",
        "layers: not allowed beside ops, which set it",
    ]
    assert (blueprint.width, blueprint.depth, len(blueprint.layers)) == (2, 2, 1)


def test_ops_that_spread_too_far_are_refused_and_drawn_bounded(tmp_path):
    data = from_ops(
        {"op": "fill", "item": "S", "x": [-64, -1], "y": [-64, -1], "z": [-32, -1]},
        {"op": "fill", "item": "G", "x": [64, 127], "y": [64, 127], "z": [32, 63]},
    )
    blueprint, errors = bp.parse(data)
    assert errors == [
        "ops: the result is 192 wide, the limit is 64",
        "ops: the result is 192 deep, the limit is 64",
        "ops: the result is 96 layers, the limit is 32",
    ]
    assert (blueprint.width, blueprint.depth, len(blueprint.layers)) == (64, 64, 32)
    assert len(bp.preview(write(tmp_path, data))) < 20_000_000


def test_a_blueprint_may_sit_anywhere_in_the_ops_coordinates():
    far = {"op": "fill", "item": "S", "x": -1000, "y": 5000, "z": [70, 71]}
    roof = {"op": "roof", "item": "G", "x": -1000, "y": 5000, "z": 72}
    blueprint, errors = bp.parse(from_ops(far, roof))
    assert errors == []
    assert blueprint.origin == (-1000, 5000, 70)
    assert [layer.rows for layer in blueprint.layers] == [["S"], ["S"], ["G"]]


def test_the_cells_kept_while_expanding_are_bounded():
    tiles = [
        {
            "op": "fill",
            "item": "S",
            "x": [64 * i, 64 * i + 63],
            "y": [0, 63],
            "z": [0, 31],
        }
        for i in range(30)
    ]
    errors: list[str] = []
    cells, _ = bp._expand(from_ops(*tiles), {"S": STONE}, errors)
    assert len(errors) == 1
    assert errors[0].startswith("op 3: more than 262144 blocks before it")
    assert len(cells) == 3 * 64 * 64 * 32


def test_an_area_ring_must_be_a_boolean():
    data = corner_wall()
    data["pieces"] = [
        {
            "item": "小麥",
            "kind": "plant",
            "layer": 1,
            "area": {"x": [1, 3], "y": [1, 3], "ring": "false"},
        }
    ]
    assert bp.parse(data)[1] == ["piece 0: area ring must be true or false"]
