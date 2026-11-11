# Radxa X4 RP2040 Front-Panel & Reset Firmware (`AC-T18`, `J-03`, `J-04`, `J-07`)

The Radxa X4's 40-pin GPIO header is wired to its onboard RP2040 microcontroller (powered by the
always-on `S5` standby rail whenever USB-C PD power is connected) and connected internally to the
Intel Processor N100 over USB 2.0.

[`code.py`](code.py) runs on the onboard RP2040 to manage both the **3-lead Red/Green front status
LED** (`Kingbright L-93WEGW`) and the **momentary `RESET` microswitch** (`Omron SS-5GL`), while the
**momentary `POWER` microswitch** (`Omron SS-5GL`) triggers hardware `PWR_BTN_N` (`S5` wake / ACPI
shutdown).

## Complete Wiring Table (`Omron SS-5GL` x2 + `Kingbright L-93WEGW` Bi-Colour LED)

> **Pin 1 identification on the inverted Radxa X4:** **Pin 1 (`3V3`)** is the **square through-hole
> solder pad** on the 40-pin header footprint (Pins 2–40 are round pads). Odd pins (`1, 3, 5, 7 …
> 39`) are in the Pin-1 row; even pins (`2, 4, 6, 8 … 40`) are in the adjacent row. When the Radxa
> X4 is mounted inverted in `lower_tub`, the 40-pin footprint sits along the **front (`-Y`) edge of
> the PCB**, directly behind the `POWER`/`RESET` switches and LED cradle (`15–25 mm` wire runs).

| Component | Terminal / Lead | Series Component | Radxa X4 Pad / Pin | RP2040 / System Signal & Behaviour |
|---|---|---|---|---|
| **`RESET` Switch** (`Omron SS-5GL`, right `X = +32.32 mm`) | **`NO`** (Middle terminal, Normally Open) | — | **40-pin Header Pin 5** | **`GP3`** (`INPUT` with internal `Pull.UP` in `code.py`; active-LOW press sends USB HID `KEY_F13` -> `n64mini-reset`) |
| **`RESET` Switch** (`Omron SS-5GL`, right `X = +32.32 mm`) | **`COM`** (Bottom terminal nearest hinge) | — | **40-pin Header Pin 6** | **`GND`** (leave top `NC` terminal unconnected) |
| **`POWER` Switch** (`Omron SS-5GL`, left `X = -32.32 mm`) | **`NO`** (Middle terminal, Normally Open) | — | **Onboard `PWR` button signal pad** *(or in parallel with Header Pin 3)* | **`PWR_BTN_N`** (active-LOW momentary pulse wakes Intel N100 from `S5` standby or triggers clean Batocera ACPI shutdown) |
| **`POWER` Switch** (`Omron SS-5GL`, left `X = -32.32 mm`) | **`COM`** (Bottom terminal nearest hinge) | — | **40-pin Header Pin 14** *(or `PWR` button `GND` pad)* | **`GND`** (leave top `NC` terminal unconnected) |
| **Front Status LED** (`Kingbright L-93WEGW`, `X = 0`) | **Pin 1 — Red Anode** (`625 nm`, outer lead on flat side) | **`330 Ω` 0.25W resistor** | **40-pin Header Pin 7** | **`GP4`** (`OUTPUT`: driven `HIGH` when `supervisor.runtime.usb_connected` is `False` -> **Red in `S5` standby**) |
| **Front Status LED** (`Kingbright L-93WEGW`, `X = 0`) | **Pin 2 — Common Cathode** (centre longest lead) | — | **40-pin Header Pin 9** | **`GND`** |
| **Front Status LED** (`Kingbright L-93WEGW`, `X = 0`) | **Pin 3 — Green Anode** (`565 nm`, opposite outer lead) | **`330 Ω` 0.25W resistor** | **40-pin Header Pin 29** | **`GP5`** (`OUTPUT`: driven `HIGH` when `supervisor.runtime.usb_connected` is `True` -> **Green when console is `ON`**) |

## Flashing on the Radxa X4

1. Short the onboard RP2040 `BOOTSEL` jumper/button while applying USB-C power (or assert
   `RUN`/`BOOTSEL` per the Radxa X4 RP2040 docs) so the `RPI-RP2` mass-storage volume mounts on
   Batocera.
2. Copy the Raspberry Pi Pico CircuitPython `.uf2` image to `RPI-RP2`; it re-enumerates as
   `CIRCUITPY`.
3. Copy `adafruit_hid` into `CIRCUITPY/lib/` and copy [`code.py`](code.py) from this folder to
   `CIRCUITPY/code.py`.

## How Batocera handles `KEY_F13`

- `bundle/console/hotkeygen-default_mapping.conf` (`/userdata/system/configs/hotkeygen/default_mapping.conf`)
  maps `"KEY_F13": "n64mini-reset"`.
- `bundle/console/hotkeygen-common_context.conf` (`/userdata/system/configs/hotkeygen/common_context.conf`)
  maps `"n64mini-reset"` to `/userdata/system/n64mini/python/n64mini/console/data/n64mini-reset.sh`.
- When a game is running in **Emulator** mode, `n64mini-reset.sh` hands off to a detached copy of
  itself (hotkeygen runs it with `os.system` and reads no keys until it returns), which runs
  `hotkeygen --send save_state` (saving `<rom>.st9` and `<rom>.st9.png`), stages `<rom>.pending`,
  exits the emulator (`hotkeygen --send exit`) and opens the `Suspend Point List` in Pending Save
  mode.
- When a game is running in **Port** mode, `n64mini-reset.sh` exits the port cleanly.
- When no game is running (in the carousel), pressing Reset does nothing.
