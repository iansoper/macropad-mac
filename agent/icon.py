"""
icon.py — the mark, drawn rather than shipped as artwork.

The only existing artwork is the editor's inline favicon: a 3x4 grid of
rounded keys on a 24x32 viewBox, one colour per row. Rather than commit a
binary that drifts from it, this redraws the same geometry with AppKit and
both consumers read from here — the menu bar item at runtime, and
tools/make_icon.py building the .icns.

AppKit only, deliberately. Quartz would be the obvious choice for this but
would mean depending on a package that is currently only a transitive of
pynput, and NSBezierPath draws a rounded rect perfectly well.
"""

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSDeviceRGBColorSpace,
    NSGraphicsContext,
    NSImage,
)
from Foundation import NSMakeRect, NSMakeSize

# Straight from the favicon in agent/ui/index.html — 6pt keys on an 8pt
# pitch, 1pt margin, 1.5pt corner radius, in a 24x32 box.
VIEW_W, VIEW_H = 24.0, 32.0
KEY, PITCH, MARGIN, RADIUS = 6.0, 8.0, 1.0, 1.5
COLS, ROWS = 3, 4
ROW_HEX = ["#3E8E9B", "#7C9A72", "#C4703F", "#4A4A4A"]

# PNG. The named constant moved around between pyobjc releases; the raw
# value has been stable since forever.
_PNG = 4


def _color(hex_str: str):
    r, g, b = (int(hex_str[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    return NSColor.colorWithDeviceRed_green_blue_alpha_(r, g, b, 1.0)


def _paint(width: float, height: float, colors, inset: float):
    """Draw the grid into whatever graphics context is current.

    `inset` is the fraction of the smaller dimension left as padding — app
    icons want breathing room inside their tile, the menu bar does not.
    """
    usable_h = height * (1.0 - 2.0 * inset)
    usable_w = width * (1.0 - 2.0 * inset)
    scale = min(usable_w / VIEW_W, usable_h / VIEW_H)
    ox = (width - VIEW_W * scale) / 2.0
    oy = (height - VIEW_H * scale) / 2.0

    for row in range(ROWS):
        colors[row].set()
        for col in range(COLS):
            x = MARGIN + col * PITCH
            # SVG counts y downward, NSImage upward. Flip so row 0 — the
            # teal one — stays at the top where the favicon has it.
            y = VIEW_H - (MARGIN + row * PITCH) - KEY
            rect = NSMakeRect(
                ox + x * scale, oy + y * scale, KEY * scale, KEY * scale)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, RADIUS * scale, RADIUS * scale).fill()


def _bitmap(width: int, height: int, colors, inset: float):
    """Render at exact pixel dimensions.

    Going through an explicit NSBitmapImageRep rather than lockFocus on an
    NSImage, because iconutil wants precise sizes and lockFocus renders at
    whatever backing scale it feels like.
    """
    rep = NSBitmapImageRep.alloc().\
        initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, width, height, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    _paint(float(width), float(height), colors, inset)
    NSGraphicsContext.restoreGraphicsState()
    return rep


def png_bytes(size: int) -> bytes:
    """One square PNG of the full-colour mark, for the iconset."""
    rep = _bitmap(size, size, [_color(h) for h in ROW_HEX], inset=0.10)
    return bytes(rep.representationUsingType_properties_(_PNG, {}))


def status_image(height: float = 16.0):
    """The menu bar item's image.

    Flat black and marked as a template, which is how AppKit is told to
    recolour it for light and dark menu bars and for the highlighted state.
    Drawing it in the profile's colours would look right in exactly one of
    those and wrong in the rest.
    """
    width = height * (VIEW_W / VIEW_H)
    image = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()
    _paint(width, height, [NSColor.blackColor()] * ROWS, inset=0.0)
    image.unlockFocus()
    image.setTemplate_(True)
    return image
