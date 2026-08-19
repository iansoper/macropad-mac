"""
Tests for the editor's HTTP layer.

server.py is deliberately stdlib-only so it can be tested off a Mac — none
of pyserial, AppKit, or pynput is imported here. The agent loop itself still
needs real hardware, so what's covered is the part that can be: config
validation, the atomic write, and the request routing.
"""

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import server  # noqa: E402


VALID = {
    "defaultProfile": "global",
    "profiles": {
        "global": {
            "name": "Global",
            "keys": [{"label": "Shot", "color": "#3E8E9B",
                      "action": {"type": "keys", "value": "cmd+shift+4"}}],
            "encoder": {"cw": {"type": "keys", "value": "volup"}},
        }
    },
}


# ------------------------------------------------------------ validation --

def test_accepts_the_shipped_config():
    """The config in the repo must pass the validator we gate saves on."""
    shipped = json.loads((ROOT / "config" / "profiles.json").read_text())
    assert server.validate_config(shipped) is None


def test_accepts_minimal_valid_config():
    assert server.validate_config(VALID) is None


def test_null_slots_are_allowed():
    """A null key is how fall-through to the default profile is expressed."""
    cfg = json.loads(json.dumps(VALID))
    cfg["profiles"]["com.example.App"] = {"name": "X", "keys": [None, None, {
        "label": "Go", "action": {"type": "app", "value": "Figma"}}]}
    assert server.validate_config(cfg) is None


@pytest.mark.parametrize("bad, expect", [
    ([], "must be a JSON object"),
    ({"profiles": []}, "profiles must be an object"),
    ({"defaultProfile": "nope", "profiles": {}}, "not present in profiles"),
    ({"defaultProfile": "g", "profiles": {"g": {"keys": {}}}}, "keys must be a list"),
    ({"defaultProfile": "g", "profiles": {"g": {"keys": [None] * 13}}}, "the pad has 12"),
    ({"defaultProfile": "g", "profiles": {"g": {"keys": ["x"]}}}, "must be an object or null"),
    ({"defaultProfile": "g", "profiles": {"g": {"keys": [{"action": 5}]}}}, "action must be an object"),
    ({"defaultProfile": "g", "profiles": {"g": {"encoder": []}}}, "encoder must be an object"),
])
def test_rejects_broken_shapes(bad, expect):
    problem = server.validate_config(bad)
    assert problem is not None and expect in problem


# ---------------------------------------------------------------- write --

def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "profiles.json"
    server.write_config_atomically(target, '{"a": 1}\n')
    assert json.loads(target.read_text()) == {"a": 1}
    assert list(tmp_path.iterdir()) == [target], "temp file was left behind"


def test_atomic_write_replaces_existing(tmp_path):
    target = tmp_path / "profiles.json"
    target.write_text('{"old": true}')
    server.write_config_atomically(target, '{"new": true}\n')
    assert json.loads(target.read_text()) == {"new": True}


# ----------------------------------------------------------------- http --

@pytest.fixture
def api(tmp_path):
    """A live server on an ephemeral port, torn down after each test."""
    config_path = tmp_path / "profiles.json"
    config_path.write_text(json.dumps(VALID))
    state = server.State()
    state.update(connected=True, appName="Figma", bundleId="com.figma.Desktop")
    icons = server.IconStore()

    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), server.make_handler(config_path, state, icons))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def request(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def raw(method, path, body: bytes):
        """Send bytes json.dumps would never produce, e.g. malformed JSON."""
        req = urllib.request.Request(base + path, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    request.config_path = config_path
    request.base = base
    request.raw = raw
    request.icons = icons
    yield request
    httpd.shutdown()


def test_binds_loopback_only():
    """A `shell` action is arbitrary code execution; this must not listen wide."""
    state = server.State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(Path("x"), state))
    assert httpd.server_address[0] == "127.0.0.1"
    httpd.server_close()


def test_get_config_returns_the_file(api):
    status, body = api("GET", "/api/config")
    assert status == 200
    assert body == VALID


def test_get_state_reports_the_snapshot(api):
    status, body = api("GET", "/api/state")
    assert status == 200
    assert body["connected"] is True
    assert body["appName"] == "Figma"


def test_get_meta_lists_action_types(api):
    status, body = api("GET", "/api/meta")
    assert status == 200
    types = [t["type"] for t in body["actionTypes"]]
    assert types == ["keys", "text", "app", "shell", "url", "shortcut", "applescript"]
    assert "playpause" in body["namedKeys"]
    assert "f20" in body["namedKeys"]


def test_put_config_writes_to_disk(api):
    updated = json.loads(json.dumps(VALID))
    updated["profiles"]["global"]["name"] = "Renamed"
    status, body = api("PUT", "/api/config", updated)
    assert status == 200 and body == {"ok": True}

    on_disk = json.loads(api.config_path.read_text())
    assert on_disk["profiles"]["global"]["name"] == "Renamed"


def test_put_rejects_invalid_json_without_touching_the_file(api):
    before = api.config_path.read_text()
    status, body = api.raw("PUT", "/api/config", b"{ not json }")
    assert status == 400 and "invalid JSON" in body["error"]
    assert api.config_path.read_text() == before


def test_put_rejects_structurally_broken_config(api):
    before = api.config_path.read_text()
    status, body = api("PUT", "/api/config", {"defaultProfile": "gone", "profiles": {}})
    assert status == 400 and "not present in profiles" in body["error"]
    assert api.config_path.read_text() == before, "a rejected save must not hit disk"


def test_unknown_routes_404(api):
    assert api("GET", "/api/nope")[0] == 404
    assert api("PUT", "/api/nope")[0] == 404


def test_put_rejects_an_empty_body(api):
    before = api.config_path.read_text()
    status, _ = api.raw("PUT", "/api/config", b"")
    assert status == 413
    assert api.config_path.read_text() == before


def test_serves_the_ui(api):
    with urllib.request.urlopen(api.base + "/") as res:
        assert res.status == 200
        assert res.headers["Content-Type"].startswith("text/html")
        assert b"MacroPad" in res.read()


# --------------------------------------------------------------- icons --

def test_icon_store_returns_none_for_unknown_bundle():
    store = server.IconStore()
    assert store.get("com.example.App") is None


def test_icon_store_roundtrips_bytes():
    store = server.IconStore()
    store.set("com.example.App", b"\x89PNG...")
    assert store.get("com.example.App") == b"\x89PNG..."


def test_get_icon_404s_when_not_yet_cached(api):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(api.base + "/api/icon/com.example.App")
    assert exc_info.value.code == 404


def test_get_icon_serves_cached_png(api):
    api.icons.set("com.figma.Desktop", b"\x89PNG-bytes")
    with urllib.request.urlopen(api.base + "/api/icon/com.figma.Desktop") as res:
        assert res.status == 200
        assert res.headers["Content-Type"] == "image/png"
        assert res.read() == b"\x89PNG-bytes"


def test_get_icon_without_a_store_404s():
    """make_handler's icon_store defaults to None — must not crash the route."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(Path("x"), server.State()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/api/icon/com.example.App")
        assert exc_info.value.code == 404
    finally:
        httpd.shutdown()
