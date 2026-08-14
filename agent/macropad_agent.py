#!/usr/bin/env python3
"""
macropad_agent.py — the brain of the setup.

Responsibilities:
  1. Watch the frontmost macOS app and pick a matching profile.
  2. Push that profile's labels + colors to the MacroPad's OLED and NeoPixels.
  3. Receive key/encoder events from the pad and run the mapped action.
  4. Hot-reload config/profiles.json whenever it changes on disk.

Usage:
    python3 agent/macropad_agent.py              # run the agent + editor
    python3 agent/macropad_agent.py whoami       # print frontmost bundle IDs
    python3 agent/macropad_agent.py ports        # list candidate serial ports

The editor is served on http://127.0.0.1:8765 from inside this process.
Set MACROPAD_UI_PORT to move it, or MACROPAD_NO_UI=1 to run headless.

Requires Accessibility permission for whatever runs this (Terminal, iTerm,
or the packaged app). System Settings > Privacy & Security > Accessibility.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

from AppKit import NSWorkspace
from pynput.keyboard import Controller, Key

import server

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("MACROPAD_CONFIG", ROOT / "config" / "profiles.json"))
ADAFRUIT_VID = 0x239A
POLL_INTERVAL = 0.02
APP_POLL_INTERVAL = 0.25
PING_INTERVAL = 2.0
# Belt and braces: the pad asks for a layer until it gets one, but a push can
# still be lost if it lands while the pad is mid-boot. Re-pushing on a slow
# cadence means a missed push self-heals in seconds instead of persisting
# until the next app switch. The pad ignores a layer identical to the one it
# is already showing, so this costs it nothing.
LAYER_REPUSH_INTERVAL = 5.0
# How long the pad may stay silent after we open the port before we say so.
# It announces itself every 2s, so anything past this is a real problem.
PAD_SILENCE_WARNING = 6.0

keyboard = Controller()

# ------------------------------------------------------------ key parsing ---

MODIFIERS = {
    "cmd": Key.cmd,
    "command": Key.cmd,
    "opt": Key.alt,
    "option": Key.alt,
    "alt": Key.alt,
    "ctrl": Key.ctrl,
    "control": Key.ctrl,
    "shift": Key.shift,
}

NAMED = {
    "enter": Key.enter,
    "return": Key.enter,
    "tab": Key.tab,
    "esc": Key.esc,
    "escape": Key.esc,
    "space": Key.space,
    "delete": Key.backspace,
    "backspace": Key.backspace,
    "fwddelete": Key.delete,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "home": Key.home,
    "end": Key.end,
    "pageup": Key.page_up,
    "pagedown": Key.page_down,
    "volup": Key.media_volume_up,
    "voldown": Key.media_volume_down,
    "mute": Key.media_volume_mute,
    "playpause": Key.media_play_pause,
    "next": Key.media_next,
    "prev": Key.media_previous,
}
for n in range(1, 21):
    NAMED[f"f{n}"] = getattr(Key, f"f{n}")


def press_combo(combo: str) -> None:
    """Send something like 'cmd+shift+4' or 'f13' or 'a'."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return
    *mod_names, final = parts
    mods = [MODIFIERS[m] for m in mod_names if m in MODIFIERS]
    target = NAMED.get(final, final)
    for m in mods:
        keyboard.press(m)
    try:
        keyboard.press(target)
        keyboard.release(target)
    finally:
        for m in reversed(mods):
            keyboard.release(m)


# ---------------------------------------------------------------- actions ---

def run_action(action) -> None:
    if not action:
        return
    kind = action.get("type")
    value = action.get("value", "")
    try:
        if kind == "keys":
            press_combo(value)
        elif kind == "text":
            keyboard.type(value)
        elif kind == "shell":
            subprocess.Popen(value, shell=True)
        elif kind == "app":
            subprocess.Popen(["open", "-a", value])
        elif kind == "url":
            subprocess.Popen(["open", value])
        elif kind == "shortcut":
            subprocess.Popen(["shortcuts", "run", value])
        elif kind == "applescript":
            subprocess.Popen(["osascript", "-e", value])
        else:
            print(f"[warn] unknown action type: {kind}")
    except Exception as exc:  # never let a bad mapping kill the agent
        print(f"[error] action {kind}={value!r}: {exc}")


# ----------------------------------------------------------------- config ---

class Config:
    def __init__(self, path: Path):
        self.path = path
        self.mtime = 0.0
        self.data = {"defaultProfile": "global", "profiles": {}}
        self.reload()

    def reload(self) -> bool:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        if mtime == self.mtime:
            return False
        try:
            self.data = json.loads(self.path.read_text())
            self.mtime = mtime
            print(f"[config] loaded {self.path}")
            return True
        except json.JSONDecodeError as exc:
            print(f"[config] invalid JSON, keeping previous: {exc}")
            return False

    def resolve(self, bundle_id: str | None) -> dict:
        """Merge the app profile over the default profile, slot by slot."""
        profiles = self.data.get("profiles", {})
        base = profiles.get(self.data.get("defaultProfile", "global"), {})
        override = profiles.get(bundle_id or "", {})

        keys = []
        base_keys = base.get("keys", []) + [None] * 12
        over_keys = override.get("keys", []) + [None] * 12
        for i in range(12):
            keys.append(over_keys[i] or base_keys[i])

        encoder = dict(base.get("encoder", {}))
        encoder.update(override.get("encoder", {}))

        return {
            "name": override.get("name") or base.get("name") or "MacroPad",
            "keys": keys,
            "encoder": encoder,
        }


def hex_to_int(value) -> int:
    if not value:
        return 0
    if isinstance(value, int):
        return value
    return int(str(value).lstrip("#"), 16)


# ------------------------------------------------------------------ serial ---

def candidate_ports():
    return sorted(
        (p for p in list_ports.comports() if p.vid == ADAFRUIT_VID),
        key=lambda p: p.device,
    )


_warned = set()


def warn_once(key: str, message: str) -> None:
    """Print a diagnostic the first time only.

    These live on paths the reconnect loop retries every second, and a
    warning repeated 60 times a minute is indistinguishable from noise.
    """
    if key not in _warned:
        _warned.add(key)
        print(message)


def find_port() -> str | None:
    override = os.environ.get("MACROPAD_PORT")
    if override:
        return override
    ports = candidate_ports()
    if not ports:
        return None
    if len(ports) == 1:
        # One port means usb_cdc.data was never enabled, so this is the
        # console. Opening it "works" — the agent would report success and
        # then spend the session typing JSON at the REPL while the pad sat on
        # its waiting screen. Refuse it and say why instead.
        warn_once(
            "console-only",
            f"[agent] only one Adafruit serial port found ({ports[0].device}).\n"
            "        That is the console — usb_cdc.data is not enabled, so the pad\n"
            "        can never receive a layer. Copy firmware/boot.py to CIRCUITPY\n"
            "        and UNPLUG/REPLUG the pad; boot.py does not take effect on a\n"
            "        soft reset. Set MACROPAD_PORT to override this check.",
        )
        return None
    # With console+data enabled the pad exposes two CDC ports.
    # The data channel is the higher-numbered one.
    return ports[-1].device


# -------------------------------------------------------------------- app ---

def frontmost_bundle_id():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None, None
    return app.bundleIdentifier(), app.localizedName()


# NSApplicationActivationPolicyRegular — apps with a Dock icon. Filters out
# the agents and helpers that would otherwise flood the "add app" list.
ACTIVATION_POLICY_REGULAR = 0


def running_apps():
    """Apps the user could plausibly want a profile for, for the editor."""
    apps = []
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != ACTIVATION_POLICY_REGULAR:
            continue
        bundle_id = app.bundleIdentifier()
        if not bundle_id:
            continue
        apps.append({"bundleId": bundle_id, "name": app.localizedName() or bundle_id})
    apps.sort(key=lambda a: a["name"].lower())
    return apps


# ------------------------------------------------------------------- loop ---

def push_layer(port, profile):
    labels = [(k or {}).get("label", "") for k in profile["keys"]]
    colors = [hex_to_int((k or {}).get("color")) for k in profile["keys"]]
    payload = {"t": "layer", "name": profile["name"], "labels": labels, "colors": colors}
    port.write((json.dumps(payload) + "\n").encode("utf-8"))


def handle_message(msg, profile):
    kind = msg.get("t")
    if kind == "key" and msg.get("e") == "down":
        idx = msg.get("i", -1)
        if 0 <= idx < 12:
            entry = profile["keys"][idx]
            if entry:
                run_action(entry.get("action"))
    elif kind == "enc":
        slot = "cw" if msg.get("d", 0) > 0 else "ccw"
        run_action(profile["encoder"].get(slot))
    elif kind == "encbtn" and msg.get("e") == "down":
        run_action(profile["encoder"].get("press"))
    elif kind == "hello":
        return "hello"
    return None


def main():
    config = Config(CONFIG_PATH)
    port = None
    buf = bytearray()
    current_bundle = object()  # sentinel so the first check always fires
    profile = config.resolve(None)
    last_app_poll = 0.0
    last_ping = 0.0
    last_app_list = 0.0
    last_push = 0.0
    last_pad_rx = 0.0
    pad_spoke = False
    silence_warned = False

    state = server.State()
    if os.environ.get("MACROPAD_NO_UI"):
        print("[agent] editor disabled (MACROPAD_NO_UI)")
    else:
        try:
            ui_port = server.serve(CONFIG_PATH, state)
            print(f"[agent] editor on http://127.0.0.1:{ui_port}")
        except OSError as exc:
            # Almost always "address already in use" from a second copy of
            # the agent. Not fatal — the pad still works without the editor.
            print(f"[agent] editor unavailable: {exc}")

    print("[agent] running. Ctrl-C to stop.")

    while True:
        # --- connect / reconnect -------------------------------------------
        if port is None:
            device = find_port()
            if device is None:
                state.update(connected=False, port=None)
                time.sleep(1.0)
                continue
            try:
                port = serial.Serial(device, 115200, timeout=0)
                buf.clear()
                current_bundle = object()
                last_pad_rx = time.monotonic()
                pad_spoke = False
                silence_warned = False
                state.update(connected=True, port=device, padSilent=False)
                print(f"[agent] connected to {device}")
            except serial.SerialException:
                state.update(connected=False, port=None)
                time.sleep(1.0)
                continue

        now = time.monotonic()

        # --- config hot reload ---------------------------------------------
        if config.reload():
            current_bundle = object()

        # --- frontmost app --------------------------------------------------
        if now - last_app_poll > APP_POLL_INTERVAL:
            last_app_poll = now
            bundle_id, name = frontmost_bundle_id()
            if bundle_id != current_bundle:
                current_bundle = bundle_id
                profile = config.resolve(bundle_id)
                state.update(bundleId=bundle_id, appName=name, profileName=profile["name"])
                print(f"[agent] {name} ({bundle_id}) -> {profile['name']}")
                try:
                    push_layer(port, profile)
                    last_push = now
                except serial.SerialException:
                    port = None
                    continue

        # --- running apps, for the editor's app picker -----------------------
        # Much heavier than the frontmost check, and it only feeds a UI that
        # polls on demand, so it runs on a slow cadence of its own.
        if now - last_app_list > 5.0:
            last_app_list = now
            state.update(runningApps=running_apps())

        # --- keepalive ------------------------------------------------------
        if now - last_ping > PING_INTERVAL:
            last_ping = now
            try:
                port.write(b'{"t":"ping"}\n')
            except serial.SerialException:
                port = None
                continue

        # --- re-push the current layer --------------------------------------
        # A ping proves we are alive but paints nothing. Without this the only
        # things that ever draw are an app switch and the pad's own hello, so a
        # single lost push left the pad on its waiting screen indefinitely.
        if now - last_push > LAYER_REPUSH_INTERVAL:
            last_push = now
            try:
                push_layer(port, profile)
            except serial.SerialException:
                port = None
                continue

        # --- has the pad said anything? --------------------------------------
        if not pad_spoke and not silence_warned and now - last_pad_rx > PAD_SILENCE_WARNING:
            silence_warned = True  # once per connection, not once per pass
            state.update(padSilent=True)
            print(
                f"[agent] no messages from the pad in {PAD_SILENCE_WARNING:.0f}s.\n"
                f"        Writing to {port.port} but nothing is answering.\n"
                "        Likely the console port rather than the data port, or code.py\n"
                "        is not running (check the REPL for a traceback — a missing\n"
                "        library from `make libs` stops it before the display loop)."
            )

        # --- read events ----------------------------------------------------
        try:
            waiting = port.in_waiting
            if waiting:
                buf.extend(port.read(waiting))
        except (serial.SerialException, OSError):
            print("[agent] disconnected")
            try:
                port.close()
            except Exception:
                pass
            port = None
            state.update(connected=False, port=None)
            continue

        while True:
            idx = buf.find(b"\n")
            if idx < 0:
                break
            line = bytes(buf[:idx])
            del buf[: idx + 1]
            try:
                msg = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            last_pad_rx = now
            if not pad_spoke:
                pad_spoke = True
                state.update(padSilent=False)
            if handle_message(msg, profile) == "hello":
                push_layer(port, profile)
                last_push = now

        time.sleep(POLL_INTERVAL)


def whoami():
    print("Focus each app you care about. Ctrl-C to stop.\n")
    last = None
    while True:
        bundle_id, name = frontmost_bundle_id()
        if bundle_id != last:
            last = bundle_id
            print(f"{name:<30} {bundle_id}")
        time.sleep(0.5)


def ports():
    found = candidate_ports()
    if not found:
        print("No Adafruit devices found. Is the MacroPad plugged in?")
        return
    for i, p in enumerate(found):
        role = "data" if len(found) > 1 and i == len(found) - 1 else "console"
        print(f"{p.device:<28} {role:<8} {p.description}")

    if len(found) == 1:
        print(
            "\nOnly one port. usb_cdc.data is NOT enabled, so the pad cannot\n"
            "receive layers and the OLED will stay on 'Waiting for Mac'.\n"
            "Copy firmware/boot.py to CIRCUITPY, then UNPLUG AND REPLUG the pad —\n"
            "boot.py does not take effect on a soft reset."
        )
        return

    print(f"\nThe agent will use: {found[-1].device}")
    print("Override with MACROPAD_PORT=/dev/cu.usbmodemXXXX")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    try:
        {"run": main, "whoami": whoami, "ports": ports}[cmd]()
    except KeyError:
        print(__doc__)
    except KeyboardInterrupt:
        print("\nbye")
