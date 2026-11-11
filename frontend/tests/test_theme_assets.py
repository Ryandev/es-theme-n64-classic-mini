from __future__ import annotations

import hashlib
import io
import struct
import wave
from typing import TYPE_CHECKING

from PIL import Image

from n64mini.paths import DEFAULT_BRAND_TEXT, SVG_ASSETS_DIR, THEME_ART_DIR, brand_text, svg_asset
from n64mini.theme_assets.background import BOTTOM_Y, TOP_H, build_background
from n64mini.theme_assets.console_bar import (
    CONSOLE_SIZE,
    POWER_LED_X,
    WORDMARK_POS,
    build_console,
)
from n64mini.theme_assets.frames import (
    ART_INSET,
    BADGE_SIZE,
    BRAND_SIZE,
    BUTTON_ICON_SIZE,
    CORNER,
    EMBLEM_FACES,
    FRAME_SIZE,
    ICON_X,
    LAUNCH_SPLASH_SIZE,
    LIBRARY_TOAST_SIZE,
    N_LOGO_CENTER_X,
    SUSPEND_CART_SIZE,
    SUSPEND_CART_VARIANTS,
    SUSPEND_DRAWER_SIZE,
    build_brand,
    build_button_icon,
    build_cube_emblem,
    build_flip_badge,
    build_launch_splash,
    build_library_toast,
    build_suspend_cart,
    build_suspend_drawer,
    build_tile_frame,
)
from n64mini.theme_assets.generate import generate, main
from n64mini.theme_assets.music import (
    RATE,
    build_suspend_poof_sfx,
    build_suspend_save_sfx,
)
from n64mini.theme_assets.shapes import (
    build_controller_port_svg,
    build_front_bezel_svg,
    build_n64_cartridge_svg,
    build_suspend_drawer_svg,
    draw_chiseled_box,
    extract_nintendo64_wordmark,
    make_icon_library,
    make_icon_music,
    render_svg,
)
from n64mini.theme_assets.texture import Gradient, Tex, make_n64_surface_tex, scale_col

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

FIVE_BIT_LEVELS = {int(q * 255 / 31) for q in range(32)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_surface_texture_is_deterministic_and_rgba5551_quantised() -> None:
    g = Gradient.vertical((12, 18, 44), (42, 70, 142))
    a = make_n64_surface_tex(64, 32, g, Tex(8, 4, 9))
    b = make_n64_surface_tex(64, 32, g, Tex(8, 4, 9))
    assert a.size == (64, 32)
    assert a.tobytes() == b.tobytes()
    assert set(a.tobytes()) <= FIVE_BIT_LEVELS


def test_surface_texture_clamps_degenerate_sizes() -> None:
    g = Gradient.vertical((0, 0, 0), (255, 255, 255))
    assert make_n64_surface_tex(0, -5, g).size == (1, 1)


def test_scale_col_clamps() -> None:
    assert scale_col((200, 100, 0), 1.5) == (255, 150, 0)
    assert scale_col((200, 100, 0), -1) == (0, 0, 0)


def test_generator_reproduces_committed_art(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Golden check: regenerating must match the PNGs committed in the theme byte for byte."""
    monkeypatch.delenv("N64MINI_BRAND_TEXT", raising=False)
    monkeypatch.delenv("N64MINI_SVG_DIR", raising=False)
    monkeypatch.delenv("N64MINI_SVG_FRONT_BEZEL", raising=False)
    written = generate(tmp_path)
    assert len(written) == 40  # 38 PNGs + 2 sounds/*.wav (the music comes from menu-music)
    for path in written:
        committed = THEME_ART_DIR.parent / path.relative_to(tmp_path)
        assert _sha(path) == _sha(committed), f"{committed.name} drifted"


def test_cli_writes_into_theme_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--theme-dir", str(tmp_path)]) == 0
    assert (tmp_path / "art" / "n64_classic_bg.png").is_file()
    assert "n64_classic_bg.png" in capsys.readouterr().out


def test_theme_art_is_only_generated_art(tmp_path: Path) -> None:
    """AC-T05: nothing in the theme's art folder is baked in by hand (no game art)."""
    art = tmp_path / "art"
    generated = {p.relative_to(art).as_posix() for p in generate(tmp_path) if p.is_relative_to(art)}
    committed = {
        p.relative_to(THEME_ART_DIR).as_posix() for p in THEME_ART_DIR.rglob("*") if p.is_file()
    }
    assert committed == generated
    assert not (THEME_ART_DIR / "games").exists()


def _px(image: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    value = image.getpixel(xy)
    assert isinstance(value, tuple)
    return value


def test_frames_are_valid_nine_patches() -> None:
    """The engine stretches everything between the corners, so the edges must be uniform."""
    for selected in (False, True):
        frame = build_tile_frame(selected=selected)
        assert frame.size == (FRAME_SIZE, FRAME_SIZE)
        assert _px(frame, (0, 0))[3] == 0  # chamfered: the corner is transparent
        assert _px(frame, (FRAME_SIZE // 2, FRAME_SIZE // 2))[3] == 255
        mid = range(CORNER, FRAME_SIZE - CORNER)
        for y in range(ART_INSET // 2):  # the outer bevel band
            assert len({frame.getpixel((x, y)) for x in mid}) == 1, f"top edge row {y} varies"
            assert len({frame.getpixel((y, x)) for x in mid}) == 1, f"left edge column {y} varies"


def test_selected_and_unselected_frames_differ() -> None:
    """AC-T06: bright off-white LED border for the selected card, muted grey otherwise."""
    selected = _px(build_tile_frame(selected=True), (FRAME_SIZE // 2, 3))
    unselected = _px(build_tile_frame(selected=False), (FRAME_SIZE // 2, 3))
    assert selected[0] > 230  # bright white top-edge LED highlight
    assert selected[2] > 230
    assert unselected[0] < 160  # muted grey unselected border
    assert abs(unselected[0] - unselected[2]) < 40


def test_flip_badges_match_save_dot_outline_and_gold_fill() -> None:
    """AC-T15: outline 'PORT' when available, filled gold 'PORT' when selected."""
    outline = build_flip_badge(port=False)
    filled = build_flip_badge(port=True)
    assert outline.size == filled.size == BADGE_SIZE
    assert _px(outline, (8, BADGE_SIZE[1] // 2))[:3] == (16, 18, 26)
    assert _px(filled, (8, BADGE_SIZE[1] // 2))[:3] == (255, 220, 56)


def test_button_icons_have_n64_controller_colours() -> None:
    """AC-T16: blue A, green B, red Start, yellow C-buttons, grey shoulders and D-pad."""
    a_px = _px(build_button_icon("a"), (6, 22))
    b_px = _px(build_button_icon("b"), (6, 22))
    s_px = _px(build_button_icon("start"), (6, 22))
    x_px = _px(build_button_icon("x"), (6, 22))
    l_px = _px(build_button_icon("l"), (6, 16))
    dpad_lr = build_button_icon("dpad_lr")
    assert dpad_lr.size == BUTTON_ICON_SIZE
    assert a_px[2] > a_px[0] + 100  # blue A
    assert b_px[1] > b_px[0] + 100  # green B
    assert s_px[0] > s_px[2] + 100  # red Start
    assert x_px[0] > 200  # yellow C-button
    assert x_px[1] > 150
    assert x_px[2] < 80
    assert abs(l_px[0] - l_px[2]) < 30  # grey shoulder
    assert _px(dpad_lr, (5, 15))[0] > 200  # left arrow lit
    assert _px(dpad_lr, (15, 5))[0] < 100  # up arrow dim
    for c_dir in ("c_up", "c_down", "c_left", "c_right"):
        c_img = build_button_icon(c_dir)
        assert c_img.size == BUTTON_ICON_SIZE
        c_shine = _px(c_img, (11, 8))
        c_body = _px(c_img, (21, 21))
        assert c_shine[0] > 240  # top-left specular shine matching A/B/Start
        assert c_shine[1] > 225
        assert c_body[0] > 200  # yellow C-button body
        assert c_body[2] < 80


def test_launch_splash_is_an_emblem_on_a_black_screen() -> None:
    splash = build_launch_splash()
    assert splash.size == LAUNCH_SPLASH_SIZE
    assert _px(splash, (0, 0)) == (0, 0, 0, 255)
    assert _px(splash, (LAUNCH_SPLASH_SIZE[0] // 2, LAUNCH_SPLASH_SIZE[1] // 2 - 40))[0] > 180


def test_top_and_bottom_bars_share_console_gradient_and_reference_heights() -> None:
    """Top and bottom bars match the reference proportions and share the console gradient."""
    assert TOP_H == 92
    assert 720 - BOTTOM_Y == 90
    bg = build_background()
    # Mid-height of the top bar (y=46) and mid-height of the bottom bar (y=675) share the dark
    # charcoal N64 console gradient.
    top_mid = _px(bg, (100, TOP_H // 2))
    bot_mid = _px(bg, (100, BOTTOM_Y + (720 - BOTTOM_Y) // 2))
    assert abs(top_mid[0] - bot_mid[0]) <= 10
    assert abs(top_mid[1] - bot_mid[1]) <= 10
    assert abs(top_mid[2] - bot_mid[2]) <= 10


def test_committed_svgs_and_console_port_grouping(tmp_path: Path) -> None:
    """The front bezel and controller port SVGs are reproducible and aligned."""
    assert (SVG_ASSETS_DIR / "front_bezel.svg").read_text(
        encoding="utf-8"
    ) == build_front_bezel_svg()
    assert (SVG_ASSETS_DIR / "controller_port.svg").read_text(
        encoding="utf-8"
    ) == build_controller_port_svg()
    assert (SVG_ASSETS_DIR / "n64_cartridge.svg").read_text(
        encoding="utf-8"
    ) == build_n64_cartridge_svg()
    assert (SVG_ASSETS_DIR / "suspend_drawer.svg").read_text(
        encoding="utf-8"
    ) == build_suspend_drawer_svg()
    drawer = build_suspend_drawer()
    assert drawer.size == SUSPEND_DRAWER_SIZE
    assert _px(drawer, (640, 200))[:3] == (7, 9, 14)  # recessed dark pocket
    for variant in SUSPEND_CART_VARIANTS:
        cart = build_suspend_cart(variant)
        assert cart.size == SUSPEND_CART_SIZE
    assert _px(build_suspend_cart("mask"), (108, 70))[3] == 255
    assert _px(build_suspend_cart("mask"), (12, 70))[3] == 0
    brand = build_brand()
    assert brand.size == BRAND_SIZE
    assert _px(brand, (0, 0))[3] == 0  # transparent outside the option icons and 3D 'N' logo
    for bx in ICON_X:
        assert any(_px(brand, (bx, y))[3] == 255 for y in range(20, 60))
    assert _px(brand, (N_LOGO_CENTER_X - 18, BRAND_SIZE[1] // 2))[3] == 255  # right 3D 'N' logo
    console = build_console()
    assert console.size == CONSOLE_SIZE
    assert POWER_LED_X == N_LOGO_CENTER_X  # 1:1 horizontal alignment with the 'N' logo
    assert _px(console, (WORDMARK_POS[0] + 10, WORDMARK_POS[1] + 20))[3] > 0  # NINTENDO 64 wordmark
    led_px = _px(console, (POWER_LED_X, CONSOLE_SIZE[1] // 2))
    assert led_px[0] > 200  # glowing crimson/red Power LED
    assert led_px[3] == 255
    assert _px(make_icon_library("full"), (29, 26))[2] > 180  # blue front grid card
    assert _px(make_icon_music(active=False), (22, 42))[1] < 150  # muted grey note
    opaque_wm = Image.new("RGB", (20, 20), (10, 10, 10))
    opaque_wm.putpixel((10, 10), (240, 240, 240))
    assert _px(extract_nintendo64_wordmark(opaque_wm, (0, 0, 20, 20)), (0, 0))[3] == 0
    draw_chiseled_box(
        Image.new("RGB", (40, 40)),
        (4, 4, 30, 30),
        chamf=4,
        bevel=2,
        colors=Gradient((70, 70, 70), (50, 50, 50), (60, 60, 60), (40, 40, 40)),
        shadow=True,
    )
    # Exercise remaining SVG path commands (relative l/h/v/q/s/a, 3-hex colours, empty paths)
    extra_svg = tmp_path / "extra.svg"
    xml = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g transform="translate(1) scale(1)">'
        '<path fill="#f00" d=""/>'
        '<path fill="#0f0" d="M 1 1 L 2 2"/>'
        '<path fill="#abc" d="m 2 2 l 4 0 h 4 v 4 H 2 V 2 q 2 2 4 0 Q 8 4 10 2 '
        "c 1 1 2 1 3 0 s 2 -1 3 0 S 20 3 22 2 a 0 2 0 0 1 2 0 A 1 1 0 1 1 24 2 "
        'a 2 2 30 1 0 -4 2 A 10 10 0 0 0 4 6"/>'
        "</g></svg>"
    )
    extra_svg.write_text(xml, encoding="utf-8")
    rendered = render_svg(extra_svg, (32, 16), scale=1, color_overrides={"#abc": "#123456"})
    assert rendered.size == (32, 16)


def test_theme_music_is_the_menu_music_export_with_its_licence() -> None:
    """AC-T12: the committed track is menu-music's export: a long mono loop with its licence."""
    path = THEME_ART_DIR.parent / "music" / "theme.wav"
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == RATE
        seconds = wav.getnframes() / wav.getframerate()
    # The loop is repeated inside the file so the engine's 1 s fade-in restart is rare.
    assert seconds >= 120.0
    assert (path.parent / "LICENSE").is_file()


def test_brand_text_and_svg_asset_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEC-5: brand text and SVG paths load from the environment with compliance-safe defaults."""
    monkeypatch.delenv("N64MINI_BRAND_TEXT", raising=False)
    monkeypatch.delenv("N64MINI_SVG_DIR", raising=False)
    monkeypatch.delenv("N64MINI_SVG_FRONT_BEZEL", raising=False)
    assert brand_text() == DEFAULT_BRAND_TEXT == "CLASSIC 64"
    assert svg_asset("front_bezel.svg") == SVG_ASSETS_DIR / "front_bezel.svg"

    custom_dir = tmp_path / "svg"
    custom_dir.mkdir()
    custom_bezel = custom_dir / "front_bezel.svg"
    custom_bezel.write_text(
        (SVG_ASSETS_DIR / "front_bezel.svg").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("N64MINI_SVG_DIR", str(custom_dir))
    assert svg_asset("front_bezel.svg") == custom_bezel
    assert svg_asset("n_logo.svg") == SVG_ASSETS_DIR / "n_logo.svg"  # falls back when missing

    explicit_svg = tmp_path / "explicit_bezel.svg"
    explicit_svg.write_text(custom_bezel.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("N64MINI_SVG_FRONT_BEZEL", str(explicit_svg))
    assert svg_asset("front_bezel.svg") == explicit_svg

    monkeypatch.delenv("N64MINI_SVG_FRONT_BEZEL")
    monkeypatch.delenv("N64MINI_SVG_DIR")
    monkeypatch.setenv("N64MINI_BRAND_TEXT", "RETRO 64")
    assert brand_text() == "RETRO 64"
    console_with_num = build_console()
    assert console_with_num.crop((WORDMARK_POS[0], WORDMARK_POS[1], 220, 62)).getbbox() is not None

    monkeypatch.setenv("N64MINI_BRAND_TEXT", "RETRO")
    console_single = build_console()
    assert console_single.crop((WORDMARK_POS[0], WORDMARK_POS[1], 220, 62)).getbbox() is not None


def test_suspend_save_and_poof_sfx_are_distinct_mono_wavs() -> None:
    """AC-T13: saving and poof-discarding each synthesize a valid 16-bit mono WAV."""
    save_wav = build_suspend_save_sfx()
    poof_wav = build_suspend_poof_sfx()
    assert save_wav != poof_wav
    for raw, expected_sec in ((save_wav, 0.38), (poof_wav, 0.32)):
        with wave.open(io.BytesIO(raw), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == RATE
            n_frames = wav.getnframes()
            assert n_frames == round(expected_sec * RATE)
            samples = struct.unpack(f"<{n_frames}h", wav.readframes(n_frames))
            assert max(abs(s) for s in samples) > 25000


def test_library_icons_and_dropdown_toasts_distinguish_showcase_and_full() -> None:
    """AC-T14/AC-T21: Gold Star + 'Showcase' toast vs Cyan-Blue 3-Card Hand + 'Full Library'."""
    star = make_icon_library("showcase")
    fanned = make_icon_library("full")
    assert star.size == fanned.size == (64, 64)
    star_mid = _px(star, (32, 32))
    fanned_mid = _px(fanned, (32, 32))
    assert star_mid[0] > 200
    assert star_mid[2] < 80
    assert fanned_mid[2] > 200
    assert fanned_mid[0] < 100

    t_show = build_library_toast("showcase")
    t_full = build_library_toast("full")
    assert t_show.size == t_full.size == LIBRARY_TOAST_SIZE == (336, 94)
    assert _px(t_show, (0, 0))[3] == 0
    assert _px(t_full, (0, 0))[3] == 0
    tip_show = _px(t_show, (168, 2))
    tip_full = _px(t_full, (168, 2))
    assert tip_show[3] == 255
    assert tip_full[3] == 255
    assert tip_show[0] > tip_show[2]  # gold LED pointer
    assert tip_full[2] > tip_full[0]  # cyan-blue LED pointer


def _colour_share(img: Image.Image, box: tuple[int, int, int, int], rgb: tuple[int, ...]) -> float:
    region = img.crop(box).convert("RGB")
    pixels = [region.getpixel((x, y)) for x in range(region.width) for y in range(region.height)]
    near = [
        p
        for p in pixels
        if isinstance(p, tuple) and max(abs(a - b) for a, b in zip(p, rgb, strict=True)) <= 24
    ]
    return len(near) / len(pixels)


def test_cube_emblem_has_one_colour_per_face_and_64_on_each() -> None:
    """AC-T21/DEC-5: the header emblem is the original cube, lettered on all three faces."""
    emblem = build_cube_emblem(240)
    assert emblem.size == (240, 240)
    ink = (255, 244, 220)
    regions = {  # boxes well inside each face of the 240 px cube
        "top": (90, 40, 150, 80),
        "left": (45, 115, 95, 175),
        "right": (145, 115, 195, 175),
    }
    for face, box in regions.items():
        assert _colour_share(emblem, box, EMBLEM_FACES[face]) > 0.35, face
        assert _colour_share(emblem, box, ink) > 0.05, f"no 64 on the {face} face"
    assert _px(emblem, (2, 2))[3] == 0  # transparent outside the cube


def test_brand_uses_the_owners_svg_when_one_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cube is the default; an owner's n_logo.svg (N64MINI_SVG_N_LOGO) replaces it."""
    monkeypatch.delenv("N64MINI_SVG_DIR", raising=False)
    monkeypatch.delenv("N64MINI_SVG_N_LOGO", raising=False)
    cy = BRAND_SIZE[1] // 2
    default = _px(build_brand(), (N_LOGO_CENTER_X, cy))
    assert default[3] == 255
    own = tmp_path / "n_logo.svg"
    own.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<path fill="#ff00ff" d="M 0 0 L 10 0 L 10 10 L 0 10 Z"/></svg>',
        encoding="utf-8",
    )
    monkeypatch.setenv("N64MINI_SVG_N_LOGO", str(own))
    assert _px(build_brand(), (N_LOGO_CENTER_X, cy))[:3] == (255, 0, 255)
