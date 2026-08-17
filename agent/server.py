"""
server.py — the localhost editor that ships inside the agent.

The agent already owns the serial link, the keystroke synthesis, and the
frontmost-app watch. Rather than run a second process for the GUI, this
mounts a small HTTP server on a daemon thread inside the same process, so
there is one thing to launch and one source of truth for config.

Security: binds to 127.0.0.1 and nothing else. Profiles can carry `shell`
and `applescript` actions, so anything that can PUT to this API can make the
agent run arbitrary commands on the next key press. Do not change the bind
address, and do not put this behind a tunnel.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paths

DEFAULT_PORT = int(os.environ.get("MACROPAD_UI_PORT", "8765"))
MAX_BODY = 1 << 20  # 1 MiB is far more than any plausible profiles.json


class State:
    """A snapshot the agent loop publishes for the UI to poll.

    NSWorkspace and pyserial are only ever touched from the main loop. The
    HTTP threads read this dict instead of calling into them, which keeps
    AppKit on one thread and avoids interleaving reads on the serial port.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "connected": False,
            "port": None,
            "bundleId": None,
            "appName": None,
            "profileName": None,
            # True once the port is open but the pad has never answered —
            # usually the console port, or code.py stopped on a traceback.
            "padSilent": False,
            "runningApps": [],
        }

    def update(self, **kwargs):
        with self._lock:
            self._data.update(kwargs)

    def snapshot(self):
        with self._lock:
            return dict(self._data)


def write_config_atomically(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    The agent reloads on mtime change, so a plain open()/write() risks it
    reading a truncated file mid-save. os.replace is atomic within a
    filesystem, so the agent only ever sees the old file or the new one.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# The vocabulary the agent understands, handed to the UI so the two can't
# drift apart. Mirrors NAMED and the action table in macropad_agent.py.
ACTION_TYPES = [
    {"type": "keys", "label": "Keystroke", "placeholder": "cmd+shift+4"},
    {"type": "text", "label": "Type text", "placeholder": "me@iansoper.com"},
    {"type": "app", "label": "Open app", "placeholder": "Figma"},
    {"type": "shell", "label": "Shell command", "placeholder": "open -a Ghostty"},
    {"type": "url", "label": "Open URL", "placeholder": "raycast://extensions"},
    {"type": "shortcut", "label": "Run Shortcut", "placeholder": "Daily Standup"},
    {"type": "applescript", "label": "AppleScript", "placeholder": "tell application ..."},
]

NAMED_KEYS = [
    "enter", "return", "tab", "esc", "space", "delete", "fwddelete",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "volup", "voldown", "mute", "playpause", "next", "prev",
] + [f"f{n}" for n in range(1, 21)]


def make_handler(config_path: Path, state: State):
    class Handler(BaseHTTPRequestHandler):
        # BaseHTTPRequestHandler logs every request to stderr, which would
        # bury the agent's own output once the UI starts polling.
        def log_message(self, *args):
            pass

        # -- helpers ----------------------------------------------------
        def _send(self, code, body: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # The UI is same-origin; no CORS header on purpose, so a random
            # page in another tab can't script this API.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        # -- routes -----------------------------------------------------
        def do_GET(self):
            route = self.path.split("?")[0]

            if route in ("/", "/index.html"):
                # Asked per request rather than cached at import: frozen, this
                # depends on RESOURCEPATH, which is only in the environment
                # once the bundle has actually launched.
                ui_dir = paths.ui_dir()
                try:
                    body = (ui_dir / "index.html").read_bytes()
                except FileNotFoundError:
                    self._json(500, {"error": f"UI missing at {ui_dir}"})
                    return
                self._send(200, body, "text/html; charset=utf-8")

            elif route == "/api/config":
                try:
                    self._send(200, config_path.read_bytes(), "application/json")
                except FileNotFoundError:
                    self._json(404, {"error": f"no config at {config_path}"})

            elif route == "/api/state":
                self._json(200, state.snapshot())

            elif route == "/api/meta":
                self._json(200, {
                    "actionTypes": ACTION_TYPES,
                    "namedKeys": NAMED_KEYS,
                    "configPath": str(config_path),
                })

            else:
                self._json(404, {"error": "not found"})

        def do_PUT(self):
            if self.path.split("?")[0] != "/api/config":
                self._json(404, {"error": "not found"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self._json(413, {"error": "body missing or too large"})
                return

            raw = self.rfile.read(length)

            # Parse before writing. A syntactically broken save would leave
            # the agent running on its last-good copy anyway, but there's no
            # reason to put a bad file on disk and make the user find it.
            try:
                parsed = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                self._json(400, {"error": f"invalid JSON: {exc}"})
                return

            problem = validate_config(parsed)
            if problem:
                self._json(400, {"error": problem})
                return

            try:
                write_config_atomically(config_path, json.dumps(parsed, indent=2) + "\n")
            except OSError as exc:
                self._json(500, {"error": f"could not write config: {exc}"})
                return

            # No need to poke the agent — its mtime check picks this up on
            # the next pass through the loop and re-pushes the layer.
            self._json(200, {"ok": True})

    return Handler


def validate_config(data) -> str | None:
    """Enough structural checking to catch a UI bug before it hits disk.

    Deliberately shallow: the agent already tolerates unknown action types
    and missing slots, so this only rejects shapes that would break the
    resolve() merge.
    """
    if not isinstance(data, dict):
        return "config must be a JSON object"

    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return "config.profiles must be an object"

    default = data.get("defaultProfile", "global")
    if default not in profiles:
        return f"defaultProfile {default!r} is not present in profiles"

    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            return f"profile {name!r} must be an object"

        keys = profile.get("keys", [])
        if not isinstance(keys, list):
            return f"profile {name!r}: keys must be a list"
        if len(keys) > 12:
            return f"profile {name!r}: {len(keys)} keys, the pad has 12"
        for i, key in enumerate(keys):
            if key is None:
                continue  # a null slot is how fall-through is expressed
            if not isinstance(key, dict):
                return f"profile {name!r} key {i}: must be an object or null"
            action = key.get("action")
            if action is not None and not isinstance(action, dict):
                return f"profile {name!r} key {i}: action must be an object"

        encoder = profile.get("encoder", {})
        if not isinstance(encoder, dict):
            return f"profile {name!r}: encoder must be an object"

    return None


def serve(config_path: Path, state: State, port: int = DEFAULT_PORT):
    """Start the editor on a daemon thread. Returns the server.

    Returns the ThreadingHTTPServer rather than just the port so a caller
    with a Quit menu item can shut it down; read `.server_address[1]` for
    the bound port, which is what you want when port=0.
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(config_path, state))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="macropad-ui")
    thread.start()
    return httpd
