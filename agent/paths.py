"""
paths.py — where things live, in each of the two ways this ships.

Run from the repo, everything sits next to the source: the config is
`config/profiles.json`, the editor's HTML is `agent/ui/index.html`. Run from
`MacroPad.app`, neither of those is true. `Path(__file__).parent` points into
`Contents/Resources/lib/python3.x/site-packages.zip`, and `Contents/Resources`
is read-only — which matters, because the editor writes the config back.

So both halves ask here instead of computing paths from `__file__`, and the
answers differ by whether we are frozen. Kept stdlib-only and free of any
macOS import so `server.py` can go on being tested off a Mac.

Overrides that still work either way:
    MACROPAD_CONFIG   absolute path to a profiles.json
    MACROPAD_UI_PORT  handled in server.py, mentioned here so the set is findable
"""

import os
import sys
from pathlib import Path

BUNDLE_ID = "com.iansoper.macropad"
APP_NAME = "MacroPad"

# Where the source tree is, when there is one. Resolved at import rather than
# per call because it cannot change under us.
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """True inside a py2app bundle.

    py2app sets sys.frozen to the string "macosx_app". Testing the value
    rather than truthiness keeps this from firing under some other freezer
    that sets a different marker and lays resources out differently.
    """
    return getattr(sys, "frozen", "") == "macosx_app"


def resource_dir() -> Path:
    """The directory bundled data files were copied into.

    py2app exports RESOURCEPATH into the environment at launch; that is the
    supported way to find them, and it is correct whether the modules ended
    up loose or zipped. Falling back to the source root means every caller
    below reads the same in both modes.
    """
    if is_frozen():
        resources = os.environ.get("RESOURCEPATH")
        if resources:
            return Path(resources)
        # Belt and braces: Contents/MacOS/MacroPad -> Contents/Resources
        return Path(sys.executable).resolve().parent.parent / "Resources"
    return SOURCE_ROOT


def ui_dir() -> Path:
    """Where index.html is. Frozen, it is copied to Resources/ui."""
    if is_frozen():
        return resource_dir() / "ui"
    return SOURCE_ROOT / "agent" / "ui"


def bundled_config() -> Path:
    """The config that ships with the build — the seed, not the live copy.

    Frozen this is read-only, which is exactly why config_path() points
    somewhere else. From source the two are the same file, so editing in
    place keeps working as it always has.
    """
    return resource_dir() / "config" / "profiles.json"


def support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def config_path() -> Path:
    """The live config the agent reads and the editor writes."""
    override = os.environ.get("MACROPAD_CONFIG")
    if override:
        return Path(override).expanduser()
    if is_frozen():
        return support_dir() / "profiles.json"
    return bundled_config()


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / APP_NAME / "agent.log"


def lock_path() -> Path:
    """Single-instance lock.

    Two agents on one serial port interleave their reads and neither gets a
    whole message, so the app takes this before opening anything.
    """
    return support_dir() / "agent.lock"


def launch_agent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def seed_config(dst: Path | None = None, src: Path | None = None) -> Path:
    """Put a config in place on first run, and never touch it again.

    Only ever creates. An existing file is the user's real mapping and a
    build must not overwrite it — reinstalling the app is not consent to
    lose your layout.
    """
    dst = dst or config_path()
    src = src or bundled_config()
    if dst.exists():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists() and src != dst:
        dst.write_bytes(src.read_bytes())
    return dst
