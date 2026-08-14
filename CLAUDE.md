# CLAUDE.md

Context for AI coding agents working in this repo. Read this before making changes.

## What this is

An Adafruit MacroPad RP2040 that swaps its keymap, OLED labels, and per-key RGB
based on the frontmost macOS app. Two halves that talk over USB serial:

- `firmware/` — CircuitPython on the RP2040. Deliberately thin.
- `agent/` — Python daemon on the Mac. Holds all the logic.

## The one architectural decision that matters

**The pad stores no macros.** It reports raw key/encoder events to the Mac and
renders whatever layer the Mac pushes back. All mapping lives in
`config/profiles.json` on the host.

This is a deliberate trade, not an oversight. Do not "optimize" by moving
keymaps into firmware. The reasons:

- Remapping is a JSON edit, not a reflash + file copy to MACROPAD.
- Actions aren't limited to keystrokes — shell, AppleScript, Shortcuts, URL
  schemes all work because the host executes them.
- The planned GUI is just a `profiles.json` editor. Firmware never changes again.

Accepted cost: if the agent isn't running, the pad degrades to a local volume
knob (see the `connected` fallback in `firmware/code.py`). That is the intended
behavior. Don't add a device-side macro cache to "fix" it.

Round-trip latency over USB CDC is ~2ms. Latency is not a reason to move logic
to the device.

## Wire protocol

Newline-delimited JSON over `usb_cdc.data`. Both sides buffer and split on
`\n` — never assume one read equals one message.

Device → host:

```
{"t":"hello"}                     on boot, and every 2s until the host replies
{"t":"key","i":0,"e":"down"}      i = 0..11, row-major, top-left first
{"t":"key","i":0,"e":"up"}
{"t":"enc","d":1}                 d = +1 cw, -1 ccw
{"t":"encbtn","e":"down"|"up"}
```

Host → device:

```
{"t":"layer","name":"Figma","labels":[...12],"colors":[...12]}   colors 0xRRGGBB, 0 = off
{"t":"ping"}                      keepalive every 2s
```

The device treats *any* inbound message as proof of life and marks itself
disconnected after `HOST_TIMEOUT` (5s) of silence. If you add a message type,
it participates in liveness automatically.

## Hard constraints — these have all bitten already

- **`boot.py` needs a hard reset.** Editing it does nothing until the pad is
  unplugged and replugged. Without it `usb_cdc.data` is `None`.
- **Eject the drive before resetting the pad.** `make deploy` leaves macOS
  holding the volume with writes still buffered. Resetting the board then —
  by replug, by the reset button, or via `microcontroller.reset()` — is an
  unclean unmount, and it corrupts the FAT directory. The damage does not look
  like damage: `fsck_msdos` reports the volume clean because the FAT chains are
  intact, while the root directory fills with entries that enumerate but fail
  to `stat`, and macOS then refuses to mount at all. Run
  `diskutil eject /Volumes/MACROPAD` first. If it does get corrupted, the repair
  is `storage.erase_filesystem()` from the REPL, not Disk Utility.
- **Repeated re-enumeration can wedge `diskarbitrationd`.** After enough
  reset cycles it starts reporting "Volume(s) mounted successfully" while
  `diskutil info` still says `Mounted: No`. No amount of `diskutil` retrying
  fixes it — replug the pad, or restart the daemon.
- **The pad exposes two serial ports** (console + data). The data channel is
  the higher-numbered `/dev/cu.usbmodem*`. `agent/macropad_agent.py ports`
  shows the selection; `MACROPAD_PORT` overrides it.
- **Accessibility permission is required** for synthesized keystrokes. Without
  it `pynput` silently no-ops — no exception, no error. If keys "do nothing,"
  check this before debugging anything else.
- **OLED labels truncate at 6 characters.** 128×64 display, 3 columns. Longer
  labels are silently cut, so keep sample configs within budget.
- **Firmware runs CircuitPython, not CPython.** No threading, no `asyncio` in
  the main loop, limited stdlib. Keep `code.py` a single non-blocking loop.
  Never call a blocking `readline()` — check `in_waiting` first.
- **Never let a bad mapping crash the agent.** `run_action` catches broadly on
  purpose. A typo in a user's JSON should log and continue.
- **The editor API binds to `127.0.0.1` and must stay there.** Profiles carry
  `shell` and `applescript` actions, so write access to `/api/config` is
  arbitrary code execution on the next key press. No CORS headers, no `0.0.0.0`,
  no tunnels.
- **AppKit and pyserial belong to the main loop.** HTTP handlers run on other
  threads and must read `server.State` instead of calling `NSWorkspace` or
  touching the serial port.
- **Config writes must stay atomic.** The agent reloads on mtime change, so a
  non-atomic write can be read half-finished. Use `write_config_atomically`.

## Layout

```
firmware/boot.py         enables usb_cdc.data
firmware/code.py         event reporting + layer rendering
agent/macropad_agent.py  serial loop, app watcher, action executor
agent/server.py          localhost HTTP API + State snapshot (stdlib only)
agent/ui/index.html      the mapping editor, one self-contained file
config/profiles.json     the actual mappings
launchd/                 run-at-login plist
tests/                   pytest suite for the parts that run off-Mac
```

## Config schema

Keys indexed 0–11, row-major. `null` slots fall through to the profile named by
`defaultProfile`. Profiles are keyed by macOS bundle ID.

Action types: `keys`, `text`, `app`, `shell`, `url`, `shortcut`, `applescript`.
See README for the full table. If you add a type, update the README table, the
`_comment` block in `profiles.json`, and `run_action` together.

## Testing

```bash
make dev && make test
```

Hardware can't be tested in CI, and neither can anything importing `AppKit` or
`pynput`. `agent/server.py` is deliberately stdlib-only so the HTTP layer is
testable on any machine — keep it that way; don't import from
`macropad_agent.py` into it.

Covered today (`tests/test_server.py`): config validation, atomic writes,
request routing, and the loopback bind.

Still uncovered and worth adding:

- Profile merge/fallback resolution (`Config.resolve`)
- Combo parsing (`press_combo` splitting, modifier lookup, named keys)

Both live in `macropad_agent.py` behind the AppKit import, so testing them
means either extracting them into a module that doesn't import AppKit, or
stubbing the macOS modules in `conftest.py`. Extraction is the cleaner path.

Prefer pure functions that can be exercised without a serial port or a display.
When touching `Config` or the parser, add a test rather than manual-checking on
the pad.

## Conventions

- Trunk-based: short-lived branches off `main`, squash merge.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`).
- Scope commits to one concern. Firmware and agent changes that must ship
  together go in one commit; otherwise split them.

## Rules for AI agents

- Don't reflash or copy to `/Volumes/MACROPAD` without being asked. Use
  `make deploy` and tell the user to replug if `boot.py` changed.
- Don't add dependencies to `agent/requirements.txt` without flagging it. The
  current three are load-bearing and deliberately minimal.
- Don't restructure `profiles.json`'s schema casually — the planned GUI targets
  it, and the user's real config lives in this shape.
- When you can't verify something against hardware, say so plainly rather than
  claiming it works.

## Current state and next step

Working: firmware, agent, app-aware switching, hot reload, launchd, and the
localhost mapping editor (grid editing, fall-through display and override,
learn mode, app picker, atomic validated saves).

The editor is served from inside the agent process rather than as a second
daemon — one thing to launch, one source of truth. It has no build step and no
frontend dependencies on purpose: it's a single HTML file with inline CSS and
vanilla JS, so it can't rot when a toolchain moves on.

Known limitation, documented in the README: **the browser swallows `cmd+w`,
`cmd+t`, `cmd+q`, and `cmd+n` before the page sees them**, so learn mode can't
capture those four. They must be typed by hand. This is not fixable from within
a browser; a native app with a global event tap would solve it.

Next, if it's worth the effort — going native (Tauri or SwiftUI). The JSON
schema and `/api` shape are stable, so a port can run alongside the current
editor instead of replacing it in one go. Also still open: **learn mode driven
from the pad itself** (press a pad key to pick the slot), and **app icons in
the profile list** via `NSWorkspace`.
