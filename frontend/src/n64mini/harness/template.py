"""A grid card drawn from the theme's ``itemTemplate``, as the engine does it.

With a template the engine draws only the template's children on each card
(``GridTileComponent::applyTheme``): positions and sizes are fractions of the card, a missing
``pos`` is 0 0 and a missing ``size`` is 1 1 (``GuiComponent::loadThemedChildren``), and a
selected card plays each child's ``activate`` storyboard. The preview draws the end state of that
storyboard. Only ``ninepatch``, ``image`` and ``text`` children, and storyboards animating
``opacity``, are modelled; anything else raises ``RenderError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from PIL import Image, ImageDraw, ImageFont

from n64mini.harness.bindings import Device, evaluate, substitute
from n64mini.harness.carousel import fit, nine_patch, tint
from n64mini.harness.values import RenderError, parse_hex_color, parse_pair
from n64mini.theme_assets.fonts import Face, font

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_DRAWN: Final = frozenset({"ninepatch", "image", "text"})


@dataclass(slots=True)
class TemplateChild:
    """One child of the template: its properties and its ``activate`` storyboard's end state."""

    type: str
    name: str
    props: dict[str, str] = field(default_factory=dict[str, str])
    on_select: dict[str, str] = field(default_factory=dict[str, str])


def _opacity(child: TemplateChild, *, selected: bool) -> float:
    raw = (child.on_select if selected else {}).get("opacity", child.props.get("opacity", "1"))
    return float(raw)


def _visible(child: TemplateChild, field_of: Callable[[str], str], device: Device) -> bool:
    raw = child.props.get("visible", "true")
    if "{" not in raw:
        return raw.lower() != "false"
    value = evaluate(raw, field_of, device)
    if not isinstance(value, bool):
        raise RenderError(f"<{child.type} name={child.name!r}>: visible={raw!r} isn't true/false")
    return value


def _box(props: Mapping[str, str], card: tuple[int, int]) -> tuple[float, float, float, float]:
    """The child's position and size in card pixels (defaults: pos 0 0, size 1 1)."""
    px, py = parse_pair(props.get("pos"), (0.0, 0.0))
    sw, sh = parse_pair(props.get("size"), (1.0, 1.0))
    return px * card[0], py * card[1], sw * card[0], sh * card[1]


def _open(file: Path | None, what: str) -> Image.Image | None:
    if file is None:
        return None  # an empty bound path: the engine draws nothing
    if not file.is_file():
        raise RenderError(f"{what}: image not found: {file}")
    with Image.open(file) as f:
        return f.convert("RGBA")


def _resolve(raw: str, field_of: Callable[[str], str], device: Device, what: str) -> Path | None:
    """The file a path names: a theme file as written, a bound one on the console's disk."""
    if "{" not in raw:  # the engine binds only a property with a {...} in it
        return Path(raw) if raw else None
    value = evaluate(raw, field_of, device)
    if isinstance(value, bool):
        raise RenderError(f"{what}: path {raw!r} is true/false")
    if not value:
        return None
    return device.path(value) if value.startswith("/") else Path(value)


def _place(
    card: Image.Image, sprite: Image.Image, props: Mapping[str, str], at: tuple[float, float]
) -> None:
    ox, oy = parse_pair(props.get("origin"), (0.0, 0.0))
    card.alpha_composite(
        sprite, (round(at[0] - ox * sprite.width), round(at[1] - oy * sprite.height))
    )


def _sized(child: TemplateChild, sprite: Image.Image, card: tuple[int, int]) -> Image.Image:
    """An image child at its size: ``maxSize`` keeps the aspect ratio, ``size`` stretches."""
    if "maxSize" in child.props:
        mw, mh = parse_pair(child.props["maxSize"], (1.0, 1.0))
        return fit(sprite, (round(mw * card[0]), round(mh * card[1])))
    _, _, w, h = _box(child.props, card)
    return sprite.resize((max(1, round(w)), max(1, round(h))), Image.Resampling.NEAREST)


def _draw_text(card: Image.Image, child: TemplateChild, text: str, screen_h: int) -> None:
    props = child.props
    x, y, w, h = _box(props, card.size)
    ox, oy = parse_pair(props.get("origin"), (0.0, 0.0))
    left, top = x - ox * w, y - oy * h
    size = float(props.get("fontSize", "0.035"))
    path = props.get("fontPath")
    face = (
        ImageFont.truetype(path, max(8, round(size * screen_h)))
        if path
        else font(Face.SANS_BOLD, round(size * screen_h))
    )
    draw = ImageDraw.Draw(card)
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=face)
    tw, th = x1 - x0, y1 - y0
    align = props.get("alignment", "left")
    tx = {"center": left + (w - tw) / 2, "right": left + w - tw}.get(align, left)
    ty = top + (h - th) / 2 - y0  # vertically centred in its box, as the engine does
    draw.text((tx, ty), text, font=face, fill=parse_hex_color(props.get("color", "FFFFFFFF")))


@dataclass(frozen=True, slots=True)
class Card:
    """One card to draw: its size, whether it's selected, and its game's bound values."""

    size: tuple[int, int]
    selected: bool
    field_of: Callable[[str], str]


def draw_card(
    children: Sequence[TemplateChild], spec: Card, device: Device, screen_h: int
) -> Image.Image:
    """One card: each visible child, in order, over a transparent card."""
    size, selected, field_of = spec.size, spec.selected, spec.field_of
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    for child in children:
        what = f"<{child.type} name={child.name!r}>"
        if child.type not in _DRAWN:
            raise RenderError(f"template {what} isn't modelled")
        opacity = _opacity(child, selected=selected)
        if opacity <= 0 or not _visible(child, field_of, device):
            continue
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        if child.type == "text":
            _draw_text(layer, child, substitute(child.props.get("text", ""), field_of), screen_h)
        else:
            sprite = _open(_resolve(child.props.get("path", ""), field_of, device, what), what)
            if sprite is None:
                continue
            x, y, w, h = _box(child.props, size)
            if child.type == "ninepatch":
                cx, cy = parse_pair(child.props.get("cornerSize"), (16.0, 16.0))
                sprite = nine_patch(sprite, (round(w), round(h)), (round(cx), round(cy)))
            else:
                sprite = _sized(child, sprite, size)
            if color := child.props.get("color"):
                sprite = tint(sprite, color)
            _place(layer, sprite, child.props, (x, y))
        if opacity < 1:
            alpha = layer.getchannel("A").point([round(a * opacity) for a in range(256)])
            layer.putalpha(alpha)
        card.alpha_composite(layer)
    return card
