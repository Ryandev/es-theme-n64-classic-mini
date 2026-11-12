"""The preview's ``gamecarousel`` (n64mini.harness.gamecarousel), pinned to the vendored engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from n64mini.harness import gamecarousel as gc
from n64mini.harness.render import MOCK_GAMES, SCREEN_W, render_view
from n64mini.harness.values import RenderError
from n64mini.paths import VENDOR_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ThemeWriter

ENGINE = (VENDOR_DIR / "CarouselComponent.cpp").read_text(encoding="utf-8")
BASE = {
    "type": "horizontal",
    "pos": "0 0",
    "size": "1 0.5",
    "logoSize": "0.2 0.8",
    "maxLogoCount": "5",
    "logoScale": "1",
}


def test_the_model_uses_the_engines_own_numbers() -> None:
    """The constants and formulas the module ports, as they read in the vendored source."""
    assert "const int logoBuffersLeft[] = { -5, -2, -1 };" in ENGINE
    assert "const int logoBuffersRight[] = { 1, 2, 5 };" in ENGINE
    # At rest the scrolling velocity is 0, so the buffers are entry [0 + 1].
    assert "Math::max(0, Math::min(2, getScrollingVelocity() + 1))" in ENGINE
    assert (gc.BUFFER_LEFT, gc.BUFFER_RIGHT) == (-2, 2)
    spacing = (
        "logoSpacing[0] = ((mSize.x() - (mLogoSize.x() * mMaxLogoCount)) / (mMaxLogoCount))"
        " + mLogoSize.x();"
    )
    assert spacing in ENGINE
    # In the gamecarousel view logoSize is a fraction of the carousel, not the screen (seen on
    # the 43.1 VM: a screen-fraction height squashed the cards to 40 %).
    assert 'Vector2f size = mThemeViewName == "gamecarousel" ?' in ENGINE
    assert 'mLogoSize = elem->get<Vector2f>("logoSize") * size;' in ENGINE
    assert "mMaxLogoCount = 3;" in ENGINE
    assert "mMinLogoOpacity = 0.5f;" in ENGINE


def test_cards_are_spaced_by_width_over_count_with_the_cursor_centred() -> None:
    lay = gc.layout(BASE, (1000, 400), games=5, selected=2, opacity_storyboards=True)
    assert lay.clip == (0, 0, 1000, 200)
    by_x = sorted(lay.cards, key=lambda c: c.box[0])
    # 200 x 160 cards every 200 px, the cursor's at (1000 - 200) / 2, centred vertically.
    assert [c.box for c in by_x] == [(400 + i * 200, 20, 600 + i * 200, 180) for i in range(-4, 5)]
    # It wraps: left of game 0 is the last game.
    assert [c.game for c in by_x] == [3, 4, 0, 1, 2, 3, 4, 0, 1]
    assert lay.cards[-1].selected  # drawn last
    assert [c.game for c in lay.cards if c.selected] == [2]


def test_every_copy_of_the_cursors_game_is_active_and_drawn_last() -> None:
    lay = gc.layout(BASE, (1000, 400), games=3, selected=0, opacity_storyboards=True)
    active = [c for c in lay.cards if c.selected]
    assert len(active) == 3  # positions -3, 0 and 3 all show game 0
    assert lay.cards[-3:] == tuple(active)


def test_fewer_games_than_slots_draw_fewer_cards() -> None:
    lay = gc.layout(BASE, (1000, 400), games=2, selected=1, opacity_storyboards=True)
    assert len(lay.cards) == 7  # -(2 // 2) - 2 .. 2 // 2 + 2


def test_cards_fade_by_distance_unless_a_card_animates_its_opacity() -> None:
    lay = gc.layout(BASE, (1000, 400), games=5, selected=2, opacity_storyboards=False)
    assert {c.opacity for c in lay.cards if c.selected} == {1.0}
    assert {c.opacity for c in lay.cards if not c.selected} == {128 / 255}
    faded = gc.layout(
        {**BASE, "minLogoOpacity": "0.2"},
        (1000, 400),
        games=5,
        selected=2,
        opacity_storyboards=False,
    )
    assert {c.opacity for c in faded.cards if not c.selected} == {51 / 255}
    kept = gc.layout(BASE, (1000, 400), games=5, selected=2, opacity_storyboards=True)
    assert {c.opacity for c in kept.cards} == {1.0}


@pytest.mark.parametrize(("alignment", "top"), [("top", 16), ("bottom", 24), ("center", 20)])
def test_the_logo_alignment_moves_the_cards_vertically(alignment: str, top: int) -> None:
    props = {**BASE, "logoAlignment": alignment}
    lay = gc.layout(props, (1000, 400), games=5, selected=0, opacity_storyboards=True)
    assert {c.box[1] for c in lay.cards} == {top}  # 160 / 10; 200 - 176; (200 - 160) / 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"type": "vertical"}, "only a horizontal gamecarousel"),
        ({"logoScale": "1.2"}, "logoScale other than 1"),
        ({"logoPos": "0 0"}, "logoPos isn't modelled"),
        ({"logoSize": None}, "without logoSize"),
    ],
)
def test_what_isnt_modelled_raises(change: dict[str, str | None], message: str) -> None:
    props = {k: v for k, v in {**BASE, **change}.items() if v is not None}
    with pytest.raises(RenderError, match=message):
        gc.layout(props, (1000, 400), games=5, selected=0, opacity_storyboards=True)
    with pytest.raises(RenderError, match="only a horizontal"):
        gc.layout({}, (1000, 400), games=5, selected=0, opacity_storyboards=True)
    with pytest.raises(RenderError, match="logoScale"):
        gc.layout(
            {"type": "horizontal"}, (1000, 400), games=5, selected=0, opacity_storyboards=True
        )  # the engine's default logoScale is 1.2


def test_a_cursor_outside_the_list_raises() -> None:
    with pytest.raises(RenderError, match="cursor 5 is outside the 5 games"):
        gc.layout(BASE, (1000, 400), games=5, selected=5, opacity_storyboards=True)


# ------------------------------------------------------------------ rendered


def _carousel_theme(write_theme: ThemeWriter, children: str, *, view: str = "gamecarousel") -> Path:
    carousel = (
        "<gamecarousel name='gamecarousel'><type>horizontal</type><pos>0 0</pos>"
        "<size>1 0.5</size><logoSize>0.2 0.8</logoSize><maxLogoCount>5</maxLogoCount>"
        f"<logoScale>1</logoScale><zIndex>50</zIndex>{children}</gamecarousel>"
    )
    return write_theme(
        {
            "theme.xml": (
                f'<?xml version="1.0"?>\n<theme defaultView="{view}">'
                "<formatVersion>7</formatVersion>"
                f"<view name='gamecarousel'>{carousel}</view></theme>\n"
            )
        }
    )


_ART = "<image name='art'><path>{game:thumbnail}</path><size>1 0.5</size></image>"


def test_the_theme_opens_in_its_default_view_with_side_cards_faded(
    write_theme: ThemeWriter,
) -> None:
    clean = render_view(_carousel_theme(write_theme, f"<itemTemplate>{_ART}</itemTemplate>")).clean
    # 256 x 288 cards every 256 px; the cursor (Super Mario 64) in the middle.
    centre, left = (SCREEN_W // 2, 60), (128, 60)
    assert clean.getpixel(centre) == MOCK_GAMES[2].color
    assert clean.getpixel(left) != MOCK_GAMES[0].color  # half faded (minLogoOpacity 0.5)


def test_an_opacity_storyboard_stops_the_fade(write_theme: ThemeWriter) -> None:
    board = "<storyboard event='deactivate'><animation property='opacity' to='1'/></storyboard>"
    art = _ART.replace("</image>", f"{board}</image>")
    clean = render_view(_carousel_theme(write_theme, f"<itemTemplate>{art}</itemTemplate>")).clean
    assert clean.getpixel((128, 60)) == MOCK_GAMES[0].color


def test_a_carousel_without_a_template_or_another_view_raises(write_theme: ThemeWriter) -> None:
    with pytest.raises(RenderError, match="without an itemTemplate"):
        render_view(_carousel_theme(write_theme, ""))
    with pytest.raises(RenderError, match="not 'detailed'"):
        render_view(_carousel_theme(write_theme, "", view="detailed"))
