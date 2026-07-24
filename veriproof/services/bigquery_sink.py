"""BigQuerySink — audit ledger sink (architecture 4, 5.3).

Active when ``BIGQUERY_DATASET`` is set; otherwise a no-op. Tables:
``transactions``, ``events``, ``royalties``. ``google-cloud-bigquery`` is
import-guarded.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class BigQuerySink:
    """Appends audit rows to BigQuery tables."""

    def __init__(
        self,
        dataset: str | None = None,
        project_id: str | None = None,
        client: Any = None,
    ) -> None:
        self.dataset = dataset
        self.project_id = project_id
        self._client = client

    # --- Architecture 4 method (SPEC-004) -----------------------------------
    def insert(self, table: str, row: dict) -> None:
        """Insert ``row`` into ``table``. No-op when no dataset configured.

        SPEC-004/008 R14. ``table`` in {transactions, events, royalties}.
        """
        if not self.dataset:
            return None  # graceful no-op (offline / local TDD)
        client = self._get_client()
        if client is None:
            # Dataset configured but SDK unavailable: degrade silently.
            logger.info("bigquery client unavailable; insert() is a no-op")
            return None
        # Injected-client seam or real google-cloud-bigquery path.
        try:
            client.insert_rows_json(f"{self.dataset}.{table}", [row])
        except Exception as exc:  # noqa: BLE001 (audit failure must not abort)
            logger.warning("bigquery insert(%s) failed: %s", table, exc)
        return None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:  # pragma: no cover
            from google.cloud import bigquery  # import-guarded  # pragma: no cover
        except ImportError:  # pragma: no cover
            return None  # pragma: no cover
        try:  # pragma: no cover
            return bigquery.Client(project=self.project_id)  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("bigquery client construction failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover


def get_bigquery_sink() -> BigQuerySink:
    """Factory: build a BigQuerySink from current Django settings."""
    from django.conf import settings

    return BigQuerySink(
        dataset=getattr(settings, "BIGQUERY_DATASET", "") or None,
        project_id=getattr(settings, "GCP_PROJECT_ID", "") or None,
    )
