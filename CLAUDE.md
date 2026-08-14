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

- Remapping is a JSON edit, not a reflash + file copy to CIRCUITPY.
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

## Layout

```
firmware/boot.py       enables usb_cdc.data
firmware/code.py       event reporting + layer rendering
agent/macropad_agent.py  serial loop, app watcher, action executor
config/profiles.json   the actual mappings
launchd/               run-at-login plist
```

## Config schema

Keys indexed 0–11, row-major. `null` slots fall through to the profile named by
`defaultProfile`. Profiles are keyed by macOS bundle ID.

Action types: `keys`, `text`, `app`, `shell`, `url`, `shortcut`, `applescript`.
See README for the full table. If you add a type, update the README table, the
`_comment` block in `profiles.json`, and `run_action` together.

## Testing

Hardware can't be tested in CI. What can and should be:

- Profile merge/fallback resolution (`Config.resolve`)
- Combo parsing (`press_combo` splitting, modifier lookup, named keys)
- Config schema validation — 12 keys per profile, labels ≤ 6 chars, valid hex
  colors, known action types

Prefer pure functions that can be exercised without a serial port or a display.
When touching `Config` or the parser, add a test rather than manual-checking on
the pad.

## Conventions

- Trunk-based: short-lived branches off `main`, squash merge.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`).
- Scope commits to one concern. Firmware and agent changes that must ship
  together go in one commit; otherwise split them.

## Rules for AI agents

- Don't reflash or copy to `/Volumes/CIRCUITPY` without being asked. Use
  `make deploy` and tell the user to replug if `boot.py` changed.
- Don't add dependencies to `agent/requirements.txt` without flagging it. The
  current three are load-bearing and deliberately minimal.
- Don't restructure `profiles.json`'s schema casually — the planned GUI targets
  it, and the user's real config lives in this shape.
- When you can't verify something against hardware, say so plainly rather than
  claiming it works.

## Current state and next step

Working: firmware, agent, app-aware switching, hot reload, launchd.

Next: the mapping GUI. The config is plain JSON with a stable schema, so the
editor is decoupled from the runtime. Planned approach is a local web UI served
by the agent on localhost — Svelte frontend, small HTTP API for read/write of
`profiles.json`. The agent already hot-reloads on file change, so writes take
effect without a restart.

Two features that matter more than they sound:

- **Learn mode** — press a pad key to select the slot, then press the real
  shortcut on the keyboard to capture it. Typing `cmd+shift+opt+e` into a text
  field is how these tools become tedious.
- **Grab frontmost app** — capture bundle ID and icon of whatever was last in
  front, so nobody has to run `whoami` and copy strings by hand.
