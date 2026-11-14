"""Power LED: red in the Batocera menu, green while a game runs (owner, 2026-11-13; C7).

The console side writes ``G``/``R`` to the RP2040's USB data channel around each game; the
firmware turns that into the colour. Both halves are run here: the console side against a stand-in
file and a real pseudo-terminal, and the real ``code.py`` against stand-in CircuitPython modules
that behave like CircuitPython (pins start as inputs, an input pin's value can't be set, a
pull-up reads high), including its RESET path (Gemini review #2 and #3, 2026-11-14).
"""

from __future__ import annotations

import os
import runpy
import sys
import termios
import time
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from n64mini.console import led
from n64mini.paths import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

FIRMWARE = REPO_ROOT / "firmware" / "rp2040_reset" / "code.py"
BOOT = REPO_ROOT / "firmware" / "rp2040_reset" / "boot.py"
DEVICE = "usb-n64mini_Console_LED_and_RESET_E660C0D1-if02"


def _device(tmp_path: Path) -> Path:
    serial_dir = tmp_path / "by-id"
    serial_dir.mkdir()
    dev = serial_dir / DEVICE
    dev.write_bytes(b"")
    return dev


def test_a_game_sends_g_and_the_menu_sends_r(tmp_path: Path) -> None:
    dev = _device(tmp_path)
    assert led.send("game", dev.parent)
    assert led.send("menu", dev.parent)
    assert dev.read_bytes() == b"GR"


def test_only_the_rp2040s_data_channel_is_used(tmp_path: Path) -> None:
    """Another serial device, or the RP2040's REPL console (if00), is never written to."""
    serial_dir = tmp_path / "by-id"
    serial_dir.mkdir()
    other = serial_dir / "usb-FTDI_FT232R-if00-port0"
    console = serial_dir / "usb-n64mini_Console_LED_and_RESET_E660C0D1-if00"
    for path in (other, console):
        path.write_bytes(b"")
    assert led.find_device(serial_dir) is None
    assert not led.send("game", serial_dir)
    assert other.read_bytes() == b""
    assert console.read_bytes() == b""


@pytest.fixture
def pty_device(tmp_path: Path) -> Iterator[tuple[Path, int, int]]:
    """A real tty standing in for the RP2040's data channel: (by-id dir, master fd, slave fd)."""
    master, slave = os.openpty()
    serial_dir = tmp_path / "by-id"
    serial_dir.mkdir()
    (serial_dir / DEVICE).symlink_to(os.ttyname(slave))
    yield serial_dir, master, slave
    os.close(master)
    os.close(slave)


def test_a_real_tty_gets_the_byte_raw_with_no_echo(pty_device: tuple[Path, int, int]) -> None:
    serial_dir, master, slave = pty_device
    assert termios.tcgetattr(slave)[3] & termios.ECHO  # a fresh tty echoes
    assert led.send("game", serial_dir)
    assert os.read(master, 16) == b"G"
    assert not termios.tcgetattr(slave)[3] & termios.ECHO  # left raw: nothing bounces back


def test_a_port_that_never_drains_cant_hold_up_a_launch(
    pty_device: tuple[Path, int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The RP2040 stopped reading: the byte is dropped after DRAIN_S instead of close() waiting."""
    serial_dir, _master, _slave = pty_device
    flushed: list[int] = []

    def stuck(_fd: int) -> int:
        return 1

    def flush(_fd: int, queue: int) -> None:
        flushed.append(queue)

    monkeypatch.setattr(led, "_queued", stuck)
    monkeypatch.setattr(led, "DRAIN_S", 0.05)
    monkeypatch.setattr(termios, "tcflush", flush)
    start = time.monotonic()
    assert led.send("menu", serial_dir)
    assert time.monotonic() - start < 1.0
    assert flushed == [termios.TCOFLUSH]


def test_no_rp2040_leaves_the_led_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("N64MINI_SERIAL_DIR", str(tmp_path / "missing"))
    assert led.main(["game"]) == 0
    assert capsys.readouterr().err == (
        f"n64mini-led: no RP2040 under {tmp_path / 'missing'}; LED left as it is\n"
    )


def test_main_writes_to_the_device_it_finds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dev = _device(tmp_path)
    monkeypatch.setenv("N64MINI_SERIAL_DIR", str(dev.parent))
    assert led.main(["menu"]) == 0
    assert dev.read_bytes() == b"R"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("argv", [[], ["green"], ["game", "menu"]])
def test_bad_usage_exits_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert led.main(argv) == 2
    assert capsys.readouterr().err == "usage: python3 -m n64mini.console.led game|menu\n"


def test_the_firmware_names_itself_and_opens_the_data_channel() -> None:
    text = BOOT.read_text(encoding="utf-8")
    assert 'manufacturer="n64mini"' in text
    assert "usb_cdc.enable(console=True, data=True)" in text
    assert led.DEVICE_GLOB == "usb-n64mini_*-if02"  # the data channel, not the if00 REPL


# --- the firmware, against stand-in CircuitPython modules ----------------------------------------


class _StopLoopError(Exception):
    """Raised from the stand-in ``time.sleep`` once the script runs out: ends ``while True``."""


INPUT, OUTPUT, UP = "in", "out", "up"


class _Pin:
    """``digitalio.DigitalInOut`` as CircuitPython has it: an input until made an output, an
    input's value can't be set, and with a pull-up an open switch reads high."""

    def __init__(self, board: _Board, gp: str) -> None:
        self._board, self._gp = board, gp
        self.direction = INPUT
        self.pull: str | None = None
        self._out = False

    @property
    def value(self) -> bool:
        if self.direction == OUTPUT:
            return self._out
        pressed = self._gp == "GP29" and self._board.reset_down
        return not pressed if self.pull == UP else False

    @value.setter
    def value(self, v: bool) -> None:
        if self.direction != OUTPUT:
            msg = "Cannot set value when direction is input."
            raise AttributeError(msg)
        self._out = v


class _Keyboard:
    def __init__(self, board: _Board) -> None:
        self._board = board

    def press(self, key: int) -> None:
        self._board.keys.append(("press", key))

    def release(self, key: int) -> None:
        self._board.keys.append(("release", key))


@dataclass
class _Board:
    """Drives ``code.py``: per loop pass, whether USB is up, what the console sent on the data
    channel, and whether RESET is held."""

    script: list[tuple[bool, bytes, bool]]
    pins: dict[str, _Pin] = field(default_factory=dict[str, _Pin])
    seen: list[str] = field(default_factory=list[str])
    pending: bytearray = field(default_factory=bytearray)
    keys: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    usb: bool = False
    reset_down: bool = False
    clock: float = 0.0

    def tick(self) -> None:
        if "GP4" in self.pins:  # after each pass: record the colour, then load the next pass
            red, green = self.pins["GP4"].value, self.pins["GP7"].value
            self.seen.append({(True, False): "red", (False, True): "green"}[(red, green)])
        if not self.script:
            raise _StopLoopError
        self.usb, sent, self.reset_down = self.script.pop(0)
        self.pending.extend(sent)
        self.clock += 0.1  # a pass apart is longer than the 30 ms debounce


class _Data:
    """``usb_cdc.data``: the bytes the console sent, as ``in_waiting`` and ``read``."""

    def __init__(self, board: _Board) -> None:
        self._board = board

    @property
    def in_waiting(self) -> int:
        return len(self._board.pending)

    def read(self, n: int) -> bytes:
        n = min(n, self.in_waiting)  # like the real port: never more than has arrived
        out = bytes(self._board.pending[:n])
        del self._board.pending[:n]
        return out


class _Runtime:
    """``supervisor.runtime``: USB state comes from the board's script."""

    def __init__(self, board: _Board) -> None:
        self._board = board

    @property
    def usb_connected(self) -> bool:
        return self._board.usb


F13 = 0x68


def _run_firmware(board: _Board, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    def pin(gp: str) -> _Pin:
        return board.pins.setdefault(gp, _Pin(board, gp))

    def keyboard(_devices: object) -> _Keyboard:
        return _Keyboard(board)

    def sleep(_s: float) -> None:
        board.tick()

    def monotonic() -> float:
        return board.clock

    stubs = {
        "board": types.SimpleNamespace(GP29="GP29", GP4="GP4", GP7="GP7"),
        "digitalio": types.SimpleNamespace(
            DigitalInOut=pin,
            Direction=types.SimpleNamespace(INPUT=INPUT, OUTPUT=OUTPUT),
            Pull=types.SimpleNamespace(UP=UP),
        ),
        "supervisor": types.SimpleNamespace(runtime=_Runtime(board)),
        "usb_cdc": types.SimpleNamespace(data=_Data(board)),
        "usb_hid": types.SimpleNamespace(devices=()),
        "adafruit_hid": types.SimpleNamespace(),
        "adafruit_hid.keyboard": types.SimpleNamespace(Keyboard=keyboard),
        "adafruit_hid.keycode": types.SimpleNamespace(Keycode=types.SimpleNamespace(F13=F13)),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(time, "monotonic", monotonic)
    board.tick()  # load the first pass
    with pytest.raises(_StopLoopError):
        runpy.run_path(str(FIRMWARE))
    return board.seen


def test_the_firmware_is_red_in_the_menu_and_green_in_a_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [
        (False, b"", False),  # USB still coming up
        (True, b"", False),  # Batocera's menu
        (True, b"G", False),  # a game starts
        (True, b"x", False),  # noise changes nothing
        (True, b"R", False),  # back to the menu
        (True, b"G", False),  # another game...
        (False, b"", False),  # ...and the PC goes away mid-game
        (True, b"", False),  # back: the menu colour, not the stale game one
    ]
    seen = _run_firmware(_Board(script), monkeypatch)
    assert seen == ["red", "red", "green", "green", "red", "green", "red", "red"]


def test_reset_sends_f13_while_held_and_releases_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RESET path runs too: press -> F13 down, release -> F13 up; nothing while USB is down."""
    script = [
        (False, b"", True),  # held before USB is up: no keyboard yet, nothing sent
        (True, b"", False),
        (True, b"", True),  # pressed
        (True, b"", True),  # still held: no repeat
        (True, b"", False),  # released
    ]
    board = _Board(script)
    _run_firmware(board, monkeypatch)
    assert board.keys == [("press", F13), ("release", F13)]
