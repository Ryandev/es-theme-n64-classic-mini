# CircuitPython boot.py for the Radxa X4's RP2040: names the device on USB so the console can find
# its serial console (n64mini.console.led looks for /dev/serial/by-id/usb-n64mini_*-if00).
# Copy to the CIRCUITPY drive next to code.py; it takes effect after a hard reset.

import supervisor

supervisor.set_usb_identification(manufacturer="n64mini", product="Console LED and RESET")
