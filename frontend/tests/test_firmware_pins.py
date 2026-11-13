"""The RP2040 firmware and its wiring table name the GPIO that is really on each header pin (C7).

The firmware and README once put RESET on GP3, POWER on GP2 and the green LED on GP5. Radxa's X4
table has GP29, GP28 and GP7 on those header pins, so the buttons and LED would have been dead.
The table is vendored in ``vendor/radxa-x4/`` and these tests hold both files to it.
"""

from __future__ import annotations

import re

import pytest

from n64mini.paths import FRONTEND_ROOT, REPO_ROOT

TABLE = FRONTEND_ROOT / "vendor" / "radxa-x4" / "gpio_header.tsv"
FIRMWARE = REPO_ROOT / "firmware" / "rp2040_reset" / "code.py"
WIRING = REPO_ROOT / "firmware" / "rp2040_reset" / "README.md"


def _header() -> dict[int, str]:
    rows = TABLE.read_text(encoding="utf-8").splitlines()[1:]
    return {int(pin): signal for pin, signal in (row.split("\t") for row in rows)}


def _mismatches(pairs: list[tuple[int, int]]) -> list[str]:
    header = _header()
    return [
        f"header pin {pin} is {header.get(pin, 'missing')}, not GP{gp}"
        for pin, gp in pairs
        if header.get(pin) != f"GPIO{gp:02d}"
    ]


def _firmware_pairs(text: str) -> list[tuple[int, int]]:
    return [(int(p), int(g)) for p, g in re.findall(r"\((\d+), board\.GP(\d+)\)", text)]


def _wiring_pairs(text: str) -> list[tuple[int, int]]:
    rows = [r for r in text.splitlines() if r.startswith("|")]
    found = [re.search(r"Header Pin (\d+)\*?\*? \| \*\*`GP(\d+)`", r) for r in rows]
    return [(int(m.group(1)), int(m.group(2))) for m in found if m]


def test_the_vendored_table_has_all_40_pins() -> None:
    header = _header()
    assert sorted(header) == list(range(1, 41))
    assert header[1] == "+3.3V"
    assert header[2] == "+5.0V"
    assert header[6] == "GND"


def test_the_firmware_uses_the_gpio_on_each_header_pin() -> None:
    pairs = _firmware_pairs(FIRMWARE.read_text(encoding="utf-8"))
    assert len(pairs) == 3  # RESET switch, red LED, green LED: an empty match checks nothing
    assert _mismatches(pairs) == []


def test_the_wiring_table_uses_the_gpio_on_each_header_pin() -> None:
    pairs = _wiring_pairs(WIRING.read_text(encoding="utf-8"))
    assert len(pairs) == 3
    assert _mismatches(pairs) == []


@pytest.mark.parametrize(
    ("planted", "message"),
    [
        ("RESET_SWITCH = (5, board.GP3)", "header pin 5 is GPIO29, not GP3"),
        ("LED_GREEN = (29, board.GP5)", "header pin 29 is GPIO07, not GP5"),
    ],
)
def test_a_wrong_gpio_is_caught(planted: str, message: str) -> None:
    """The old wiring, planted: the check must fail on it."""
    assert _mismatches(_firmware_pairs(planted)) == [message]
