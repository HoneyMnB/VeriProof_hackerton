"""SPEC-001 unit tests — StorageService (local backend, services layer).

Covers the TDD list:
- test_storage_saves_permanent_and_temporary (local backend)

Uses the REAL local backend against a tmp MEDIA_ROOT (no GCS, no network).
freezegun pins the clock so the temporary-asset expiry is asserted exactly.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest
from freezegun import freeze_time

from services.storage_service import StorageService


@pytest.fixture
def local_storage(tmp_path) -> StorageService:
    """A StorageService configured for the local backend at a tmp MEDIA_ROOT."""
    return StorageService(backend="local", media_root=tmp_path)


# --- save_permanent ----------------------------------------------------------


def test_storage_saves_permanent_writes_file_and_returns_url(local_storage, tmp_path):
    """Permanent artifacts (thumbnail/watermark) persist on disk; URL is /media."""
    url = local_storage.save_permanent("thumbnail", "asset-123", b"thumb-bytes")

    assert "thumbnail" in url
    assert "asset-123" in url
    assert url.startswith("/media/")
    # The file actually landed on disk somewhere under MEDIA_ROOT.
    files = list(Path(tmp_path).rglob("*"))
    contents = [p for p in files if p.is_file()]
    assert any(b"thumb-bytes" == p.read_bytes() for p in contents)


def test_storage_saves_permanent_separates_kinds(local_storage):
    """Thumbnail and watermark for the same asset do NOT collide."""
    u1 = local_storage.save_permanent("thumbnail", "aid", b"a")
    u2 = local_storage.save_permanent("watermark", "aid", b"b")
    assert u1 != u2


# --- save_temporary ----------------------------------------------------------


@freeze_time("2026-01-15T12:00:00Z")
def test_storage_saves_permanent_and_temporary_records_expiry(
    local_storage, tmp_path
):
    """Temporary original is stored + its expiry is recorded as now + ttl."""
    ttl = datetime.timedelta(days=7)

    with freeze_time("2026-01-15T12:00:00Z"):
        url = local_storage.save_temporary("asset-7", b"orig-bytes", ttl)

    assert url.startswith("/media/")
    assert "asset-7" in url

    # Expiry is recorded and equals the frozen "now" + ttl (R5).
    expiry = local_storage.get_temporary_expiry("asset-7")
    assert expiry == datetime.datetime(2026, 1, 22, 12, 0, 0, tzinfo=datetime.timezone.utc)


def test_storage_purge_original_removes_temporary(local_storage):
    """purge_original deletes the stored temporary original."""
    local_storage.save_temporary("asset-p", b"orig", datetime.timedelta(days=1))
    assert local_storage.has_temporary("asset-p") is True

    local_storage.purge_original("asset-p")

    assert local_storage.has_temporary("asset-p") is False


def test_storage_temporary_uses_image_extension(local_storage, tmp_path):
    """Image originals are stored with their image extension, not .bin."""
    url = local_storage.save_temporary(
        "asset-img",
        b"image-bytes",
        datetime.timedelta(days=1),
        "image/png",
    )

    assert url == "/media/temporary/asset-img.png"
    assert (tmp_path / "temporary" / "asset-img.png").read_bytes() == b"image-bytes"
    assert local_storage.read_temporary("asset-img") == b"image-bytes"


def test_storage_temporary_uses_pdf_extension(local_storage, tmp_path):
    """PDF originals are stored as .pdf, not .bin."""
    url = local_storage.save_temporary(
        "asset-pdf",
        b"%PDF-1.4",
        datetime.timedelta(days=1),
        "application/pdf",
    )

    assert url == "/media/temporary/asset-pdf.pdf"
    assert (tmp_path / "temporary" / "asset-pdf.pdf").read_bytes() == b"%PDF-1.4"
    assert local_storage.read_temporary("asset-pdf") == b"%PDF-1.4"


def test_storage_purge_original_removes_typed_temporary(local_storage):
    """Purging removes typed originals as well as legacy .bin originals."""
    local_storage.save_temporary(
        "asset-pdf", b"%PDF-1.4", datetime.timedelta(days=1), "application/pdf"
    )
    assert local_storage.has_temporary("asset-pdf") is True

    local_storage.purge_original("asset-pdf")

    assert local_storage.has_temporary("asset-pdf") is False


def test_storage_signed_download_url_returns_url_when_present(local_storage):
    """signed_download_url returns a URL for a present temporary; None otherwise."""
    assert local_storage.signed_download_url("nope", datetime.timedelta(hours=1)) is None
    local_storage.save_temporary("asset-s", b"orig", datetime.timedelta(days=1))
    url = local_storage.signed_download_url(
        "asset-s", datetime.timedelta(hours=1)
    )
    assert url is not None
    assert "asset-s" in url


def test_storage_gcs_backend_not_required_for_local_path():
    """Sanity: STORAGE_BACKEND local has no GCS client dependency (offline)."""
    # The constructor does not need a client; the local path works offline.
    svc = StorageService(backend="local", media_root="/tmp/whatever")
    assert svc.backend == "local"


# --- GCS backend (exercised via injected stub client; no real SDK) ----------


class _StubBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.uploaded: bytes | None = None

    def upload_from_string(self, data: bytes) -> None:
        self.uploaded = data

    def download_as_bytes(self) -> bytes:
        if self.uploaded is None:
            raise FileNotFoundError(self.name)
        return self.uploaded

    def delete(self) -> None:
        self.uploaded = None


class _StubBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, _StubBlob] = {}

    def blob(self, name: str) -> _StubBlob:
        return self.blobs.setdefault(name, _StubBlob(name))


class _StubGCSClient:
    def __init__(self) -> None:
        self.buckets: dict[str, _StubBucket] = {}

    def bucket(self, name: str) -> _StubBucket:
        return self.buckets.setdefault(name, _StubBucket())

    def list_blobs(self, bucket_name: str, prefix: str = "") -> list[_StubBlob]:
        bucket = self.bucket(bucket_name)
        return [
            blob
            for name, blob in bucket.blobs.items()
            if name.startswith(prefix) and blob.uploaded is not None
        ]


def test_storage_gcs_save_permanent_uses_injected_client():
    """backend=gcs with an injected client uploads and returns a GCS URL."""
    stub = _StubGCSClient()
    svc = StorageService(
        backend="gcs", gcs_bucket="veriproof-bucket", client=stub
    )
    url = svc.save_permanent("thumbnail", "asset-gcs", b"gcs-bytes")

    assert url == (
        "https://storage.googleapis.com/veriproof-bucket/"
        "permanent/thumbnail/asset-gcs.bin"
    )
    bucket = stub.buckets["veriproof-bucket"]
    assert bucket.blobs["permanent/thumbnail/asset-gcs.bin"].uploaded == b"gcs-bytes"


def test_storage_gcs_save_temporary_uses_injected_client():
    """backend=gcs temporary original uploads to GCS and records expiry."""
    stub = _StubGCSClient()
    svc = StorageService(
        backend="gcs", gcs_bucket="veriproof-bucket", client=stub
    )
    url = svc.save_temporary("asset-t", b"orig", datetime.timedelta(days=2))

    assert url == (
        "https://storage.googleapis.com/veriproof-bucket/"
        "temporary/asset-t.bin"
    )
    # Expiry still recorded.
    assert svc.get_temporary_expiry("asset-t") is not None


def test_storage_gcs_save_temporary_uses_pdf_extension():
    """GCS temporary originals use the upload MIME extension."""
    stub = _StubGCSClient()
    svc = StorageService(
        backend="gcs", gcs_bucket="veriproof-bucket", client=stub
    )
    url = svc.save_temporary(
        "asset-pdf", b"%PDF-1.4", datetime.timedelta(days=2), "application/pdf"
    )

    assert url == (
        "https://storage.googleapis.com/veriproof-bucket/"
        "temporary/asset-pdf.pdf"
    )
    bucket = stub.buckets["veriproof-bucket"]
    assert bucket.blobs["temporary/asset-pdf.pdf"].uploaded == b"%PDF-1.4"


def test_storage_gcs_save_temporary_fails_without_client(tmp_path, monkeypatch):
    """GCS 클라이언트가 없으면 로컬 URL을 꾸며내지 않고 실패한다."""
    svc = StorageService(backend="gcs", gcs_bucket="b", media_root=tmp_path)
    monkeypatch.setattr(svc, "_get_gcs_client", lambda: None)
    with pytest.raises(RuntimeError):
        svc.save_temporary("aid", b"orig", datetime.timedelta(days=1))


def test_storage_local_save_permanent_requires_media_root():
    """backend=local with no media_root -> RuntimeError (misconfiguration)."""
    svc = StorageService(backend="local", media_root=None)
    with pytest.raises(RuntimeError):
        svc.save_permanent("thumbnail", "aid", b"x")


def test_storage_gcs_public_download_url_when_original_exists():
    """Public GCS deployments return the canonical object URL without signing."""
    stub = _StubGCSClient()
    svc = StorageService(backend="gcs", gcs_bucket="b", client=stub)
    svc.save_temporary("aid", b"orig", datetime.timedelta(hours=1))

    assert svc.signed_download_url("aid", datetime.timedelta(hours=1)) == (
        "https://storage.googleapis.com/b/temporary/aid.bin"
    )


def test_storage_gcs_reads_checks_and_purges_temporary_original():
    stub = _StubGCSClient()
    svc = StorageService(backend="gcs", gcs_bucket="b", client=stub)
    svc.save_temporary(
        "asset-pdf",
        b"%PDF-1.4",
        datetime.timedelta(days=1),
        "application/pdf",
    )

    assert svc.has_temporary("asset-pdf") is True
    assert svc.read_temporary("asset-pdf") == b"%PDF-1.4"

    svc.purge_original("asset-pdf")

    assert svc.has_temporary("asset-pdf") is False
    assert svc.read_temporary("asset-pdf") is None


def test_storage_gcs_temporary_lookup_does_not_match_id_prefix():
    stub = _StubGCSClient()
    svc = StorageService(backend="gcs", gcs_bucket="b", client=stub)
    svc.save_temporary("asset-10", b"other", datetime.timedelta(days=1))

    assert svc.has_temporary("asset-1") is False


def test_storage_gcs_save_permanent_rejects_unknown_kind():
    """GCS permanent storage only accepts thumbnail/watermark kinds."""
    svc = StorageService(backend="gcs", gcs_bucket="b", client=_StubGCSClient())
    with pytest.raises(ValueError):
        svc.save_permanent("unknown-kind", "aid", b"x")


def test_storage_gcs_save_permanent_fails_without_client(tmp_path, monkeypatch):
    """GCS 클라이언트가 없으면 가짜 로컬 성공으로 대체하지 않는다."""
    svc = StorageService(backend="gcs", gcs_bucket="b", media_root=tmp_path)
    monkeypatch.setattr(svc, "_get_gcs_client", lambda: None)
    with pytest.raises(RuntimeError):
        svc.save_permanent("thumbnail", "aid", b"x")


def test_storage_unknown_backend_raises(tmp_path):
    """An unrecognized backend raises ValueError (no silent success)."""
    svc = StorageService(backend="s3", media_root=tmp_path)
    with pytest.raises(ValueError):
        svc.save_permanent("thumbnail", "aid", b"x")


# --- No-media-root paths (URLs still returned; expiry tracked in memory) ----


def test_storage_temporary_without_media_root_still_records_expiry():
    """A null media_root still returns a URL and records the expiry."""
    svc = StorageService(backend="local", media_root=None)
    url = svc.save_temporary("aid", b"orig", datetime.timedelta(days=3))
    assert url.startswith("/media/temporary/")
    assert svc.has_temporary("aid") is True  # tracked via expiry record
    assert svc.get_temporary_expiry("aid") is not None


def test_storage_purge_without_media_root_is_noop():
    """Purge with no media_root clears the in-memory record only."""
    svc = StorageService(backend="local", media_root=None)
    svc.save_temporary("aid", b"orig", datetime.timedelta(days=1))
    svc.purge_original("aid")
    assert svc.has_temporary("aid") is False


def test_storage_signed_url_none_when_no_temporary(local_storage):
    """signed_download_url returns None when nothing is stored."""
    assert (
        local_storage.signed_download_url(
            "absent", datetime.timedelta(hours=1)
        )
        is None
    )


# --- Factory -----------------------------------------------------------------


def test_storage_factory_builds_service():
    """get_storage_service() returns a StorageService wired from settings."""
    from services.storage_service import StorageService, get_storage_service

    svc = get_storage_service()
    assert isinstance(svc, StorageService)
