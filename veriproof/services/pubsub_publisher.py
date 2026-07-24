"""PubSubPublisher — payment-event queue ingress (architecture 2.1, 4).

The pay.sh webhook publishes a payment event here for at-least-once delivery
into the settlement Workflows pipeline. ``google-cloud-pubsub`` is
import-guarded.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PubSubPublisher:
    """Publishes messages to Pub/Sub topics."""

    def __init__(
        self,
        topic: str | None = None,
        project_id: str | None = None,
        client: Any = None,
    ) -> None:
        self.topic = topic
        self.project_id = project_id
        self._client = client

    # --- Architecture 4 method (SPEC-004) -----------------------------------
    def publish(self, topic: str, message: dict) -> str | None:
        """Publish ``message`` to ``topic``; return the message id.

        SPEC-004 R13. Returns ``None`` when Pub/Sub is unavailable (no project,
        no SDK) so the webhook can fall back to the synchronous settlement
        pipeline. A live publish returns the server-assigned message id.
        """
        client = self._get_client()
        if client is None:
            # Disabled / offline signal: callers (webhook) use this to switch
            # to the sync fallback instead of blocking.
            return None
        # Injected-client seam: publish(topic, message) returns a msg id.
        seam = getattr(client, "publish", None)
        if seam is not None and callable(seam):
            return seam(topic, message)
        # Real google-cloud-pubsub path (cloud only).
        try:  # pragma: no cover
            from google.cloud import pubsub_v1  # import-guarded  # pragma: no cover
        except ImportError:  # pragma: no cover
            logger.info("google-cloud-pubsub not installed; publish is a no-op")  # pragma: no cover
            return None  # pragma: no cover
        try:  # pragma: no cover
            path = client.topic_path(self.project_id, topic)  # pragma: no cover
            future = client.publish(path, json.dumps(message).encode("utf-8"))  # pragma: no cover
            return future.result()  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("pubsub publish failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover

    def _get_client(self) -> Any:
        """Return the injected client or a lazily-built real PublisherClient.

        Returns None when neither an injected client nor a configured project
        is available (offline / local TDD default).
        """
        if self._client is not None:
            return self._client
        if not self.project_id:
            return None
        try:  # pragma: no cover
            from google.cloud import pubsub_v1  # import-guarded  # pragma: no cover
        except ImportError:  # pragma: no cover
            return None  # pragma: no cover
        try:  # pragma: no cover
            return pubsub_v1.PublisherClient()  # pragma: no cover
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("pubsub client construction failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover


def get_pubsub_publisher() -> PubSubPublisher:
    """Factory: build a PubSubPublisher from current Django settings."""
    from django.conf import settings

    return PubSubPublisher(
        topic=getattr(settings, "PUBSUB_PAYMENTS_TOPIC", "veriproof-payments"),
        project_id=getattr(settings, "GCP_PROJECT_ID", "") or None,
    )
