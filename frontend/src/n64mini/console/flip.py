"""``n64mini-flip``: flip a ported game's card between Emulator and Port (AC-T15).

- ``init``: copy the two badges beside the gamelist and point every ported game's
  ``cartridge`` field at the Emulator badge. The theme draws ``{game:cartridge}`` under the
  selected card, so a game without a port shows nothing.
- ``selected SYSTEM PATH NAME``: the engine's ``game-selected`` event script calls this; it
  records the highlighted N64 game.
- ``flip``: flips the recorded game. A game without a port is left alone. The mode is kept in
  ``modes.json`` beside the badges, the one record of it: the N64 launch wrapper reads it when the
  game starts, and ``init`` badges from it. The new badge is uploaded to the running engine as the
  game's ``cartridge`` image (its HTTP API ignores path fields in a metadata update), so the card
  updates at once.
  (The engine's own per-game emulator setting isn't used: Batocera caches batocera.conf at
  start-up, so a change there would only apply after a restart.)

Runs on the console, so it uses only the standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Final, cast

from n64mini.console.engine import SYSTEM, Engine, EngineError
from n64mini.console.showcase import (
    CARD,
    CARD_IMAGES,
    CATALOG,
    GAME_RE,
    MEDIA_DIR,
    ShowcaseError,
    current_mode,
    entry_path,
    list_roms,
    load_catalog,
    place_card,
    resolve,
    set_element,
    xml_escape,
)

BADGE_DIR: Final = MEDIA_DIR  # beside the gamelist, so the path in it is relative
BADGES: Final = {"emulator": "flip-emulator.png", "port": "flip-port.png"}
PORTS_FILE: Final = Path("/userdata/system/n64mini/ports.json")  # installed ports (AC-T15)
MODES_FILE: Final = "modes.json"  # in BADGE_DIR: {rom file name: "port"}; absent means emulator
SELECTED_FILE: Final = Path(tempfile.gettempdir()) / "n64mini-selected"  # cleared at each boot


class FlipError(Exception):
    """The flip couldn't be done; nothing was changed."""


def badge_path(mode: str) -> str:
    """The ``cartridge`` value for a mode, relative to the gamelist."""
    return f"./{BADGE_DIR}/{BADGES[mode]}"


def ported_roms(roms: list[str], catalog: Path = CATALOG) -> set[str]:
    """The ROM files that have a port: the catalogue's chosen dump of each ported game.

    Only the chosen file counts, so a second dump of the same game shows no badge.
    """
    return set(_ported(roms, catalog).values())


def _ported(roms: list[str], catalog: Path) -> dict[str, str]:
    games = [g for g in load_catalog(catalog) if g.ports and any(g.rom.search(r) for r in roms)]
    return resolve(games, roms)  # title -> ROM file


def _command(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = cast("list[object]", value)
    words = [a for a in items if isinstance(a, str)]
    return words if words and len(words) == len(items) else None


def load_ports(path: Path) -> dict[str, list[str]]:
    """Installed ports: catalogue title -> command, where ``{rom}`` is the ROM's path."""
    if not path.exists():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    entries = cast("dict[str, object]", data) if isinstance(data, dict) else {"": None}
    ports = {title: _command(value) for title, value in entries.items()}
    if any(cmd is None for cmd in ports.values()):
        raise FlipError(f"{path} must map game titles to a non-empty command list")
    return {title: cmd for title, cmd in ports.items() if cmd is not None}


def flippable_roms(roms: list[str], ports_file: Path, catalog: Path = CATALOG) -> set[str]:
    """Ported games whose port is installed (in ``ports_file``): only these can be flipped.

    The owner's ruling (AC-T15): with no installed port there is nothing to flip, so no badge.
    """
    installed = load_ports(ports_file)
    return {rom for title, rom in _ported(roms, catalog).items() if title in installed}


def mark(
    text: str,
    flippable: set[str],
    modes: dict[str, str] | None = None,
    clear: frozenset[str] = frozenset(),
) -> str:
    """Badge each flippable game for its mode (``modes``; else Emulator); unbadge ``clear``.

    ``clear`` is the ported games whose port isn't installed (any more): their ``cartridge`` is
    ours, never scraped, so it's removed. Running it again changes nothing, and it never
    undoes a flip: the mode comes from ``modes``.
    """
    modes = modes or {}
    seen: set[str] = set()

    def element(rom: str) -> str:
        return f"<cartridge>{badge_path(modes.get(rom, 'emulator'))}</cartridge>"

    def edit(m: re.Match[str]) -> str:
        block, path = m.group(0), entry_path(m.group(0))
        if path in clear:
            return re.sub(r"\n?[ \t]*<cartridge>[^<]*</cartridge>", "", block, count=1)
        if path not in flippable:
            return block
        seen.add(path)
        return set_element(block, "cartridge", element(path))[0]

    text = GAME_RE.sub(edit, text)
    missing = sorted(flippable - seen)
    if missing:
        text = re.sub(r"<gameList\s*/>", "<gameList>\n</gameList>", text, count=1)
        end = text.rindex("</gameList>")
        entries = "".join(
            f"  <game>\n    <path>./{xml_escape(r)}</path>\n    {element(r)}\n  </game>\n"
            for r in missing
        )
        text = text[:end] + entries + text[end:]
    return text


def _write_modes(roms_dir: Path, modes: dict[str, str]) -> None:
    target = roms_dir / BADGE_DIR / MODES_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(modes, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


def init(
    roms_dir: Path, badges_from: Path, ports_file: Path = PORTS_FILE, catalog: Path = CATALOG
) -> int:
    """Copy the badges and badge the flippable games; returns how many can be flipped.

    A game whose port has been removed loses its badge and goes back to Emulator.
    """
    target = roms_dir / BADGE_DIR
    target.mkdir(parents=True, exist_ok=True)
    card = (roms_dir / CARD).exists()  # the Showcase/Full card (AC-T14), when it's installed
    for name in [*BADGES.values(), *(CARD_IMAGES.values() if card else ())]:
        source = badges_from / name
        if not source.is_file():
            raise FlipError(f"badge not found: {source}")
        shutil.copyfile(source, target / name)
    roms = list_roms(roms_dir)
    flippable = flippable_roms(roms, ports_file, catalog)
    modes = {rom: m for rom, m in load_modes(roms_dir).items() if rom in flippable}
    _write_modes(roms_dir, modes)
    gamelist = roms_dir / "gamelist.xml"
    text = (
        gamelist.read_text(encoding="utf-8") if gamelist.exists() else "<gameList>\n</gameList>\n"
    )
    clear = frozenset(ported_roms(roms, catalog) - flippable)
    tmp = gamelist.with_name(gamelist.name + ".tmp")
    marked = mark(text, flippable, modes, clear)
    if card:
        marked = place_card(marked, current_mode(gamelist))
    tmp.write_text(marked, encoding="utf-8")
    tmp.replace(gamelist)
    return len(flippable)


def load_modes(roms_dir: Path) -> dict[str, str]:
    """The games flipped to Port, by ROM file name."""
    path = roms_dir / BADGE_DIR / MODES_FILE
    if not path.exists():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    modes = cast("dict[str, object]", data) if isinstance(data, dict) else None
    if modes is None or any(v != "port" for v in modes.values()):
        raise FlipError(f'{path} must map ROM file names to "port"')
    return dict.fromkeys(modes, "port")


def record_selected(system: str, path: str, note: Path | None = None) -> None:
    """Remember the highlighted N64 game (the game-selected event's arguments).

    ``note`` defaults to SELECTED_FILE looked up at call time, not bound at import.
    """
    if system == SYSTEM:
        (note or SELECTED_FILE).write_text(path, encoding="utf-8")


def flip(
    path: str,
    engine: Engine,
    roms_dir: Path,
    ports_file: Path = PORTS_FILE,
    catalog: Path = CATALOG,
) -> str | None:
    """Flip ``path`` between Emulator and Port; returns the new mode, or None if it can't be.

    Only a game whose port is installed can be flipped (AC-T15).
    """
    rom = Path(path).name
    if rom not in flippable_roms(list_roms(roms_dir), ports_file, catalog):
        return None
    modes = load_modes(roms_dir)
    new = "emulator" if modes.get(rom) == "port" else "port"
    if new == "port":
        modes[rom] = "port"
    else:
        del modes[rom]
    badge = (roms_dir / BADGE_DIR / BADGES[new]).read_bytes()
    engine.set_badge(path, badge)  # first: if the engine is down, nothing changes
    _write_modes(roms_dir, modes)
    return new


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Flip a ported N64 game between emulator and port."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init_p = sub.add_parser("init", help="copy the badges and mark ported games")
    init_p.add_argument(
        "--badges", type=Path, default=Path("/userdata/themes/n64-classic-mini/art")
    )
    sub.add_parser("flip", help="flip the highlighted game")
    sel = sub.add_parser("selected", help="the game-selected event script's entry")
    sel.add_argument("system")
    sel.add_argument("path")
    sel.add_argument("name", nargs="?")
    parser.add_argument("--roms", type=Path, default=Path("/userdata/roms/n64"))
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--ports", type=Path, default=PORTS_FILE, help="installed ports")
    args = parser.parse_args(argv)
    roms_dir: Path = args.roms
    try:
        if args.command == "init":
            count = init(roms_dir, args.badges, args.ports, args.catalog)
            print(f"{count} games can be flipped")
        elif args.command == "selected":
            record_selected(args.system, args.path)
        else:
            # The hotkey works in every context, a running game's too, where it must do nothing.
            if not SELECTED_FILE.exists() or Engine().game_running():
                return 0  # nothing highlighted yet, or in a game
            selected = SELECTED_FILE.read_text(encoding="utf-8")
            mode = flip(selected, Engine(), roms_dir, args.ports, args.catalog)
            print(mode or "no installed port")
    except (FlipError, EngineError, ShowcaseError, OSError) as exc:
        print(f"flip: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
