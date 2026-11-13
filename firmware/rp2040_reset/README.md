# Radxa X4 RP2040 Front-Panel & Reset Firmware (`AC-T18`, `J-04`, `J-07`)

The Radxa X4's 40-pin GPIO header belongs to its onboard RP2040, which talks to the Intel N100 over
USB 2.0. [`code.py`](code.py) runs on it: the **momentary `RESET` microswitch** (`Omron SS-5GL`)
and the **3-lead Red/Green front LED** (`Kingbright L-93WEGW`).

**The RP2040 only runs while the PC is on** (Radxa X4 V1.11 schematic, sheet 21 "RS2040"): it is
powered from the system 3.3 V rail (`P3V3_S`, via `P3V_RP`), and its `RUN` pin follows the N100's
platform reset (`PLTRST_2040`). So it can't wake the PC or light a standby LED, and the header's
3.3 V pins (also `P3V3_S`) are dead in standby. The header's 5 V pins (`P5V_A`, via a diode) are
the only always-on supply there. The **`POWER` microswitch** is therefore wired across the X4's
own power button (`K1`, sheet 9: `PMC_PWRBTN_N`), which the RP2040 isn't connected to.

Header pins and GPIOs below are from Radxa's X4 GPIO table
(`frontend/vendor/radxa-x4/gpio_header.tsv`, checked against the schematic);
`frontend/tests/test_firmware_pins.py` fails if this table or `code.py` names a different GPIO.

## Complete Wiring Table (`Omron SS-5GL` x2 + `Kingbright L-93WEGW` Bi-Colour LED)

> **Pin 1 identification on the inverted Radxa X4:** **Pin 1 (`3V3`)** is the **square through-hole
> solder pad** on the 40-pin header footprint (Pins 2–40 are round pads). Odd pins (`1, 3, 5, 7 …
> 39`) are in the Pin-1 row; even pins (`2, 4, 6, 8 … 40`) are in the adjacent row. When the Radxa
> X4 is mounted inverted in `lower_tub`, the 40-pin footprint sits along the **front (`-Y`) edge of
> the PCB**, directly behind the `POWER`/`RESET` switches and LED cradle (`15–25 mm` wire runs).

| Component | Terminal / Lead | Series Component | Radxa X4 Pad / Pin | RP2040 / System Signal & Behaviour |
|---|---|---|---|---|
| **`RESET` Switch** (`Omron SS-5GL`, right `X = +32.32 mm`) | **`NO`** (Middle terminal, Normally Open) | — | **40-pin Header Pin 5** | **`GP29`** (`INPUT` with internal `Pull.UP` in `code.py`; active-LOW press sends USB HID `KEY_F13` -> `n64mini-reset`) |
| **`RESET` Switch** (`Omron SS-5GL`, right `X = +32.32 mm`) | **`COM`** (Bottom terminal nearest hinge) | — | **40-pin Header Pin 6** | **`GND`** (leave top `NC` terminal unconnected) |
| **`POWER` Switch** (`Omron SS-5GL`, left `X = -32.32 mm`) | **`NO`** (Middle terminal, Normally Open) | — | **Signal side of the X4's onboard power button `K1`** | **`PMC_PWRBTN_N`** (active-LOW: a press wakes the N100 from standby, or asks Batocera to shut down) |
| **`POWER` Switch** (`Omron SS-5GL`, left `X = -32.32 mm`) | **`COM`** (Bottom terminal nearest hinge) | — | **Ground side of `K1`** | **`GND`** (leave top `NC` terminal unconnected) |
| **Front Status LED** (`Kingbright L-93WEGW`, `X = 0`) | **Pin 1 — Red Anode** (`625 nm`, outer lead on flat side) | **`330 Ω` 0.25W resistor** | **40-pin Header Pin 7** | **`GP4`** (`OUTPUT`: on while the RP2040 is starting, before USB is up; it can't show standby, see above) |
| **Front Status LED** (`Kingbright L-93WEGW`, `X = 0`) | **Pin 2 — Common Cathode** (centre longest lead) | — | **40-pin Header Pin 9** | **`GND`** |
| **Front Status LED** (`Kingbright L-93WEGW`, `X = 0`) | **Pin 3 — Green Anode** (`565 nm`, opposite outer lead) | **`330 Ω` 0.25W resistor** | **40-pin Header Pin 29** | **`GP7`** (`OUTPUT`: on while the console is on) |

LED pinout from Kingbright's L-93WEGW datasheet: 1 anode red, 2 common cathode, 3 anode green.

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
