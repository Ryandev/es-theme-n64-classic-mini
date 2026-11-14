"""``n64mini-launch``: choose between the port and the emulator for an N64 launch (AC-T15, AC-T17).

Batocera runs ``n64mini-launch.sh`` instead of ``emulatorlauncher`` for N64
(``es_systems_n64.cfg``), with the same arguments. The script asks this module what to
run (the command comes back NUL-separated) and runs it, waiting so the power LED can go back
to red after the game (``n64mini.console.led``).

On a real game launch the script also starts the card-flip animation
(``n64mini.console.launch_animation``, AC-T17) before starting the game.

A game flipped to Port (``modes.json``, written by ``n64mini-flip``) runs its port, as
configured in ``ports.json``. Everything else is handed to ``emulatorlauncher``
unchanged, so normal games start exactly as before.

A game flipped to Port whose port isn't installed doesn't fall back to the emulator: the owner
chose Port, so a silent switch would hide the problem. The reason goes on screen (the engine's
notification) and the launch exits non-zero.

The Showcase/Full card (``showcase.CARD``) isn't a game: launching it switches the list (AC-T14).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Final, TypedDict, cast

from n64mini.console.engine import Engine, EngineError
from n64mini.console.flip import PORTS_FILE, RESUME_FILE, FlipError, load_modes, load_ports
from n64mini.console.reset import WRITE_SLOT
from n64mini.console.showcase import (
    CARD,
    CATALOG,
    ShowcaseError,
    load_catalog,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

EMULATORLAUNCHER: Final = "emulatorlauncher"


class LaunchError(Exception):
    """The game can't be launched as chosen."""


class Decision(TypedDict):
    """What the wrapper would run, and why."""

    mode: str
    rom: str
    command: list[str]


def rom_argument(args: list[str]) -> str:
    """The value of ``-rom`` in Batocera's launch arguments."""
    try:
        return args[args.index("-rom") + 1]
    except (ValueError, IndexError):
        raise LaunchError("no -rom in the launch arguments") from None


def rom_file_name(rom: str) -> str:
    """The ROM's file name, from a POSIX or (in the engine harness) Windows path."""
    # The Windows engine harness passes Windows paths; the console passes POSIX ones.
    return PureWindowsPath(rom).name if "\\" in rom else Path(rom).name


def consume_resume_args(rom_name: str, resume_file: Path = RESUME_FILE) -> list[str]:
    """Consume a one-shot ``Suspend Point List`` resume request for ``rom_name`` if present.

    Batocera 43.1's ``emulatorlauncher`` parses ``-state_slot`` and ``-state_filename``:
    ``mupenGenerator.py`` passes ``--savestate <state_filename>`` to ``mupen64plus`` while
    ``mupenConfig.py`` writes ``CurrentStateSlot = <state_slot>`` into ``mupen64plus.cfg``.
    The ``-state_slot`` is ``WRITE_SLOT``, as on every emulator launch (``decide``).
    """
    if not resume_file.is_file():
        return []
    try:
        raw: object = json.loads(resume_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        resume_file.unlink(missing_ok=True)
        return []
    resume_file.unlink(missing_ok=True)
    if not isinstance(raw, dict):
        return []
    entries = cast(
        "dict[str, object]", raw
    )  # typeshed json.loads returns Any -> dict[Unknown, Unknown]
    if entries.get("rom") != rom_name:
        return []
    state_file = entries.get("state_file")
    if not isinstance(state_file, str) or not Path(state_file).is_file():
        return []
    return ["-state_slot", str(WRITE_SLOT), "-state_filename", state_file]


def _without_state_slot(args: Sequence[str]) -> list[str]:
    """``args`` with any ``-state_slot N`` pair removed."""
    out: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == "-state_slot":
            skip = True
        else:
            out.append(arg)
    return out


def decide(
    args: list[str],
    roms_dir: Path,
    ports_file: Path,
    catalog: Path = CATALOG,
    *,
    resume_file: Path = RESUME_FILE,
) -> Decision:
    """Choose between the port and the emulator for Batocera's launch ``args``."""
    rom = rom_argument(args)
    name = rom_file_name(rom)
    if name == CARD:
        # Detached, so the launch ends at once and the engine saves its gamelist; the toggle
        # waits for that save, then switches (showcase.wait_for_launch_to_end).
        toggle = ["toggle", "--after-game", "--reload", "--roms", str(roms_dir)]
        detached = ["setsid", "-f", sys.executable, "-m", "n64mini.console.library", *toggle]
        return {"mode": "library", "rom": name, "command": detached}
    if load_modes(roms_dir).get(name) != "port":
        extra = [] if "-state_filename" in args else consume_resume_args(name, resume_file)
        # Every save goes to WRITE_SLOT. Without the pin mupen64plus saves into the slot it last
        # used, a player's .st0..3 included (B5), and the engine adds its own next free slot to a
        # plain launch when incremental saves are on (SaveState::setupSaveState), so replace it.
        emulator = [EMULATORLAUNCHER, *_without_state_slot(args), *extra]
        if "-state_slot" not in extra:
            emulator += ["-state_slot", str(WRITE_SLOT)]
        return {"mode": "emulator", "rom": name, "command": emulator}
    # A resume request is one-shot: left in place it would resume an old suspend point on the
    # next emulator launch. A port can't load the emulator's states, so it's dropped, and said.
    if consume_resume_args(name, resume_file):
        print(
            f"n64mini-launch: {name} is flipped to Port, which can't load a suspend point; "
            "flip the card to N64 to resume it",
            file=sys.stderr,
        )
    game = next((g for g in load_catalog(catalog) if g.rom.search(name)), None)
    if game is None:
        raise LaunchError(f"{name} is flipped to Port but isn't in the catalogue")
    try:
        command = load_ports(ports_file).get(game.title)
    except FlipError as exc:
        raise LaunchError(str(exc)) from exc
    if command is None:
        raise LaunchError(
            f"{game.title}: its PC port isn't installed. Flip the card back to N64, "
            "or update the console from the stick."
        )
    return {"mode": "port", "rom": name, "command": [a.replace("{rom}", rom) for a in command]}


def roms_dir_of(rom: str) -> Path:
    """The folder holding the ROM, as this process can open it."""
    if "\\" not in rom:
        return Path(rom).parent
    # Under WSL, a Windows path like C:\Users\x\roms\n64\game.z64 is /mnt/c/Users/x/roms/n64.
    win = PureWindowsPath(rom).parent
    drive = win.drive.rstrip(":").lower()
    return Path("/mnt", drive, *win.parts[1:])


def main(argv: list[str] | None = None) -> int:
    """Print the command to run, NUL-separated.

    ``--dry-run FILE`` records the decision instead; ``--ports FILE`` reads installed ports there;
    The card-flip animation is ``n64mini.console.launch_animation``, kept apart so deciding a
    launch never loads Pillow (a third of the decision's time on the 43.1 VM).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    record: Path | None = None
    ports = PORTS_FILE
    while args[:1] in (["--dry-run"], ["--ports"]):  # our options come before Batocera's
        if args[0] == "--dry-run":
            record = Path(args[1])
        else:
            ports = Path(args[1])
        args = args[2:]
    try:
        rom = rom_argument(args)
        decision: dict[str, object] = {**decide(args, roms_dir_of(rom), ports)}
        failed = False
    except (LaunchError, ShowcaseError, OSError, ValueError) as exc:
        print(f"n64mini-launch: {exc}", file=sys.stderr)
        failure: dict[str, object] = {"mode": "error", "error": str(exc), "args": args}
        decision, failed = failure, True
        if record is None:
            _show(str(exc))
    if record is not None:  # the engine harness: record the choice (or the failure) instead
        with record.open("a", encoding="utf-8") as out:
            out.write(json.dumps(decision) + "\n")
    if failed or record is not None:
        return 1 if failed else 0
    sys.stdout.write("\0".join(cast("list[str]", decision["command"])) + "\0")
    return 0


def _show(message: str) -> None:
    """Put a failed launch's reason on screen; stderr (the launch log) already has it.

    With no engine to answer there's no screen to show it on, so the log is the record.
    """
    try:
        Engine().notify(message)
    except (EngineError, OSError) as exc:
        print(f"n64mini-launch: couldn't show it on screen: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
