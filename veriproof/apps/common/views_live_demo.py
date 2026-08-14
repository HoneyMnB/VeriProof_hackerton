"""Authenticated live visualization of creator-owned Firestore event flows."""
from __future__ import annotations

import json
import queue
from collections import Counter

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.ip.models import IpAsset
from services.firestore_mirror import get_firestore_mirror

REGISTRATION_TYPES = {
    "REGISTRATION_STARTED", "CONTENT_HASHED", "AI_ANALYZED",
    "ANCHORING_STARTED", "ANCHORED", "REGISTRATION_CERTIFICATE_ISSUED",
    "ASSET_REGISTERED", "REGISTRATION_FAILED",
}
COMMERCE_TYPES = {
    "ASSET_DISCOVERED", "HTTP_402", "OFFER", "COUNTER", "ACCEPT", "REJECT",
    "PAYMENT_SUBMITTED", "PAYMENT_VERIFIED", "PAYMENT_FAILED",
    "LICENSE_ISSUED", "CERT_ISSUED", "ROYALTY_SPLIT", "BATCH_SETTLED",
}
DISPLAY_TYPES = REGISTRATION_TYPES | COMMERCE_TYPES
SAFE_PAYLOAD_KEYS = {
    "status", "price_sol", "price_usdc", "offer_sol", "offer_usdc",
    "counter_sol", "counter_usdc", "usage_type", "reason", "round", "network",
    "title", "category", "tag_count", "originality_score", "payment_currency",
}


@login_required
@require_GET
def page(request):
    return render(request, "live_demo.html", {
        "firestore_enabled": bool(getattr(settings, "FIRESTORE_ENABLED", False)),
        "active_nav": "live-demo",
    })


@login_required
@require_GET
def feed(request):
    """Finite snapshot used for initial state and SSE fallback."""
    mirror = get_firestore_mirror()
    unavailable = _availability_response(mirror)
    if unavailable is not None:
        return unavailable
    return JsonResponse(_snapshot(request.user, mirror.recent("events", limit=200)))


@login_required
@require_GET
def stream(request):
    """Bridge Firestore's server listener to an authenticated browser SSE stream."""
    mirror = get_firestore_mirror()
    unavailable = _availability_response(mirror)
    if unavailable is not None:
        return unavailable

    owned_assets = _owned_assets(request.user)
    owner_user_id = str(request.user.pk)

    def event_stream():
        updates: queue.Queue[list[dict]] = queue.Queue(maxsize=32)

        def enqueue(items):
            try:
                updates.put_nowait(items)
            except queue.Full:
                try:
                    updates.get_nowait()
                except queue.Empty:
                    pass
                updates.put_nowait(items)

        yield _sse(
            "snapshot",
            _snapshot_from_owned(
                owned_assets,
                mirror.recent("events", limit=200),
                owner_user_id,
            ),
        )
        watch = mirror.watch_recent("events", enqueue, limit=200)
        if watch is None:
            yield _sse("offline", {"reason": "unavailable"})
            return
        try:
            while True:
                try:
                    documents = updates.get(timeout=20)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                items = _serialize(documents, owned_assets, owner_user_id)
                for item in items:
                    yield _sse("flow", item, event_id=item["event_id"])
        finally:
            watch.unsubscribe()

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


def _availability_response(mirror):
    if not mirror.enabled:
        return JsonResponse({"connected": False, "reason": "disabled", "items": [], "metrics": {}})
    if not mirror.is_available():
        return JsonResponse({"connected": False, "reason": "unavailable", "items": [], "metrics": {}}, status=503)
    return None


def _owned_assets(user):
    return {
        str(asset.id): asset.title or "Untitled work"
        for asset in IpAsset.objects.filter(account_owner=user).only("id", "title")
    }


def _snapshot(user, documents):
    return _snapshot_from_owned(_owned_assets(user), documents, str(user.pk))


def _snapshot_from_owned(owned_assets, documents, owner_user_id=""):
    items = _serialize(documents, owned_assets, owner_user_id)
    counts = Counter(item["type"] for item in items)
    return {
        "connected": True,
        "source": "firestore-sse",
        "items": items,
        "metrics": {
            "events": len(items),
            "registrations": len({item["correlation_id"] for item in items if item["flow"] == "registration"}),
            "negotiations": sum(counts[name] for name in ("OFFER", "COUNTER", "ACCEPT", "REJECT")),
            "settlements": counts["PAYMENT_VERIFIED"],
        },
    }


def _serialize(documents, owned_assets, owner_user_id=""):
    items = []
    for document in reversed(documents):
        event_type = str(document.get("type") or "").upper()
        asset_id = str(document.get("asset_id") or "")
        document_owner = str(document.get("owner_user_id") or "")
        if event_type not in DISPLAY_TYPES:
            continue
        if asset_id not in owned_assets and (not owner_user_id or document_owner != owner_user_id):
            continue
        payload = document.get("payload") if isinstance(document.get("payload"), dict) else {}
        correlation_id = str(document.get("correlation_id") or asset_id)
        items.append({
            "event_id": str(document.get("event_id") or ""),
            "type": event_type,
            "flow": "registration" if event_type in REGISTRATION_TYPES else "commerce",
            "correlation_id": correlation_id,
            "asset_id": asset_id,
            "asset_title": owned_assets.get(asset_id) or payload.get("title") or "Registering work",
            "timestamp": document.get("created_at"),
            "payload": {key: payload[key] for key in SAFE_PAYLOAD_KEYS if key in payload},
        })
    return items


def _sse(event, data, event_id=""):
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
