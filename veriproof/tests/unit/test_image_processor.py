"""SPEC-001 unit tests — ImageProcessor (services layer).

Covers the TDD list:
- test_image_processor_sha256_is_deterministic
- test_image_processor_thumbnail_resizes_within_bounds
- test_image_processor_watermark_differs_from_original

Plus edge cases from SPEC-001 §6 (corrupt image, alpha-channel handling).
"""
from __future__ import annotations

import hashlib

import pytest
from PIL import Image

from services.image_processor import ImageProcessor


# --- sha256 ------------------------------------------------------------------


def test_image_processor_sha256_is_deterministic(png_bytes):
    """Same bytes -> same 64-hex SHA-256; different bytes -> different hash."""
    proc = ImageProcessor()
    h1 = proc.sha256(png_bytes)
    h2 = proc.sha256(png_bytes)

    # Matches hashlib reference (R2 contract: hex64 of original bytes).
    assert h1 == hashlib.sha256(png_bytes).hexdigest()
    assert len(h1) == 64
    assert h1 == h2  # deterministic

    other = proc.sha256(b"different bytes")
    assert other != h1
    assert len(other) == 64


def test_image_processor_sha256_empty_bytes():
    """Edge: empty input still yields the well-known empty SHA-256."""
    proc = ImageProcessor()
    assert proc.sha256(b"") == hashlib.sha256(b"").hexdigest()


# --- make_thumbnail ----------------------------------------------------------


def test_image_processor_thumbnail_resizes_within_bounds(large_png_bytes):
    """Thumbnail max side MUST be <= 512 (SPEC-001 R5 / EARS test list)."""
    proc = ImageProcessor()
    thumb_bytes = proc.make_thumbnail(large_png_bytes, (512, 512))

    assert isinstance(thumb_bytes, bytes)
    assert len(thumb_bytes) > 0

    img = Image.open(__import__("io").BytesIO(thumb_bytes))
    w, h = img.size
    assert max(w, h) <= 512
    # A 1024x768 input must be downscaled (not returned untouched).
    assert max(w, h) <= 512 and max(w, h) > 0


def test_image_processor_thumbnail_preserves_aspect_ratio(large_png_bytes):
    """Thumbnail keeps the original aspect ratio (1024:768 ≈ 4:3)."""
    proc = ImageProcessor()
    thumb = Image.open(
        __import__("io").BytesIO(proc.make_thumbnail(large_png_bytes, (512, 512)))
    )
    # 1024/768 = 4/3 = 1.333...; the thumbnail ratio must match within ~5%.
    orig_ratio = 1024 / 768
    thumb_ratio = thumb.width / thumb.height
    assert abs(orig_ratio - thumb_ratio) < 0.05


def test_image_processor_thumbnail_handles_rgba(rgba_png_bytes):
    """RGBA input is composited onto white and still yields a valid image."""
    proc = ImageProcessor()
    thumb_bytes = proc.make_thumbnail(rgba_png_bytes, (32, 32))
    # Pillow can re-open the result -> no alpha-handling crash.
    Image.open(__import__("io").BytesIO(thumb_bytes)).verify()


# --- make_watermark ----------------------------------------------------------


def test_image_processor_watermark_differs_from_original(png_bytes):
    """Watermark bytes MUST differ from the original (R15 preview contract)."""
    proc = ImageProcessor()
    wm_bytes = proc.make_watermark(png_bytes, "VeriProof")

    assert isinstance(wm_bytes, bytes)
    assert len(wm_bytes) > 0
    assert wm_bytes != png_bytes


def test_image_processor_watermark_is_decodable_png(png_bytes):
    """Watermark output is a valid decodable image."""
    proc = ImageProcessor()
    wm_bytes = proc.make_watermark(png_bytes, "(c) creator")
    Image.open(__import__("io").BytesIO(wm_bytes)).verify()


# --- corrupt input -----------------------------------------------------------


def test_image_processor_thumbnail_raises_on_corrupt(corrupt_bytes):
    """SPEC-001 §6: undecodable image -> exception (caller maps to 400)."""
    proc = ImageProcessor()
    with pytest.raises(Exception):
        proc.make_thumbnail(corrupt_bytes, (256, 256))


def test_image_processor_watermark_raises_on_corrupt(corrupt_bytes):
    proc = ImageProcessor()
    with pytest.raises(Exception):
        proc.make_watermark(corrupt_bytes, "x")
