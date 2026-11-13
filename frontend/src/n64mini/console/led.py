"""Tell the RP2040 which colour the power LED shows: red in the menu, green in a game (C7).

The owner's rule (2026-11-13): RED while Batocera's menu is up, GREEN while a game runs. The
RP2040 can't see which, so ``n64mini-launch.sh`` runs ``led game`` before a game and ``led menu``
after it, and this writes ``G`` or ``R`` to the RP2040's USB serial console (its ``boot.py``
names it ``n64mini`` so it can be found). No RP2040 (a dev machine, the VM) is expected: it says
so on stderr and the launch goes on.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

SERIAL_DIR: Final = Path("/dev/serial/by-id")
DEVICE_GLOB: Final = "usb-n64mini_*-if00"
COMMANDS: Final = {"game": b"G", "menu": b"R"}


def find_device(serial_dir: Path = SERIAL_DIR) -> Path | None:
    """The RP2040's serial console, or ``None`` if it isn't plugged in."""
    if not serial_dir.is_dir():
        return None
    found = sorted(serial_dir.glob(DEVICE_GLOB))
    return found[0] if found else None


def send(state: str, serial_dir: Path = SERIAL_DIR) -> bool:
    """Write the colour for ``state`` (``game`` or ``menu``); ``False`` if there's no RP2040."""
    device = find_device(serial_dir)
    if device is None:
        return False
    # Non-blocking: a wedged serial port must never hold up a game launch.
    fd = os.open(device, os.O_WRONLY | os.O_APPEND | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        os.write(fd, COMMANDS[state])
    finally:
        os.close(fd)
    return True


def main(argv: list[str] | None = None) -> int:
    """``led game`` or ``led menu``. Exit 0 even without an RP2040; 2 on bad usage."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in COMMANDS:
        sys.stderr.write("usage: python3 -m n64mini.console.led game|menu\n")
        return 2
    serial_dir = Path(os.environ.get("N64MINI_SERIAL_DIR", str(SERIAL_DIR)))
    if not send(args[0], serial_dir):
        sys.stderr.write(f"n64mini-led: no RP2040 under {serial_dir}; LED left as it is\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
