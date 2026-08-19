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
from Foundation import NSDate, NSRunLoop
from pynput.keyboard import Controller, Key

import app_icons
import paths
import server

# Resolved once at import so `ports`/`whoami` and the run loop agree, but the
# real answer depends on whether we are inside MacroPad.app — see paths.py.
CONFIG_PATH = paths.config_path()
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

def frontmost_bundle_id(pump_runloop: bool = True):
    # NSWorkspace does not poll for the frontmost app — it learns about
    # switches from notifications, and those are only delivered while a run
    # loop spins. Run from a terminal there is no AppKit event loop, so
    # without pumping it here the value stays pinned to whatever was frontmost
    # when the agent started, and no layer ever switches. 10ms is enough to
    # drain the queue and is cheap at APP_POLL_INTERVAL.
    #
    # Inside MacroPad.app there already is one: NSApplication owns the main
    # run loop and dispatches the timer that lands here, so the app passes
    # pump_runloop=False. Spinning a run loop from inside a callback that
    # same run loop dispatched is re-entrancy for no gain.
    if pump_runloop:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.01)
        )
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

def push_layer(port, profile, icon_bits: bytes | None = None):
    labels = [(k or {}).get("label", "") for k in profile["keys"]]
    colors = [hex_to_int((k or {}).get("color")) for k in profile["keys"]]
    payload = {"t": "layer", "name": profile["name"], "labels": labels, "colors": colors}
    if icon_bits:
        # A plain list of row bytes rather than base64 — code.py can read it
        # straight off the parsed JSON with no binascii import, keeping the
        # firmware side of this exactly as thin as everything else it does.
        payload["icon"] = list(icon_bits)
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


RECONNECT_INTERVAL = 1.0


class Runtime:
    """The agent loop, as one pass at a time.

    Two things drive this. From a terminal, main() calls tick() in a while
    loop with a sleep. Inside MacroPad.app, an NSTimer on the main run loop
    calls the same tick() — which is what keeps pyserial and AppKit on a
    single thread in both cases, exactly as the HTTP layer assumes.

    The rule that falls out of that: **tick() must never block.** Under the
    timer, a sleep in here freezes the menu bar and the editor window along
    with the agent. That is why the reconnect backoff below is a deadline
    rather than the time.sleep(1.0) this loop used to carry.
    """

    def __init__(self, config_path: Path | None = None, pump_runloop: bool = True):
        self.config = Config(config_path or CONFIG_PATH)
        self.state = server.State()
        self.icons = server.IconStore()  # UI icons; populated on the main loop, read by HTTP threads
        self.pump_runloop = pump_runloop
        self.httpd = None
        self.ui_port = None

        self.port = None
        self.buf = bytearray()
        self.current_bundle = object()  # sentinel so the first check always fires
        self.profile = self.config.resolve(None)
        self.current_icon = None  # 8-byte pad bitmap for self.profile's app, if any
        self._ui_icon_attempted = set()  # bundle IDs already looked up, hit or miss
        self._pad_icon_cache = {}        # bundle ID -> 8-byte bitmap or None
        self.next_connect = 0.0
        self.last_app_poll = 0.0
        self.last_ping = 0.0
        self.last_app_list = 0.0
        self.last_push = 0.0
        self.last_pad_rx = 0.0
        self.pad_spoke = False
        self.silence_warned = False

    # -------------------------------------------------------- lifecycle ---

    def start(self):
        if os.environ.get("MACROPAD_NO_UI"):
            print("[agent] editor disabled (MACROPAD_NO_UI)")
            return
        try:
            self.httpd = server.serve(self.config.path, self.state, self.icons)
            self.ui_port = self.httpd.server_address[1]
            print(f"[agent] editor on http://127.0.0.1:{self.ui_port}")
        except OSError as exc:
            # Almost always "address already in use" from a second copy of
            # the agent. Not fatal — the pad still works without the editor.
            print(f"[agent] editor unavailable: {exc}")

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self._drop_port()

    def _drop_port(self):
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
            self.port = None
        self.state.update(connected=False, port=None)

    # ------------------------------------------------------------- pass ---

    def tick(self):
        now = time.monotonic()

        if self.port is None:
            if now < self.next_connect:
                return
            self.next_connect = now + RECONNECT_INTERVAL
            if not self._connect(now):
                return

        if self.config.reload():
            self.current_bundle = object()
            self._refresh_ui_icons(self.config.data.get("profiles", {}).keys())

        if now - self.last_app_poll > APP_POLL_INTERVAL:
            self.last_app_poll = now
            if not self._sync_frontmost(now):
                return

        # Much heavier than the frontmost check, and it only feeds a UI that
        # polls on demand, so it runs on a slow cadence of its own.
        if now - self.last_app_list > 5.0:
            self.last_app_list = now
            apps = running_apps()
            self.state.update(runningApps=apps)
            self._refresh_ui_icons(a["bundleId"] for a in apps)

        if now - self.last_ping > PING_INTERVAL:
            self.last_ping = now
            try:
                self.port.write(b'{"t":"ping"}\n')
            except serial.SerialException:
                self._drop_port()
                return

        # A ping proves we are alive but paints nothing. Without this the only
        # things that ever draw are an app switch and the pad's own hello, so a
        # single lost push left the pad on its waiting screen indefinitely.
        if now - self.last_push > LAYER_REPUSH_INTERVAL:
            self.last_push = now
            try:
                push_layer(self.port, self.profile, self.current_icon)
            except serial.SerialException:
                self._drop_port()
                return

        self._warn_if_silent(now)
        self._read_events(now)

    # ---------------------------------------------------------- internals --

    def _connect(self, now) -> bool:
        device = find_port()
        if device is None:
            self.state.update(connected=False, port=None)
            return False
        try:
            self.port = serial.Serial(device, 115200, timeout=0)
        except serial.SerialException:
            self.state.update(connected=False, port=None)
            return False
        self.buf.clear()
        self.current_bundle = object()
        self.last_pad_rx = now
        self.pad_spoke = False
        self.silence_warned = False
        self.state.update(connected=True, port=device, padSilent=False)
        print(f"[agent] connected to {device}")
        return True

    def _sync_frontmost(self, now) -> bool:
        """False if the port died pushing the new layer."""
        bundle_id, name = frontmost_bundle_id(self.pump_runloop)
        if bundle_id == self.current_bundle:
            return True
        self.current_bundle = bundle_id
        self.profile = self.config.resolve(bundle_id)
        self.current_icon = self._pad_icon_for(bundle_id)
        self.state.update(
            bundleId=bundle_id, appName=name, profileName=self.profile["name"])
        print(f"[agent] {name} ({bundle_id}) -> {self.profile['name']}")
        try:
            push_layer(self.port, self.profile, self.current_icon)
            self.last_push = now
        except serial.SerialException:
            self._drop_port()
            return False
        return True

    @staticmethod
    def _fetch_icon(fn, bundle_id, cache):
        """Try/cache wrapper around an AppKit icon lookup.

        Never let a missing or oddly-shaped app bundle take the agent down —
        the pad should just show no icon for it.
        """
        if bundle_id in cache:
            return cache[bundle_id]
        try:
            result = fn(bundle_id)
        except Exception as exc:
            print(f"[agent] icon lookup failed for {bundle_id!r}: {exc}")
            result = None
        cache[bundle_id] = result
        return result

    def _pad_icon_for(self, bundle_id):
        if not bundle_id:
            return None
        return self._fetch_icon(app_icons.pad_bitmap, bundle_id, self._pad_icon_cache)

    def _refresh_ui_icons(self, bundle_ids):
        """Populate self.icons for the editor. Cheap once per bundle ID —
        _ui_icon_attempted makes every later call for the same ID a no-op.
        """
        for bundle_id in bundle_ids:
            if not bundle_id or bundle_id in self._ui_icon_attempted:
                continue
            self._ui_icon_attempted.add(bundle_id)
            try:
                png = app_icons.ui_png(bundle_id)
            except Exception as exc:
                print(f"[agent] icon lookup failed for {bundle_id!r}: {exc}")
                continue
            if png:
                self.icons.set(bundle_id, png)

    def _warn_if_silent(self, now):
        if self.pad_spoke or self.silence_warned:
            return
        if now - self.last_pad_rx <= PAD_SILENCE_WARNING:
            return
        self.silence_warned = True  # once per connection, not once per pass
        self.state.update(padSilent=True)
        print(
            f"[agent] no messages from the pad in {PAD_SILENCE_WARNING:.0f}s.\n"
            f"        Writing to {self.port.port} but nothing is answering.\n"
            "        Likely the console port rather than the data port, or code.py\n"
            "        is not running (check the REPL for a traceback — a missing\n"
            "        library from `make libs` stops it before the display loop)."
        )

    def _read_events(self, now):
        try:
            waiting = self.port.in_waiting
            if waiting:
                self.buf.extend(self.port.read(waiting))
        except (serial.SerialException, OSError):
            print("[agent] disconnected")
            self._drop_port()
            return

        while True:
            idx = self.buf.find(b"\n")
            if idx < 0:
                break
            line = bytes(self.buf[:idx])
            del self.buf[: idx + 1]
            try:
                msg = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            self.last_pad_rx = now
            if not self.pad_spoke:
                self.pad_spoke = True
                self.state.update(padSilent=False)
            if handle_message(msg, self.profile) == "hello":
                push_layer(self.port, self.profile, self.current_icon)
                self.last_push = now


def main():
    runtime = Runtime()
    runtime.start()
    print("[agent] running. Ctrl-C to stop.")
    try:
        while True:
            runtime.tick()
            time.sleep(POLL_INTERVAL)
    finally:
        runtime.stop()


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
