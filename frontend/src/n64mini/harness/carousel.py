"""The ``grid`` view's carousel, laid out and drawn the way the engine does it.

Ported from the vendored ``ImageGridComponent.h`` (auto layout, margin, padding,
clipping) and ``GridTileComponent.cpp`` (tile padding, nine-patch background, image
fit), including two engine quirks the theme has to live with:

- A tile ``padding`` whose values are all below 1 is meant to be scaled to the screen,
  but ``Vector4f::operator*=(const Vector2f&)`` is a no-op, so the values stay as
  (sub-)pixels. The grid's own ``padding`` multiplies by a ``Vector4f`` and does scale.
- An unselected tile's background is tinted ``AAAAEEFF`` unless the theme sets a
  ``backgroundColor``.

The preview puts the cursor on the middle column, which is where the engine's
centred camera puts no scroll offset. Anything this module doesn't model raises
``RenderError`` instead of being approximated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from PIL import Image, ImageChops

from n64mini.harness.values import RenderError, parse_hex_color, parse_pair, parse_rect

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

type Rect = tuple[float, float, float, float]  # left, top, right, bottom
type TileMode = Literal["default", "selected"]
type TileProps = Mapping[TileMode, Mapping[str, str]]

_DEFAULT_TILE_PADDING: Final = 16.0  # GridTileComponent::resetProperties
_DEFAULT_CORNER: Final = (16.0, 16.0)  # GridNinePatchProperties
_DEFAULT_TILE_TINT: Final = "AAAAEEFF"  # resetProperties, unselected tile only
_DEFAULT_IMAGE_COLOR: Final = "FFFFFFDD"


@dataclass(frozen=True, slots=True)
class Tile:
    """One laid-out tile: its centre and size in screen pixels."""

    index: int
    selected: bool
    cx: float
    cy: float
    w: float
    h: float

    @property
    def box(self) -> tuple[int, int, int, int]:
        """Pixel bounds, rounded."""
        x0, y0 = round(self.cx - self.w / 2), round(self.cy - self.h / 2)
        return (x0, y0, x0 + round(self.w), y0 + round(self.h))


@dataclass(frozen=True, slots=True)
class Layout:
    """The grid's clip rectangle and its tiles, in drawing order (selected last)."""

    grid_box: tuple[int, int, int, int]
    tiles: tuple[Tile, ...]


def _scaled_rect(value: str | None, screen: tuple[int, int]) -> Rect:
    """A grid padding: all values below 1 scale to the screen, else pixels."""
    rect = parse_rect(value, (0.0, 0.0, 0.0, 0.0))
    if all(abs(v) < 1 for v in rect):
        sx, sy = screen
        return (rect[0] * sx, rect[1] * sy, rect[2] * sx, rect[3] * sy)
    return rect


def layout(grid: Mapping[str, str], screen: tuple[int, int], selected: int) -> Layout:
    """Lay out one auto-layout row of tiles with ``selected`` under the cursor."""
    sx, sy = screen
    cols_f, rows_f = parse_pair(grid.get("autoLayout"), (0.0, 0.0))
    cols, rows = int(cols_f), int(rows_f)
    if cols <= 0 or rows != 1:
        raise RenderError(f"only a one-row autoLayout is modelled, got {grid.get('autoLayout')!r}")
    if not 0 <= selected < cols:
        raise RenderError(f"cursor {selected} is outside the {cols} visible columns")
    px, py = parse_pair(grid.get("pos"), (0.0, 0.0))
    gw, gh = parse_pair(grid.get("size"), (0.0, 0.0))
    gx, gy, gw, gh = px * sx, py * sy, gw * sx, gh * sy
    mx, my = parse_pair(grid.get("margin"), (0.0, 0.0))
    if abs(mx) < 1 and abs(my) < 1:
        mx, my = mx * sx, my * sy
    left, top, right, bottom = _scaled_rect(grid.get("padding"), screen)
    if grid.get("centerSelection", "false") != "false" and abs(left - right) > 0.5:
        raise RenderError("centerSelection with asymmetric padding isn't modelled")

    tw = (gw - mx * (cols - 1) - left - right) / cols
    th = gh - top - bottom
    zoom = float(grid.get("autoLayoutSelectedZoom", "1"))
    tiles = [
        Tile(
            i,
            i == selected,
            gx + left + tw / 2 + i * (tw + mx),
            gy + top + th / 2,
            tw * (zoom if i == selected else 1.0),
            th * (zoom if i == selected else 1.0),
        )
        for i in range(cols)
    ]
    tiles.sort(key=lambda t: t.selected)  # the engine draws the selected tile last
    grid_box = (round(gx), round(gy), round(gx + gw), round(gy + gh))
    return Layout(grid_box, tuple(tiles))


def tile_padding(value: str | None) -> Rect:
    """A tile padding in pixels, with the engine's no-op scaling of small values."""
    d = _DEFAULT_TILE_PADDING
    return parse_rect(value, (d, d, d, d))


def nine_patch(texture: Image.Image, size: tuple[int, int], corner: tuple[int, int]) -> Image.Image:
    """Corners copied 1:1, edges and centre stretched (``NinePatchComponent::buildVertices``)."""
    w, h = size
    cx, cy = corner
    tw, th = texture.size
    if w < 2 * cx or h < 2 * cy or tw < 2 * cx or th < 2 * cy:
        raise RenderError(f"nine-patch corner {corner} doesn't fit {size} / texture {texture.size}")
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    src_x, dst_x = (0, cx, tw - cx, tw), (0, cx, w - cx, w)
    src_y, dst_y = (0, cy, th - cy, th), (0, cy, h - cy, h)
    for j in range(3):
        for i in range(3):
            piece = texture.crop((src_x[i], src_y[j], src_x[i + 1], src_y[j + 1]))
            target = (dst_x[i + 1] - dst_x[i], dst_y[j + 1] - dst_y[j])
            if target[0] > 0 and target[1] > 0:
                out.paste(piece.resize(target, Image.Resampling.NEAREST), (dst_x[i], dst_y[j]))
    return out


def tint(image: Image.Image, color: str) -> Image.Image:
    """Multiply every channel, alpha included, by an ``RRGGBBAA`` colour (vertex colour)."""
    solid = Image.new("RGBA", image.size, parse_hex_color(color))
    return ImageChops.multiply(image, solid)


def fit(art: Image.Image, box: tuple[int, int]) -> Image.Image:
    """``maxSize``: scale to fit inside ``box``, keeping the aspect ratio."""
    ratio = min(box[0] / art.width, box[1] / art.height)
    size = (max(1, round(art.width * ratio)), max(1, round(art.height * ratio)))
    return art.resize(size, Image.Resampling.LANCZOS)


def _tile_props(
    tiles: TileProps, *, selected: bool
) -> dict[str, str]:  # selected inherits default, then overrides it
    props = dict(tiles["default"])
    if selected:
        props.update(tiles["selected"])
    for unsupported in ("backgroundCenterColor", "backgroundEdgeColor", "reflexion"):
        if unsupported in props:
            raise RenderError(f"gridtile {unsupported} isn't modelled")
    if props.get("selectionMode", "full") != "full":
        raise RenderError(f"gridtile selectionMode {props['selectionMode']!r} isn't modelled")
    if props.get("imageSizeMode", "maxSize") != "maxSize":
        raise RenderError(f"gridtile imageSizeMode {props['imageSizeMode']!r} isn't modelled")
    return props


def draw_tile(tiles: TileProps, tile: Tile, art: Image.Image) -> Image.Image:
    """One tile: tinted nine-patch background, then the game's art inside the padding."""
    props = _tile_props(tiles, selected=tile.selected)
    x0, y0, x1, y1 = tile.box
    size = (x1 - x0, y1 - y0)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    if background := props.get("backgroundImage"):
        if not Path(background).is_file():
            raise RenderError(f"gridtile backgroundImage not found: {background}")
        corner_x, corner_y = parse_pair(props.get("backgroundCornerSize"), _DEFAULT_CORNER)
        with Image.open(background) as f:
            frame = nine_patch(f.convert("RGBA"), size, (round(corner_x), round(corner_y)))
        default_tint = "FFFFFFFF" if tile.selected else _DEFAULT_TILE_TINT
        out.alpha_composite(tint(frame, props.get("backgroundColor", default_tint)))
    left, top, right, bottom = tile_padding(props.get("padding"))
    inner = (round(size[0] - left - right), round(size[1] - top - bottom))
    if inner[0] <= 0 or inner[1] <= 0:
        raise RenderError(f"tile padding leaves no room for the image: {props.get('padding')!r}")
    sprite = tint(fit(art, inner), props.get("imageColor", _DEFAULT_IMAGE_COLOR))
    ax = round(left + (inner[0] - sprite.width) / 2)
    ay = round(top + (inner[1] - sprite.height) / 2)
    out.alpha_composite(sprite, (ax, ay))
    return out


def draw_carousel(
    canvas: Image.Image,
    grid: Mapping[str, str],
    tiles: TileProps,
    arts: Sequence[Image.Image],
    selected: int,
) -> Layout:
    """Draw the carousel onto ``canvas``, clipped to the grid; returns the layout used."""
    lay = layout(grid, canvas.size, selected)
    if len(arts) < len(lay.tiles):
        raise RenderError(f"{len(lay.tiles)} tiles but only {len(arts)} games")
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    for tile in lay.tiles:
        layer.alpha_composite(draw_tile(tiles, tile, arts[tile.index]), tile.box[:2])
    clip = Image.new("L", canvas.size, 0)
    clip.paste(255, lay.grid_box)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), clip))
    canvas.alpha_composite(layer)
    return lay
