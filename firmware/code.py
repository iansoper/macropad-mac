"""
code.py — MacroPad RP2040 <-> macOS agent bridge.

Design: the MacroPad is deliberately "thin". It does not store macros.
It reports raw events to the Mac and renders whatever layer the Mac pushes
back (OLED labels + per-key NeoPixel colors). All mapping logic lives in
config/profiles.json on the Mac, so you can remap without touching firmware.

Protocol: newline-delimited JSON over usb_cdc.data.

  device -> host
    {"t":"hello"}                        sent on boot / reconnect
    {"t":"key","i":0,"e":"down"}         i = 0..11, row-major, top-left first
    {"t":"key","i":0,"e":"up"}
    {"t":"enc","d":1}                    d = +1 clockwise, -1 counter-clockwise
    {"t":"encbtn","e":"down"|"up"}

  host -> device
    {"t":"layer","name":"Figma",
     "labels":["Move","Frame", ... 12 items ...],
     "colors":[4098459, 0, ... 12 ints ...]}   0xRRGGBB, 0 = off
    {"t":"ping"}                         keepalive

If the host goes quiet for HOST_TIMEOUT seconds the pad falls back to a local
volume knob so it is never completely dead.
"""

import json
import time

import displayio
import terminalio
import usb_cdc
from adafruit_display_shapes.rect import Rect
from adafruit_display_text import label
from adafruit_macropad import MacroPad

HOST_TIMEOUT = 5.0  # seconds without a message before we assume the Mac is gone
BRIGHTNESS = 0.25

macropad = MacroPad()
macropad.display.auto_refresh = False
macropad.pixels.auto_write = False
macropad.pixels.brightness = BRIGHTNESS

serial = usb_cdc.data  # None if boot.py did not run

# ---------------------------------------------------------------- display ---

group = displayio.Group()
key_labels = []
for i in range(12):
    x = i % 3
    y = i // 3
    lbl = label.Label(
        terminalio.FONT,
        text="",
        color=0xFFFFFF,
        anchored_position=(
            (macropad.display.width - 1) * x / 2,
            macropad.display.height - 1 - (3 - y) * 12,
        ),
        anchor_point=(x / 2, 1.0),
    )
    key_labels.append(lbl)
    group.append(lbl)

group.append(Rect(0, 0, macropad.display.width, 12, fill=0xFFFFFF))
title = label.Label(
    terminalio.FONT,
    text="",
    color=0x000000,
    anchored_position=(macropad.display.width // 2, -2),
    anchor_point=(0.5, 0.0),
)
group.append(title)

try:
    macropad.display.root_group = group  # CircuitPython 9+
except AttributeError:
    macropad.display.show(group)  # CircuitPython 8


def render(name, labels, colors):
    title.text = name[:20]
    for i in range(12):
        key_labels[i].text = (labels[i] or "")[:6] if i < len(labels) else ""
        macropad.pixels[i] = colors[i] if i < len(colors) else 0
    macropad.pixels.show()
    macropad.display.refresh()


def disconnected_screen():
    render("Waiting for Mac", [""] * 12, [0x001014] * 12)


# ----------------------------------------------------------------- serial ---

buf = bytearray()
last_rx = 0.0
connected = False


def send(obj):
    if serial is None or not serial.connected:
        return
    try:
        serial.write((json.dumps(obj) + "\n").encode("utf-8"))
    except Exception:
        pass


def handle(line):
    try:
        msg = json.loads(line)
    except ValueError:
        return
    if msg.get("t") == "layer":
        render(
            msg.get("name", ""),
            msg.get("labels", []),
            msg.get("colors", []),
        )


def pump_serial():
    """Read whatever is waiting and dispatch complete lines."""
    global last_rx, connected
    if serial is None or not serial.in_waiting:
        return
    buf.extend(serial.read(serial.in_waiting))
    while True:
        idx = buf.find(b"\n")
        if idx < 0:
            break
        line = bytes(buf[:idx])
        del buf[: idx + 1]
        last_rx = time.monotonic()
        if not connected:
            connected = True
        handle(line)


# ------------------------------------------------------------------- main ---

disconnected_screen()
send({"t": "hello"})

last_position = macropad.encoder
last_hello = time.monotonic()

while True:
    pump_serial()

    now = time.monotonic()
    if connected and (now - last_rx) > HOST_TIMEOUT:
        connected = False
        disconnected_screen()
    if not connected and (now - last_hello) > 2.0:
        send({"t": "hello"})  # re-announce until the agent answers
        last_hello = now

    event = macropad.keys.events.get()
    if event:
        send({"t": "key", "i": event.key_number, "e": "down" if event.pressed else "up"})

    position = macropad.encoder
    if position != last_position:
        delta = position - last_position
        last_position = position
        if connected:
            send({"t": "enc", "d": 1 if delta > 0 else -1})
        else:
            # local fallback so the knob still does something useful
            key = (
                macropad.ConsumerControlCode.VOLUME_INCREMENT
                if delta > 0
                else macropad.ConsumerControlCode.VOLUME_DECREMENT
            )
            for _ in range(abs(delta)):
                macropad.consumer_control.send(key)

    macropad.encoder_switch_debounced.update()
    if macropad.encoder_switch_debounced.pressed:
        if connected:
            send({"t": "encbtn", "e": "down"})
        else:
            macropad.consumer_control.send(macropad.ConsumerControlCode.MUTE)
    if macropad.encoder_switch_debounced.released and connected:
        send({"t": "encbtn", "e": "up"})
