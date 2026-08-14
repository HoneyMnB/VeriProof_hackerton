"""FirestoreMirror — real-time status mirror (architecture 4, 5.2).

Active when ``FIRESTORE_ENABLED=true``; otherwise a no-op so core flows run
offline. ``google-cloud-firestore`` is import-guarded.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class FirestoreMirror:
    """Writes real-time status documents (asset_status / sandbox_feed)."""

    def __init__(
        self,
        enabled: bool | None = None,
        database: str | None = None,
        client: Any = None,
    ) -> None:
        self.enabled = enabled
        self.database = database
        self._client = client

    # --- Architecture 4 method (SPEC-004) -----------------------------------
    def set(self, collection: str, doc_id: str, data: dict) -> None:
        """Upsert ``data`` at ``collection/doc_id``. No-op when disabled.

        SPEC-004/006 R14. ``asset_status`` carries the per-session display
        mirror (UNPAID/NEGOTIATING/LICENSED), distinct from IpAsset.status.
        """
        if not self.enabled:
            return None  # graceful no-op (offline / local TDD)
        client = self._get_client()
        if client is None:
            # Enabled but no SDK / no client: degrade silently rather than
            # abort the settlement pipeline (architecture §8 GCP-unavailable).
            logger.info("firestore client unavailable; set() is a no-op")
            return None
        # Injected-client seam or real google-cloud-firestore path.
        try:
            client.collection(collection).document(str(doc_id)).set(data)
        except Exception as exc:  # noqa: BLE001 (mirror failure must not abort)
            logger.warning("firestore set(%s/%s) failed: %s", collection, doc_id, exc)
        return None

    def recent(self, collection: str, *, limit: int = 80) -> list[dict]:
        """Read recent mirror documents without inventing an offline fallback."""
        if not self.enabled:
            return []
        client = self._get_client()
        if client is None:
            return []
        try:
            documents = []
            for snapshot in client.collection(collection).stream():
                item = snapshot.to_dict() or {}
                item["event_id"] = snapshot.id
                documents.append(item)
            documents.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            return documents[: max(1, min(limit, 200))]
        except Exception as exc:  # noqa: BLE001
            logger.warning("firestore recent(%s) failed: %s", collection, exc)
            return []

    def watch_recent(self, collection: str, callback: Any, *, limit: int = 200) -> Any:
        """Subscribe to recent Firestore documents and return the watch handle."""
        if not self.enabled:
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            query = client.collection(collection).order_by(
                "created_at", direction="DESCENDING"
            ).limit(max(1, min(limit, 200)))

            def on_snapshot(snapshots, changes, read_time):
                items = []
                for change in changes:
                    snapshot = change.document
                    item = snapshot.to_dict() or {}
                    item["event_id"] = snapshot.id
                    item["change_type"] = str(getattr(change.type, "name", change.type)).lower()
                    items.append(item)
                if items:
                    callback(items)

            return query.on_snapshot(on_snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning("firestore watch_recent(%s) failed: %s", collection, exc)
            return None

    def is_available(self) -> bool:
        """Return whether an enabled mirror has a usable server client."""
        return bool(self.enabled and self._get_client() is not None)

    def _get_client(self) -> Any:
        """주입된 클라이언트 또는 지연 생성한 firestore.Client를 반환한다. SDK가 없으면 None."""
        if self._client is not None:
            return self._client
        try:  # pragma: no cover
            from google.cloud import firestore  # import-guarded  # pragma: no cover
        except ImportError:  # pragma: no cover
            return None  # pragma: no cover
        try:  # pragma: no cover
            return firestore.Client(database=self.database)  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("firestore client construction failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover


def get_firestore_mirror() -> FirestoreMirror:
    """Factory: build a FirestoreMirror from current Django settings."""
    from django.conf import settings

    return FirestoreMirror(
        enabled=getattr(settings, "FIRESTORE_ENABLED", False),
        database=getattr(settings, "FIRESTORE_DATABASE", "(default)"),
    )
