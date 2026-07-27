"""Settlement app M2M API views: settle + pay.sh webhook + download.

SPEC-004 implements:
- POST /api/v1/ip/{asset_id}/settle -> 200 (download_url, cert_tx) | 400 | 404 | 422
- POST /api/v1/paysh/webhook -> 200 | 401 (signature mismatch)
- GET /files/{token} -> 200 (original) | 403 (expired/invalid) | 410 (purged)

Both ``/settle`` and the webhook sync fallback delegate to the SAME
``SettlementService.settle_pipeline`` SSOT (architecture 2.1 / 8) so there is
no logic duplication between the sync and async (Workflows) paths.
"""
from __future__ import annotations

import decimal
import hashlib
import hmac
import io
import json
import logging
from pathlib import Path
import zipfile

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.ip.models import IpAsset
from apps.negotiation.models import NegotiationSession
from apps.settlement.batch_services import BatchValidationError, get_batch_service
from apps.settlement.models import License
from services.gemini_service import GeminiResponseError, GeminiUnavailableError
from services.license_service import get_license_service
from services.pubsub_publisher import get_pubsub_publisher
from services.storage_service import extension_for_mime, get_storage_service

from .services import get_settlement_service

logger = logging.getLogger(__name__)


# === POST /api/v1/ip/{asset_id}/settle (SPEC-004) ===========================


@csrf_exempt
def settle(request: HttpRequest, asset_id) -> JsonResponse:
    """POST /api/v1/ip/{asset_id}/settle -> 200 SUCCESS | 400/404/422.

    Architecture 6.1/6.3 / R1..R16. Sync-fallback path: runs the
    ``SettlementService.settle_pipeline`` SSOT in-process (verify -> grant ->
    cert -> Firestore -> BigQuery -> royalty -> event).
    """
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", status=405)

    asset = IpAsset.objects.filter(id=asset_id).first()
    if asset is None:
        return _error("not_found", "asset not found", status=404)

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _error("invalid_json", "request body must be valid JSON", status=422)
    if not isinstance(data, dict):
        return _error("invalid_json", "request body must be a JSON object", status=422)

    tx_signature = (data.get("tx_signature") or "").strip()
    buyer_wallet = (data.get("buyer_wallet") or "").strip()
    session_id = data.get("session_id")

    # tx_signature is the payment proof — required for settlement.
    if not tx_signature:
        return _error(
            "invalid_tx_signature", "tx_signature is required", status=422
        )
    if not buyer_wallet:
        return _error(
            "invalid_buyer_wallet", "buyer_wallet is required", status=422
        )

    # Resolve the optional session (links the license, R8, + carries final price).
    session = _resolve_session(session_id, asset)

    svc = get_settlement_service()
    result = svc.settle_pipeline(
        asset=asset,
        session=session,
        tx_signature=tx_signature,
        buyer_wallet=buyer_wallet,
        usage_type=(session.usage_type if session is not None else None),
    )

    if not result.ok:
        # R3 / AC-2 / AC-3: invalid on-chain settlement.
        return _error("invalid_settlement", "payment verification failed", status=400)

    expires_at = result.download_expires_at
    return JsonResponse(
        {
            "status": "SUCCESS",
            "certificate_tx": result.certificate_tx,
            "download_url": result.download_url,
            "download_expires_at": (
                expires_at.isoformat() if expires_at is not None else None
            ),
        },
        status=200,
    )


# === POST /api/v1/paysh/webhook (SPEC-004 R12/R13/R17) ======================


@csrf_exempt
def paysh_webhook(request: HttpRequest) -> JsonResponse:
    """POST /api/v1/paysh/webhook -> 200 | 401 (signature mismatch).

    R12: verify ``X-PaySh-Signature`` HMAC against ``PAYSH_WEBHOOK_SECRET``.
    R13: on valid signature, publish the event to Pub/Sub and return 200
    immediately (non-blocking). When Pub/Sub is disabled (local), fall back to
    the synchronous ``settle_pipeline`` so the flow still completes offline.
    R17: idempotent on replayed tx_signature (the pipeline is idempotent).
    """
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", status=405)

    # R12 / AC-8: HMAC signature verification.
    if not _verify_paysh_signature(request):
        return _error("invalid_signature", "webhook signature mismatch", status=401)

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return _error("invalid_json", "request body must be valid JSON", status=422)
    if not isinstance(data, dict):
        return _error("invalid_json", "request body must be a JSON object", status=422)

    tx_signature = (data.get("tx_signature") or "").strip()
    buyer_wallet = (data.get("buyer_wallet") or "").strip()
    asset_id = data.get("asset_id")

    # Minimal field validation (R13): need a tx + an asset to settle.
    if not tx_signature:
        return _error("invalid_tx_signature", "tx_signature is required", status=422)
    if not asset_id:
        return _error("invalid_asset_id", "asset_id is required", status=422)

    # R13: publish to Pub/Sub (non-blocking). When Pub/Sub is disabled (local),
    # the publisher returns None and we fall back to the sync pipeline.
    publisher = get_pubsub_publisher()
    topic = getattr(settings, "PUBSUB_PAYMENTS_TOPIC", "veriproof-payments")
    msg_id = publisher.publish(topic, data)

    if msg_id is None:
        # Sync fallback (local / PubSub-disabled). R17 idempotency is handled
        # inside settle_pipeline (License.payment_tx_sig unique).
        _sync_fallback_settle(asset_id, data, tx_signature, buyer_wallet)

    return JsonResponse({"status": "accepted"}, status=200)


def _sync_fallback_settle(asset_id, data, tx_signature, buyer_wallet) -> None:
    """Local sync fallback: run the pipeline directly when Pub/Sub is off.

    R17: idempotent — re-replays of the same tx_signature hit the existing
    License and do not duplicate. Failures are logged (the webhook still 200s).
    """
    asset = IpAsset.objects.filter(id=asset_id).first()
    if asset is None:
        logger.info("webhook sync fallback: asset %s not found", asset_id)
        return

    session = _resolve_session(data.get("session_id"), asset)
    svc = get_settlement_service()
    try:
        svc.settle_pipeline(
            asset=asset,
            session=session,
            tx_signature=tx_signature,
            buyer_wallet=buyer_wallet,
            expected_amount=_parse_amount(data.get("amount_usdc")),
            usage_type=(session.usage_type if session is not None else None),
        )
    except Exception as exc:  # noqa: BLE001 (webhook must still 200)
        logger.warning("webhook sync fallback pipeline failed: %s", exc)


# === GET /files/{token} (SPEC-004 R9/R10/R11) ===============================


def download(request: HttpRequest, token: str) -> HttpResponse:
    """GET /files/{token} -> 200 original | 403 (expired/invalid) | 410 (purged).

    R9: valid + unexpired token -> serve the original.
    R10: expired / unknown token -> 403.
    R11: token valid but original purged -> 410 (Gone).

    Routed from apps.ip.urls_web (root-level ``/files/<token>``) since the
    architecture (6.5) exposes it without the ``/api/v1/`` prefix.
    """
    license = License.objects.filter(download_token=token).first()
    if license is None:
        # R10: unknown token (invalid).
        return _error("invalid_token", "download token is invalid", status=403)

    # R10: expiry check.
    from django.utils import timezone

    if not get_license_service().is_download_active(license):
        return _error("expired_token", "download token has expired", status=403)

    if license.buyer_user_id is not None:
        if not request.user.is_authenticated or request.user.id != license.buyer_user_id:
            return _error("license_not_owned", "download is not available for this account", status=403)

    # R11: original purged?
    asset = license.asset
    storage = get_storage_service()
    original = storage.read_temporary(asset.id)
    if asset.original_purged or original is None:
        return _error("purged", "original has been purged", status=410)

    gallery_images = list(asset.gallery_images.all())
    if not gallery_images:
        mime = _download_mime(asset.content_mime_type)
        response = HttpResponse(original, content_type=mime)
        response["Content-Disposition"] = (
            f'attachment; filename="{_download_file_name("original", mime)}"'
        )
        return response

    files = [
        (_download_file_name("original-1", _download_mime(asset.content_mime_type)), original)
    ]
    for image in gallery_images:
        image_bytes = storage.read_temporary(image.id)
        if image_bytes is None:
            return _error("purged", "an original work image has been purged", status=410)
        files.append((_safe_download_name(image.file_name, image.position + 1), image_bytes))

    # R9: one license delivers the entire multi-image work as a single archive.
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, image_bytes in files:
            bundle.writestr(name, image_bytes)
    response = HttpResponse(archive.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="work-images.zip"'
    return response


def _safe_download_name(file_name: str, position: int) -> str:
    """ZIP 항목이 경로 이탈 없이 원래 파일명을 최대한 보존하도록 정규화한다."""
    name = Path(file_name or "").name
    return name or f"image-{position}"


def _download_mime(content_mime_type: str | None) -> str:
    """저장된 원본 MIME이 있으면 그대로 쓰고, 없을 때만 기존 바이너리 응답으로 후퇴한다."""
    mime = (content_mime_type or "").split(";", 1)[0].strip().lower()
    return mime or "application/octet-stream"


def _download_file_name(stem: str, mime: str) -> str:
    """구매자가 받는 원본 파일명에 실제 이미지/PDF 확장자를 붙인다."""
    return f"{stem}{extension_for_mime(mime)}"


# === View helpers ===========================================================


def _resolve_session(session_id, asset):
    """Look up a NegotiationSession by id (must belong to the asset). None if absent."""
    if not session_id:
        return None
    try:
        return NegotiationSession.objects.filter(id=session_id, asset=asset).first()
    except (ValueError, TypeError):
        return None


def _parse_amount(raw) -> decimal.Decimal | None:
    """Parse an optional amount_usdc from the webhook payload."""
    if raw is None:
        return None
    try:
        value = decimal.Decimal(str(raw))
        return value if value.is_finite() else None
    except (decimal.InvalidOperation, ValueError, TypeError):
        return None


def _verify_paysh_signature(request: HttpRequest) -> bool:
    """R12: constant-time HMAC-SHA256 of the raw body vs PAYSH_WEBHOOK_SECRET.

    The signature header carries a hex digest. When no secret is configured the
    webhook is rejected (fail-closed) — a missing secret must NOT accept forged
    payloads.
    """
    secret = getattr(settings, "PAYSH_WEBHOOK_SECRET", "")
    if not secret:
        return False
    provided = request.headers.get("X-PaySh-Signature", "")
    if not provided:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), request.body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided)


def _error(code: str, detail: str, status: int = 400) -> JsonResponse:
    """Standard error envelope (architecture 8)."""
    return JsonResponse({"error": code, "detail": detail}, status=status)


# === POST /api/v1/ip/batch/* (결제 앱 소유) =================================


@csrf_exempt
def batch_negotiate(request: HttpRequest) -> JsonResponse:
    """대량 라이선스 견적 요청을 결제 유스케이스에 전달한다."""
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", 405)
    data, error = _json_object(request)
    if error is not None:
        return error
    items = data.get("items")
    if not isinstance(items, list):
        return _error("invalid_items", "items must be a list of asset ids", 422)
    try:
        order, item_rows = get_batch_service().quote_batch_order(
            buyer_agent_id=(data.get("buyer_agent_id") or "").strip(),
            asset_ids=items,
            usage_type=data.get("usage_type") or "commercial",
        )
    except BatchValidationError as exc:
        body = {"error": exc.code, "detail": "batch quote validation failed"}
        if exc.invalid_ids:
            body["invalid_ids"] = exc.invalid_ids
        return JsonResponse(body, status=422)
    except (GeminiUnavailableError, GeminiResponseError) as exc:
        logger.error("batch quote unavailable buyer_agent_id=%s error=%s", data.get("buyer_agent_id"), exc)
        return _error("batch_pricing_unavailable", "AI batch pricing could not be completed", 503)
    return JsonResponse(
        {
            "status": order.status,
            "order_id": str(order.id),
            "buyer_agent_id": order.buyer_agent_id,
            "total_usdc": str(order.total_usdc),
            "usage_type": data.get("usage_type") or "commercial",
            "items": [
                {
                    "item_id": str(item.id),
                    "asset_id": str(item.asset_id),
                    "unit_price_usdc": str(item.unit_price_usdc),
                }
                for item in item_rows
            ],
        }
    )


@csrf_exempt
def batch_settle(request: HttpRequest) -> JsonResponse:
    """검증된 하나의 체인 결제로 대량 라이선스를 정산한다."""
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", 405)
    data, error = _json_object(request)
    if error is not None:
        return error
    order_id = (data.get("order_id") or "").strip()
    tx_signature = (data.get("tx_signature") or "").strip()
    if not order_id:
        return _error("invalid_order_id", "order_id is required", 422)
    if not tx_signature:
        return _error("invalid_tx_signature", "tx_signature is required", 422)
    result = get_batch_service().settle_batch_order(
        order_id=order_id,
        tx_signature=tx_signature,
        buyer_wallet=(data.get("buyer_wallet") or "").strip() or None,
    )
    if not result.ok:
        status = {"invalid_settlement": 400, "not_found": 404, "already_settled": 409}.get(
            result.error or "", 400
        )
        return _error(result.error or "batch_error", "batch settle failed", status)
    body: dict[str, object] = {
        "status": result.status,
        "order_id": str(result.order.id) if result.order else order_id,
        "items": [
            {
                "item_id": item.item_id,
                "asset_id": item.asset_id,
                "license_id": item.license_id,
                "download_token": item.download_token,
                "download_url": item.download_url,
            }
            for item in result.successes
        ],
    }
    if result.failures:
        body["failures"] = [
            {"item_id": item.item_id, "asset_id": item.asset_id, "error": item.error, "retry": item.retry}
            for item in result.failures
        ]
    return JsonResponse(body)


def _json_object(request: HttpRequest) -> tuple[dict, JsonResponse | None]:
    """결제 API가 공유하는 JSON 객체 파싱 경계다."""
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return {}, _error("invalid_json", "request body must be valid JSON", 422)
    if not isinstance(data, dict):
        return {}, _error("invalid_json", "request body must be a JSON object", 422)
    return data, None
