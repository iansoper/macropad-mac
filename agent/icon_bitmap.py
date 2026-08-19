"""
icon_bitmap.py — packs an 8x8 sample of an app icon into the 8-byte,
1-bit-per-pixel bitmap the pad's OLED header understands.

Pure and AppKit-free on purpose, unlike app_icons.py: rendering an NSImage
down to 8x8 samples needs AppKit, but turning those samples into wire bytes
does not, and only the part that doesn't is worth testing off a Mac.
"""

ICON_SIZE = 8

# A sample counts as "ink" (bit=1, drawn as the header's black-on-white text
# color) when it is opaque enough to matter and dark enough to read as a mark
# rather than background. Transparent padding around a square icon must never
# set a bit, regardless of what color happens to be under the alpha=0 pixels.
ALPHA_THRESHOLD = 128
LUMA_THRESHOLD = 128


def pack(pixels) -> bytes:
    """pixels: 64 (luma, alpha) pairs, row-major, top-left first, 8x8.

    Returns 8 bytes, one per row, MSB = leftmost pixel — the shape the
    firmware's set_icon() in code.py unpacks with `(row >> (7 - x)) & 1`.
    """
    if len(pixels) != ICON_SIZE * ICON_SIZE:
        raise ValueError(f"expected {ICON_SIZE * ICON_SIZE} pixels, got {len(pixels)}")
    out = bytearray(ICON_SIZE)
    for y in range(ICON_SIZE):
        row = 0
        for x in range(ICON_SIZE):
            luma, alpha = pixels[y * ICON_SIZE + x]
            if alpha >= ALPHA_THRESHOLD and luma < LUMA_THRESHOLD:
                row |= 1 << (7 - x)
        out[y] = row
    return bytes(out)
