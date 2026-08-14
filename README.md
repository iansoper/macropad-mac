# App-aware MacroPad for macOS

An Adafruit MacroPad RP2040 that changes its keymap, OLED labels, and per-key
RGB depending on which macOS app is in front.

```
MacroPad (CircuitPython)                 Mac agent (Python)
  key/encoder events  ──── USB serial ────►  looks up action, executes it
  OLED + NeoPixels    ◄─── USB serial ─────  pushes layer on app switch
                                             watches NSWorkspace frontmost app
                                             reads config/profiles.json
```

## Why the pad is "dumb"

The obvious approach is to store 12 layers on the device and have the Mac say
"switch to layer 3." That works, but it means every remap is a firmware edit,
and keys can only ever send keystrokes.

Here the pad reports raw events and the Mac decides what they mean. Consequences:

- Remapping is a JSON edit. No reflashing, no file copying to CIRCUITPY.
- Actions aren't limited to keystrokes — shell commands, `open -a`, AppleScript,
  Shortcuts, and URL schemes all work.
- A future GUI just edits `profiles.json`. The firmware never changes again.
- Trade-off: if the agent isn't running the pad falls back to a volume knob.
  That's the price of putting the brains on the host.

Round-trip latency is ~2ms over USB CDC. You will not feel it.

## Setup

### 1. Firmware

Install CircuitPython 9.x on the MacroPad (double-tap reset, drag the UF2 to
the `RPI-RP2` drive). Then install the libraries and copy the code:

```bash
pip3 install circup
circup install adafruit_macropad adafruit_display_text adafruit_display_shapes
cp firmware/boot.py firmware/code.py /Volumes/CIRCUITPY/
```

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

```bash
python3 agent/macropad_agent.py whoami
```

Click around your apps and it prints bundle IDs. Add a profile keyed by the
bundle ID to `profiles.json`. Any `null` slot falls through to the `global`
profile, so a Figma layer that only overrides 6 keys keeps your global media
controls on the rest.

The agent hot-reloads the config on save — no restart while you're tuning.

### 4. Run at login (optional)

```bash
cp launchd/com.iansoper.macropad.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.iansoper.macropad.plist
```

Edit the paths in the plist first. Grant Accessibility to the Python binary
itself when running under launchd, not to Terminal.

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

## Building the mapping GUI

The config is plain JSON with a stable schema, so the editor is a separate
concern from the runtime. Three ways to go, roughly in order of effort:

**Local web UI (a weekend).** Have the agent serve a small localhost page that
reads and writes `profiles.json`. Svelte, your own tokens, done. No signing, no
packaging, and the agent already hot-reloads. Best value per hour.

**Tauri + Svelte (a week or two).** Real `.app`, tray icon, launches at login.
You'd move the serial loop into Rust (`serialport`), keystroke synthesis to
`enigo`, and frontmost-app detection to `objc2-app-kit`. Lets you keep the whole
UI in your existing frontend stack.

**SwiftUI menu bar app (a week or two, steeper ramp).** The most native result.
`NSWorkspace`, `CGEventPost`, and POSIX serial are all first-class, so the agent
collapses into the app with no Python runtime to ship. Worth it if this ever
becomes a thing you distribute.

Two features that will matter more than they sound:

- **Learn mode.** Press a pad key to select the slot, then press the real
  shortcut on your keyboard to capture it. Typing `cmd+shift+opt+e` into a text
  field is how these tools become tedious.
- **Grab frontmost app.** A button that captures the bundle ID and icon of
  whatever was in front, so you never touch `whoami` again.

## Off-the-shelf alternative

If the OLED and RGB weren't part of the appeal, you could skip all of this:
flash the pad to send F13–F24 (exactly 12 keys), then use Karabiner-Elements
(`frontmost_application_if`), BetterTouchTool, or Keyboard Maestro to scope
those to apps with a real GUI you didn't have to build.

Those tools can't drive the display or the LEDs, which is the whole reason to
own this particular pad. But they're worth knowing about as a fallback.
