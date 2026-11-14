# CircuitPython firmware for the Radxa X4's onboard RP2040 (AC-T18, J-04, J-07).
#
# Copy this file to the RP2040's CIRCUITPY drive as `code.py` (with `adafruit_hid` in `lib/`).
#
# What the RP2040 can and can't do (Radxa X4 V1.11 schematic, sheet 21 "RS2040"): it is powered
# from the system 3.3 V rail (P3V3_S, via P3V_RP) and its RUN pin follows the N100's platform
# reset (PLTRST_2040), so it only runs while the PC is on. It can't light a standby LED or wake
# the PC, and the POWER switch is wired across the X4's own power button (K1), not to the header.
#
# Header pins -> RP2040 GPIOs from Radxa's X4 GPIO table, vendored in
# frontend/vendor/radxa-x4/gpio_header.tsv; tests/test_firmware_pins.py checks every entry below.
#   - RESET `Omron SS-5GL`: NO -> header pin 5 (GP29), COM -> pin 6 (GND); sends `KEY_F13`
#   - LED `Kingbright L-93WEGW` (1 red anode, 2 common cathode, 3 green anode):
#       red anode -> 330 R -> header pin 7 (GP4); green anode -> 330 R -> pin 29 (GP7);
#       common cathode -> pin 9 (GND)
#
# LED (owner, 2026-11-13): RED in the Batocera menu, GREEN while a game runs. The RP2040 can't see
# which, so the console tells it: the launch wrapper sends "G" before a game and "R" after it
# (n64mini.console.led). Red too while USB is still coming up. The bytes come on the second USB
# serial port, the data channel boot.py turns on, not the REPL console: a stray Ctrl-C byte on
# the console would stop this program, and with it RESET (Gemini review #3, 2026-11-14).

import time
import board
import digitalio
import supervisor
import usb_cdc
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

# (header pin, GPIO): the pairs tests/test_firmware_pins.py checks against Radxa's table
RESET_SWITCH = (5, board.GP29)
LED_RED = (7, board.GP4)
LED_GREEN = (29, board.GP7)

DEBOUNCE_S = 0.030
POLL_S = 0.005

reset_pin = digitalio.DigitalInOut(RESET_SWITCH[1])
reset_pin.direction = digitalio.Direction.INPUT
reset_pin.pull = digitalio.Pull.UP

led_red = digitalio.DigitalInOut(LED_RED[1])
led_red.direction = digitalio.Direction.OUTPUT

led_green = digitalio.DigitalInOut(LED_GREEN[1])
led_green.direction = digitalio.Direction.OUTPUT

keyboard = None
in_game = False
was_pressed = False
last_change = time.monotonic()

while True:
    now = time.monotonic()
    host_on = bool(supervisor.runtime.usb_connected)
    data = usb_cdc.data
    if data is not None and data.in_waiting:
        for command in data.read(data.in_waiting):
            if command in b"GR":
                in_game = command == ord("G")
    if not host_on:
        in_game = False  # the PC went away mid-game: start again from the menu colour
    green = host_on and in_game
    led_red.value = not green
    led_green.value = green

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
