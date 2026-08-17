"""
editor_window.py — the editor in a window instead of a browser tab.

Same HTML, same localhost server; only the host changes. The reason it is
worth a dependency: the editor's Learn mode listens for keydown to capture a
shortcut, and a browser eats cmd+w, cmd+t, cmd+q and cmd+n before the page
sees them. Those four are the README's documented limitation.

A menu bar app installs no default main menu, so nothing claims those key
equivalents and WebKit should hand them to the page — which is also why the
Quit item in app.py deliberately has no cmd+Q. Quit from the menu instead.
Whether all four actually arrive is the one thing here worth testing by hand.
"""

from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect, NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

STYLE = (NSWindowStyleMaskTitled
         | NSWindowStyleMaskClosable
         | NSWindowStyleMaskMiniaturizable
         | NSWindowStyleMaskResizable)

# The editor's grid plus inspector needs roughly this much before it starts
# wrapping awkwardly.
WIDTH, HEIGHT = 1100, 780
MIN_WIDTH, MIN_HEIGHT = 720, 560


class Editor:
    """Holds the window open.

    Python has to keep these referenced: a window with no strong reference
    gets collected out from under AppKit and takes the app with it.
    """

    def __init__(self):
        self.window = None
        self.webview = None

    def show(self, url: str):
        if self.window is None:
            self._build(url)
        # An accessory-policy app is not in the Cmd-Tab list and does not
        # come forward on its own, so the window would open behind whatever
        # is frontmost without this.
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def _build(self, url: str):
        frame = NSMakeRect(0, 0, WIDTH, HEIGHT)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, STYLE, NSBackingStoreBuffered, False)
        window.setTitle_("MacroPad")
        window.setMinSize_(NSMakeRect(0, 0, MIN_WIDTH, MIN_HEIGHT).size)
        # Closing the editor must not deallocate it — the menu item reopens
        # this same instance.
        window.setReleasedWhenClosed_(False)
        window.center()

        webview = WKWebView.alloc().initWithFrame_configuration_(
            frame, WKWebViewConfiguration.alloc().init())
        webview.setAutoresizingMask_(2 | 16)  # width | height sizable
        window.setContentView_(webview)
        webview.loadRequest_(
            NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

        self.window = window
        self.webview = webview

    def reload(self):
        if self.webview is not None:
            self.webview.reload_(None)
