"""``n64mini.console.strip``: box art under the carousel, a cursor over the game (AC-T20).

The theme shows one image per selected game, ``<STRIP_DIR>/<rom file name>.png``, through a
bound path (``"/userdata/…/strip/" + {game:rom} + ".png"``). A theme can't draw other games' art
or know the cursor's position, so each image is drawn here: every game in the list, in the
engine's order, small, with a cursor over that one game. Changing the selection then just
changes which image the engine shows; nothing in the engine's data changes, so nothing reloads.

The list is the one the engine shows (engine fact 30): the ROM files with the system's
extensions, minus the gamelist's hidden entries, each named by its gamelist ``sortname`` or
``name`` (else its file name's stem), sorted with the engine's case-insensitive compare. A list
too long to fit shows the ``WINDOW`` games around the selected one.

Runs on the console. Batocera 43.1 ships Pillow (11.0.0, used by its own bezel code), so this
module, and only this one, may import it (``pyproject.toml``, import-linter).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from PIL import Image, ImageDraw, UnidentifiedImageError

from n64mini.console.showcase import GAME_RE, element_text, entry_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# Under the userdata folder (/userdata on the console).
ROMS: Final = Path("roms/n64")
STRIPS: Final = Path("system/n64mini/strip")
TOPBAR: Final = Path("system/n64mini/topbar")
THEME_BRAND: Final = Path("themes/n64-classic-mini/art/brand.png")
MUSIC_FULL: Final = Path("themes/n64-classic-mini/music/theme.full.wav")
SYSTEMS_CFG: Final = Path("system/configs/emulationstation/es_systems_n64.cfg")
STRIP_DIR: Final = Path("/userdata") / STRIPS  # where the theme reads them
POCKET_SIZE: Final = (341, 73)
_ICON_CENTRES: Final = (505, 600, 695)  # icon x centres inside brand.png (1200x82)
# 1280x720 pixels; the theme's <image name="strip"> has the same size (test_n64_theme.py).
SIZE: Final = (1152, 68)
CURSOR_H: Final = 18  # the cursor's row, above the art
ART_TOP: Final = 22
WINDOW: Final = 32  # games shown at once: 36 px each at most, still recognisable
MAX_CELL: Final = 48  # a short list isn't stretched across the whole width
GAP: Final = 2
CURSOR: Final = (255, 220, 56, 255)  # the theme's gold
_CURSOR_LIT: Final = (255, 236, 110, 255)
_CURSOR_SHADE: Final = (218, 174, 32, 255)
_BLANK: Final = (40, 44, 56, 255)  # a game without box art
_BOX_ASPECT: Final = 200 / 276  # height / width of a standard landscape N64 box front
_EXTENSION_RE: Final = re.compile(r"<extension>(.*?)</extension>", re.DOTALL)
_COMMENT_RE: Final = re.compile(r"<!--.*?-->", re.DOTALL)


class StripError(Exception):
    """The list or an image can't be read."""


@dataclass(frozen=True, slots=True)
class Game:
    """One game in the list, as the engine shows it."""

    rom: str  # the file name, which names its strip image
    sort_name: str
    art: Path | None  # its box art (the gamelist's thumbnail, else its image), if any


def compare_key(name: str) -> str:
    """The engine's sort key: upper case, one character at a time (StringUtil::compareIgnoreCase).

    A character whose upper case is longer (German ß) keeps its own code point, as there.
    """
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in name)


def extensions(systems_cfg: Path) -> frozenset[str]:
    """The system's ROM extensions, from the installed system config (lower case)."""
    try:
        text = systems_cfg.read_text(encoding="utf-8")
    except OSError as exc:
        raise StripError(f"can't read the extensions from {systems_cfg}: {exc}") from exc
    m = _EXTENSION_RE.search(_COMMENT_RE.sub("", text))  # a comment may name the tag too
    if m is None or not m.group(1).split():
        raise StripError(f"{systems_cfg} lists no extensions")
    return frozenset(e.lower() for e in m.group(1).split())


def _media_path(roms_dir: Path, value: str) -> Path:
    return Path(value) if value.startswith("/") else roms_dir / value.removeprefix("./")


def list_games(roms_dir: Path, exts: Iterable[str]) -> list[Game]:
    """The games the engine lists for ``roms_dir``, in its order."""
    wanted = frozenset(exts)
    gamelist = roms_dir / "gamelist.xml"
    text = gamelist.read_text(encoding="utf-8") if gamelist.exists() else ""
    entries = {Path(entry_path(m.group(0))).name: m.group(0) for m in GAME_RE.finditer(text)}
    games: list[Game] = []
    for file in roms_dir.iterdir():
        if not file.is_file() or file.name.startswith(".") or file.suffix.lower() not in wanted:
            continue
        block = entries.get(file.name, "")
        if element_text(block, "hidden") == "true":
            continue
        name = element_text(block, "sortname") or element_text(block, "name") or file.stem
        # The thumbnail, else the image, as the cards show (FileData::getThumbnailPath)
        thumb = element_text(block, "thumbnail") or element_text(block, "image")
        art = _media_path(roms_dir, thumb) if thumb else None
        games.append(Game(file.name, name, art))
    games.sort(key=lambda g: (compare_key(g.sort_name), g.rom))
    return games


def window(count: int, index: int, size: int = WINDOW) -> range:
    """The games shown when ``index`` is selected: all of them, or ``size`` around it."""
    if count <= size:
        return range(count)
    start = min(max(0, index - size // 2), count - size)
    return range(start, start + size)


def _cell(shown: int) -> int:
    return min(MAX_CELL, SIZE[0] // max(1, shown))


def _box_size(cell: int) -> tuple[int, int]:
    """Uniform landscape box dimensions for a strip cell."""
    w = cell - GAP
    h = min(SIZE[1] - ART_TOP, max(1, round(w * _BOX_ASPECT)))
    return (w, h)


def _thumbnail(game: Game, box: tuple[int, int]) -> Image.Image:
    if game.art is None or not game.art.is_file():  # not scraped: the engine shows no art either
        return Image.new("RGBA", box, _BLANK)
    try:
        with Image.open(game.art) as f:
            art = f.convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise StripError(f"{game.rom}: can't read its box art {game.art}: {exc}") from exc
    return art.resize(box, Image.Resampling.LANCZOS)


def _thumbs_row(
    games: Sequence[Game], index: int, thumbs: Sequence[Image.Image]
) -> tuple[Image.Image, int]:
    """The window's box-art row and the horizontal centre ``cx`` of ``games[index]``."""
    shown = window(len(games), index)
    cell = _cell(len(shown))
    left = (SIZE[0] - cell * len(shown)) // 2
    row = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    art_h = SIZE[1] - ART_TOP
    for slot, i in enumerate(shown):
        art = thumbs[i]
        x = left + slot * cell + (cell - art.width) // 2
        row.alpha_composite(art, (x, ART_TOP + (art_h - art.height) // 2))
    cx = left + (index - shown.start) * cell + cell // 2
    return row, cx


def draw(games: Sequence[Game], index: int, thumbs: Sequence[Image.Image]) -> Image.Image:
    """The strip for ``games[index]``: the window's art, centred, the cursor over that game."""
    strip, cx = _thumbs_row(games, index, thumbs)
    half = CURSOR_H * 2 // 3
    ImageDraw.Draw(strip).polygon([(cx - half, 0), (cx + half, 0), (cx, CURSOR_H)], fill=CURSOR)
    return strip


def _spin_frames(
    games: Sequence[Game], index: int, thumbs: Sequence[Image.Image]
) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Full-width, mid-turn and edge-on cursor frames around the vertical axis at ``cx``."""
    row, cx = _thumbs_row(games, index, thumbs)
    full = row.copy()
    half = CURSOR_H * 2 // 3
    ImageDraw.Draw(full).polygon([(cx - half, 0), (cx + half, 0), (cx, CURSOR_H)], fill=CURSOR)

    base = row.copy()
    ImageDraw.Draw(base).polygon([(cx - 2, 0), (cx + 2, 0), (cx, CURSOR_H)], fill=_CURSOR_LIT)

    mid = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    mid_half = CURSOR_H // 3
    d_mid = ImageDraw.Draw(mid)
    d_mid.polygon([(cx - mid_half, 0), (cx + mid_half, 0), (cx, CURSOR_H)], fill=_CURSOR_SHADE)
    d_mid.polygon([(cx - mid_half, 0), (cx, 0), (cx, CURSOR_H)], fill=_CURSOR_LIT)
    return full, mid, base


def _save(image: Image.Image, path: Path) -> None:
    """Write whole or not at all: the engine may load the image at any moment."""
    tmp = path.with_name(f".{path.name}.tmp")
    image.save(tmp, format="PNG", compress_level=1)
    tmp.replace(path)


def generate(roms_dir: Path, out_dir: Path, exts: Iterable[str]) -> int:
    """Write every game's strip into ``out_dir`` and remove stale ones; returns the count."""
    games = list_games(roms_dir, exts)
    base_dir = out_dir.with_name(f"{out_dir.name}-base")
    mid_dir = out_dir.with_name(f"{out_dir.name}-mid")
    for d in (out_dir, base_dir, mid_dir):
        d.mkdir(parents=True, exist_ok=True)
    cell = _cell(len(window(len(games), 0)))
    box = _box_size(cell)
    thumbs = [_thumbnail(g, box) for g in games]
    wanted = {f"{g.rom}.png" for g in games}
    for index, game in enumerate(games):
        full, mid, base = _spin_frames(games, index, thumbs)
        _save(full, out_dir / f"{game.rom}.png")
        _save(mid, mid_dir / f"{game.rom}.png")
        _save(base, base_dir / f"{game.rom}.png")
    for d in (out_dir, base_dir, mid_dir):
        for old in d.glob("*.png"):
            if old.name not in wanted:
                old.unlink()
    return len(games)


def clear(out_dir: Path) -> None:
    """Remove every strip PNG in ``out_dir``, ``out_dir-base`` and ``out_dir-mid``."""
    base_dir = out_dir.with_name(f"{out_dir.name}-base")
    mid_dir = out_dir.with_name(f"{out_dir.name}-mid")
    for d in (out_dir, base_dir, mid_dir):
        if d.is_dir():
            for old in d.glob("*.png"):
                old.unlink()


def topbar_brand_path(userdata: Path) -> Path:
    """Path to the live top-bar overlay PNG for the current Showcase and music state."""
    showcase_on = int((userdata / ROMS / "gamelist.xml.showcase.json").is_file())
    music_on = int(not (userdata / MUSIC_FULL).is_file())
    return userdata / TOPBAR / f"brand-{showcase_on}-{music_on}.png"


def _recolor_region(img: Image.Image, box: tuple[int, int, int, int], *, to_grey: bool) -> None:
    """Recolor a 64x64 top-bar icon region in-place (gold->blue for Full, green->grey+slash)."""
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            px = img.getpixel((x, y))
            if not isinstance(px, tuple) or len(px) < 4 or px[3] == 0:
                continue
            r, g, b, a = px[0], px[1], px[2], px[3]
            if to_grey and g > r + 20:
                lum = (r + g + b) // 3
                img.putpixel((x, y), (lum, min(255, lum + 4), min(255, lum + 12), a))
            elif not to_grey and r > b + 40:
                img.putpixel((x, y), (b, g, r, a))
    if to_grey:
        d = ImageDraw.Draw(img)
        p0, p1 = (x0 + 16, y0 + 18), (x0 + 48, y0 + 46)
        d.line([p0, p1], fill=(38, 42, 50, 255), width=6)
        d.line([p0, p1], fill=(180, 186, 198, 255), width=3)


def _pocket_frame(brand_img: Image.Image, selected: int | None) -> Image.Image:
    """Render the 341x73 recessed top-bar pocket, optionally highlighting icon ``selected``."""
    pw, ph = POCKET_SIZE
    pocket = Image.new("RGBA", POCKET_SIZE, (36, 39, 44, 255))
    draw = ImageDraw.Draw(pocket)
    draw.line([(2, 1), (pw - 3, 1)], fill=(18, 20, 24, 255), width=2)
    draw.line([(2, ph - 2), (pw - 3, ph - 2)], fill=(96, 102, 112, 255), width=2)
    draw.line([(1, 2), (1, ph - 3)], fill=(22, 24, 28, 255), width=2)
    draw.line([(pw - 2, 2), (pw - 2, ph - 3)], fill=(90, 96, 106, 255), width=2)
    pocket_icons = brand_img.crop((430, 5, 430 + pw, 5 + ph))
    pocket.alpha_composite(pocket_icons, (0, 0))
    if selected is None:
        return pocket
    cx = _ICON_CENTRES[selected] - 430
    cy = 36
    bx0, by0, bx1, by1 = cx - 29, cy - 29, cx + 29, cy + 29
    draw.rounded_rectangle(
        (bx0 - 1, by0 - 1, bx1 + 1, by1 + 1),
        radius=11,
        outline=(18, 20, 26, 255),
        width=1,
    )
    draw.rounded_rectangle(
        (bx0, by0, bx1, by1),
        radius=10,
        outline=(192, 202, 216, 255),
        width=3,
    )
    draw.line([(bx0 + 8, by0 + 1), (bx1 - 8, by0 + 1)], fill=(255, 255, 255, 255), width=2)
    return pocket


def _apply_library_icon(brand: Image.Image, brand_path: Path, *, showcase_on: bool) -> None:
    """Swap the middle 64x64 top-bar icon to ``icon_showcase.png`` or ``icon_full.png``."""
    icon_path = brand_path.with_name("icon_showcase.png" if showcase_on else "icon_full.png")
    if icon_path.is_file():
        with Image.open(icon_path) as ic:
            icon_img = ic.convert("RGBA")
        brand.paste((0, 0, 0, 0), (568, 9, 632, 73))
        brand.alpha_composite(icon_img, (568, 9))
    elif not showcase_on:
        _recolor_region(brand, (568, 9, 632, 73), to_grey=False)


def sync_topbar(userdata: Path) -> bool:
    """Sync ``art/brand.png`` and ``system/n64mini/topbar/`` to library/music state."""
    brand_path = userdata / THEME_BRAND
    if not brand_path.is_file():
        return False
    orig_path = brand_path.with_name("brand.orig.png")
    if not orig_path.is_file():
        orig_path.write_bytes(brand_path.read_bytes())
    showcase_on = (userdata / ROMS / "gamelist.xml.showcase.json").is_file()
    music_on = not (userdata / MUSIC_FULL).is_file()
    with Image.open(orig_path) as f:
        brand = f.convert("RGBA")
    _apply_library_icon(brand, brand_path, showcase_on=showcase_on)
    if not music_on:
        _recolor_region(brand, (663, 9, 727, 73), to_grey=True)
    _save(brand, brand_path)
    topbar_dir = userdata / TOPBAR
    topbar_dir.mkdir(parents=True, exist_ok=True)
    overlay = Image.new("RGBA", brand.size, (0, 0, 0, 0))
    overlay.paste(_pocket_frame(brand, None), (430, 5))
    _save(overlay, topbar_brand_path(userdata))
    for idx in (0, 1, 2):
        frame = _pocket_frame(brand, idx)
        raw_path = topbar_dir / f"pocket-{idx}.rgba"
        tmp = raw_path.with_name(f".{raw_path.name}.tmp")
        tmp.write_bytes(frame.tobytes())
        tmp.replace(raw_path)
    return True


def redraw(userdata: Path) -> int:
    """Draw the N64 list's strips on a console whose userdata folder is ``userdata``."""
    sync_topbar(userdata)
    return generate(userdata / ROMS, userdata / STRIPS, extensions(userdata / SYSTEMS_CFG))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: 0 when written, 1 when the list or an image can't be read."""
    parser = argparse.ArgumentParser(description="Draw the strip of box art for each game.")
    parser.add_argument("--userdata", type=Path, default=Path("/userdata"))
    args = parser.parse_args(argv)
    try:
        count = redraw(args.userdata)
    except (StripError, OSError) as exc:
        print(f"strip: {exc}", file=sys.stderr)
        return 1
    print(f"strip: {count} games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
