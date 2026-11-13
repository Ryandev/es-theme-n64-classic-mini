"""Power LED: red in the Batocera menu, green while a game runs (owner, 2026-11-13; C7).

The console side writes ``G``/``R`` to the RP2040's serial console around each game; the firmware
turns that into the colour. Both halves are run here: the launch wrapper end to end with a stand-in
device, and the real ``code.py`` against stand-in CircuitPython modules.
"""

from __future__ import annotations

import runpy
import sys
import time
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from n64mini.console import led
from n64mini.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

FIRMWARE = REPO_ROOT / "firmware" / "rp2040_reset" / "code.py"
BOOT = REPO_ROOT / "firmware" / "rp2040_reset" / "boot.py"
DEVICE = "usb-n64mini_Console_LED_and_RESET_E660C0D1-if00"


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


def test_only_the_rp2040s_own_console_is_used(tmp_path: Path) -> None:
    """Another serial device, or the RP2040's second interface, is never written to."""
    serial_dir = tmp_path / "by-id"
    serial_dir.mkdir()
    other = serial_dir / "usb-FTDI_FT232R-if00-port0"
    second = serial_dir / "usb-n64mini_Console_LED_and_RESET_E660C0D1-if02"
    for path in (other, second):
        path.write_bytes(b"")
    assert led.find_device(serial_dir) is None
    assert not led.send("game", serial_dir)
    assert other.read_bytes() == b""
    assert second.read_bytes() == b""


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


def test_the_firmware_names_itself_as_the_console_looks_for_it() -> None:
    text = BOOT.read_text(encoding="utf-8")
    assert 'manufacturer="n64mini"' in text
    assert led.DEVICE_GLOB.startswith("usb-n64mini_")


# --- the firmware, against stand-in CircuitPython modules ----------------------------------------


class _StopLoopError(Exception):
    """Raised from the stand-in ``time.sleep`` once the script runs out: ends ``while True``."""


@dataclass
class _Pin:
    value: bool = False
    direction: object = None
    pull: object = None


@dataclass
class _Board:
    """Drives ``code.py``: per loop pass, whether USB is up and what the console sent."""

    script: list[tuple[bool, str]]
    pins: dict[str, _Pin] = field(default_factory=dict[str, _Pin])
    seen: list[str] = field(default_factory=list[str])
    pending: list[str] = field(default_factory=list[str])
    usb: bool = False

    def tick(self) -> None:
        if self.pins:  # after each pass: record the colour, then load the next pass
            red, green = self.pins["GP4"].value, self.pins["GP7"].value
            self.seen.append({(True, False): "red", (False, True): "green"}[(red, green)])
        if not self.script:
            raise _StopLoopError
        self.usb, sent = self.script.pop(0)
        self.pending.extend(sent)


class _Runtime:
    """``supervisor.runtime``: USB state and console bytes come from the board's script."""

    def __init__(self, board: _Board) -> None:
        self._board = board

    @property
    def usb_connected(self) -> bool:
        return self._board.usb

    @property
    def serial_bytes_available(self) -> int:
        return len(self._board.pending)


def _run_firmware(board: _Board, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    def pin(gp: str) -> _Pin:
        return board.pins.setdefault(gp, _Pin())

    def keyboard(_devices: object) -> None:
        return None

    def read(_n: int) -> str:
        return board.pending.pop(0)

    def sleep(_s: float) -> None:
        board.tick()

    stubs = {
        "board": types.SimpleNamespace(GP29="GP29", GP4="GP4", GP7="GP7"),
        "digitalio": types.SimpleNamespace(
            DigitalInOut=pin,
            Direction=types.SimpleNamespace(INPUT="in", OUTPUT="out"),
            Pull=types.SimpleNamespace(UP="up"),
        ),
        "supervisor": types.SimpleNamespace(runtime=_Runtime(board)),
        "usb_hid": types.SimpleNamespace(devices=()),
        "adafruit_hid": types.SimpleNamespace(),
        "adafruit_hid.keyboard": types.SimpleNamespace(Keyboard=keyboard),
        "adafruit_hid.keycode": types.SimpleNamespace(Keycode=types.SimpleNamespace(F13=0x68)),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(read=read))
    monkeypatch.setattr(time, "sleep", sleep)
    board.tick()  # load the first pass
    with pytest.raises(_StopLoopError):
        runpy.run_path(str(FIRMWARE))
    return board.seen


def test_the_firmware_is_red_in_the_menu_and_green_in_a_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [
        (False, ""),  # USB still coming up
        (True, ""),  # Batocera's menu
        (True, "G"),  # a game starts
        (True, "x"),  # noise changes nothing
        (True, "R"),  # back to the menu
        (True, "G"),  # another game...
        (False, ""),  # ...and the PC goes away mid-game
        (True, ""),  # back: the menu colour, not the stale game one
    ]
    seen = _run_firmware(_Board(script), monkeypatch)
    assert seen == ["red", "red", "green", "green", "red", "green", "red", "red"]
