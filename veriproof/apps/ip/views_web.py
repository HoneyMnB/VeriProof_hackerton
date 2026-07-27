"""Web (template) views for the IP app: creator workspace + library.

- ``/`` (``workspace``): creator assistant and registration surface.
- ``/discover``: public marketplace discovery.
- ``/library`` (``library``): creator asset grid + on-chain proof. SPEC-005.

The ``/files/<token>`` download route lives in ``apps.settlement.views_api``
(License authorisation) and is wired from ``apps/ip/urls_web.py``.

Access control (architecture 6.5 edge): full wallet-signature / session
verification is deferred post-hackathon. The hackathon minimum accepts a
``creator`` (or ``wallet``) query parameter and renders ONLY that creator's
assets (SPEC-005 R5 / AC-4).
"""
from __future__ import annotations

import decimal
import json
from django.conf import settings
from django.db.models import Prefetch
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.template.response import TemplateResponse

from apps.ip.browser_license_session import (
    get_active_browser_license,
    remember_browser_payment_request,
)
from apps.ip.models import AssetImage, IpAsset
from apps.settlement.models import License
from services.catalog_service import get_catalog_service
from services.certificate_document_service import get_registration_certificate_document_service
from services.preview_service import thumbnail_preview_url, watermark_preview_url
from services.storage_service import get_storage_service
from services.x402_service import get_x402_service

from . import dashboard

_SORT_OPTIONS = [
    ("recent", "Most recent"),
    ("price_low", "Price: low to high"),
    ("price_high", "Price: high to low"),
    ("originality", "Most original"),
]
# (key, label, price_min, price_max) — None means unbounded. Bounds are passed
# as strings so they survive the query-param round-trip unchanged.
_PRICE_BUCKETS = [
    ("all", "Any price", None, None),
    ("under5", "Under 5 SOL", None, "5"),
    ("5to25", "5 - 25 SOL", "5", "25"),
    ("over25", "25+ SOL", "25", None),
]


def _model_label(model_id: str | None) -> str:
    """Humanise a Gemini model id, e.g. gemini-3.1-flash-lite -> Gemini 3.1 Flash Lite.

    Renders the badge in the workspace header from the configured model so the
    label never drifts from the model actually in use (UX-005).
    """
    core = (model_id or "").lower().replace("gemini-", "").strip()
    if not core:
        return model_id or ""
    parts = [p.capitalize() for p in core.replace("-", " ").split()]
    return "Gemini " + " ".join(parts)


def discover(request: HttpRequest) -> HttpResponse:
    """Public marketplace discovery. Exposes only catalog-safe metadata.

    Supports search (q), work-type filter (asset_type), sort (recent / price /
    originality) and a price-range bucket (price) — all reflected back into the
    filter UI (UX: exploration depth).
    """
    query = (request.GET.get("q") or "").strip()
    asset_type = (request.GET.get("asset_type") or "").strip()
    sort = (request.GET.get("sort") or "recent").strip()
    price_key = (request.GET.get("price") or "all").strip()
    pmin, pmax = _bucket_range(price_key)
    catalog = get_catalog_service()
    assets = [
        catalog.serialize(asset)
        for asset in catalog.search(query, asset_type, sort=sort, price_min=pmin, price_max=pmax)
    ]
    return TemplateResponse(
        request,
        "discover.html",
        {
            "assets": assets,
            "query": query,
            "asset_type": asset_type,
            "sort": sort,
            "price": price_key,
            "asset_types": IpAsset.ASSET_TYPE_CHOICES,
            "sort_options": _SORT_OPTIONS,
            "price_buckets": _price_bucket_links(query, asset_type, sort, price_key),
            "active_nav": "discover",
        },
    )


def asset_detail(request: HttpRequest, asset_id) -> HttpResponse:
    """사람 구매자에게 워터마크·가격·즉시결제만 제공하는 공개 상세 화면이다."""
    asset = (
        IpAsset.objects.select_related("creator")
        .prefetch_related("gallery_images")
        .filter(
            id=asset_id,
            visibility=IpAsset.PUBLIC,
            status__in=(IpAsset.ANCHORED, IpAsset.LISTED),
            registration_certificate_tx_sig__isnull=False,
        )
        .first()
    )
    if asset is None:
        raise Http404("public asset not found")
    payment = get_x402_service().build_solana_pay_fallback(asset)
    active_license = get_active_browser_license(request, asset)
    if request.user.is_authenticated:
        remember_browser_payment_request(request, asset, payment["solana_pay"]["reference"])
    return TemplateResponse(
        request,
        "asset_detail.html",
        {
            "asset": get_catalog_service().serialize(asset),
            "gallery_images": _gallery_previews(asset),
            "payment": payment,
            "active_license": _active_license_context(active_license),
            "active_nav": "discover",
        },
    )


def preview(request: HttpRequest, asset_id, variant: str) -> HttpResponse:
    """원본 경로를 공개하지 않고 허용된 미리보기 바이트만 반환한다."""
    asset = IpAsset.objects.select_related("creator", "account_owner").filter(id=asset_id).first()
    if asset is None:
        raise Http404("asset not found")
    if variant == "watermark":
        if (
            asset.visibility != IpAsset.PUBLIC
            or asset.status not in {IpAsset.ANCHORED, IpAsset.LISTED}
            or not asset.registration_certificate_tx_sig
        ):
            raise Http404("preview not found")
    elif variant == "thumbnail":
        if not request.user.is_authenticated or asset.account_owner_id != request.user.id:
            raise Http404("preview not found")
    else:
        raise Http404("preview not found")
    data = get_storage_service().read_permanent(variant, asset.id)
    if data is None:
        raise Http404("preview not found")
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "private, max-age=300" if variant == "thumbnail" else "public, max-age=300"
    return response


def gallery_preview(request: HttpRequest, asset_id, image_id) -> HttpResponse:
    """공개 작품에 속한 추가 이미지의 워터마크 미리보기만 반환한다."""
    image = (
        AssetImage.objects.select_related("asset")
        .filter(
            id=image_id,
            asset_id=asset_id,
            asset__visibility=IpAsset.PUBLIC,
            asset__status__in=(IpAsset.ANCHORED, IpAsset.LISTED),
            asset__registration_certificate_tx_sig__isnull=False,
        )
        .first()
    )
    if image is None:
        raise Http404("preview not found")
    data = get_storage_service().read_permanent("watermark", image.id)
    if data is None:
        raise Http404("preview not found")
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "public, max-age=300"
    return response


def _gallery_previews(asset: IpAsset) -> list[dict]:
    """상세 화면에 필요한 보호 미리보기 목록만 만든다. 원본 위치는 포함하지 않는다."""
    previews = [
        {
            "position": 0,
            "watermark_url": watermark_preview_url(asset.id),
        }
    ]
    previews.extend(
        {
            "position": image.position,
            "watermark_url": f"/previews/{asset.id}/gallery/{image.id}",
        }
        for image in asset.gallery_images.all()
    )
    return previews


def _active_license_context(license: License | None) -> dict | None:
    """Template DTO for a browser session's currently active download right."""
    if license is None or not license.download_token:
        return None
    return {
        "download_url": f"/files/{license.download_token}",
        "download_expires_at": (
            license.download_expires_at.isoformat()
            if license.download_expires_at
            else None
        ),
    }


def _bucket_range(key: str):
    """선택된 가격 버킷 키의 (최소, 최대) 범위를 반환한다. 미정의 키면 (None, None)."""
    for k, _label, lo, hi in _PRICE_BUCKETS:
        if k == key:
            return lo, hi
    return None, None


def _price_bucket_links(query: str, asset_type: str, sort: str, current: str) -> list[dict]:
    """현재 필터 상태를 유지한 채 각 가격 버킷의 쿼리스트링 링크 목록을 만든다."""
    from urllib.parse import urlencode

    links = []
    for k, label, _lo, _hi in _PRICE_BUCKETS:
        params = {}
        if query:
            params["q"] = query
        if asset_type:
            params["asset_type"] = asset_type
        if sort and sort != "recent":
            params["sort"] = sort
        if k != "all":
            params["price"] = k
        links.append({"value": k, "label": label, "qs": urlencode(params), "is_active": k == current})
    return links


def workspace(request: HttpRequest) -> HttpResponse:
    """Creator workspace page (architecture 6.5: ``/``). SPEC-001/005.

    Exposes the upload size limit, the register endpoint, and feature flags to
    the template so the vanilla JS can wire drag&drop -> register without any
    hardcoded values.
    """
    return TemplateResponse(
        request,
        "workspace.html",
        {
            "register_url": "/api/v1/ip/register",
            "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
            "asset_types": IpAsset.ASSET_TYPE_CHOICES,
            "assistant_model_label": _model_label(settings.GEMINI_ASSISTANT_MODEL),
            "active_nav": "assistant",
            "debug": settings.DEBUG,
        },
    )


@login_required
def library(request: HttpRequest) -> HttpResponse:
    """IP library + certificate page (architecture 6.5: ``/library``). SPEC-005.

    Resolves the creator wallet from the ``creator`` (or ``wallet`` alias) query
    parameter, loads that creator's IpAsset rows, and injects per-asset proof
    payloads (Explorer URL + certificate QR data) into the template context so
    the page renders server-side with zero extra round-trips.
    """
    preference = getattr(request.user, "veriproof_preferences", None)
    active_wallet = (getattr(preference, "creator_wallet", "") or "").strip()
    assets_qs = (
        IpAsset.objects.filter(account_owner=request.user)
        .select_related("creator")
        .prefetch_related(Prefetch("licenses", queryset=License.objects.order_by("-granted_at")))
        .order_by("-created_at")
    )
    # R8 / AC-7: pre-compute the on-chain proof payload (Explorer URL + cert QR
    # data) per asset. NO original bytes/url is ever surfaced here.
    asset_cards = [_asset_card(asset) for asset in assets_qs]

    return TemplateResponse(
        request,
        "library.html",
        {
            "has_active_wallet": bool(active_wallet),
            "assets": asset_cards,
            "firestore_enabled": getattr(settings, "FIRESTORE_ENABLED", False),
            "events_url": "/api/v1/events",
            "transactions_url_template": "/api/v1/ip/{asset_id}/transactions",
            "assets_api_url": "/api/v1/assets",
            "active_nav": "library",
            "debug": settings.DEBUG,
        },
    )


@login_required
def download_registration_certificate(request: HttpRequest, asset_id) -> HttpResponse:
    """Download the owner's persisted registration proof as a PDF certificate."""
    asset = IpAsset.objects.select_related("creator").filter(
        id=asset_id,
        account_owner=request.user,
    ).first()
    if asset is None:
        raise Http404("certificate not found")
    if not asset.registration_certificate_tx_sig or not asset.anchor_tx_sig:
        return HttpResponse("registration certificate is pending", status=409, content_type="text/plain")
    explorer_url = dashboard.explorer_url(asset.anchor_tx_sig)
    if not explorer_url:
        return HttpResponse("registration certificate is pending", status=409, content_type="text/plain")
    payload = get_registration_certificate_document_service().render(asset, explorer_url)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="veriproof-registration-{asset.id}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response


def _asset_card(asset: IpAsset) -> dict:
    """Build the per-asset view-model rendered as a card in the library grid.

    Pulls the latest License (if any) to surface its certificate tx signature in
    the QR payload. Deliberately excludes ``original_url``/original bytes — only
    proof + preview (watermark/thumbnail) data is exposed to the browser.
    """
    licenses = list(asset.licenses.all())
    latest_license = licenses[0] if licenses else None
    sales = [
        {
            "buyer_wallet": license.buyer_wallet,
            "price_usdc": str(license.price_usdc) if license.price_usdc is not None else None,
            "price_sol": str(license.price_sol) if license.price_sol is not None else None,
            "payment_currency": license.payment_currency,
            "usage_type": license.usage_type,
            "granted_at": license.granted_at.isoformat() if license.granted_at else None,
            "certificate_tx_sig": license.certificate_tx_sig or "",
        }
        for license in licenses
    ]
    sales_summary = {
        "sale_count": len(licenses),
        "gross_usdc": str(sum((license.price_usdc or decimal.Decimal("0") for license in licenses), decimal.Decimal("0"))),
        "last_sale_at": sales[0]["granted_at"] if sales else None,
    }
    manage_data = {
        "title": asset.title or "",
        "description": asset.description or "",
        "tags": list(asset.tags or []),
        "asset_type": asset.asset_type,
        "category": asset.category or "",
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "image_sha256": asset.image_sha256,
        "sales_summary": sales_summary,
        "sales": sales,
    }
    return {
        "asset_id": str(asset.id),
        "title": asset.title or "",
        "status": asset.status,
        "tags": list(asset.tags or []),
        "category": asset.category,
        "originality_score": asset.originality_score,
        "min_price_usdc": str(asset.min_price_usdc),
        "target_price_usdc": str(asset.target_price_usdc),
        "target_price_sol": str(asset.target_price_sol) if asset.target_price_sol is not None else "",
        # 판매 조건 편집 폼은 서버가 가진 현재 공개 상태를 그대로 렌더링한다.
        # 누락하면 폼이 항상 private처럼 보이는 UI/데이터 불일치가 생긴다.
        "visibility": asset.visibility,
        # Preview artifacts only (R6 toggle switches between these two).
        "watermark_url": watermark_preview_url(asset.id),
        "thumbnail_url": thumbnail_preview_url(asset.id),
        # On-chain proof (R7/R8).
        "anchor_tx_sig": asset.anchor_tx_sig,
        "registration_certificate_tx_sig": asset.registration_certificate_tx_sig,
        "explorer_url": dashboard.explorer_url(asset.anchor_tx_sig),
        "certificate": dashboard.build_certificate_payload(
            asset, certificate_tx_sig=(
                latest_license.certificate_tx_sig if latest_license else None
            ) or asset.registration_certificate_tx_sig,
        ),
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "manage_json": json.dumps(manage_data, ensure_ascii=False),
        "sales_summary": sales_summary,
    }
