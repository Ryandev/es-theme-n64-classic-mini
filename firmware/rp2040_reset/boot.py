# CircuitPython boot.py for the Radxa X4's RP2040: names the device on USB and turns on the second
# USB serial port (the data channel) that the console sends the LED colour on, so the REPL console
# never sees it. n64mini.console.led looks for /dev/serial/by-id/usb-n64mini_*-if02 (the data
# channel; if00 is the REPL console).
# Copy to the CIRCUITPY drive next to code.py; it takes effect after a hard reset.

import supervisor
import usb_cdc

supervisor.set_usb_identification(manufacturer="n64mini", product="Console LED and RESET")
usb_cdc.enable(console=True, data=True)
