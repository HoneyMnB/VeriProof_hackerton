"""Unit tests for image fingerprinting and original-image comparison."""
from __future__ import annotations

import io
import hashlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw, PngImagePlugin

from services.image_fingerprint import FingerprintService


def _structured_png(size: tuple[int, int] = (256, 192)) -> bytes:
    image = Image.new("RGB", size, (236, 238, 232))
    draw = ImageDraw.Draw(image)
    draw.rectangle((18, 24, 110, 132), fill=(210, 48, 52))
    draw.ellipse((130, 32, 222, 124), fill=(42, 116, 190))
    draw.line((0, size[1] - 12, size[0], 18), fill=(20, 20, 20), width=5)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _with_metadata(source: bytes) -> bytes:
    image = Image.open(io.BytesIO(source))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("comment", "metadata only")
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=metadata)
    return buf.getvalue()


def _resized_jpeg(source: bytes) -> bytes:
    image = Image.open(io.BytesIO(source)).resize(
        (128, 96), Image.Resampling.LANCZOS
    )
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_create_returns_proof_and_visual_hashes():
    service = FingerprintService()

    fingerprint = service.create(_structured_png())

    assert len(fingerprint.file_sha256) == 64
    assert len(fingerprint.canonical_sha256) == 64
    assert len(fingerprint.phash) == 16
    assert len(fingerprint.dhash) == 16
    assert len(fingerprint.whash) == 16
    assert len(fingerprint.tile_phash) == 9


def test_sha256_hashes_any_bytes_payload():
    service = FingerprintService()

    assert service.sha256(b"plain bytes") == hashlib.sha256(b"plain bytes").hexdigest()


def test_upload_manifest_hashes_ordered_supported_file_bytes():
    service = FingerprintService()
    pdf = SimpleUploadedFile("work.pdf", b"%PDF-1.4\nbody", content_type="application/pdf")
    archive = SimpleUploadedFile("source.zip", b"zip-bytes", content_type="application/zip")

    pdf_hash = hashlib.sha256(b"%PDF-1.4\nbody").hexdigest()
    archive_hash = hashlib.sha256(b"zip-bytes").hexdigest()
    expected = hashlib.sha256(f"{pdf_hash}\n{archive_hash}".encode("ascii")).hexdigest()

    assert service.upload_manifest_sha256((pdf, archive)) == expected
    assert pdf.read() == b"%PDF-1.4\nbody"
    assert archive.read() == b"zip-bytes"


def test_compare_images_exact_file():
    service = FingerprintService()
    source = _structured_png()

    result = service.compare_images(source, source)

    assert result.verdict == "exact_file"
    assert result.file_sha256_match is True
    assert result.is_match is True


def test_compare_images_same_pixels_ignores_metadata_only_changes():
    service = FingerprintService()
    source = _structured_png()

    result = service.compare_images(source, _with_metadata(source))

    assert result.verdict == "same_pixels"
    assert result.file_sha256_match is False
    assert result.canonical_sha256_match is True


def test_compare_images_detects_visual_similarity_after_resize_and_reencode():
    service = FingerprintService()
    source = _structured_png()

    result = service.compare_images(source, _resized_jpeg(source))

    assert result.verdict in {"same_pixels", "visually_similar"}
    assert result.file_sha256_match is False
    assert result.phash_distance <= 10


def test_compare_images_rejects_unrelated_image():
    service = FingerprintService()
    source = _structured_png()
    unrelated = _structured_png((256, 192))
    image = Image.open(io.BytesIO(unrelated)).transpose(Image.Transpose.ROTATE_180)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 256, 192), fill=(30, 210, 120))
    draw.polygon(((18, 18), (235, 70), (64, 175)), fill=(118, 30, 190))
    buf = io.BytesIO()
    image.save(buf, format="PNG")

    result = service.compare_images(source, buf.getvalue())

    assert result.verdict == "different"
    assert result.is_match is False


def test_create_rejects_corrupt_image(corrupt_bytes):
    service = FingerprintService()

    with pytest.raises(Exception):
        service.create(corrupt_bytes)
