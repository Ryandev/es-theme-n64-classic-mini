"""The ``gamecarousel`` view's horizontal carousel, laid out the way the engine does it.

Ported from the vendored ``CarouselComponent.cpp`` (``renderCarousel``,
``getCarouselFromTheme``, ``ensureLogo``):

- In the ``gamecarousel`` view ``logoSize`` and ``logoPos`` are fractions of the carousel's own
  size, not the screen's.
- A horizontal carousel spaces its cards ``width / maxLogoCount`` apart and centres the selected
  one; it wraps, so the cards beyond either end are the list's other end.
- It draws two cards past each visible edge (``logoBuffers``, at rest), the selected card last,
  and clips everything to its own rectangle.
- Cards away from the cursor fade towards ``minLogoOpacity``, unless any card has an opacity
  storyboard: then the engine leaves opacity to the storyboards.

The preview shows the carousel at rest with the cursor on game ``selected``. Anything this module
doesn't model raises ``RenderError`` instead of being approximated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from n64mini.harness.values import RenderError, parse_pair

if TYPE_CHECKING:
    from collections.abc import Mapping

BUFFER_LEFT: Final = -2  # logoBuffersLeft[1]: the carousel isn't scrolling
BUFFER_RIGHT: Final = 2  # logoBuffersRight[1]
_DEFAULT_MAX_LOGOS: Final = 3
_DEFAULT_MIN_OPACITY: Final = 0.5
_UNMODELLED: Final = ("logoPos", "logoRotation", "scaledLogoSpacing", "color", "colorEnd")


@dataclass(frozen=True, slots=True)
class Card:
    """One card on screen: which game, where, and how opaque."""

    game: int  # index into the game list
    selected: bool
    box: tuple[int, int, int, int]
    opacity: float


@dataclass(frozen=True, slots=True)
class Layout:
    """The carousel's clip rectangle and its cards in drawing order (selected last)."""

    clip: tuple[int, int, int, int]
    cards: tuple[Card, ...]


def _logo_y(alignment: str, height: float, logo_h: float) -> float:
    if alignment == "top":
        return logo_h / 10.0
    if alignment == "bottom":
        return height - logo_h * 1.1
    return (height - logo_h) / 2.0


def layout(
    props: Mapping[str, str],
    screen: tuple[int, int],
    *,
    games: int,
    selected: int,
    opacity_storyboards: bool,
) -> Layout:
    """Lay out a horizontal carousel of ``games`` cards, at rest on ``selected``."""
    if props.get("type", "vertical") != "horizontal":
        raise RenderError(f"only a horizontal gamecarousel is modelled, got {props.get('type')!r}")
    if float(props.get("logoScale", "1.2")) != 1.0:
        raise RenderError("a gamecarousel logoScale other than 1 isn't modelled")
    if unmodelled := [p for p in _UNMODELLED if p in props]:
        raise RenderError(f"gamecarousel {', '.join(unmodelled)} isn't modelled")
    if "logoSize" not in props:
        raise RenderError("a gamecarousel without logoSize isn't modelled")
    if not 0 <= selected < games:
        raise RenderError(f"cursor {selected} is outside the {games} games")
    sx, sy = screen
    px, py = parse_pair(props.get("pos"), (0.0, 0.0))
    w, h = parse_pair(props.get("size"), (1.0, 1.0))
    x, y, w, h = px * sx, py * sy, w * sx, h * sy
    lw, lh = parse_pair(props["logoSize"], (0.0, 0.0))
    lw, lh = lw * w, lh * h  # fractions of the carousel in this view (getCarouselFromTheme)
    count = round(float(props.get("maxLogoCount", str(_DEFAULT_MAX_LOGOS))))
    spacing = (w - lw * count) / count + lw
    x_off = (w - lw) / 2.0
    y_off = _logo_y(props.get("logoAlignment", "center"), h, lh)
    min_opacity = min(1.0, max(0.0, float(props.get("minLogoOpacity", str(_DEFAULT_MIN_OPACITY)))))

    cards: list[Card] = []
    shown = min(count, games)
    for i in range(-(shown // 2) + BUFFER_LEFT, shown // 2 + BUFFER_RIGHT + 1):
        left, top = x + x_off + i * spacing, y + y_off
        opacity = 1.0
        if not opacity_storyboards:
            floor = round(min_opacity * 255)
            opacity = max(floor, round(floor + (255 - floor) * (1.0 - abs(i)))) / 255.0
        game = (selected + i) % games
        box = (round(left), round(top), round(left + lw), round(top + lh))
        # Every position showing the cursor's game is "active" (index == mCursor), not just i 0.
        cards.append(Card(game, game == selected, box, opacity))
    cards.sort(key=lambda c: c.selected)  # the engine draws the cursor's card last
    clip = (round(x), round(y), round(x + w), round(y + h))
    return Layout(clip, tuple(cards))
