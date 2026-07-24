"""M2M REST API views for the IP app.

SPEC-001 implements ``POST /api/v1/ip/register`` here. The remaining views
stay as 501 stubs until their owning SPEC lands. The register view calls the
``services.get_*()`` factories for its four external dependencies so tests
can swap them via ``monkeypatch.setattr("apps.ip.views_api.get_*", ...)``.
"""
from __future__ import annotations

import datetime
import decimal
import json
import logging
import uuid

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.common.models import AgentEvent
from apps.ip.models import IpAsset
from apps.settlement.models import License
from services.catalog_service import get_catalog_service
from services.event_recorder import get_event_recorder
from services.preview_service import watermark_preview_url
from services.registration_draft_service import (
    DraftValidationError,
    get_registration_draft_service,
)
from services.registration_service import (
    RegistrationError,
    RegistrationMetadata,
    get_registration_service,
)
from services.subscription_service import SubscriptionRequiredError
from services.x402_service import get_x402_service

logger = logging.getLogger(__name__)

# SPEC-001 R8: allowed upload MIME types.
ALLOWED_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
ALLOWED_CONTENT_MIMES = {
    IpAsset.DOCUMENT: frozenset({"application/pdf", "text/plain"}),
    IpAsset.AUDIO: frozenset({"audio/mpeg", "audio/wav", "audio/x-wav"}),
    IpAsset.VIDEO: frozenset({"video/mp4", "video/webm"}),
    IpAsset.SOFTWARE: frozenset({"application/zip", "application/x-tar"}),
    IpAsset.PRODUCT: frozenset({"application/zip", "application/pdf"}),
    IpAsset.OTHER: frozenset({"application/pdf", "text/plain", "application/zip"}),
}



# === POST /api/v1/ip/register (SPEC-001) ====================================


@csrf_exempt
def register(request: HttpRequest) -> JsonResponse:
    """HTTP 입력을 검증한 뒤 등록 유스케이스에만 위임한다."""
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", status=405)
    if not request.user.is_authenticated:
        return _error("authentication_required", "sign in to register an IP asset", status=401)

    upload = request.FILES.get("image")
    if upload is None:
        return _error("missing_content", "a content file is required")
    metadata, error = _registration_metadata(request, upload)
    if error is not None:
        return error
    gallery_uploads = tuple(request.FILES.getlist("gallery_images"))
    gallery_error = _validate_gallery_uploads(metadata, gallery_uploads)
    if gallery_error is not None:
        return gallery_error
    draft_id = (request.POST.get("draft_id") or "").strip()
    if draft_id:
        try:
            confirmed = get_registration_draft_service().consume(
                metadata.creator_wallet,
                draft_id,
                request.POST.get("confirmation_token") or "",
                (upload, *gallery_uploads),
            )
            metadata = _metadata_from_draft(metadata.creator_wallet, confirmed.fields, upload)
        except DraftValidationError as exc:
            return _error("invalid_registration_confirmation", str(exc), status=422)
    gallery_error = _validate_gallery_uploads(metadata, gallery_uploads)
    if gallery_error is not None:
        return gallery_error
    try:
        supporting_uploads = tuple(request.FILES.getlist("supporting_files"))
        if any(item.size > settings.MAX_UPLOAD_BYTES for item in supporting_uploads):
            return _error("payload_too_large", "a supporting file exceeds the configured upload limit", 413)
        supported_component_mimes = set(ALLOWED_IMAGE_MIMES)
        for mime_set in ALLOWED_CONTENT_MIMES.values():
            supported_component_mimes.update(mime_set)
        if any(item.content_type not in supported_component_mimes for item in supporting_uploads):
            return _error("unsupported_media_type", "a supporting file type is not allowed", 415)
        outcome = get_registration_service().register(
            upload,
            metadata,
            supporting_uploads,
            gallery_uploads=gallery_uploads,
            account_owner=request.user,
        )
    except SubscriptionRequiredError as exc:
        logger.warning("registration subscription rejected creator_wallet=%s", metadata.creator_wallet)
        return _error("subscription_required", str(exc), status=402)
    except RegistrationError as exc:
        logger.warning(
            "registration rejected creator_wallet=%s code=%s",
            metadata.creator_wallet,
            exc.code,
        )
        return _error(exc.code, exc.detail, status=exc.status)

    asset, analysis = outcome.asset, outcome.analysis
    if draft_id:
        get_registration_draft_service().mark_executed(draft_id, asset)
    return JsonResponse(
        {
            "asset_id": str(asset.id),
            "asset_type": asset.asset_type,
            "visibility": asset.visibility,
            "anchor_tx": asset.anchor_tx_sig,
            "registration_certificate_tx": asset.registration_certificate_tx_sig,
            "analysis": {
                "tags": list(analysis.tags),
                "category": analysis.category,
                "originality_score": analysis.originality_score,
                "recommended_min_price_usdc": str(analysis.recommended_min_price_usdc),
                "description": analysis.description,
            },
            "hash_prefix": asset.image_sha256[:16],
            "x402_endpoint": f"/api/v1/ip/{asset.id}",
            "watermark_url": watermark_preview_url(asset.id),
        },
        status=201,
    )


def _validate_gallery_uploads(
    metadata: RegistrationMetadata, gallery_uploads: tuple
) -> JsonResponse | None:
    """추가 이미지는 이미지 작품에만, 같은 제한으로 허용한다."""
    if not gallery_uploads:
        return None
    if metadata.asset_type != IpAsset.IMAGE:
        return _error("invalid_gallery", "gallery images are only supported for image works", 422)
    if len(gallery_uploads) > settings.MAX_WORK_IMAGES - 1:
        return _error("too_many_gallery_images", "too many images for one work", 422)
    if any(item.content_type not in ALLOWED_IMAGE_MIMES for item in gallery_uploads):
        return _error("unsupported_media_type", "gallery images must be PNG, JPEG, or WebP", 415)
    if any(item.size > settings.MAX_UPLOAD_BYTES for item in gallery_uploads):
        return _error("payload_too_large", "a gallery image exceeds the configured upload limit", 413)
    return None


def _metadata_from_draft(wallet: str, fields: dict, upload) -> RegistrationMetadata:
    """확정 초안 값만 실제 등록 메타데이터로 변환해 클라이언트 재조립을 막는다."""
    asset_type = str(fields.get("asset_type") or "").strip().lower()
    allowed_mimes = ALLOWED_IMAGE_MIMES if asset_type == IpAsset.IMAGE else ALLOWED_CONTENT_MIMES.get(asset_type)
    if allowed_mimes is None or upload.content_type not in allowed_mimes:
        raise DraftValidationError("The attachment type does not match the confirmed work type.")
    min_price = _parse_money(fields.get("min_price"))
    target_price = _parse_money(fields.get("target_price"))
    visibility = str(fields.get("visibility") or "").strip().lower()
    if min_price is None or target_price is None or min_price < 0 or target_price < min_price or visibility not in {IpAsset.PRIVATE, IpAsset.PUBLIC}:
        raise DraftValidationError("The confirmed pricing or visibility is invalid.")
    return RegistrationMetadata(
        creator_wallet=wallet, asset_type=asset_type, visibility=visibility,
        min_price=min_price, target_price=target_price,
        title=str(fields.get("title") or "").strip(), description=str(fields.get("description") or "").strip() or None,
        tags=tuple(_parse_tags(str(fields.get("tags") or ""))),
    )


# === SPEC-001 helpers =======================================================


def _error(code: str, detail: str, status: int = 400) -> JsonResponse:
    """Standard error envelope (architecture §8)."""
    return JsonResponse({"error": code, "detail": detail}, status=status)


@csrf_exempt
def update_asset_terms(request: HttpRequest, asset_id: uuid.UUID) -> JsonResponse:
    """소유 창작자가 작품 메타데이터와 판매 조건을 함께 수정한다."""
    if request.method != "POST":
        return _error("method_not_allowed", "POST required", status=405)
    if not request.user.is_authenticated:
        return _error("authentication_required", "sign in to manage an IP asset", status=401)
    try:
        data = json.loads(request.body or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _error("invalid_json", "JSON body required", status=422)
    asset = IpAsset.objects.filter(id=asset_id, account_owner=request.user).first()
    if asset is None:
        logger.warning("asset terms rejected user=%s asset_id=%s", request.user.get_username(), asset_id)
        return _error("asset_not_found", "asset not found", status=404)
    minimum = _parse_money(data.get("min_price_usdc"))
    target = _parse_money(data.get("target_price_usdc"))
    visibility = str(data.get("visibility") or "").strip().lower()
    if minimum is None or target is None or minimum < 0 or target < minimum or visibility not in {IpAsset.PRIVATE, IpAsset.PUBLIC}:
        return _error("invalid_asset_terms", "invalid pricing or visibility", status=422)
    if visibility == IpAsset.PUBLIC and not asset.registration_certificate_tx_sig:
        logger.warning(
            "public listing rejected without registration certificate user=%s asset_id=%s",
            request.user.get_username(),
            asset_id,
        )
        return _error(
            "registration_certificate_required",
            "a registration certificate is required before public listing",
            status=409,
        )
    title_raw = data.get("title", asset.title or "")
    description_raw = data.get("description", asset.description or "")
    tags = data.get("tags", asset.tags or [])
    if not isinstance(title_raw, str) or not isinstance(description_raw, str):
        return _error("invalid_asset_metadata", "title and description must be text values", status=422)
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        return _error("invalid_asset_metadata", "tags must be a list of text values", status=422)
    title = title_raw.strip()
    description = description_raw.strip()
    tags = [tag.strip() for tag in tags if tag.strip()]
    if len(title) > 120:
        return _error("invalid_asset_metadata", "title must be 120 characters or fewer", status=422)
    asset.min_price_usdc = minimum
    asset.target_price_usdc = target
    asset.visibility = visibility
    asset.title = title or None
    asset.description = description or None
    asset.tags = tags
    asset.save(update_fields=[
        "min_price_usdc", "target_price_usdc", "visibility", "title", "description", "tags"
    ])
    logger.info("asset terms updated user=%s asset_id=%s", request.user.get_username(), asset_id)
    return JsonResponse({
        "asset_id": str(asset.id),
        "title": asset.title or "",
        "description": asset.description or "",
        "tags": asset.tags,
        "min_price_usdc": str(asset.min_price_usdc),
        "target_price_usdc": str(asset.target_price_usdc),
        "visibility": asset.visibility,
    })


def _is_valid_pubkey(value: str) -> bool:
    """SPEC-001 R10: validate a Solana base58 32-byte pubkey.

    ``solders.pubkey.Pubkey``으로 실제 32바이트 공개키를 검증한다. 검증기가
    없으면 불완전한 정규식으로 통과시키지 않고 요청을 거부한다. 이 검증에는
    RPC 주소·지갑 비밀키·API 키가 필요하지 않으며, 공개키 문자열만 검사한다.
    """
    if not value:
        return False
    try:
        # 로컬 데모의 mock 체인 어댑터와 무관하게, 입력 지갑 주소 자체는
        # 실제 Solana 공개키 규격으로 검증해 잘못된 주소 등록을 막는다.
        from solders.pubkey import Pubkey  # import-guarded
    except ImportError:
        logger.error("wallet validation unavailable: solders is not installed")
        return False
    try:
        Pubkey.from_string(value)
        return True
    except Exception:  # noqa: BLE001
        return False


def _parse_money(raw: str | None) -> decimal.Decimal | None:
    """Parse a decimal money string; return None on any failure."""
    if raw is None or raw == "":
        return None
    try:
        value = decimal.Decimal(str(raw))
        return value if value.is_finite() else None
    except (decimal.InvalidOperation, ValueError):
        return None


def _parse_int(raw: str | None) -> int | None:
    """문자열을 정수로 변환한다. 빈 값이거나 변환 실패 시 None을 반환한다."""
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _parse_tags(raw: str | None) -> list[str]:
    """Parse creator-entered discovery tags without inventing a taxonomy."""
    if not raw:
        return []
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def _registration_metadata(
    request: HttpRequest, upload
) -> tuple[RegistrationMetadata | None, JsonResponse | None]:
    """등록 입력만 검증하여 HTTP와 비즈니스 유스케이스의 경계를 유지한다."""
    asset_type = (request.POST.get("asset_type") or IpAsset.IMAGE).strip().lower()
    allowed_mimes = (
        ALLOWED_IMAGE_MIMES if asset_type == IpAsset.IMAGE else ALLOWED_CONTENT_MIMES.get(asset_type)
    )
    if allowed_mimes is None:
        return None, _error("invalid_asset_type", "unsupported asset_type")
    if upload.content_type not in allowed_mimes:
        return None, _error("unsupported_media_type", "content MIME type is not allowed", 415)
    if upload.size > settings.MAX_UPLOAD_BYTES:
        return None, _error("payload_too_large", "file exceeds the configured upload limit", 413)

    wallet = (request.POST.get("creator_wallet") or "").strip()
    if not _is_valid_pubkey(wallet):
        return None, _error("invalid_wallet", "creator_wallet is not a valid Solana pubkey")
    min_price = _parse_money(request.POST.get("min_price"))
    target_price = _parse_money(request.POST.get("target_price"))
    if min_price is None or min_price < 0:
        return None, _error("invalid_min_price", "min_price must be a non-negative number")
    if target_price is None or target_price < min_price:
        return None, _error("invalid_target_price", "target_price must be at least min_price")

    visibility_value = (request.POST.get("visibility") or "").strip().lower()
    share_requested = (request.POST.get("share") or "").strip().lower() == "true"
    visibility = IpAsset.PUBLIC if visibility_value == IpAsset.PUBLIC or share_requested else IpAsset.PRIVATE
    return (
        RegistrationMetadata(
            creator_wallet=wallet,
            asset_type=asset_type,
            visibility=visibility,
            min_price=min_price,
            target_price=target_price,
            title=(request.POST.get("title") or "").strip() or None,
            description=(request.POST.get("description") or "").strip() or None,
            parent_asset_id=(request.POST.get("parent_asset_id") or "").strip() or None,
            royalty_share_bps=_parse_int(request.POST.get("royalty_share_bps")),
            tags=tuple(_parse_tags(request.POST.get("tags"))),
            category=(request.POST.get("category") or "").strip() or None,
        ),
        None,
    )


# === GET /api/v1/ip/{asset_id} (SPEC-002 x402 interceptor) ==================


def get_asset(request: HttpRequest, asset_id: uuid.UUID) -> JsonResponse:
    """Access an IP asset with x402 payment-required interception. SPEC-002.

    Decision flow (R1..R10):
    1. R1: 404 if the asset does not exist.
    2. R2: if ``X-Solana-Tx-Sig`` is present and its persisted License exists,
       return only the real expiry-bound download URL.
    3. R3/R6: if NOT licensed and the client is an agent -> 402 with the
       a2a-x402 envelope from ``X402Service.build_payment_required`` and an
       ``HTTP_402`` AgentEvent is recorded (R9).
    4. R7: if NOT licensed and the client is a browser -> 200 Solana Pay
       Buy-It-Now fallback.

    Implemented as a view function scoped to this path (the SSOT §6 edge note
    constrains the interceptor to ``GET /api/v1/ip/{asset_id}`` only; register,
    negotiate and settle pass through untouched).
    """
    # R1 / AC-1: asset existence.
    asset = IpAsset.objects.filter(id=asset_id).first()
    if asset is None:
        return _error("not_found", "asset not found", status=404)

    # R2 / AC-3: licensed path — 실제 DB 라이선스와 토큰이 모두 있어야 한다.
    tx_sig = request.headers.get("X-Solana-Tx-Sig")
    license = License.objects.filter(asset=asset, payment_tx_sig=tx_sig).first() if tx_sig else None
    if license is not None:
        return _licensed_response(asset, license)

    # 비공개 자산은 소유자가 명시적으로 공유하기 전 외부 구매·에이전트 탐색
    # 경로에 존재하지 않는 것처럼 처리한다. UUID를 안다고 결제 조건을 얻을 수 없다.
    if (
        asset.visibility != IpAsset.PUBLIC
        or asset.status not in {IpAsset.ANCHORED, IpAsset.LISTED}
        or not asset.registration_certificate_tx_sig
    ):
        return _error("not_found", "asset not found", status=404)

    # Unlicensed — classify the client (R6/R7).
    x402 = get_x402_service()
    if x402.classify_client(request) == "agent":
        return _payment_required_response(request, asset, x402)

    # R7 / AC-4: browser fallback (200 Solana Pay).
    return JsonResponse(x402.build_solana_pay_fallback(asset), status=200)


def _licensed_response(asset: IpAsset, license: License) -> JsonResponse:
    """Build the 200 response for a licensed agent (R2).

    저장된 만료 토큰만 반환한다. 임시 또는 조립한 URL은 성공 응답에 넣지 않는다.
    """
    expires_at = license.download_expires_at
    download_url = (
        f"/files/{license.download_token}"
        if license.download_token and (expires_at is None or expires_at > timezone.now())
        else None
    )
    return JsonResponse(
        {
            "status": "LICENSED",
            "asset_id": str(asset.id),
            "download_url": download_url,
            "watermark_url": watermark_preview_url(asset.id),
        },
        status=200,
    )


def _payment_required_response(
    request: HttpRequest, asset: IpAsset, x402_service
) -> JsonResponse:
    """Build the 402 response (R3/R4/R5) and record the HTTP_402 event (R9)."""
    headers, body = x402_service.build_payment_required(asset)
    response = JsonResponse(body, status=402)
    for name, value in headers.items():
        response[name] = value

    # R9 / AC-6: record an HTTP_402 AgentEvent for observability / fan-out.
    buyer_hint = request.headers.get("X-Buyer-Agent-Id", "")
    recorder = get_event_recorder()
    recorder.record(
        "HTTP_402",
        {"asset_id": str(asset.id), "buyer_hint": buyer_hint},
        asset=asset,
    )
    return response


# === GET /api/v1/ip/{asset_id}/certificate/{cert_id} (SPEC-004) ==============


def get_certificate(
    request: HttpRequest, asset_id: uuid.UUID, cert_id: str
) -> JsonResponse:
    """GET certificate -> 200 (payload, no original bytes) | 404.

    SPEC-004. ``cert_id`` is the on-chain certificate Memo tx signature
    (``License.certificate_tx_sig``). The payload intentionally EXCLUDES the
    original bytes and the download token — it is an attestation record only.
    """
    license = License.objects.filter(
        asset_id=asset_id, certificate_tx_sig=cert_id
    ).first()
    if license is None:
        return _error("not_found", "certificate not found", status=404)

    return JsonResponse(
        {
            "asset_id": str(license.asset_id),
            "certificate_tx": license.certificate_tx_sig,
            "payment_tx_sig": license.payment_tx_sig,
            "buyer_wallet": license.buyer_wallet,
            "usage_type": license.usage_type,
            "price_usdc": str(license.price_usdc),
            "granted_at": (
                license.granted_at.isoformat() if license.granted_at else None
            ),
        },
        status=200,
    )


# === GET /api/v1/ip/{asset_id}/transactions (SPEC-005) =======================
# R9 / AC-8: merged License + AgentEvent timeline, time-ascending.


def transactions(
    request: HttpRequest, asset_id: uuid.UUID
) -> JsonResponse:
    """Return the merged transaction timeline for an asset. SPEC-005 R9/AC-8.

    Merges the asset's ``License`` rows (commercial deals) with its
    ``AgentEvent`` rows (negotiation/payment observability) into a single
    time-ascending timeline. Each entry carries a ``kind`` discriminator
    (``license`` | ``event``) plus ``timestamp`` (ISO-8601 UTC). The payload
    NEVER includes original bytes/urls (R8 edge).

    Returns 404 for an unknown ``asset_id`` (architecture 6.1).
    """
    asset = IpAsset.objects.filter(id=asset_id).first()
    if asset is None:
        return _error("not_found", "asset not found", status=404)

    items: list[dict] = []

    # License entries — commercial deal record.
    for lic in License.objects.filter(asset=asset).order_by("granted_at"):
        items.append(
            {
                "kind": "license",
                "timestamp": lic.granted_at.isoformat() if lic.granted_at else None,
                "license_id": str(lic.id),
                "buyer_wallet": lic.buyer_wallet,
                "price_usdc": str(lic.price_usdc),
                "usage_type": lic.usage_type,
                "payment_tx_sig": lic.payment_tx_sig,
                "certificate_tx_sig": lic.certificate_tx_sig,
            }
        )

    # AgentEvent entries — negotiation / payment observability fan-out.
    for ev in AgentEvent.objects.filter(asset=asset).order_by("created_at"):
        items.append(
            {
                "kind": "event",
                "timestamp": ev.created_at.isoformat() if ev.created_at else None,
                "type": ev.type,
                "payload": ev.payload or {},
            }
        )

    # Stable time-ascending order: Python's sort is stable, so same-timestamp
    # entries keep their insertion order (licenses before events at equal ts).
    items.sort(key=lambda it: it["timestamp"] or "")

    return JsonResponse({"asset_id": str(asset.id), "items": items}, status=200)


# === GET /api/v1/assets (SPEC-005) ===========================================
# R11: list assets for the authenticated account. R8 excludes original bytes/url.


def asset_list(request: HttpRequest) -> JsonResponse:
    """List the authenticated account's IpAssets. SPEC-005 R11.

    Creator wallets remain item metadata. Request parameters cannot narrow or
    switch the account library, which prevents a URL from selecting another
    account's private assets.
    """
    if not request.user.is_authenticated:
        return _error("authentication_required", "sign in to view a creator library", status=401)
    qs = IpAsset.objects.select_related("creator").filter(
        account_owner=request.user
    ).order_by("-created_at")

    items = [
        {
            "asset_id": str(asset.id),
            "title": asset.title or "",
            "description": asset.description or "",
            "asset_type": asset.asset_type,
            "visibility": asset.visibility,
            "status": asset.status,
            "tags": list(asset.tags or []),
            "category": asset.category,
            "originality_score": asset.originality_score,
            "min_price_usdc": str(asset.min_price_usdc),
            "target_price_usdc": str(asset.target_price_usdc),
            # Preview artifacts only (R6 toggle).
            "watermark_url": watermark_preview_url(asset.id),
            # Proof (Explorer disabled when anchor_tx_sig is None — draft).
            "anchor_tx_sig": asset.anchor_tx_sig,
            "creator_wallet": asset.creator.wallet_address,
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
        }
        for asset in qs
    ]
    return JsonResponse({"items": items}, status=200)


def catalog(request: HttpRequest) -> JsonResponse:
    """Public discovery for external agents; no original content is exposed."""
    query = (request.GET.get("q") or "").strip()
    asset_type = (request.GET.get("asset_type") or "").strip()
    service = get_catalog_service()
    return JsonResponse(
        {"items": [service.serialize(asset) for asset in service.search(query, asset_type)]},
        status=200,
    )


# === GET /api/v1/events?asset_id=&since= (SPEC-005 shared with SPEC-006) =====
# R10 / AC-9: Firestore-disabled polling fallback. SPEC-006 sandbox consumes it.


def events(request: HttpRequest) -> JsonResponse:
    """Return AgentEvents for an asset, optionally created after ``since``.

    SPEC-005 R10 / AC-9 (Firestore fallback) and shared with SPEC-006 sandbox.
    ``since`` is an ISO-8601 timestamp; only events with ``created_at > since``
    are returned (strictly greater — incremental polling boundary). Without
    ``since`` every event for the asset is returned, oldest first.
    """
    asset_id = (request.GET.get("asset_id") or "").strip()
    since_raw = (request.GET.get("since") or "").strip()

    qs = AgentEvent.objects.all()
    if asset_id:
        qs = qs.filter(asset_id=asset_id)
    if since_raw:
        since_dt = _parse_iso8601(since_raw)
        if since_dt is None:
            return _error(
                "invalid_since", "since must be a valid ISO-8601 timestamp"
            )
        qs = qs.filter(created_at__gt=since_dt)

    qs = qs.order_by("created_at")
    items = [
        {
            "event_id": str(ev.id),
            "asset_id": str(ev.asset_id) if ev.asset_id else None,
            "type": ev.type,
            "payload": ev.payload or {},
            "timestamp": ev.created_at.isoformat() if ev.created_at else None,
        }
        for ev in qs
    ]
    return JsonResponse({"items": items}, status=200)


def _parse_iso8601(raw: str):
    """Parse an ISO-8601 timestamp (incl. trailing 'Z') into an aware datetime.

    Returns ``None`` if the input cannot be parsed. Used by the ``since`` filter
    on ``/api/v1/events``.
    """
    if not raw:
        return None
    try:
        value = raw.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def ai_plugin(request: HttpRequest) -> JsonResponse:
    """외부 에이전트가 실제 x402 API를 발견할 수 있는 manifest를 제공한다."""
    api_base = request.build_absolute_uri("/api/v1/")
    return JsonResponse(
        {
            "schema_version": "v1",
            "name_for_human": "VeriProof AI",
            "name_for_model": "veriproof_ip_agent",
            "description_for_human": "Discover licensable works and settle verified USDC licenses.",
            "description_for_model": (
                "Integration workflow: GET /api/v1/catalog to discover public, "
                "creator-approved works. GET /api/v1/ip/{asset_id} with "
                "X-Agent-Protocol: x402 to receive HTTP 402 payment terms for an "
                "unlicensed work. POST /api/v1/ip/{asset_id}/negotiate with "
                "buyer_agent_id, offer_usdc, and usage_type. After the accepted "
                "Solana USDC payment is confirmed, POST /api/v1/ip/{asset_id}/settle "
                "with tx_signature, buyer_wallet, and the optional session_id. A "
                "successful settlement returns the license certificate and an "
                "expiry-bound download URL. Do not expect the original file in the "
                "catalog or an unlicensed asset response. Read the OpenAPI contract "
                "at the api.url before calling an endpoint."
            ),
            "api": {"type": "openapi", "url": f"{api_base}openapi.json"},
            "auth": {"type": "none"},
            "endpoints": {
                "catalog": f"{api_base}catalog",
                "asset": f"{api_base}ip/{{asset_id}}",
                "negotiate": f"{api_base}ip/{{asset_id}}/negotiate",
                "settle": f"{api_base}ip/{{asset_id}}/settle",
            },
        }
    )


def openapi(request: HttpRequest) -> JsonResponse:
    """외부 에이전트가 호출 가능한 공개 x402 경로만 명시한 OpenAPI 문서다."""
    return JsonResponse(
        {
            "openapi": "3.0.3",
            "info": {"title": "VeriProof Agent API", "version": "1.0.0"},
            "paths": {
                "/api/v1/catalog": {"get": {"summary": "Search shared licensable works"}},
                "/api/v1/ip/{asset_id}": {"get": {"summary": "Read x402 access terms"}},
                "/api/v1/ip/{asset_id}/negotiate": {"post": {"summary": "Negotiate a license"}},
                "/api/v1/ip/{asset_id}/settle": {"post": {"summary": "Settle a verified USDC payment"}},
            },
        }
    )
