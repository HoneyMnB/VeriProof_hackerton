"""Sandbox app M2M API views: buyer-agent simulation runner (SPEC-006).

POST /api/v1/sandbox/run -> 200/202 simulation result | 404 (unknown asset).

Triggers ``SandboxRunner.run_simulation`` which replays the full agent flow
(``GET /ip/{id}`` -> 402 -> ``POST /negotiate`` -> ``POST /settle``) through the
REAL view functions, records ``AgentEvent``s, and pushes display docs to the
Firestore ``sandbox_feed`` collection (architecture 6.1 / SPEC-006 R1/R2/R3).
"""
from __future__ import annotations

import decimal
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.ip.models import IpAsset

from .services import get_sandbox_runner

logger = logging.getLogger(__name__)


@csrf_exempt
def run(request: HttpRequest) -> JsonResponse:
    """POST /api/v1/sandbox/run -> 200/202 | 404. SPEC-006 R1/R2.

    Request body: ``{asset_id, buyer_agent_id, offer_usdc, usage_type}``.
    The runner drives the real endpoints in order; the response carries the
    ``run_id`` the frontend polls ``/api/v1/events`` / subscribes to
    ``sandbox_feed`` with.
    """
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", status=405)

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _error("invalid_json", "request body must be valid JSON", status=422)
    if not isinstance(data, dict):
        return _error("invalid_json", "request body must be a JSON object", status=422)

    asset_id = (data.get("asset_id") or "").strip()
    if not asset_id:
        return _error("invalid_asset_id", "asset_id is required", status=422)

    # R1 / AC-1: 404 if the target asset does not exist.
    if not IpAsset.objects.filter(id=asset_id).exists():
        return _error("not_found", "asset not found", status=404)

    buyer_agent_id = (data.get("buyer_agent_id") or "").strip() or "sandbox-buyer"
    usage_type = data.get("usage_type") or "commercial"
    payment_tx_sig = (data.get("payment_tx_sig") or "").strip()
    buyer_wallet = (data.get("buyer_wallet") or "").strip()
    offer_usdc = _parse_money(data.get("offer_usdc"))
    if offer_usdc is None or offer_usdc <= 0:
        return _error(
            "invalid_offer", "offer_usdc must be a positive number", status=422
        )
    if not payment_tx_sig:
        return _error("invalid_payment_tx_sig", "payment_tx_sig is required", status=422)
    if not buyer_wallet:
        return _error("invalid_buyer_wallet", "buyer_wallet is required", status=422)

    runner = get_sandbox_runner()
    result = runner.run_simulation(
        asset_id=asset_id,
        buyer_agent_id=buyer_agent_id,
        initial_offer_usdc=offer_usdc,
        usage_type=usage_type,
        payment_tx_sig=payment_tx_sig,
        buyer_wallet=buyer_wallet,
    )

    return JsonResponse(
        {
            "run_id": result.run_id,
            "asset_id": result.asset_id,
            "status": result.status,
            "ok": result.ok,
            "session_id": result.session_id,
            "payment_tx_sig": result.payment_tx_sig,
            "certificate_tx": result.certificate_tx,
            "download_url": result.download_url,
            "error": result.error,
            "steps": result.steps,
        },
        # 200 carries both outcomes: the simulation ran; ``ok``/``status`` report
        # success vs a surfaced step failure (R9). The run itself succeeded.
        status=200,
    )


# === View helpers ===========================================================


def _error(code: str, detail: str, status: int = 400) -> JsonResponse:
    """Standard error envelope (architecture 8)."""
    return JsonResponse({"error": code, "detail": detail}, status=status)


def _parse_money(raw) -> decimal.Decimal | None:
    """Parse a positive Decimal from a JSON value; None on any failure."""
    if raw is None:
        return None
    try:
        value = decimal.Decimal(str(raw))
        return value if value.is_finite() else None
    except (decimal.InvalidOperation, ValueError, TypeError):
        return None
