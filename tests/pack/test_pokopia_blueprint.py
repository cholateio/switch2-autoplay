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
