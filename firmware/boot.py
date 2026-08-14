"""
boot.py — runs once at power-on, before code.py.

Enables a second USB CDC channel ("data") so the Mac agent can talk to the
MacroPad without fighting the REPL for the console port.

IMPORTANT: changes to boot.py only take effect after a hard reset
(unplug/replug, or press the reset button on the back of the MacroPad).

Once everything is stable you can uncomment the storage lines below to hide
the CIRCUITPY drive. Keep them commented while you're still iterating —
otherwise you'll need to hold a key at boot to get the drive back.
"""

import usb_cdc

usb_cdc.enable(console=True, data=True)

# import storage
# storage.disable_usb_drive()
