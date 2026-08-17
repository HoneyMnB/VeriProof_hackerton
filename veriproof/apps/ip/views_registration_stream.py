"""Owner-scoped SSE feed for an in-flight work registration.

``POST /api/v1/ip/register`` runs the whole pipeline (hash → AI → Solana anchor →
certificate → storage → asset row) inside one request, so the browser cannot see
progress from the response alone. This view streams the ``AgentEvent`` rows the
pipeline records (PostgreSQL, no Firestore dependency) to the signed-in owner as
they are written, so the workspace can visualise each stage live.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import HttpRequest, StreamingHttpResponse
from django.views.decorators.http import require_GET

from apps.common.models import AgentEvent

REGISTRATION_STAGE_TYPES = (
    "REGISTRATION_STARTED",
    "CONTENT_HASHED",
    "AI_ANALYZED",
    "ANCHORING_STARTED",
    "ANCHORED",
    "REGISTRATION_CERTIFICATE_ISSUED",
    "CONTENT_STORED",
    "ASSET_REGISTERED",
    "REGISTRATION_FAILED",
)
# Only owner-visible, non-sensitive payload keys are streamed to the browser.
SAFE_PAYLOAD_KEYS = {
    "title", "status", "reason", "network", "category", "tag_count",
    "originality_score", "content_sha256", "anchor_tx_sig",
    "registration_certificate_tx_sig", "gallery_count", "retention_days",
}
POLL_SECONDS = 0.35
KEEPALIVE_SECONDS = 15
MAX_STREAM_SECONDS = 600


@login_required
@require_GET
def stream(request: HttpRequest) -> StreamingHttpResponse:
    """Stream registration stage events recorded for the signed-in owner after connect."""
    user_id = request.user.pk
    cursor = _latest_event_id()
    response = StreamingHttpResponse(
        registration_stream(lambda after: _events_after(user_id, after), cursor),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


async def registration_stream(
    fetch_after: Callable[[int], list[dict[str, Any]]],
    cursor: int,
    poll_seconds: float = POLL_SECONDS,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
    max_seconds: float = MAX_STREAM_SECONDS,
):
    """Yield SSE frames: ``ready`` once, then one ``stage`` frame per new event.

    The client waits for ``ready`` before submitting the registration so the
    cursor is guaranteed to precede the flow's first event.
    """
    yield _sse("ready", {"cursor": cursor})
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_frame = started
    fetch = sync_to_async(fetch_after, thread_sensitive=True)
    while loop.time() - started < max_seconds:
        items = await fetch(cursor)
        for item in items:
            cursor = item["cursor"]
            last_frame = loop.time()
            yield _sse("stage", item, event_id=str(cursor))
        if not items and loop.time() - last_frame >= keepalive_seconds:
            last_frame = loop.time()
            yield ": keep-alive\n\n"
        await asyncio.sleep(poll_seconds)
    yield _sse("closed", {"reason": "timeout"})


def _latest_event_id() -> int:
    """Cursor for "events created after this connection was opened"."""
    return AgentEvent.objects.aggregate(latest=Max("id"))["latest"] or 0


def _events_after(user_id: Any, after: int) -> list[dict[str, Any]]:
    """Registration stage events owned by ``user_id`` with id greater than ``after``."""
    rows = (
        AgentEvent.objects.filter(
            account_owner_id=user_id,
            type__in=REGISTRATION_STAGE_TYPES,
            id__gt=after,
        )
        .order_by("id")
        .values("id", "type", "correlation_id", "asset_id", "payload", "created_at")[:50]
    )
    return [_serialize(row) for row in rows]


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    return {
        "cursor": row["id"],
        "type": row["type"],
        "correlation_id": str(row["correlation_id"]) if row["correlation_id"] else None,
        "asset_id": str(row["asset_id"]) if row["asset_id"] else None,
        "timestamp": row["created_at"].isoformat(),
        "payload": {key: payload[key] for key in SAFE_PAYLOAD_KEYS if key in payload},
    }


def _sse(event: str, data: Any, event_id: str = "") -> str:
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
