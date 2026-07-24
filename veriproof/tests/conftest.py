"""Shared pytest fixtures and configuration (test-plan 4).

Design:
- ``settings_local`` is autouse: forces the local-fallback environment
  (FIRESTORE off, local storage, AP2 off, mock sandbox) so the entire suite
  runs offline with zero external keys.
- DB access uses pytest-django's built-in ``db`` fixture / ``django_db`` mark.
- Fake adapters (test-plan 4) are exposed as fixtures, implementing the real
  service INTERFACES so any service can be swapped in tests.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from tests.fakes import (
    FakeBigQuery,
    FakeFirestore,
    FakeGeminiService,
    FakePubSub,
    FakeSolanaService,
    FakeStorageService,
)


@pytest.fixture(autouse=True)
def settings_local(settings):
    """Force local-fallback flags for every test (architecture 1.4 local mode).

    Individual tests can still override a specific flag via the ``settings``
    fixture after this autouse fixture runs.
    """
    settings.FIRESTORE_ENABLED = False
    settings.STORAGE_BACKEND = "local"
    settings.AP2_ENABLED = False


# --- Fake adapter fixtures (test-plan 4) ------------------------------------
# Each fake records calls and supports failure injection. Tests inject them
# into services via constructor (e.g. EventRecorder(firestore=fake_firestore)).


@pytest.fixture
def fake_gemini() -> FakeGeminiService:
    return FakeGeminiService()


@pytest.fixture
def fake_solana() -> FakeSolanaService:
    return FakeSolanaService()


@pytest.fixture
def fake_storage() -> FakeStorageService:
    return FakeStorageService()


@pytest.fixture
def fake_firestore() -> FakeFirestore:
    return FakeFirestore()


@pytest.fixture
def fake_bigquery() -> FakeBigQuery:
    return FakeBigQuery()


@pytest.fixture
def fake_pubsub() -> FakePubSub:
    return FakePubSub()


# --- Image-byte fixtures (real PNGs generated with Pillow) -------------------
# Used by SPEC-001 unit + integration tests. Each fixture returns raw bytes.


def _png_bytes(size: tuple[int, int], color=(255, 0, 0), mode: str = "RGB") -> bytes:
    """Encode a solid-color PNG of the given size/mode to bytes."""
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    """A small 64x64 red PNG (RGB)."""
    return _png_bytes((64, 64), color=(255, 0, 0), mode="RGB")


@pytest.fixture
def large_png_bytes() -> bytes:
    """A 1024x768 PNG used to verify thumbnail downscaling."""
    return _png_bytes((1024, 768), color=(10, 20, 30), mode="RGB")


@pytest.fixture
def rgba_png_bytes() -> bytes:
    """A 48x48 RGBA PNG with partial transparency (alpha-composite path)."""
    img = Image.new("RGBA", (48, 48), (255, 0, 0, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def corrupt_bytes() -> bytes:
    """Bytes that are NOT a decodable image (Pillow must reject)."""
    return b"not an image at all"


# --- Shared constants --------------------------------------------------------

VALID_WALLET = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
INVALID_WALLET = "not-a-valid-wallet!!"
