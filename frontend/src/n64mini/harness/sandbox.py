"""``n64mini-sandbox``: fill the engine sandbox's N64 folder for ``./run engine`` and ``shot``.

With ``--from`` (the owner's ROM folder), the sandbox mirrors that library **by file name only**:
each ROM becomes an empty placeholder file, so no game data is copied, and the engine lists
them as it would on the console. Without it, the sandbox holds the preview's 5 mock games. Each
game gets generated placeholder art, so the carousel always shows an image and nothing depends
on third-party art.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final

from n64mini.console.showcase import ROM_SUFFIXES, list_roms, xml_escape
from n64mini.harness.render import MOCK_GAMES, placeholder_box_art

if TYPE_CHECKING:
    from collections.abc import Sequence

_TAGS: Final = re.compile(r"\s*[(\[][^)\]]*[)\]]")  # "(U)", "[!]", "(V1.1)"...
_THE: Final = re.compile(r"^(?P<head>[^-]+?), The\b")


def display_name(rom: str) -> str:
    """A readable title from a GoodN64/No-Intro style file name.

    "Legend of Zelda, The - Ocarina of Time (U) (V1.2) [!].v64" becomes
    "The Legend of Zelda - Ocarina of Time".
    """
    stem = _TAGS.sub("", Path(rom).stem).replace("_", " ").strip()
    return _THE.sub(lambda m: f"The {m['head']}", stem)


def colour_for(name: str) -> tuple[int, int, int]:
    """A stable, mid-dark colour per title, so neighbouring cards are easy to tell apart."""
    digest = hashlib.sha256(name.encode()).digest()
    return (40 + digest[0] % 160, 40 + digest[1] % 160, 40 + digest[2] % 160)


type RGB = tuple[int, int, int]


def populate(dest: Path, games: Sequence[tuple[str, str, RGB]]) -> Path:
    """Write placeholder ROMs, art and a gamelist for ``(file name, title, colour)`` triples.

    ``dest`` must be empty or absent: the sandbox is rebuilt, never merged.
    """
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"sandbox folder isn't empty: {dest}")
    images = dest / "images"
    images.mkdir(parents=True)
    entries: list[str] = []
    for rom, title, colour in games:
        if Path(rom).suffix.lower() not in ROM_SUFFIXES:
            raise ValueError(f"not a ROM file name: {rom!r}")
        (dest / rom).write_bytes(b"")
        image = f"{Path(rom).stem}.png"
        placeholder_box_art(title, colour).save(images / image)
        entries.append(
            "  <game>\n"
            f"    <path>./{xml_escape(rom)}</path>\n"
            f"    <name>{xml_escape(title)}</name>\n"
            f"    <image>./images/{xml_escape(image)}</image>\n"
            "  </game>\n"
        )
    gamelist = dest / "gamelist.xml"
    gamelist.write_text('<?xml version="1.0"?>\n<gameList>\n' + "".join(entries) + "</gameList>\n")
    return gamelist


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Fill the engine sandbox's N64 folder.")
    parser.add_argument("dest", type=Path, help="the sandbox's roms/n64 folder (rebuilt)")
    parser.add_argument("--from", dest="source", type=Path, help="mirror this ROM folder's names")
    parser.add_argument("--saves", type=Path, help="the sandbox's saves/n64 folder")
    parser.add_argument("--suspend-points", metavar="ROM", help="give this game sample save states")
    args = parser.parse_args(argv)
    dest: Path = args.dest
    source: Path | None = args.source
    if source is None:
        games = [(f"{g.slug}.z64", g.name, g.color) for g in MOCK_GAMES]  # the preview's own art
    else:
        named = [(rom, display_name(rom)) for rom in list_roms(source)]
        games = [(rom, name, colour_for(name)) for rom, name in named]
    gamelist = populate(dest, games)
    print(f"{len(games)} games -> {gamelist}")
    if args.suspend_points:
        if args.saves is None:
            parser.error("--suspend-points needs --saves")
        written = write_suspend_points(args.saves, args.suspend_points)
        print(f"{len(written) // 2} suspend points for {args.suspend_points}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def write_suspend_points(saves_dir: Path, rom: str, count: int = 3) -> list[Path]:
    """Libretro save states for ``rom``, named as Batocera's ``es_savestates.cfg`` expects.

    ``{{romfilename}}.state{{slot}}`` (slot 0 has no number) with ``.png`` screenshots, slots
    1..count, each a minute newer than the last so incremental sorting has an order to show.
    The state files are placeholders: the engine lists them, it never reads them.
    """
    saves_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(rom).stem
    written: list[Path] = []
    base = 1_790_000_000  # a fixed 2026 time, so runs are reproducible
    for slot in range(1, count + 1):
        state = saves_dir / f"{stem}.state{slot}"
        state.write_bytes(b"")
        shot = saves_dir / f"{stem}.state{slot}.png"
        placeholder_box_art(f"SUSPEND POINT {slot}", colour_for(f"{stem}{slot}")).save(shot)
        for path in (state, shot):
            os.utime(path, (base + 60 * slot, base + 60 * slot))
        written += [state, shot]
    return written
