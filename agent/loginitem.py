"""
loginitem.py — the "Start at Login" toggle.

SMAppService would be the modern way and would mean another pyobjc framework
package for one boolean, so this writes the LaunchAgent plist directly. Same
mechanism the README already documents, minus the /Users/CHANGEME/ hand-edit:
the path is filled in from the running bundle.

Enabling deliberately does *not* run `launchctl bootstrap`. launchd loads
everything in ~/Library/LaunchAgents at login by itself, and we are already
running — bootstrapping would start a second copy that immediately loses the
single-instance lock and exits. Writing the file is the whole job.

Stdlib-only, so the plist rendering is testable off a Mac.
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import paths

LABEL = paths.BUNDLE_ID


def executable() -> Path:
    """The binary launchd should run.

    Inside a bundle sys.executable is Contents/MacOS/MacroPad, which is
    exactly what we want — launching that starts the app proper.
    """
    return Path(sys.executable).resolve()


def render(program: Path, stderr_log: Path) -> bytes:
    """The plist, as bytes. Pure — everything testable lives here."""
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(program)],
        "RunAtLoad": True,
        # Restart if it crashes, but respect Quit. A bare KeepAlive=True
        # would relaunch the app every time you chose Quit from the menu.
        "KeepAlive": {"SuccessfulExit": False},
        # This is a UI app; without these launchd may throttle it or start
        # it outside the Aqua session, where NSStatusBar has nothing to
        # attach to.
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        # The app redirects its own stdout/stderr once it is up. This only
        # catches what dies before that — an ImportError from a bad build,
        # which is otherwise completely invisible.
        "StandardErrorPath": str(stderr_log),
    })


def available() -> bool:
    """Only offer this for an installed app.

    Pointed at a venv interpreter it would half-work and then break the
    next time the venv was rebuilt.
    """
    return paths.is_frozen()


def is_enabled() -> bool:
    return paths.launch_agent_plist().exists()


def enable() -> Path:
    plist = paths.launch_agent_plist()
    plist.parent.mkdir(parents=True, exist_ok=True)
    stderr_log = paths.log_path().with_name("launchd.err")
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(render(executable(), stderr_log))
    return plist


def disable() -> None:
    plist = paths.launch_agent_plist()
    # Only meaningful if we were started by launchd this session; it errors
    # harmlessly when we were not, hence the swallowed result.
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True, text=True)
    plist.unlink(missing_ok=True)
