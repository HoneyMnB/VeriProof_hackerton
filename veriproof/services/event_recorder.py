"""EventRecorder — cross-cutting event fan-out (architecture 4, 8).

On every state transition ``record()`` fans an AgentEvent out to:
PostgreSQL (AgentEvent row) + Firestore (real-time) + BigQuery (audit ledger).
"""
from __future__ import annotations

from typing import Any

from django.utils import timezone


class EventRecorder:
    """Persists an event to PostgreSQL and mirrors it to Firestore/BigQuery."""

    def __init__(
        self,
        firestore: Any = None,
        bigquery: Any = None,
    ) -> None:
        # FirestoreMirror / BigQuerySink (or fakes); None is allowed.
        self.firestore = firestore
        self.bigquery = bigquery

    # --- Architecture 4 method (SPEC-001 implements record) -----------------
    def record(
        self,
        type: str,
        payload: dict,
        asset: Any = None,
        session: Any = None,
    ) -> Any:
        """Create an AgentEvent and fan out to Firestore + BigQuery.

        Returns the persisted AgentEvent. SPEC-001/003/004/008.

        SPEC-001 only records the ``ANCHORED`` event (R7). Firestore and
        BigQuery fans-out is a graceful no-op when those sinks are disabled
        (the offline/local default).
        """
        from apps.common.models import AgentEvent

        event = AgentEvent.objects.create(
            type=type,
            payload=payload or {},
            asset=asset,
            session=session,
        )
        # Fan-out (architecture 8). Sinks no-op when disabled, so this is safe.
        if self.firestore is not None:
            try:
                self.firestore.set(
                    "events",
                    str(event.id),
                    {
                        "type": type,
                        "payload": payload or {},
                        "asset_id": str(asset.id) if asset is not None else None,
                        "session_id": str(session.id) if session is not None else None,
                        "created_at": timezone.now().isoformat(),
                    },
                )
            except Exception:  # noqa: BLE001 (mirror failure must not abort)
                pass
        if self.bigquery is not None:
            try:
                self.bigquery.insert(
                    "events",
                    {
                        "type": type,
                        "payload": payload or {},
                        "asset_id": str(asset.id) if asset is not None else None,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        return event


def get_event_recorder() -> EventRecorder:
    """Factory: build an EventRecorder with Firestore/BigQuery dependencies."""
    from .bigquery_sink import get_bigquery_sink
    from .firestore_mirror import get_firestore_mirror

    return EventRecorder(
        firestore=get_firestore_mirror(),
        bigquery=get_bigquery_sink(),
    )
