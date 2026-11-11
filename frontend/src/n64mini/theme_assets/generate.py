"""``n64mini-generate-assets``: render the theme's static PNG art.

Writes ``art/n64_classic_bg.png`` and the two carousel card frames. The theme ships
frames only, never game art (docs/requirements/theme.md AC-T05): each card's image
comes from the user's own gamelist at runtime. Nothing here reads the golden reference,
so a preview score against it measures the design, not a copy of it.

The theme XML under ``themes/n64-classic-mini`` is hand-authored source; this tool
doesn't write it, and the validator gates it instead.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from n64mini.paths import THEME_DIR
from n64mini.theme_assets.background import build_background
from n64mini.theme_assets.console_bar import build_console
from n64mini.theme_assets.frames import (
    BUTTON_ICONS,
    SUSPEND_CART_VARIANTS,
    build_brand,
    build_button_icon,
    build_flip_badge,
    build_launch_splash,
    build_library_card,
    build_library_toast,
    build_save_dot,
    build_suspend_cart,
    build_suspend_drawer,
    build_system_logo,
    build_tile_frame,
)
from n64mini.theme_assets.music import (
    build_suspend_poof_sfx,
    build_suspend_save_sfx,
)
from n64mini.theme_assets.shapes import make_icon_library


def generate(theme_dir: Path) -> list[Path]:
    """Write all generated art and sounds under ``theme_dir``; returns the files written.

    The menu music (``music/theme.wav``) is not generated here: it is menu-music's export, synced
    by that project (AC-T12).
    """
    art = theme_dir / "art"
    art.mkdir(parents=True, exist_ok=True)
    images = {
        "n64_classic_bg.png": build_background(),
        "tile_default.png": build_tile_frame(selected=False),
        "tile_selected.png": build_tile_frame(selected=True),
        "flip-emulator.png": build_flip_badge(port=False),
        "flip-port.png": build_flip_badge(port=True),
        "library-full.png": build_library_card("full"),
        "library-showcase.png": build_library_card("showcase"),
        "icon_showcase.png": make_icon_library("showcase"),
        "icon_full.png": make_icon_library("full"),
        "toast_showcase.png": build_library_toast("showcase"),
        "toast_full.png": build_library_toast("full"),
        "system-n64.png": build_system_logo(),
        "brand.png": build_brand(),
        "launch_splash.png": build_launch_splash(),
        "save-empty.png": build_save_dot(filled=False),
        "save-full.png": build_save_dot(filled=True),
        "console.png": build_console(),
        "suspend_drawer.png": build_suspend_drawer(),
        **{f"cart_{variant}.png": build_suspend_cart(variant) for variant in SUSPEND_CART_VARIANTS},
        **{f"btn_{kind}.png": build_button_icon(kind) for kind in BUTTON_ICONS},
    }
    written: list[Path] = []
    for name, image in images.items():
        image.save(art / name)
        written.append(art / name)
    sounds = theme_dir / "sounds"
    sounds.mkdir(exist_ok=True)
    for sfx_name, sfx_bytes in (
        ("suspend_save.wav", build_suspend_save_sfx()),
        ("suspend_poof.wav", build_suspend_poof_sfx()),
    ):
        (sounds / sfx_name).write_bytes(sfx_bytes)
        written.append(sounds / sfx_name)
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description="Render the N64 Classic Mini theme PNG art.")
    parser.add_argument("--theme-dir", type=Path, default=THEME_DIR)
    args = parser.parse_args(argv)
    theme_dir: Path = args.theme_dir

    for path in generate(theme_dir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
