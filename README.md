# App-aware MacroPad for macOS

An Adafruit MacroPad RP2040 that changes its keymap, OLED labels, and per-key
RGB depending on which macOS app is in front.

```
MacroPad (CircuitPython)                 Mac agent (Python)
  key/encoder events  ──── USB serial ────►  looks up action, executes it
  OLED + NeoPixels    ◄─── USB serial ─────  pushes layer on app switch
                                             watches NSWorkspace frontmost app
                                             reads config/profiles.json
                                             serves the editor on :8765
```

Mapping keys is done in a browser-based editor the agent serves itself — see
[Editor](#editor). Everything it does is a write to `profiles.json`, so hand-editing
the file remains a first-class option.

## Why the pad is "dumb"

The obvious approach is to store 12 layers on the device and have the Mac say
"switch to layer 3." That works, but it means every remap is a firmware edit,
and keys can only ever send keystrokes.

Here the pad reports raw events and the Mac decides what they mean. Consequences:

- Remapping is a JSON edit. No reflashing, no file copying to MACROPAD.
- Actions aren't limited to keystrokes — shell commands, `open -a`, AppleScript,
  Shortcuts, and URL schemes all work.
- A future GUI just edits `profiles.json`. The firmware never changes again.
- Trade-off: if the agent isn't running the pad falls back to a volume knob.
  That's the price of putting the brains on the host.

Round-trip latency is ~2ms over USB CDC. You will not feel it.

## Setup

### 1. Firmware

Install CircuitPython 10.x on the MacroPad (double-tap reset, drag the UF2 to
the `RPI-RP2` drive). Then install the libraries and copy the code:

```bash
pip3 install --upgrade circup
circup install adafruit_macropad adafruit_display_text adafruit_display_shapes
cp firmware/boot.py firmware/code.py /Volumes/MACROPAD/
```

`circup` reads the version off the board and pulls the matching bundle, so
upgrading the UF2 first and re-running `circup install` is what gets you 10.x
libraries. Libraries built for 9.x are not guaranteed to load on 10.x — if you
are coming from an older install, delete `/Volumes/CIRCUITPY/lib` and let
`circup` repopulate it rather than installing over the top.

The firmware needs 10.x (or at minimum 9.x): `display.show()` was removed in
CircuitPython 9 and `code.py` assigns `display.root_group` directly.

Unplug and replug the pad. `boot.py` only takes effect after a hard reset —
this is the step people miss. The OLED should read **Waiting for Mac**.

### 2. Agent

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
python3 agent/macropad_agent.py ports     # confirm the pad is found
python3 agent/macropad_agent.py           # run it
```

The first key press will fail silently until you grant Accessibility
permission: **System Settings → Privacy & Security → Accessibility**, add
whatever app is running Python (Terminal, iTerm, VS Code). Synthesizing
keystrokes is privileged on macOS; there's no way around this.

### 3. Add your own apps

Open <http://127.0.0.1:8765> (`make ui`) and hit **+ Add app**. It lists your
running apps with their bundle IDs, so you never have to look one up.

If you'd rather work in the JSON, `python3 agent/macropad_agent.py whoami`
prints bundle IDs as you click between apps. Add a profile keyed by the bundle
ID. Any `null` slot falls through to the `global` profile, so a Figma layer
that only overrides 6 keys keeps your global media controls on the rest.

The agent hot-reloads the config on save — no restart while you're tuning,
whichever way you edit it.

### 4. Run at login (optional)

Either build the app (below), which has a **Start at Login** toggle, or wire up
launchd by hand:

```bash
cp launchd/com.iansoper.macropad.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.iansoper.macropad.plist
```

Edit the paths in the plist first. Grant Accessibility to the Python binary
itself when running under launchd, not to Terminal.

## MacroPad.app

The same agent and the same editor, wrapped in a menu bar app — no terminal, a
visible connection status, and one stable thing to grant Accessibility to
instead of whichever Python your venv happens to hold.

```bash
make dev          # installs py2app alongside the test deps
make app          # build and sign dist/MacroPad.app
make app-install  # copy it to /Applications
```

Launch it and a small grid appears in the menu bar. The menu shows which port
the pad is on, the frontmost app and the profile it resolved to, and warns when
Accessibility is missing. **Start at Login** writes the LaunchAgent for you, so
there are no paths to hand-edit.

`make app-dev` builds in alias mode instead: the bundle points at this checkout
rather than copying it, so edits are live and a rebuild takes seconds. Use it
while working on the app, and `make app` for the real thing.

**Where your config lives changes when you run the app.** From source it stays
`config/profiles.json`. Installed, the app copies that file once to
`~/Library/Application Support/MacroPad/profiles.json` and edits it there —
`Contents/Resources` is read-only, and the editor writes to this file. Existing
configs are never overwritten by a reinstall. **Reveal profiles.json** in the
menu opens whichever one is live. Logs go to `~/Library/Logs/MacroPad/`.

One wrinkle worth knowing: the app is ad-hoc signed, and an ad-hoc signature
changes on every build, so macOS may drop the Accessibility grant each time you
rebuild. If that gets tiresome, make a self-signed **Code Signing** certificate
in Keychain Access and build with `make app SIGN_IDENTITY="Your Cert Name"` —
a stable identity keeps the grant.

## Editor

The agent serves a mapping editor at <http://127.0.0.1:8765>. It runs on a
thread inside the agent process — there's no second thing to launch, and no
build step, npm, or bundle involved. It's one HTML file.

```bash
make run    # agent + editor
make ui     # open it
```

The grid mirrors the physical pad, so slot 0 on screen is the top-left key.
Pick a profile on the left, click a key, edit it on the right, hit **Save**
(or ⌘S). The agent notices the file change within about a second and re-pushes
the layer, so the OLED and LEDs update while you're still looking at them.

Keys inherited from the default profile are drawn dashed and dimmed with a `↓`,
which makes fall-through visible instead of something you infer from `null`s.
**Override here** gives that slot its own binding, seeded from the inherited
one; **Reset to default** puts it back.

**Learn mode** is the reason to use the editor over the JSON: click **Learn**
and press the actual shortcut instead of typing `cmd+shift+opt+e` into a field.

One real limitation, **in a browser**: it claims a handful of combos before any
page sees them, so `cmd+w`, `cmd+t`, `cmd+q`, and `cmd+n` cannot be captured by
Learn mode there. Type those into the field by hand; they work fine on the pad,
they just can't be recorded through a browser tab.

Opening the editor from **MacroPad.app** should lift that: a menu bar app
installs no default main menu, so nothing claims those key equivalents and
WebKit hands them to the page. That is also why Quit in the menu has no ⌘Q.

### Notes

- The server binds to `127.0.0.1` only, and deliberately sends no CORS headers.
  Profiles can carry `shell` and `applescript` actions, so anything that can
  write to this API can make the agent run arbitrary commands. Don't expose it.
- Saves are validated and written atomically (temp file + `os.replace`), so a
  rejected or half-finished save can't leave the agent with a broken config.
- `MACROPAD_UI_PORT` moves it; `MACROPAD_NO_UI=1` runs the agent headless.
- If the port is taken — usually a second copy of the agent — it logs and keeps
  going. The pad still works without the editor.

## Troubleshooting

### The OLED and LEDs never change

The pad sits on **Waiting for Mac** with dim blue keys while the agent looks
happy. In order of likelihood:

1. **`boot.py` hasn't taken effect.** Run `make ports`. If it lists only one
   port, `usb_cdc.data` is not enabled — that port is the console, and the pad
   has no channel to receive layers on. Copy `boot.py` to CIRCUITPY and
   **unplug and replug**; a soft reset is not enough. The agent refuses to open
   a lone console port rather than pretending it worked.
2. **`code.py` stopped on a traceback.** Usually a missing or version-mismatched
   library after a CircuitPython upgrade. Open the *console* port
   (`screen /dev/cu.usbmodemXXXX 115200`) and look. `make libs-reset` fixes the
   stale-bundle case.
3. **Wrong port.** The agent picks the higher-numbered of the two. If your setup
   differs, `MACROPAD_PORT=/dev/cu.usbmodemXXXX make run`.

The agent prints a warning if it opens a port and the pad never answers within
six seconds, and `/api/state` exposes the same thing as `padSilent`. It should
never silently sit there writing into nothing.

Keys doing nothing at all — but labels and colors updating fine — is the other
failure, and it's Accessibility permission, not serial. See step 2 of Setup.

## Config reference

Keys are indexed 0–11, row-major, top-left first (3 wide × 4 tall).

```json
{
  "label": "Export",
  "color": "#3E8E9B",
  "action": { "type": "keys", "value": "cmd+shift+e" }
}
```

Labels are truncated to 6 characters on the 128×64 OLED. Keep them short.

| Action type   | Value                  | Notes                             |
| ------------- | ---------------------- | --------------------------------- |
| `keys`        | `cmd+shift+4`          | Modifiers: cmd, opt, ctrl, shift  |
| `text`        | `ian@iansoper.com`     | Types the literal string          |
| `app`         | `Figma`                | `open -a`                         |
| `shell`       | `open -a Ghostty`      | Runs via the shell, non-blocking  |
| `url`         | `raycast://extensions` | Any URL scheme                    |
| `shortcut`    | `Daily Standup`        | Runs a macOS Shortcut             |
| `applescript` | `tell application ...` | Via `osascript -e`                |

Named keys for `keys`: `enter return tab esc space delete fwddelete up down
left right home end pageup pagedown f1–f20 volup voldown mute playpause next
prev`.

The encoder takes `cw`, `ccw`, and `press`, each a normal action object.

## Going native

Mostly done, and more cheaply than expected. `MacroPad.app` above is the Python
agent frozen with py2app behind an `NSStatusItem`, with the editor in a
`WKWebView`. No rewrite, one added dependency, and the schema and `/api` shape
never moved. The two rewrites below stay on the table but are no longer the
only way to get an app:

**Tauri + Svelte (a week or two).** Real `.app`, tray icon, launches at login.
The editor's markup ports over more or less directly. You'd move the serial
loop into Rust (`serialport`), keystroke synthesis to `enigo`, and
frontmost-app detection to `objc2-app-kit`.

**SwiftUI menu bar app (a week or two, steeper ramp).** The most native result,
and the one thing it still buys over the current app is size: no Python runtime
to ship, so ~40 MB becomes a couple. `NSWorkspace`, `CGEventPost`, and POSIX
serial are all first-class. Worth it if this ever becomes a thing you
distribute.

Either way the JSON schema and `/api` shape stay as they are, so a port can
coexist with what's here rather than requiring a big-bang switch.

Still worth building:

- **Learn mode from the pad itself.** Press a pad key to select the slot rather
  than clicking it, so your hands never leave the hardware.
- **App icons in the profile list.** `NSWorkspace` already has them, and a list
  of bundle IDs is harder to scan than a list of icons.

## Off-the-shelf alternative

If the OLED and RGB weren't part of the appeal, you could skip all of this:
flash the pad to send F13–F24 (exactly 12 keys), then use Karabiner-Elements
(`frontmost_application_if`), BetterTouchTool, or Keyboard Maestro to scope
those to apps with a real GUI you didn't have to build.

Those tools can't drive the display or the LEDs, which is the whole reason to
own this particular pad. But they're worth knowing about as a fallback.
