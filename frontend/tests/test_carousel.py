from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image

from n64mini.harness.carousel import (
    Tile,
    TileMode,
    draw_carousel,
    draw_tile,
    fit,
    layout,
    nine_patch,
    tile_padding,
    tint,
)
from n64mini.harness.values import RenderError, parse_rect

if TYPE_CHECKING:
    from pathlib import Path

SCREEN = (1280, 720)
# The shipped theme's carousel (themes/n64-classic-mini/base.xml).
GRID = {
    "pos": "0 0.30556",
    "size": "1 0.34444",
    "autoLayout": "5 1",
    "autoLayoutSelectedZoom": "1.28",
    "centerSelection": "true",
    "margin": "0.03125 0",
    "padding": "0.01953125 0.03958333 0.01953125 0.03958333",
}


def _tiles(**default: str) -> dict[TileMode, dict[str, str]]:
    return {"default": dict(default), "selected": {}}


# ---------------------------------------------------------------- layout: ImageGridComponent
def test_layout_matches_the_engine_formula_for_the_shipped_grid() -> None:
    lay = layout(GRID, SCREEN, selected=2)
    by_index = {t.index: t for t in lay.tiles}
    # Width: 1280 less four 40px margins and two 25px paddings, over 5 columns.
    # Height: the 248px grid less two 28.5px paddings.
    assert by_index[0].w == pytest.approx(214, abs=0.01)
    assert by_index[0].h == pytest.approx(191, abs=0.01)
    assert by_index[2].w == pytest.approx(214 * 1.28, abs=0.01)
    assert by_index[2].cx == pytest.approx(640, abs=0.01)
    assert lay.grid_box == (0, 220, 1280, 468)


def test_selected_tile_is_drawn_last() -> None:
    lay = layout(GRID, SCREEN, selected=1)
    assert [t.selected for t in lay.tiles] == [False, False, False, False, True]
    assert lay.tiles[-1].index == 1


@given(
    cols=st.integers(1, 9),
    margin=st.floats(0, 0.05),
    pad=st.floats(0, 0.05),
    zoom=st.floats(1, 1.5),
    data=st.data(),
)
def test_layout_invariants(
    cols: int, margin: float, pad: float, zoom: float, data: st.DataObject
) -> None:
    selected = data.draw(st.integers(0, cols - 1))
    grid = {
        "pos": "0 0.2",
        "size": "1 0.4",
        "autoLayout": f"{cols} 1",
        "autoLayoutSelectedZoom": str(zoom),
        "margin": f"{margin} 0",
        "padding": f"{pad} {pad} {pad} {pad}",
    }
    tiles = sorted(layout(grid, SCREEN, selected).tiles, key=lambda t: t.index)
    sel = tiles[selected]
    unzoomed = sel.w / zoom
    # Every unselected tile has the unzoomed size, on one centre line.
    for t in tiles:
        if not t.selected:
            assert t.w == pytest.approx(unzoomed, abs=1e-6)
        assert t.cy == pytest.approx(sel.cy, abs=1e-6)
    # Uniform pitch of tile + margin, whatever the zoom.
    for a, b in pairwise(tiles):
        assert b.cx - a.cx == pytest.approx(unzoomed + margin * SCREEN[0], abs=1e-6)
    # The tiles and margins exactly fill the padded width.
    used = cols * unzoomed + (cols - 1) * margin * SCREEN[0] + 2 * pad * SCREEN[0]
    assert used == pytest.approx(SCREEN[0], abs=1e-6)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"autoLayout": "5 2"}, "one-row autoLayout"),
        ({"autoLayout": "0 1"}, "one-row autoLayout"),
        ({"padding": "0.01 0 0.03 0"}, "asymmetric padding"),
    ],
)
def test_layout_refuses_what_it_does_not_model(change: dict[str, str], match: str) -> None:
    with pytest.raises(RenderError, match=match):
        layout({**GRID, **change}, SCREEN, selected=2)


def test_layout_rejects_a_cursor_outside_the_row() -> None:
    with pytest.raises(RenderError, match="outside the 5 visible columns"):
        layout(GRID, SCREEN, selected=5)


def test_pixel_margin_and_padding_are_not_scaled() -> None:
    grid = {**GRID, "margin": "40 0", "padding": "25 28.5 25 28.5"}
    pixels, normalised = layout(grid, SCREEN, 2), layout(GRID, SCREEN, 2)
    assert pixels.grid_box == normalised.grid_box
    for a, b in zip(pixels.tiles, normalised.tiles, strict=True):
        assert (a.index, a.selected) == (b.index, b.selected)
        assert (a.cx, a.cy, a.w, a.h) == pytest.approx((b.cx, b.cy, b.w, b.h), abs=0.01)


# ---------------------------------------------------------------- tile: GridTileComponent
def test_tile_padding_defaults_to_16px_and_small_values_stay_unscaled() -> None:
    assert tile_padding(None) == (16.0, 16.0, 16.0, 16.0)
    # Vector4f::operator*=(const Vector2f&) is a no-op: 0.5 stays half a pixel.
    assert tile_padding("0.5") == (0.5, 0.5, 0.5, 0.5)
    assert tile_padding("14 10") == (14.0, 10.0, 14.0, 10.0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", (1.0,) * 4), ("1 2", (1.0, 2.0, 1.0, 2.0)), ("1 2 3 4", (1.0, 2.0, 3.0, 4.0))],
)
def test_parse_rect_arities(value: str, expected: tuple[float, ...]) -> None:
    assert parse_rect(value, (0.0, 0.0, 0.0, 0.0)) == expected


@pytest.mark.parametrize("value", ["1 2 3", "1 2 3 4 5", "a b"])
def test_parse_rect_rejects_what_the_engine_would_zero(value: str) -> None:
    with pytest.raises(RenderError):
        parse_rect(value, (0.0, 0.0, 0.0, 0.0))


def _texture() -> Image.Image:
    """8x8 texture: a distinct colour in each 2x2 corner, grey elsewhere."""
    tex = Image.new("RGBA", (8, 8), (128, 128, 128, 255))
    for i, (x, y) in enumerate([(0, 0), (6, 0), (0, 6), (6, 6)]):
        tex.paste((40 * (i + 1), 0, 0, 255), (x, y, x + 2, y + 2))
    return tex


@given(w=st.integers(4, 80), h=st.integers(4, 80))
def test_nine_patch_keeps_corners_one_to_one(w: int, h: int) -> None:
    out = nine_patch(_texture(), (w, h), (2, 2))
    assert out.size == (w, h)
    tex = _texture()
    for (sx, sy), (dx, dy) in [
        ((0, 0), (0, 0)),
        ((6, 0), (w - 2, 0)),
        ((0, 6), (0, h - 2)),
        ((6, 6), (w - 2, h - 2)),
    ]:
        assert (
            out.crop((dx, dy, dx + 2, dy + 2)).tobytes()
            == tex.crop((sx, sy, sx + 2, sy + 2)).tobytes()
        )


def test_nine_patch_rejects_corners_that_do_not_fit() -> None:
    with pytest.raises(RenderError, match="doesn't fit"):
        nine_patch(_texture(), (3, 40), (2, 2))


def test_tint_multiplies_every_channel() -> None:
    out = tint(Image.new("RGBA", (1, 1), (255, 200, 100, 255)), "AAAAEEFF")
    assert out.getpixel((0, 0)) == (170, 133, 93, 255)


def test_fit_keeps_aspect_ratio_inside_the_box() -> None:
    assert fit(Image.new("RGBA", (276, 200)), (186, 163)).size == (186, 135)
    assert fit(Image.new("RGBA", (100, 200)), (186, 163)).size == (82, 163)


def _frame(tmp_path: Path) -> str:
    path = tmp_path / "frame.png"
    Image.new("RGBA", (48, 48), (255, 255, 255, 255)).save(path)
    return str(path)


def test_unselected_frame_is_tinted_unless_the_theme_sets_a_colour(tmp_path: Path) -> None:
    tile = Tile(0, selected=False, cx=50, cy=50, w=100, h=100)
    art = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    bare = draw_tile(_tiles(backgroundImage=_frame(tmp_path)), tile, art)
    assert bare.getpixel((2, 2)) == (170, 170, 238, 255)
    white = draw_tile(
        _tiles(backgroundImage=_frame(tmp_path), backgroundColor="FFFFFFFF"), tile, art
    )
    assert white.getpixel((2, 2)) == (255, 255, 255, 255)
    selected = draw_tile(
        _tiles(backgroundImage=_frame(tmp_path)),
        Tile(0, selected=True, cx=50, cy=50, w=100, h=100),
        art,
    )
    assert selected.getpixel((2, 2)) == (255, 255, 255, 255)


def test_art_sits_inside_the_tile_padding() -> None:
    tile = Tile(0, selected=False, cx=50, cy=50, w=100, h=100)
    art = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    out = draw_tile(_tiles(padding="14", imageColor="FFFFFFFF"), tile, art)
    assert out.getbbox() == (14, 14, 86, 86)


@pytest.mark.parametrize(
    ("props", "match"),
    [
        ({"selectionMode": "image"}, "selectionMode"),
        ({"imageSizeMode": "minSize"}, "imageSizeMode"),
        ({"backgroundEdgeColor": "FFFFFFFF"}, "backgroundEdgeColor"),
        ({"backgroundImage": "/nope.png"}, "backgroundImage not found"),
        ({"padding": "50"}, "no room"),
    ],
)
def test_tile_refuses_what_it_does_not_model(props: dict[str, str], match: str) -> None:
    with pytest.raises(RenderError, match=match):
        draw_tile(
            _tiles(**props),
            Tile(0, selected=False, cx=50, cy=50, w=100, h=100),
            Image.new("RGBA", (1, 1)),
        )


def test_selected_tile_inherits_default_then_overrides() -> None:
    tiles: dict[TileMode, dict[str, str]] = {
        "default": {"padding": "40"},
        "selected": {"padding": "0"},
    }
    art = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    plain = draw_tile(tiles, Tile(0, selected=False, cx=50, cy=50, w=100, h=100), art)
    chosen = draw_tile(tiles, Tile(0, selected=True, cx=50, cy=50, w=100, h=100), art)
    assert plain.getbbox() == (40, 40, 60, 60)
    assert chosen.getbbox() == (0, 0, 100, 100)


def test_carousel_is_clipped_to_the_grid() -> None:
    grid = {**GRID, "padding": "0.01953125 0 0.01953125 0"}  # zoomed tile taller than the grid
    canvas = Image.new("RGBA", SCREEN, (0, 0, 0, 0))
    arts = [Image.new("RGBA", (10, 10), (255, 0, 0, 255))] * 5
    lay = draw_carousel(canvas, grid, _tiles(padding="0", imageColor="FFFFFFFF"), arts, 2)
    assert canvas.getbbox() is not None
    x0, y0, x1, y1 = canvas.getbbox() or (0, 0, 0, 0)
    gx0, gy0, gx1, gy1 = lay.grid_box
    assert gx0 <= x0
    assert gy0 <= y0
    assert x1 <= gx1
    assert y1 <= gy1


def test_carousel_needs_a_game_per_tile() -> None:
    with pytest.raises(RenderError, match="5 tiles but only 1 games"):
        draw_carousel(Image.new("RGBA", SCREEN), GRID, _tiles(), [Image.new("RGBA", (1, 1))], 2)
