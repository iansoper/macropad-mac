"""
Tests for the Start at Login plist.

launchd reads this file, so a mistake in it fails at login — hours later,
silently, with no terminal to print to. The rendering is pure and stdlib-only
precisely so it can be checked here instead.

Nothing in this file shells out to launchctl.
"""

import plistlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import loginitem  # noqa: E402
import paths  # noqa: E402

PROGRAM = Path("/Applications/MacroPad.app/Contents/MacOS/MacroPad")
STDERR = Path("/Users/x/Library/Logs/MacroPad/launchd.err")


@pytest.fixture
def rendered():
    return plistlib.loads(loginitem.render(PROGRAM, STDERR))


def test_label_matches_the_bundle_id(rendered):
    assert rendered["Label"] == paths.BUNDLE_ID == "com.iansoper.macropad"


def test_runs_the_app_binary(rendered):
    assert rendered["ProgramArguments"] == [str(PROGRAM)]
    assert rendered["RunAtLoad"] is True


def test_quit_stays_quit(rendered):
    """A bare KeepAlive=True relaunches the app every time you choose Quit."""
    assert rendered["KeepAlive"] == {"SuccessfulExit": False}


def test_loads_into_the_gui_session(rendered):
    """NSStatusBar has nothing to attach to outside an Aqua session."""
    assert rendered["LimitLoadToSessionType"] == "Aqua"
    assert rendered["ProcessType"] == "Interactive"


def test_captures_stderr_for_failures_before_the_app_redirects(rendered):
    assert rendered["StandardErrorPath"] == str(STDERR)


def test_round_trips_as_a_real_plist():
    """plistlib is strict on load, so this catches an unserialisable value."""
    raw = loginitem.render(PROGRAM, STDERR)
    assert raw.startswith(b"<?xml")
    assert plistlib.loads(raw)["Label"] == paths.BUNDLE_ID


# ----------------------------------------------------------------- state --

def test_not_offered_when_running_from_source():
    """Pointed at a venv interpreter it breaks on the next venv rebuild."""
    assert loginitem.available() is False


def test_is_enabled_follows_the_plist(monkeypatch, tmp_path):
    plist = tmp_path / "com.iansoper.macropad.plist"
    monkeypatch.setattr(paths, "launch_agent_plist", lambda: plist)

    assert loginitem.is_enabled() is False
    plist.write_bytes(loginitem.render(PROGRAM, STDERR))
    assert loginitem.is_enabled() is True
