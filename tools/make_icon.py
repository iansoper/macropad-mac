#!/usr/bin/env python3
"""
make_icon.py — build build/MacroPad.icns from agent/icon.py.

There is no artwork file to keep in sync: the mark is drawn from the same
geometry the editor's favicon uses. Run via `make icon`; py2app wants the
.icns to exist before it builds.

Needs macOS for both AppKit and iconutil.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import icon  # noqa: E402

# The set iconutil expects. Each logical size appears twice, once at 1x and
# once as the @2x of the size below it, and both files must be present.
SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def main() -> int:
    build = ROOT / "build"
    iconset = build / "MacroPad.iconset"
    iconset.mkdir(parents=True, exist_ok=True)

    for name, size in SIZES:
        (iconset / name).write_bytes(icon.png_bytes(size))
    print(f"[icon] rendered {len(SIZES)} sizes into {iconset}")

    icns = build / "MacroPad.icns"
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[icon] iconutil failed: {result.stderr.strip()}")
        return 1

    print(f"[icon] wrote {icns} ({icns.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
