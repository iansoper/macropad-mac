"""
app_icons.py — fetches macOS application icons via NSWorkspace.

`ui_png` renders a full-colour icon for the editor (the profile list and the
"add app" picker) — the only consumer; the pad's OLED shows no app icon.

Same threading rule as frontmost_bundle_id() and running_apps() in
macropad_agent.py: NSWorkspace belongs to the main loop. It is called from
Runtime.tick(), never from an HTTP handler thread — the results are cached
into server.IconStore, which the HTTP threads read without touching AppKit
at all.
"""

from AppKit import (
    NSBitmapImageRep,
    NSDeviceRGBColorSpace,
    NSGraphicsContext,
    NSWorkspace,
)
from Foundation import NSMakeRect

# NSCompositingOperationCopy. Hardcoded for the same reason icon.py hardcodes
# its PNG file-type constant: the symbolic name has moved around between
# pyobjc releases, but the raw value has been stable since forever.
_COMPOSITE_COPY = 1

# NSBitmapImageFileTypePNG — see icon.py's _PNG for the same reasoning.
_PNG = 4

UI_ICON_SIZE = 64


def _app_path(bundle_id: str):
    return NSWorkspace.sharedWorkspace().absolutePathForAppBundleWithIdentifier_(bundle_id)


def _bitmap_rep(image, size: int):
    """Render an NSImage into an exact-size RGBA NSBitmapImageRep.

    Mirrors icon.py's _bitmap(): an explicit bitmap context rather than
    lockFocus, because lockFocus renders at whatever backing scale the
    display happens to be, and callers here want exact pixel dimensions.
    """
    rep = NSBitmapImageRep.alloc().\
        initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    image.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0, 0, size, size), NSMakeRect(0, 0, 0, 0),
        _COMPOSITE_COPY, 1.0)
    NSGraphicsContext.restoreGraphicsState()
    return rep


def ui_png(bundle_id: str, size: int = UI_ICON_SIZE) -> bytes | None:
    """Full-colour PNG for the editor UI, or None if macOS has no app for this ID."""
    path = _app_path(bundle_id)
    if not path:
        return None
    image = NSWorkspace.sharedWorkspace().iconForFile_(path)
    rep = _bitmap_rep(image, size)
    return bytes(rep.representationUsingType_properties_(_PNG, {}))
