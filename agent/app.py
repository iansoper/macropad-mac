#!/usr/bin/env python3
"""
app.py — MacroPad.app.

The same agent, with an NSApplication around it. Three things change versus
running macropad_agent.py from a terminal:

  1. NSApplication owns the main run loop, and an NSTimer drives
     Runtime.tick(). pyserial and AppKit therefore still share one thread,
     which is what server.py's State snapshot assumes.
  2. stdout and stderr go to ~/Library/Logs/MacroPad/agent.log, because
     there is no terminal to print to. This is set up before anything
     interesting is imported — a missing module in a py2app build raises at
     import time, and without the redirect that traceback goes nowhere.
  3. A file lock keeps a login-item copy and a double-clicked copy from
     fighting over the serial port.

Run it from source to develop:  python3 agent/app.py
"""

import fcntl
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402

MAX_LOG_BYTES = 1 << 20
ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")

# Held for the process lifetime — flock is released when the fd closes, so
# this must not be a local that falls out of scope.
_lock_handle = None


def redirect_output():
    """Send print() somewhere findable. No-op from source."""
    if not paths.is_frozen():
        return
    log = paths.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > MAX_LOG_BYTES:
        log.unlink()
    stream = open(log, "a", buffering=1, encoding="utf-8")
    sys.stdout = stream
    sys.stderr = stream


def take_lock() -> bool:
    """False if another copy already holds it."""
    global _lock_handle
    path = paths.lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _lock_handle = handle
    return True


def accessibility_granted() -> bool:
    """Without this, pynput silently does nothing — no exception, no error.

    Reachable because pynput pulls in the Quartz wrapper. Guarded anyway:
    failing to answer is not a reason to refuse to start.
    """
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:
        return True
    return bool(AXIsProcessTrusted())


def _open(*args):
    subprocess.Popen(["open", *args])


def main() -> int:
    redirect_output()

    if not take_lock():
        print("[agent] another copy is already running — exiting")
        return 1

    paths.seed_config()

    # Imported after the redirect and the lock, so an ImportError from a
    # bad build lands in the log and a second copy never touches the port.
    import macropad_agent
    import editor_window
    import icon
    import loginitem

    import objc
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

    class AppDelegate(NSObject):
        # Every method on an NSObject subclass becomes an Objective-C method
        # whose selector is derived from its name, so plain helpers have to
        # be marked python_method or pyobjc rejects the class outright.
        def initWithRuntime_(self, runtime):
            self = objc.super(AppDelegate, self).init()
            if self is None:
                return None
            self.runtime = runtime
            self.editor = editor_window.Editor()
            self.status_item = None
            self.timer = None
            return self

        # -- lifecycle ----------------------------------------------------

        def applicationDidFinishLaunching_(self, notification):
            bar = NSStatusBar.systemStatusBar()
            self.status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
            self.status_item.button().setImage_(icon.status_image())

            menu = NSMenu.alloc().init()
            menu.setDelegate_(self)          # so menuWillOpen_ fires
            self.status_item.setMenu_(menu)

            self.runtime.start()
            print("[agent] running as MacroPad.app")

            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                macropad_agent.POLL_INTERVAL, self, "tick:", None, True)
            # Default-mode timers stop firing while a menu is open or a
            # window is being resized. Without this the pad goes dead for
            # as long as the menu is down.
            NSRunLoop.currentRunLoop().addTimer_forMode_(
                self.timer, NSRunLoopCommonModes)

        def applicationWillTerminate_(self, notification):
            if self.timer is not None:
                self.timer.invalidate()
            self.runtime.stop()

        def tick_(self, timer):
            self.runtime.tick()

        # -- menu ---------------------------------------------------------

        def menuWillOpen_(self, menu):
            """Rebuilt on open rather than polled — it is only ever read here."""
            menu.removeAllItems()
            snap = self.runtime.state.snapshot()

            if not snap["connected"]:
                status = "Pad: not connected"
            elif snap["padSilent"]:
                status = f"Pad: silent on {snap['port']}"
            else:
                status = f"Pad: {snap['port']}"
            menu.addItem_(self._label(status))

            app_name = snap["appName"] or "—"
            profile = snap["profileName"] or "—"
            menu.addItem_(self._label(f"{app_name}  →  {profile}"))

            if not accessibility_granted():
                menu.addItem_(NSMenuItem.separatorItem())
                menu.addItem_(self._action(
                    "Accessibility not granted — fix", "openAccessibility:"))

            menu.addItem_(NSMenuItem.separatorItem())
            menu.addItem_(self._action("Open Editor", "openEditor:"))
            menu.addItem_(self._action("Open Editor in Browser", "openBrowser:"))

            menu.addItem_(NSMenuItem.separatorItem())
            menu.addItem_(self._action("Reveal profiles.json", "revealConfig:"))
            menu.addItem_(self._action("Open Log", "openLog:"))

            if loginitem.available():
                menu.addItem_(NSMenuItem.separatorItem())
                item = self._action("Start at Login", "toggleLogin:")
                item.setState_(1 if loginitem.is_enabled() else 0)
                menu.addItem_(item)

            menu.addItem_(NSMenuItem.separatorItem())
            # No cmd+Q on purpose: leaving that key equivalent unclaimed is
            # what lets the editor's Learn mode capture it.
            menu.addItem_(self._action("Quit MacroPad", "quit:"))

        @objc.python_method
        def _label(self, title):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, None, "")
            item.setEnabled_(False)
            return item

        @objc.python_method
        def _action(self, title, selector):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, selector, "")
            item.setTarget_(self)
            return item

        # -- actions ------------------------------------------------------

        def openEditor_(self, sender):
            if self.runtime.ui_port is None:
                print("[agent] editor is not running")
                return
            self.editor.show(f"http://127.0.0.1:{self.runtime.ui_port}")

        def openBrowser_(self, sender):
            if self.runtime.ui_port is not None:
                _open(f"http://127.0.0.1:{self.runtime.ui_port}")

        def revealConfig_(self, sender):
            _open("-R", str(self.runtime.config.path))

        def openLog_(self, sender):
            log = paths.log_path()
            if log.exists():
                _open(str(log))
            else:
                _open(str(log.parent))

        def openAccessibility_(self, sender):
            _open(ACCESSIBILITY_PANE)

        def toggleLogin_(self, sender):
            if loginitem.is_enabled():
                loginitem.disable()
                print("[agent] start at login: off")
            else:
                print(f"[agent] start at login: on ({loginitem.enable()})")

        def quit_(self, sender):
            NSApplication.sharedApplication().terminate_(self)

    app = NSApplication.sharedApplication()
    # No Dock icon, no menu bar menus. LSUIElement in Info.plist does this
    # for the built bundle; setting it here covers running from source too.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    runtime = macropad_agent.Runtime(pump_runloop=False)
    delegate = AppDelegate.alloc().initWithRuntime_(runtime)
    app.setDelegate_(delegate)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
