"""Negotiation app M2M API view: autonomous negotiation round (SPEC-003).

POST /api/v1/ip/{asset_id}/negotiate -> 200 SOL NegotiateResponse | 404/409/422.

Orchestration decision:
    The view delegates the round to ``NegotiationEngine.run_round``. Gemini의
    구조화 응답에 세션 한도·창작자 가격 하한·수취 지갑 불변식을 적용한다.
    모델이 없거나 실패하면 계약 조건을 추정하지 않고 503으로 종료한다.

The view is responsible for:
- Request validation (R11 offer > 0 numeric; R12 usage_type allowlist; R13 404).
- Session create/resume keyed by (asset, buyer_agent_id) (R1).
- Appending the round to ``session.rounds`` (R6).
- Finalising the session on ACCEPT (R7) and storing the AP2 Cart Mandate (R14).
- Recording the OFFER/COUNTER/ACCEPT AgentEvent (R6 / AC-9).
- Projecting the §6.2 response contract (``session_id`` added here).
"""
from __future__ import annotations

import decimal
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.ip.models import IpAsset
from apps.negotiation.models import NegotiationSession
from services.event_recorder import get_event_recorder
from services.negotiation_engine import (
    NegotiationUnavailableError,
    get_negotiation_engine,
)

logger = logging.getLogger(__name__)

# R12: allowed usage_type values (architecture 5.1 NegotiationSession).
ALLOWED_USAGE_TYPES = frozenset({"commercial", "non-commercial", "editorial"})

# R6: map a round's NegotiationResult.status to the AgentEvent type. REJECT has
# no dedicated event in {OFFER, COUNTER, ACCEPT}; record it as OFFER (the buyer
# offer that was logged and rejected).
_ROUND_EVENT_TYPE = {
    "ACCEPT": "ACCEPT",
    "COUNTER_OFFER": "COUNTER",
    "REJECT": "OFFER",
}


@csrf_exempt
def negotiate(request: HttpRequest, asset_id) -> JsonResponse:
    """POST /api/v1/ip/{asset_id}/negotiate. SPEC-003 R1..R14."""
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", status=405)

    # R13 / AC: 404 if the asset does not exist.
    asset = IpAsset.objects.filter(id=asset_id).first()
    if asset is None:
        return _error("not_found", "asset not found", status=404)

    # Parse the JSON body once.
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _error("invalid_json", "request body must be valid JSON", status=422)
    if not isinstance(data, dict):
        return _error("invalid_json", "request body must be a JSON object", status=422)

    buyer_agent_id = (data.get("buyer_agent_id") or "").strip()
    offer_sol = _parse_money(data.get("offer_sol"))
    usage_type = data.get("usage_type")

    if offer_sol is None or offer_sol <= 0:
        return _error(
            "invalid_offer", "offer_sol must be a positive number", status=422
        )
    if (
        asset.min_price_sol is None
        or asset.target_price_sol is None
        or asset.min_price_sol <= 0
        or asset.target_price_sol < asset.min_price_sol
    ):
        return _error(
            "sol_price_not_configured",
            "valid min_price_sol and target_price_sol are required for negotiation",
            status=409,
        )

    # R12 / AC-8: usage_type allowlist.
    if usage_type not in ALLOWED_USAGE_TYPES:
        return _error(
            "invalid_usage_type",
            f"usage_type must be one of {sorted(ALLOWED_USAGE_TYPES)}",
            status=422,
        )

    if not buyer_agent_id:
        return _error(
            "invalid_buyer_agent_id", "buyer_agent_id is required", status=422
        )

    # R1: create or resume the session keyed by (asset, buyer_agent_id). Each
    # buyer gets its own session thread per asset (edge note: separate sessions
    # per buyer).
    session, _created = NegotiationSession.objects.get_or_create(
        asset=asset,
        buyer_agent_id=buyer_agent_id,
        defaults={
            "usage_type": usage_type,
            "initial_offer_sol": offer_sol,
        },
    )
    if session.initial_offer_sol is None:
        session.initial_offer_sol = offer_sol

    # Gemini 결과에만 세션 불변식을 적용한다. 모델 오류는 503으로 경계 처리한다.
    engine = get_negotiation_engine()
    try:
        result = engine.run_round(asset, session, offer_sol, usage_type)
    except NegotiationUnavailableError as exc:
        logger.error(
            "negotiation unavailable buyer_agent_id=%s asset_id=%s error=%s",
            buyer_agent_id,
            asset.id,
            exc,
        )
        return _error("negotiation_unavailable", "AI negotiation could not be completed", 503)

    # R6: append the round to the session log.
    now = timezone.now().isoformat()
    rounds = list(session.rounds or [])
    rounds.append(
        {
            "offer_sol": str(offer_sol),
            "counter_sol": (
                str(result.price_sol) if result.price_sol is not None else None
            ),
            "status": result.status,
            "reason": result.reason,
            "ts": now,
        }
    )
    session.rounds = rounds

    # R7 / AC-3: ACCEPT finalises the session (engine already resolved
    # pay_address via the shared resolve_pay_to SSOT).
    if result.status == "ACCEPT":
        session.status = NegotiationSession.ACCEPTED
        session.final_price_sol = result.price_sol
        session.pay_address = result.pay_address

    session.save()

    # R6 / AC-9: record the round event for observability / fan-out.
    recorder = get_event_recorder()
    recorder.record(
        _ROUND_EVENT_TYPE.get(result.status, "OFFER"),
        {
            "asset_id": str(asset.id),
            "session_id": str(session.id),
            "offer_sol": str(offer_sol),
            "status": result.status,
            "price_sol": (
                str(result.price_sol) if result.price_sol is not None else None
            ),
            "reason": result.reason,
        },
        asset=asset,
        session=session,
    )

    # §6.2 response contract. Money is serialised as a string (Decimal-safe);
    # session_id is projected here (not part of NegotiationResult).
    return JsonResponse(
        {
            "status": result.status,
            "price_sol": (
                str(result.price_sol) if result.price_sol is not None else None
            ),
            "reason": result.reason,
            "pay_address": result.pay_address,
            "session_id": str(session.id),
        },
        status=200,
    )


# === SPEC-003 view helpers ===================================================


def _error(code: str, detail: str, status: int = 400) -> JsonResponse:
    """Standard error envelope (architecture §8)."""
    return JsonResponse({"error": code, "detail": detail}, status=status)


def _parse_money(raw) -> decimal.Decimal | None:
    """Parse a Decimal from a JSON value; None on any failure.

    Accepts both JSON numbers (int/float) and numeric strings. Returns None for
    ``None``, non-numeric strings, or any parse error so the caller can map it
    to a 422 (R11).
    """
    if raw is None:
        return None
    try:
        value = decimal.Decimal(str(raw))
        return value if value.is_finite() else None
    except (decimal.InvalidOperation, ValueError, TypeError):
        return None
