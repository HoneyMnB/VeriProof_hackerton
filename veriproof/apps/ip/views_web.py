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

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.template.response import TemplateResponse

from apps.ip.models import Creator, IpAsset
from apps.settlement.models import License
from services.catalog_service import get_catalog_service
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
    ("under5", "Under 5 USDC", None, "5"),
    ("5to25", "5 – 25 USDC", "5", "25"),
    ("over25", "25+ USDC", "25", None),
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
    return TemplateResponse(
        request,
        "asset_detail.html",
        {
            "asset": get_catalog_service().serialize(asset),
            "payment": get_x402_service().build_solana_pay_fallback(asset),
            "active_nav": "discover",
            "local_mock_payment": settings.DEBUG and settings.PAYMENT_VERIFIER == "mock",
        },
    )


def preview(request: HttpRequest, asset_id, variant: str) -> HttpResponse:
    """원본 경로를 공개하지 않고 허용된 미리보기 바이트만 반환한다."""
    asset = IpAsset.objects.select_related("creator").filter(id=asset_id).first()
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
        preference = getattr(request.user, "veriproof_preferences", None)
        if not request.user.is_authenticated or preference is None or preference.creator_wallet != asset.creator.wallet_address:
            raise Http404("preview not found")
    else:
        raise Http404("preview not found")
    data = get_storage_service().read_permanent(variant, asset.id)
    if data is None:
        raise Http404("preview not found")
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "private, max-age=300" if variant == "thumbnail" else "public, max-age=300"
    return response


def _bucket_range(key: str):
    for k, _label, lo, hi in _PRICE_BUCKETS:
        if k == key:
            return lo, hi
    return None, None


def _price_bucket_links(query: str, asset_type: str, sort: str, current: str) -> list[dict]:
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


def library(request: HttpRequest) -> HttpResponse:
    """IP library + certificate page (architecture 6.5: ``/library``). SPEC-005.

    Resolves the creator wallet from the ``creator`` (or ``wallet`` alias) query
    parameter, loads that creator's IpAsset rows, and injects per-asset proof
    payloads (Explorer URL + certificate QR data) into the template context so
    the page renders server-side with zero extra round-trips.
    """
    wallet = (request.GET.get("creator") or request.GET.get("wallet") or "").strip()
    if not wallet and request.user.is_authenticated:
        preference = getattr(request.user, "veriproof_preferences", None)
        wallet = (getattr(preference, "creator_wallet", "") or "").strip()

    assets_qs = IpAsset.objects.none()
    wallet_resolved = False
    if wallet:
        wallet_resolved = Creator.objects.filter(wallet_address=wallet).exists()
        assets_qs = (
            IpAsset.objects.filter(creator__wallet_address=wallet)
            .select_related("creator")
            .order_by("-created_at")
        )

    # R8 / AC-7: pre-compute the on-chain proof payload (Explorer URL + cert QR
    # data) per asset. NO original bytes/url is ever surfaced here.
    asset_cards = [_asset_card(asset) for asset in assets_qs]

    return TemplateResponse(
        request,
        "library.html",
        {
            "wallet": wallet,
            "wallet_resolved": wallet_resolved,
            "assets": asset_cards,
            "firestore_enabled": getattr(settings, "FIRESTORE_ENABLED", False),
            "events_url": "/api/v1/events",
            "transactions_url_template": "/api/v1/ip/{asset_id}/transactions",
            "assets_api_url": "/api/v1/assets",
            "active_nav": "library",
            "debug": settings.DEBUG,
        },
    )


def _asset_card(asset: IpAsset) -> dict:
    """Build the per-asset view-model rendered as a card in the library grid.

    Pulls the latest License (if any) to surface its certificate tx signature in
    the QR payload. Deliberately excludes ``original_url``/original bytes — only
    proof + preview (watermark/thumbnail) data is exposed to the browser.
    """
    latest_license = (
        License.objects.filter(asset=asset).order_by("-granted_at").first()
    )
    return {
        "asset_id": str(asset.id),
        "title": asset.title or "",
        "status": asset.status,
        "tags": list(asset.tags or []),
        "category": asset.category,
        "originality_score": asset.originality_score,
        "min_price_usdc": str(asset.min_price_usdc),
        "target_price_usdc": str(asset.target_price_usdc),
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
    }
