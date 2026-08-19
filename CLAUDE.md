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
{"t":"hello"}                     on boot, and every 2s until a layer arrives
{"t":"key","i":0,"e":"down"}      i = 0..11, row-major, top-left first
{"t":"key","i":0,"e":"up"}
{"t":"enc","d":1}                 d = +1 cw, -1 ccw
{"t":"encbtn","e":"down"|"up"}
```

Host → device:

```
{"t":"layer","name":"Figma","labels":[...12],"colors":[...12],"icon":[...8]}
{"t":"ping"}                      keepalive every 2s
```

`colors` is 12 `0xRRGGBB` ints, 0 = off. `icon` is optional: 8 ints, one per
row top-to-bottom, MSB = leftmost pixel — an 8x8 1-bit silhouette of the
app's icon drawn in the header next to the name. Omit it (or send a falsy
value) for the centered, icon-less header layout the pad has always used.
`agent/icon_bitmap.py` packs it; `agent/app_icons.py` samples the app icon
via `NSWorkspace` to produce the pixels. There is no room on a 1-bit 128x64
display for more than a silhouette — don't mistake it for a full icon.

The device treats *any* inbound message as proof of life and marks itself
disconnected after `HOST_TIMEOUT` (5s) of silence. If you add a message type,
it participates in liveness automatically.

Liveness is *not* the same as having something on screen, and the two must stay
separate. A `ping` proves the Mac is alive but paints nothing, so the pad keeps
sending `hello` until an actual `layer` arrives — not merely until the host makes
a noise. Tying the re-announce to liveness strands the pad on "Waiting for Mac"
whenever a push is lost, because pings then keep it quiet forever. The agent
also re-pushes the current layer every 5s for the same reason; the pad ignores a
layer identical to the one it is already showing, so the repeat is free.

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
- **Plug the pad straight into the Mac, not through a hub.** On macOS 26 the
  drive mounts via `fskit` (check `mount` for the flag), and behind a chained
  hub that path writes unreliably: files land truncated to 0 bytes, the
  directory corrupts in the way described above, and eventually the volume
  throws `EIO` and macOS force-remounts it read-only. The same `circup install`
  that fails through a hub succeeds on a direct port.
- **`circup` trusts a directory's existence, not its contents.** A partial
  library install makes it print "'x' is already installed" forever. Force it
  with `circup install -U`, then confirm nothing landed empty:
  `find /Volumes/MACROPAD/lib -name '*.mpy' -size 0`. A missing submodule
  surfaces only as an `ImportError` at boot, which reads as a dead OLED.
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
- **Firmware targets CircuitPython 10.x.** `display.show()` was removed in 9,
  so `code.py` assigns `display.root_group` directly and will not boot on 8.
  Libraries must come from the bundle matching the board — `circup` picks it
  from the board's version, so upgrade the UF2 *first*, then `make libs-reset`
  to clear a stale `lib/` rather than installing over the top. A library that
  fails to import stops `code.py` before the display loop: the OLED shows a
  traceback, and the agent looks connected while nothing renders.
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
- **`Runtime.tick()` must never block.** Under `app.py` it runs from an
  `NSTimer` on the main run loop, so a `time.sleep()` in there freezes the menu
  bar and the editor window along with the agent. This is why the reconnect
  backoff is a `next_connect` deadline rather than the `time.sleep(1.0)` the
  loop used to carry. The timer is also registered in `NSRunLoopCommonModes` —
  a default-mode timer stops firing while a menu is open, which would kill the
  pad for as long as you hold the menu down.
- **Never compute bundled paths from `__file__`.** Inside the app, modules load
  from a zip and `Contents/Resources` is read-only. `agent/paths.py` owns this:
  the UI comes from `RESOURCEPATH`, and the live config moves to
  `~/Library/Application Support/MacroPad/`, seeded once from the bundled copy
  and never overwritten afterwards. From source everything resolves exactly as
  it always did, which is why `make run` and the tests are unaffected.
- **Every method on a pyobjc `NSObject` subclass becomes an ObjC method.**
  The selector is derived from the name, so a plain helper like `_label(self,
  title)` is rejected at class-creation time with `BadPrototypeError`.
  Decorate helpers with `@objc.python_method`, and use `objc.super(...)` rather
  than `super()`.
- **Ad-hoc signing resets the Accessibility grant.** The signature changes
  every build, so TCC stops recognising the app and keys silently stop working
  — the same symptom as never having granted it. Build with
  `make app SIGN_IDENTITY="<a self-signed cert>"` to keep it stable.
- **py2app needs the runtime-chosen backends listed.** `pyserial` and `pynput`
  both pick a platform module at import, and modulegraph does not see it. They
  are in `setup_app.py` under both `packages` and `includes`; a missing one
  shows up as an `ImportError` at launch with no terminal to print to, which
  is why `app.py` redirects stdout before importing anything interesting.

## Layout

```
firmware/boot.py         enables usb_cdc.data
firmware/code.py         event reporting + layer rendering
agent/macropad_agent.py  Runtime.tick(), app watcher, action executor
agent/app_icons.py       NSWorkspace icon lookups: UI PNG + pad silhouette
agent/icon_bitmap.py     packs pad silhouette pixels into wire bytes (stdlib only)
agent/server.py          localhost HTTP API + State/IconStore snapshots (stdlib only)
agent/paths.py           source-vs-bundle path resolution (stdlib only)
agent/app.py             MacroPad.app: NSApplication + menu bar, drives tick()
agent/editor_window.py   the editor in a WKWebView
agent/loginitem.py       Start at Login (writes the LaunchAgent plist)
agent/icon.py            draws the mark for the menu bar and the .icns
agent/ui/index.html      the mapping editor, one self-contained file
config/profiles.json     the actual mappings (the seed, once bundled)
launchd/                 run-at-login plist for running from source
tools/make_icon.py       renders build/MacroPad.icns
setup_app.py             py2app build config
tests/                   pytest suite for the parts that run off-Mac
```

Two entry points, one loop. `macropad_agent.main()` drives `Runtime.tick()` in
a `while` loop for terminal use; `app.py` drives the same `tick()` from an
`NSTimer`. Adding behavior to one driver and not the other is the mistake to
avoid — put it in `tick()`.

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

Covered today:

- `tests/test_server.py` — config validation, atomic writes, request routing,
  the loopback bind.
- `tests/test_paths.py` — source-vs-bundle resolution, including the frozen
  branch, which running from source never exercises.
- `tests/test_loginitem.py` — the LaunchAgent plist. It fails at login, hours
  later, with nothing attached to print to, so it is checked here instead.
- `tests/test_icon_bitmap.py` — the pad silhouette's threshold and bit-packing,
  the AppKit-free half of turning an app icon into wire bytes.

Still uncovered and worth adding:

- Profile merge/fallback resolution (`Config.resolve`)
- Combo parsing (`press_combo` splitting, modifier lookup, named keys)

Both live in `macropad_agent.py` behind the AppKit import, so testing them
means either extracting them into a module that doesn't import AppKit, or
stubbing the macOS modules in `conftest.py`. Extraction is the cleaner path,
and `paths.py` is the pattern to copy — stdlib-only, imported by both halves.

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

Working: firmware, agent, app-aware switching, hot reload, launchd, the
localhost mapping editor (grid editing, fall-through display and override,
learn mode, app picker, atomic validated saves, `NSWorkspace` app icons in
the profile list and app picker), `MacroPad.app` — a menu bar build of the
same agent with the editor in a WKWebView — and an 8x8 silhouette of the
frontmost app's icon in the pad's OLED header. **The pad-side icon is
unverified against hardware; confirm the header layout and legibility on a
real MacroPad before believing it.**

The editor is served from inside the agent process rather than as a second
daemon — one thing to launch, one source of truth. It has no build step and no
frontend dependencies on purpose: it's a single HTML file with inline CSS and
vanilla JS, so it can't rot when a toolchain moves on. The app wraps that same
process rather than replacing it, which is why going native cost one dependency
instead of a rewrite.

Known limitation, documented in the README: **a browser swallows `cmd+w`,
`cmd+t`, `cmd+q`, and `cmd+n` before the page sees them**, so learn mode can't
capture those four in a browser tab. The app's WKWebView should get them, since
a menu bar app installs no main menu to claim those key equivalents — hence no
⌘Q on the Quit item. **Unverified against hardware; confirm before believing
it.**

Still open: **learn mode driven from the pad itself** (press a pad key to pick
the slot). Also worth noting — with no pad connected, `tick()` returns before
polling the frontmost app, so the menu bar and the editor's app picker stay
empty until the pad is plugged in. That predates the app but is far more
visible in it.
