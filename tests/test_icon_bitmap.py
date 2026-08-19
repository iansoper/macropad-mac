"""
Tests for the pad-icon bit-packing.

icon_bitmap.py is deliberately AppKit-free (see its module docstring) so the
threshold logic — the only part worth checking — can run off a Mac.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import icon_bitmap  # noqa: E402

OPAQUE = 255
TRANSPARENT = 0
DARK = 0
LIGHT = 255


def blank(luma=LIGHT, alpha=OPAQUE):
    return [(luma, alpha)] * 64


def test_all_light_packs_to_zero():
    assert icon_bitmap.pack(blank()) == bytes(8)


def test_all_dark_opaque_packs_to_all_ones():
    assert icon_bitmap.pack(blank(luma=DARK)) == bytes([0xFF] * 8)


def test_transparent_dark_pixel_is_not_ink():
    """Padding around a square icon is dark-under-alpha=0 in some renders —
    it must never show up as a bit just because it happens to be black."""
    pixels = blank(luma=DARK, alpha=TRANSPARENT)
    assert icon_bitmap.pack(pixels) == bytes(8)


def test_leftmost_pixel_is_msb():
    pixels = blank()
    pixels[0] = (DARK, OPAQUE)  # row 0, x=0
    packed = icon_bitmap.pack(pixels)
    assert packed[0] == 0b1000_0000
    assert packed[1:] == bytes(7)


def test_rightmost_pixel_is_lsb():
    pixels = blank()
    pixels[7] = (DARK, OPAQUE)  # row 0, x=7
    packed = icon_bitmap.pack(pixels)
    assert packed[0] == 0b0000_0001


def test_rows_are_independent():
    pixels = blank()
    pixels[1 * 8 + 3] = (DARK, OPAQUE)  # row 1, x=3
    packed = icon_bitmap.pack(pixels)
    assert packed[0] == 0
    assert packed[1] == 1 << (7 - 3)
    assert packed[2:] == bytes(6)


def test_threshold_is_inclusive_of_alpha_boundary():
    pixels = blank()
    pixels[0] = (DARK, icon_bitmap.ALPHA_THRESHOLD)
    assert icon_bitmap.pack(pixels)[0] == 0b1000_0000


def test_wrong_pixel_count_raises():
    try:
        icon_bitmap.pack(blank()[:-1])
    except ValueError as exc:
        assert "64" in str(exc)
    else:
        raise AssertionError("expected ValueError")
