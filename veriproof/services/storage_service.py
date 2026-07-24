"""StorageService — hybrid image storage (GCS / local MEDIA_ROOT).

Architecture 4 contract. ``google-cloud-storage`` is import-guarded; with
``STORAGE_BACKEND=local`` (the default) it persists under MEDIA_ROOT and needs
no GCP credentials.
"""
from __future__ import annotations

import datetime
import logging
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Permanent artifacts (architecture 4 ``kind`` domain).
PERMANENT_KINDS = ("thumbnail", "watermark", "supporting")


class StorageService:
    """Stores thumbnail/watermark (permanent) + temporary originals.

    Constructor MUST NOT touch the filesystem/network at import time.
    """

    def __init__(
        self,
        backend: str | None = None,
        gcs_bucket: str | None = None,
        media_root: Any = None,
        client: Any = None,
    ) -> None:
        self.backend = backend
        self.gcs_bucket = gcs_bucket
        # In-memory record of temporary expiries: {asset_id: datetime}.
        self.media_root = Path(media_root) if media_root else None
        self._temp_expries: dict[Any, datetime.datetime] = {}
        self._client = client

    # --- Architecture 4 methods (SPEC-001 implements local backend) ---------

    def save_permanent(self, kind: str, asset_id: Any, data: bytes) -> str:
        """Persist a permanent artifact. ``kind`` in {thumbnail, watermark}.

        SPEC-001 R5. Returns a public/canonical ``/media/...`` URL.
        """
        if self.backend in (None, "local"):
            return self._save_permanent_local(kind, asset_id, data)
        if self.backend == "gcs":
            return self._save_permanent_gcs(kind, asset_id, data)
        raise ValueError(f"unknown STORAGE_BACKEND: {self.backend!r}")

    def save_temporary(
        self, asset_id: Any, data: bytes, ttl: datetime.timedelta
    ) -> str:
        """Persist the original temporarily for ``ttl``. SPEC-001/004.

        Records the expiry (now + ttl) so callers / schedulers can purge.
        """
        if self.backend == "gcs":
            url = self._save_temporary_gcs(asset_id, data)
        else:
            url = self._save_temporary(asset_id, data)
        # Record the expiry timestamp (UTC, timezone-aware) for the asset.
        now = datetime.datetime.now(datetime.UTC)
        self._temp_expries[asset_id] = now + ttl
        return url

    def purge_original(self, asset_id: Any) -> None:
        """Delete the temporary original (retention purge). SPEC-001/004."""
        self._purge_temporary(asset_id)
        self._temp_expries.pop(asset_id, None)

    def signed_download_url(
        self, asset_id: Any, ttl: datetime.timedelta
    ) -> str | None:
        """Return a time-limited signed download URL or None. SPEC-004.

        SPEC-001 only uses the truthiness (present vs absent); the actual
        signing scheme is wired in SPEC-004. The local path returns a URL
        when the temporary original is still present.
        """
        if not self.has_temporary(asset_id):
            return None
        if self.backend in (None, "local"):
            token = secrets.token_urlsafe(16)
            return f"/files/{token}?asset={asset_id}"
        return None

    def read_temporary(self, asset_id: Any) -> bytes | None:
        """Return the temporary original bytes, or None if purged/missing.

        SPEC-004: the ``/files/{token}`` download view streams the original via
        this seam. Returns None when the original has been purged (retention
        expiry) so the caller can return HTTP 410 Gone.
        """
        if self.media_root is None:
            # No filesystem configured: callers without a media_root cannot
            # serve bytes; signal absent.
            return None
        path = self._temporary_path(asset_id)
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:  # pragma: no cover (environment-specific)
            logger.warning("failed to read temporary %s: %s", asset_id, exc)
            return None

    def read_permanent(self, kind: str, asset_id: Any) -> bytes | None:
        """보호된 미리보기 바이트를 읽는다. 공개 URL은 이 메서드를 우회하지 않는다."""
        if kind not in PERMANENT_KINDS:
            raise ValueError(f"unknown permanent artifact kind: {kind!r}")
        if self.backend == "gcs":
            return self._read_permanent_gcs(kind, asset_id)
        if self.media_root is None:
            return None
        try:
            return (self.media_root / "permanent" / kind / f"{asset_id}.bin").read_bytes()
        except OSError:
            return None

    # --- Inspection helpers (SPEC-001 R5 expiry tracking) -------------------

    def get_temporary_expiry(self, asset_id: Any) -> datetime.datetime | None:
        """Return the recorded expiry (UTC) for a temporary original, or None."""
        return self._temp_expries.get(asset_id)

    def has_temporary(self, asset_id: Any) -> bool:
        """True when a temporary original is still on disk."""
        if self.media_root is None:
            return asset_id in self._temp_expries
        return self._temporary_path(asset_id).exists()

    # --- Local backend ------------------------------------------------------

    def _save_permanent_local(self, kind: str, asset_id: Any, data: bytes) -> str:
        root = self._require_media_root()
        path = root / "permanent" / kind / f"{asset_id}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"/media/permanent/{kind}/{asset_id}.bin"

    def _save_temporary(self, asset_id: Any, data: bytes) -> str:
        if self.media_root is None:
            # No filesystem configured: still record expiry for callers.
            return f"/media/temporary/{asset_id}.bin"
        path = self._temporary_path(asset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"/media/temporary/{asset_id}.bin"

    def _purge_temporary(self, asset_id: Any) -> None:
        if self.media_root is None:
            return
        path = self._temporary_path(asset_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover (environment-specific)
            logger.warning("failed to purge temporary %s: %s", asset_id, exc)

    def _temporary_path(self, asset_id: Any) -> Path:
        return self._require_media_root() / "temporary" / f"{asset_id}.bin"

    def _require_media_root(self) -> Path:
        if self.media_root is None:
            raise RuntimeError(
                "StorageService local backend requires a media_root"
            )
        return self.media_root

    # --- GCS backend (import-guarded stub for cloud runs) -------------------

    def _save_permanent_gcs(self, kind: str, asset_id: Any, data: bytes) -> str:
        if kind not in PERMANENT_KINDS:
            raise ValueError(f"unknown permanent kind: {kind!r}")
        client = self._get_gcs_client()
        if client is None:
            raise RuntimeError("GCS storage client is unavailable")
        bucket = client.bucket(self.gcs_bucket)
        blob_name = f"permanent/{kind}/{asset_id}.bin"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data)
        return f"https://storage.googleapis.com/{self.gcs_bucket}/{blob_name}"

    def _save_temporary_gcs(self, asset_id: Any, data: bytes) -> str:
        client = self._get_gcs_client()
        if client is None:
            raise RuntimeError("GCS storage client is unavailable")
        bucket = client.bucket(self.gcs_bucket)
        blob_name = f"temporary/{asset_id}.bin"
        bucket.blob(blob_name).upload_from_string(data)
        return f"https://storage.googleapis.com/{self.gcs_bucket}/{blob_name}"

    def _read_permanent_gcs(self, kind: str, asset_id: Any) -> bytes | None:
        """GCS 미리보기는 공개 버킷 URL 대신 애플리케이션 권한 경계로 읽는다."""
        client = self._get_gcs_client()
        if client is None:
            return None
        try:
            return client.bucket(self.gcs_bucket).blob(
                f"permanent/{kind}/{asset_id}.bin"
            ).download_as_bytes()
        except Exception as exc:  # noqa: BLE001 - cloud adapter errors are unavailable previews
            logger.warning("failed to read permanent preview kind=%s asset_id=%s error=%s", kind, asset_id, exc)
            return None

    def _get_gcs_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google.cloud import storage  # import-guarded
        except ImportError:
            return None
        # Real storage.Client() construction needs google-cloud-storage
        # installed (cloud only); excluded from the offline coverage gate.
        try:  # pragma: no cover
            return storage.Client()  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("GCS client construction failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover


def get_storage_service() -> StorageService:
    """Factory: build a StorageService from current Django settings."""
    from django.conf import settings

    return StorageService(
        backend=getattr(settings, "STORAGE_BACKEND", "local"),
        gcs_bucket=getattr(settings, "GCS_BUCKET", "") or None,
        media_root=getattr(settings, "MEDIA_ROOT", None),
    )
