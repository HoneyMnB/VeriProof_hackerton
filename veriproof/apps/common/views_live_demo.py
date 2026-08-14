"""Authenticated, read-only visualization of real Firestore A2A events."""
from __future__ import annotations

from collections import Counter

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.ip.models import IpAsset
from services.firestore_mirror import get_firestore_mirror

DISPLAY_TYPES = {
    "ANCHORED", "HTTP_402", "OFFER", "COUNTER", "ACCEPT", "REJECT",
    "PAYMENT_VERIFIED", "CERT_ISSUED", "ROYALTY_SPLIT", "BATCH_SETTLED",
}
SAFE_PAYLOAD_KEYS = {
    "status", "price_sol", "price_usdc", "offer_sol", "offer_usdc",
    "counter_sol", "counter_usdc", "usage_type", "reason", "round", "network",
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
    mirror = get_firestore_mirror()
    if not mirror.enabled:
        return JsonResponse({"connected": False, "reason": "disabled", "items": [], "metrics": {}})
    if not mirror.is_available():
        return JsonResponse({"connected": False, "reason": "unavailable", "items": [], "metrics": {}}, status=503)

    owned_assets = {
        str(asset.id): asset.title or "Untitled work"
        for asset in IpAsset.objects.filter(account_owner=request.user).only("id", "title")
    }
    documents = mirror.recent("events", limit=200)
    items = []
    for item in reversed(documents):
        event_type = str(item.get("type") or "").upper()
        asset_id = str(item.get("asset_id") or "")
        if event_type not in DISPLAY_TYPES or asset_id not in owned_assets:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        items.append({
            "event_id": str(item.get("event_id") or ""),
            "type": event_type,
            "asset_id": asset_id,
            "asset_title": owned_assets[asset_id],
            "timestamp": item.get("created_at"),
            "payload": {key: payload[key] for key in SAFE_PAYLOAD_KEYS if key in payload},
        })
    counts = Counter(item["type"] for item in items)
    metrics = {
        "events": len(items),
        "assets": len({item["asset_id"] for item in items}),
        "negotiations": sum(counts[name] for name in ("OFFER", "COUNTER", "ACCEPT", "REJECT")),
        "settlements": counts["PAYMENT_VERIFIED"],
    }
    return JsonResponse({"connected": True, "source": "firestore", "items": items, "metrics": metrics})
