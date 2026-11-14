"""Tell the RP2040 which colour the power LED shows: red in the menu, green in a game (C7).

The owner's rule (2026-11-13): RED while Batocera's menu is up, GREEN while a game runs. The
RP2040 can't see which, so ``n64mini-launch.sh`` runs ``led game`` before a game and ``led menu``
after it, and this writes ``G`` or ``R`` to the RP2040's USB data channel: the second serial
port its ``boot.py`` turns on (``if02``; ``if00`` is the REPL console, where a stray Ctrl-C would
stop the firmware). ``boot.py`` names the device ``n64mini`` so it can be found. The port is put
in raw mode (no echo bouncing bytes back) and never allowed to hold up a launch (Gemini review
#3). No RP2040 (a dev machine, the VM) is expected: it says so on stderr and the launch goes on.
"""

from __future__ import annotations

import fcntl
import os
import struct
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Final

SERIAL_DIR: Final = Path("/dev/serial/by-id")
DEVICE_GLOB: Final = "usb-n64mini_*-if02"
DRAIN_S: Final = 0.5  # how long the byte may take to leave before it is dropped
COMMANDS: Final = {"game": b"G", "menu": b"R"}


def find_device(serial_dir: Path = SERIAL_DIR) -> Path | None:
    """The RP2040's LED data channel, or ``None`` if it isn't plugged in."""
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
        if os.isatty(fd):
            tty.setraw(fd)  # no echo or line discipline between us and the RP2040
        os.write(fd, COMMANDS[state])
        if os.isatty(fd):
            _drain(fd)
    finally:
        os.close(fd)
    return True


def _queued(fd: int) -> int:
    """Bytes still waiting to go out on the tty ``fd``."""
    count: int = struct.unpack("i", fcntl.ioctl(fd, termios.TIOCOUTQ, b"\0\0\0\0"))[0]
    return count


def _drain(fd: int) -> None:
    """Let the byte go, then drop anything still stuck so ``close`` can't block.

    Closing a tty with output pending waits for it to drain, up to 30 s, if the RP2040 isn't
    reading (its firmware stopped): a game launch must not wait on that.
    """
    deadline = time.monotonic() + DRAIN_S
    while _queued(fd) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _queued(fd):
        termios.tcflush(fd, termios.TCOFLUSH)


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
