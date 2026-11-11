"""A port's setup recipe: the steps between "unpacked" and "listed in ``ports.json``" (AC-S04).

``setup`` downloads and unpacks a port (and any extra archives) into a build folder beside the
installed one. The recipe then, in the build folder:

1. finds the owner's ROM of the port's game: files whose names match the catalogue's pattern,
   of which one must have a SHA-1 (in z64 byte order) the port accepts, and writes it there in
   z64 byte order (``rom``);
2. runs its preparation commands (``run``), such as Torch building a HarbourMasters ``.o2r``;
3. deletes what's no longer needed (``remove``).

After ``setup`` swaps the build in, ``finish`` links files into the port's data folder
(``links``: saves and config live there, outside the folder an update replaces) and lists the
port in ``ports.json`` with its launch ``command``, so the game becomes flippable (AC-T15).

Placeholders in commands: ``{dir}`` the installed folder, ``{data}`` the data folder,
``{build}`` the build folder, ``{userdata}``.

Runs on the console, so it uses only the standard library.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, cast

from n64mini.console.flip import FlipError, load_ports
from n64mini.console.showcase import CATALOG, ShowcaseError, load_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping

PORTS: Final = "system/n64mini/ports.json"  # under /userdata; flip.PORTS_FILE is this, absolute
DATA_DIR: Final = "saves/n64mini"  # a port's saves and config, kept across its updates
RUN_TIMEOUT: Final = 900  # seconds; Torch takes ~7 s on the VM
_SHA1_RE: Final = re.compile(r"[0-9a-f]{40}")
_PLACEHOLDER_RE: Final = re.compile(r"\{(\w+)\}")
# The first word of an N64 ROM in each byte order (z64 is the console's own).
_Z64: Final = b"\x80\x37\x12\x40"
_V64: Final = b"\x37\x80\x40\x12"  # bytes swapped in pairs
_N64: Final = b"\x40\x12\x37\x80"  # 32-bit words reversed
_DUMPS: Final = frozenset({".z64", ".v64", ".n64"})


class RecipeError(Exception):
    """A recipe step failed, or the recipe itself is malformed."""


class RomMissingError(RecipeError):
    """The owner has no usable ROM for the port yet; it waits, rather than failing."""


@dataclass(frozen=True, slots=True)
class Run:
    """One preparation command, run in ``cwd`` (relative to the build folder)."""

    argv: tuple[str, ...]
    cwd: str = ""


@dataclass(frozen=True, slots=True)
class Recipe:
    """How a port is prepared and launched."""

    game: str  # catalogue title: the key in ports.json
    command: tuple[str, ...]
    rom_sha1: frozenset[str] = frozenset()  # accepted dumps, z64 byte order; empty: no ROM
    rom: str = ""  # where the ROM goes, in the build folder
    run: tuple[Run, ...] = ()
    remove: tuple[str, ...] = ()
    links: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Paths:
    """Where one port's setup reads and writes."""

    userdata: Path
    build: Path  # <installed>.new while preparing
    installed: Path
    data: Path
    roms: Path

    def expand(self, word: str) -> str:
        """Replace the placeholders in one command word. Raises on an unknown one."""
        values = {
            "dir": str(self.installed),
            "data": str(self.data),
            "build": str(self.build),
            "userdata": str(self.userdata),
        }

        def value(m: re.Match[str]) -> str:
            if m.group(1) not in values:
                raise RecipeError(f"unknown placeholder {m.group(0)} in {word!r}")
            return values[m.group(1)]

        return _PLACEHOLDER_RE.sub(value, word)


def relative(value: str, what: str) -> str:
    """``value`` if it's a relative path that stays inside its folder; else raise."""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise RecipeError(f"{what} must be a relative path inside its folder: {value!r}")
    return value


def _words(value: object, what: str) -> tuple[str, ...]:
    items = cast("list[object]", value) if isinstance(value, list) else None
    if not items or not all(isinstance(w, str) and w for w in items):
        raise RecipeError(f"{what} must be a non-empty list of words")
    return tuple(str(w) for w in items)


def _strings(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    return _words(raw[key], key) if key in raw else ()


def _run(raw: object) -> Run:
    entry = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    cwd = str(entry.get("cwd", ""))
    return Run(_words(entry.get("argv"), "run argv"), relative(cwd, "run cwd") if cwd else "")


def parse(raw: Mapping[str, object]) -> Recipe:
    """A port's recipe from its manifest entry. Raises RecipeError if it's malformed."""
    sha1 = frozenset(s.lower() for s in _strings(raw, "rom_sha1"))
    if not all(_SHA1_RE.fullmatch(s) for s in sha1):
        raise RecipeError(f"rom_sha1 must be SHA-1 digests: {sorted(sha1)}")
    rom = str(raw.get("rom", ""))
    if bool(sha1) != bool(rom):
        raise RecipeError("rom and rom_sha1 go together: where the ROM goes, and which dumps")
    runs = raw.get("run", [])
    if not isinstance(runs, list):
        raise RecipeError("run must be a list of commands")
    return Recipe(
        game=str(raw["game"]),
        command=_words(raw.get("command"), "command"),
        rom_sha1=sha1,
        rom=relative(rom, "rom") if rom else "",
        run=tuple(_run(r) for r in cast("list[object]", runs)),
        remove=tuple(relative(p, "remove") for p in _strings(raw, "remove")),
        links=tuple(relative(p, "links") for p in _strings(raw, "links")),
    )


def to_z64(data: bytes) -> bytes | None:
    """The ROM in z64 (big-endian) byte order, or None if it isn't an N64 ROM."""
    head = data[:4]
    if head == _Z64:
        return data
    out = bytearray(data)
    if head == _V64:
        out[0::2], out[1::2] = data[1::2], data[0::2]
    elif head == _N64:
        out[0::4], out[1::4], out[2::4], out[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
    else:
        return None
    return bytes(out)


def find_rom(roms: Path, game: str, accepted: frozenset[str], catalog: Path = CATALOG) -> bytes:
    """The owner's dump of ``game`` that the port accepts, in z64 byte order.

    Raises RomMissingError if there's no dump of it, or none of the right version.
    """
    try:
        pattern = next(g.rom for g in load_catalog(catalog) if g.title == game)
    except StopIteration:
        raise RecipeError(f"{game!r} isn't in the catalogue") from None
    except ShowcaseError as exc:
        raise RecipeError(str(exc)) from exc
    found = sorted(p.name for p in roms.iterdir()) if roms.is_dir() else []
    names = [n for n in found if pattern.search(n)]
    tried: list[str] = []
    for name in names:
        if PurePosixPath(name).suffix.lower() not in _DUMPS:
            continue  # zipped dumps aren't unpacked here
        data = to_z64((roms / name).read_bytes())
        if data is None:
            tried.append(f"{name} (not an N64 ROM)")
            continue
        digest = hashlib.sha1(data, usedforsecurity=False).hexdigest()
        if digest in accepted:
            return data
        tried.append(f"{name} (SHA-1 {digest})")
    if not tried:
        raise RomMissingError(f"no ROM of {game} in {roms} (names matching /{pattern.pattern}/)")
    raise RomMissingError(f"no dump of {game} is the version the port needs: {', '.join(tried)}")


def _execute(step: Run, paths: Paths) -> None:
    argv = [paths.expand(w) for w in step.argv]
    cwd = paths.build / step.cwd
    try:
        done = subprocess.run(  # noqa: S603 - manifest's own command, no shell (owner OK)
            argv, cwd=cwd, capture_output=True, text=True, timeout=RUN_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecipeError(f"{argv[0]}: {exc}") from exc
    if done.returncode != 0:
        tail = "\n".join((done.stderr or done.stdout).splitlines()[-5:])
        raise RecipeError(f"{argv[0]} exited {done.returncode}: {tail}")


def prepare(recipe: Recipe, paths: Paths, catalog: Path = CATALOG) -> None:
    """Run the recipe in the build folder: place the ROM, run the commands, tidy up."""
    if recipe.rom:
        rom = paths.build / recipe.rom
        rom.parent.mkdir(parents=True, exist_ok=True)
        rom.write_bytes(find_rom(paths.roms, recipe.game, recipe.rom_sha1, catalog))
    for step in recipe.run:
        _execute(step, paths)
    for name in recipe.remove:
        path = paths.build / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for name in recipe.links:
        if not (paths.build / name).exists():
            raise RecipeError(f"the recipe should have made {name}, but it isn't there")


def _link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(link.name + ".tmp")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    tmp.replace(link)


def finish(recipe: Recipe, paths: Paths) -> None:
    """After the swap: link the data folder to the port's files, and list the port."""
    paths.data.mkdir(parents=True, exist_ok=True)
    for name in recipe.links:
        _link(paths.data / name, paths.installed / name)
    ports_file = paths.userdata / PORTS
    try:
        ports = load_ports(ports_file)
    except (FlipError, ValueError) as exc:
        raise RecipeError(f"{ports_file}: {exc}") from exc
    ports[recipe.game] = [paths.expand(w) for w in recipe.command]
    ports_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = ports_file.with_name(ports_file.name + ".tmp")
    tmp.write_text(json.dumps(ports, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(ports_file)
