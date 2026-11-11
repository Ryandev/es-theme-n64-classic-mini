"""``n64mini-reset``: the case's Reset button captures a suspend point and exits the game (AC-T18).

The Radxa X4's 40-pin header belongs to its onboard RP2040 microcontroller, whose USB HID firmware
(``firmware/rp2040_reset/code.py``) sends ``KEY_F13`` when the physical Reset button is pressed.
Batocera's ``hotkeygen`` maps ``KEY_F13`` to ``n64mini-reset`` (``default_mapping.conf`` ->
``common_context.conf``), which runs ``n64mini-reset.sh``.

Like ``n64mini-launch.sh``, the shell script asks this module what to do so the logic is plain,
testable standard-library Python without subprocesses:

- ``decide``: checks whether a game is running (``GET /runningGame``). Outside a game it prints
  ``idle`` so pressing Reset in the carousel does nothing. Inside a game it prints ``port`` if the
  selected ROM is flipped to Port (``modes.json``), else ``emulator``.
- ``stage``: after ``hotkeygen --send save_state``, moves the state written since the press to
  ``<rom>.pending`` (with its screenshot), touching no player slot.
- ``open``: after the emulator exits, stages it if ``stage`` timed out, pins
  ``CurrentStateSlot = 9`` in ``mupen64plus.cfg`` and opens the Suspend Point List.

Every save mupen64plus makes goes to ``.st9`` (``WRITE_SLOT``): each launch passes
``-state_slot 9`` and setup turns ``n64.incrementalsavestates`` off, so no save, Reset or an
in-game hotkey, can land in (or wrap round into) the player's ``.st0..3``.

Runs on the console, so it uses only the standard library.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

from n64mini.console.engine import Engine, EngineError
from n64mini.console.flip import SELECTED_FILE, FlipError, load_modes
from n64mini.console.showcase import wait_for_launch_to_end

if TYPE_CHECKING:
    from collections.abc import Callable

SAVES_DIR: Final = Path("/userdata/saves/n64")
MUPEN_CFG: Final = Path("/userdata/system/configs/mupen64/mupen64plus.cfg")
TOPBAR_FIFO: Final = Path("/var/run/n64mini-topbar.cmd")
RESET_SHOT: Final = Path(tempfile.gettempdir()) / "n64mini-reset-shot.png"
SLOT_COUNT: Final = 10
USER_SLOTS: Final = 4
WRITE_SLOT: Final = 9  # see the module docstring: every mupen64plus save goes here
STAGE_ATTEMPTS: Final = 30  # 50 ms apart: mupen64plus gets 1.5 s to flush the state
# File times can trail the clock: the kernel stamps them from a coarse clock, and FAT keeps only
# 2 s. A resumed copy is written before the game boots, so it is always older than this.
MTIME_SLACK_NS: Final = 2_000_000_000
_SLOT_RE: Final = re.compile(r"^CurrentStateSlot\s*=.*$", re.MULTILINE)


def selected_rom(selected_file: Path = SELECTED_FILE) -> str:
    """The file name of the last selected N64 ROM, or ``""`` if none is recorded."""
    if not selected_file.is_file():
        return ""
    raw = selected_file.read_text(encoding="utf-8").strip()
    return Path(raw).name if raw else ""


def decide(
    engine: Engine,
    roms_dir: Path,
    selected_file: Path = SELECTED_FILE,
) -> str:
    """Return ``"idle"``, ``"emulator"``, or ``"port"`` for the current Reset press."""
    try:
        running = engine.game_running()
    except (EngineError, OSError):
        return "idle"
    if not running:
        return "idle"
    rom = selected_rom(selected_file)
    if not rom:
        return "emulator"
    try:
        modes = load_modes(roms_dir)
    except (FlipError, OSError, ValueError):
        return "emulator"
    return "port" if modes.get(rom) == "port" else "emulator"


def _fresh_reset_states(saves_dir: Path, rom: str, since_ns: int) -> list[tuple[int, int, Path]]:
    """Return the non-empty ``.st0..9`` states written since the Reset press at ``since_ns``.

    Age, not slot number, marks the new state: after a Start-resume ``.st9`` already holds the
    resumed copy, so taking any overflow slot would stage that stale copy before mupen64plus has
    flushed the new one (B3).
    """
    cutoff = since_ns - MTIME_SLACK_NS
    fresh: list[tuple[int, int, Path]] = []
    for slot in range(SLOT_COUNT):
        state = saves_dir / f"{rom}.st{slot}"
        if state.is_file():
            info = state.stat()
            if info.st_size > 0 and info.st_mtime_ns >= cutoff:
                fresh.append((info.st_mtime_ns, slot, state))
    return fresh


def _stage_pending_shot(shot: Path, pending_shot: Path, fallback_shot: Path | None) -> None:
    """Move ``shot`` (or ``fallback_shot`` from ``batocera-screenshot``) into ``pending_shot``."""
    if shot.is_file():
        shot.replace(pending_shot)
        if fallback_shot is not None:
            fallback_shot.unlink(missing_ok=True)
    elif fallback_shot is not None and fallback_shot.is_file():
        fallback_shot.replace(pending_shot)
    elif pending_shot.is_file():
        pending_shot.unlink()


def stage_pending(
    saves_dir: Path,
    rom: str,
    *,
    since_ns: int,
    fallback_shot: Path | None = None,
) -> bool:
    """Stage the state written since the Reset press (``since_ns``) as ``<rom>.pending``."""
    if not rom or not saves_dir.is_dir():
        return False
    overflow = _fresh_reset_states(saves_dir, rom, since_ns)
    if not overflow:
        return False
    overflow.sort()
    _, chosen_slot, chosen_state = overflow[-1]
    chosen_state.replace(saves_dir / f"{rom}.pending")
    _stage_pending_shot(
        saves_dir / f"{rom}.st{chosen_slot}.png",
        saves_dir / f"{rom}.pending.png",
        fallback_shot,
    )
    for _, old_slot, extra_state in overflow[:-1]:
        if old_slot >= USER_SLOTS:
            extra_state.unlink(missing_ok=True)
            (saves_dir / f"{rom}.st{old_slot}.png").unlink(missing_ok=True)
    return True


def wait_and_stage_pending(
    saves_dir: Path,
    rom: str,
    fallback_shot: Path = RESET_SHOT,
    *,
    since_ns: int,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll briefly after ``save_state`` until ``mupen64plus`` flushes a non-empty state."""
    for attempt in range(STAGE_ATTEMPTS):
        if stage_pending(saves_dir, rom, since_ns=since_ns, fallback_shot=fallback_shot):
            return True
        if attempt + 1 < STAGE_ATTEMPTS:
            sleep_fn(0.05)
    return False


def notify_suspend_drawer(fifo: Path = TOPBAR_FIFO) -> bool:
    """Ask ``n64mini-topbar`` over ``fifo`` to open the Suspend Point List on the carousel."""
    if not fifo.exists():
        return False
    try:
        fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, b"suspend\n")
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def pin_mupen_slot(cfg: Path = MUPEN_CFG, slot: int = WRITE_SLOT) -> bool:
    """Set ``CurrentStateSlot = <slot>`` in ``mupen64plus.cfg`` if present."""
    if not cfg.is_file():
        return False
    text = cfg.read_text(encoding="utf-8")
    updated, count = _SLOT_RE.subn(f"CurrentStateSlot = {slot}", text)
    if count == 0:
        return False
    tmp = cfg.with_name(cfg.name + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(cfg)
    return True


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``n64mini-reset.sh``."""
    parser = argparse.ArgumentParser(
        description="Handle the console's physical Reset button (AC-T18)."
    )
    parser.add_argument("action", choices=["decide", "stage", "open"])
    parser.add_argument("--roms", type=Path, default=Path("/userdata/roms/n64"))
    parser.add_argument("--saves", type=Path, default=SAVES_DIR)
    parser.add_argument("--selected", type=Path, default=SELECTED_FILE)
    parser.add_argument("--mupen-cfg", type=Path, default=MUPEN_CFG)
    parser.add_argument("--fifo", type=Path, default=TOPBAR_FIFO)
    parser.add_argument("--shot", type=Path, default=RESET_SHOT)
    parser.add_argument(
        "--since-ns",
        type=int,
        help="when Reset was pressed, in ns since the epoch; stage and open need it",
    )
    args = parser.parse_args(argv)
    if args.action in ("stage", "open") and args.since_ns is None:
        parser.error(f"{args.action} needs --since-ns")
    if args.action == "decide":
        sys.stdout.write(decide(Engine(), args.roms, args.selected) + "\n")
    elif args.action == "stage":
        wait_and_stage_pending(
            args.saves, selected_rom(args.selected), args.shot, since_ns=args.since_ns
        )
    else:  # open
        with contextlib.suppress(EngineError, OSError, TimeoutError):
            wait_for_launch_to_end(Engine().game_running)
        stage_pending(
            args.saves, selected_rom(args.selected), since_ns=args.since_ns, fallback_shot=args.shot
        )
        args.shot.unlink(missing_ok=True)
        pin_mupen_slot(args.mupen_cfg)
        notify_suspend_drawer(args.fifo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
