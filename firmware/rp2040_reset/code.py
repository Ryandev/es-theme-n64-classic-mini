# CircuitPython firmware for the Radxa X4 onboard RP2040 (AC-T18, J-03, J-04, J-07).
#
# Copy this file to the RP2040's CIRCUITPY drive as `code.py` (with `adafruit_hid` in `lib/`).
# Since the RP2040 is powered by the Radxa X4's always-on S5 standby rail whenever USB-C PD
# power is connected, it manages both the dual-colour front Power LED (`Kingbright L-819EGW`)
# and the two momentary `Omron SS-5GL` top-deck switches (`POWER` and `RESET`):
#   - Dual-colour LED (`Kingbright L-819EGW`, Pin 2 Common Cathode -> GND Pin 9):
#       * `GP4` (Pin 7) -> 330 Ohm -> Pin 1 Red Anode:   HIGH in standby (`Red = Power`)
#       * `GP5` (Pin 29) -> 330 Ohm -> Pin 3 Green Anode: HIGH when x86 host is ON (`Green = ON`)
#   - Momentary Reset `Omron SS-5GL` (`GP3` / Pin 5 to GND): sends `KEY_F13` -> `n64mini-reset`
#   - Momentary Power `Omron SS-5GL` (`GP2` / Pin 3 to GND, wired in parallel with `PWR_BTN_N`):
#     wakes the Intel N100 from S5 standby via hardware `PWR_BTN_N` or triggers ACPI shutdown

import time
import board
import digitalio
import supervisor
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

DEBOUNCE_S = 0.030
POLL_S = 0.005

reset_pin = digitalio.DigitalInOut(board.GP3)
reset_pin.direction = digitalio.Direction.INPUT
reset_pin.pull = digitalio.Pull.UP

led_red = digitalio.DigitalInOut(board.GP4)
led_red.direction = digitalio.Direction.OUTPUT

led_green = digitalio.DigitalInOut(board.GP5)
led_green.direction = digitalio.Direction.OUTPUT

keyboard = None
was_pressed = False
last_change = time.monotonic()

while True:
    now = time.monotonic()
    host_on = bool(supervisor.runtime.usb_connected)
    # Red = Standby / Power connected; Green = Console ON (S0 active)
    led_red.value = not host_on
    led_green.value = host_on

    if host_on and keyboard is None:
        try:
            keyboard = Keyboard(usb_hid.devices)
        except Exception:
            keyboard = None
    elif not host_on:
        keyboard = None

    pressed = not reset_pin.value  # active-low momentary Omron SS-5GL switch to GND
    if pressed != was_pressed and (now - last_change) >= DEBOUNCE_S:
        was_pressed = pressed
        last_change = now
        if keyboard is not None:
            if pressed:
                keyboard.press(Keycode.F13)
            else:
                keyboard.release(Keycode.F13)
    time.sleep(POLL_S)
