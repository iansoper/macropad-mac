"""
setup_app.py — build MacroPad.app.

    make app          full build into dist/
    make app-dev      alias build; source stays live, rebuilds take seconds

Named setup_app.py rather than setup.py on purpose: this repo is not an
installable package, and `pip install .` finding a setup.py here would be a
confusing thing to happen.

Run `make icon` first — py2app wants the .icns to exist.
"""

import sys
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parent
# agent/ is a flat directory of sibling modules, not a package —
# macropad_agent.py does a bare `import server`. modulegraph resolves them
# the same way the interpreter does, so it needs the directory on the path.
sys.path.insert(0, str(ROOT / "agent"))

VERSION = "0.1.0"

APP = ["agent/app.py"]

DATA_FILES = [
    ("ui", ["agent/ui/index.html"]),
    # Seed only. On first launch this is copied to Application Support and
    # the copy is what gets edited — see paths.py.
    ("config", ["config/profiles.json"]),
]

PLIST = {
    "CFBundleName": "MacroPad",
    "CFBundleDisplayName": "MacroPad",
    "CFBundleIdentifier": "com.iansoper.macropad",
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    "LSMinimumSystemVersion": "13.0",
    # Menu bar only: no Dock icon, no app switcher entry.
    "LSUIElement": True,
    "NSHumanReadableCopyright": "MIT",
    # The editor is plain HTTP on loopback by design, and ATS blocks that by
    # default — without this the WKWebView shows a blank window.
    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    # `applescript` and `shortcut` actions shell out to osascript/shortcuts.
    # TCC attributes those Apple Events to the responsible process, which is
    # now this bundle rather than Terminal.
    "NSAppleEventsUsageDescription":
        "MacroPad runs the AppleScript and Shortcuts actions in your profiles.",
}

OPTIONS = {
    "iconfile": "build/MacroPad.icns",
    # Carbon-based, long broken, and needs Accessibility of its own.
    "argv_emulation": False,
    "packages": ["serial", "pynput", "objc", "Foundation", "AppKit", "WebKit"],
    "includes": [
        # The flat sibling modules.
        "macropad_agent", "server", "paths", "icon", "app_icons",
        "loginitem", "editor_window",
        # Both of these pick a platform backend at runtime, so static
        # analysis never sees them and the app dies at launch with an
        # ImportError that has nowhere to print. This is the single most
        # likely thing to need extending after a dependency bump.
        "serial.tools.list_ports_osx",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
    ],
    "plist": PLIST,
}

setup(
    name="MacroPad",
    version=VERSION,
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
