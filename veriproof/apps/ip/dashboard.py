"""SPEC-005 pure helpers — on-chain proof + frontend contract logic.

These functions are the **single source of truth** for the dashboard pure logic
(architecture 6.1/6.5). They are consumed server-side by the web/API views and
the templates, and mirrored 1:1 by ``static/js/dashboard.js`` (the vanilla-JS
twin the browser runs). Keeping one Python SSOT lets the offline test suite
verify the data contracts the frontend consumes without a browser toolchain.

Coverage:
- R7 / AC-6: ``explorer_url`` — Solana Explorer devnet URL builder.
- R8 / AC-7: ``build_certificate_payload`` — QR payload (proof data only).
- R6 / AC-5: ``preview_src`` — watermark/thumbnail switch (never original).
"""
from __future__ import annotations

from typing import Any

# Architecture 6.1 anchor cluster is devnet for the hackathon.
EXPLORER_BASE = "https://explorer.solana.com"
DEFAULT_CLUSTER = "devnet"


def explorer_url(
    anchor_tx_sig: str | None, cluster: str = DEFAULT_CLUSTER
) -> str | None:
    """Build a Solana Explorer transaction URL for an anchor signature.

    Returns ``None`` when ``anchor_tx_sig`` is missing/empty — this is the
    draft-status edge (architecture §6 edge: anchoring pending -> Explorer
    button disabled). SPEC-005 R7 / AC-6.
    """
    if not anchor_tx_sig:
        return None
    return f"{EXPLORER_BASE}/tx/{anchor_tx_sig}?cluster={cluster}"


def build_certificate_payload(
    asset: Any, certificate_tx_sig: str | None = None
) -> dict[str, Any]:
    """Build the on-chain proof payload encoded into the certificate QR modal.

    SPEC-005 R8 / AC-7 (edge: QR encodes only on-chain/proof data). The payload
    intentionally EXCLUDES the original bytes, the original url, and the CDN
    preview urls (thumbnail/watermark) — it is an attestation record only.

    ``asset`` is duck-typed: it needs ``id``, ``image_sha256``, ``anchor_tx_sig``
    and ``creator.wallet_address``. ``certificate_tx_sig`` is the latest
    License's on-chain certificate Memo signature (None until first license).
    """
    return {
        "asset_id": str(asset.id),
        "image_sha256": asset.image_sha256,
        "anchor_tx_sig": asset.anchor_tx_sig,
        "certificate_tx_sig": certificate_tx_sig,
        "creator_wallet": asset.creator.wallet_address,
        "explorer_url": explorer_url(asset.anchor_tx_sig),
    }


def preview_src(
    watermark_url: str, thumbnail_url: str, show_watermark: bool
) -> str:
    """Return the preview src for the asset card toggle. SPEC-005 R6 / AC-5.

    By construction this helper can only return one of its two inputs, so the
    original url can never be surfaced through the preview path.
    """
    return watermark_url if show_watermark else thumbnail_url


def should_poll_events(
    *, firestore_enabled: bool, firebase_sdk_present: bool
) -> bool:
    """브라우저가 Firestore 대신 이벤트 폴링을 해야 하는지 반환한다."""
    return not (firestore_enabled and firebase_sdk_present)


def analysis_card_fields(response: dict[str, Any]) -> dict[str, Any]:
    """등록 응답에서 분석·완료 카드가 읽는 필드만 추출한다.

    브라우저 구현과의 계약 검증에 쓰이며, 서버 응답을 변경하지 않는다.
    """
    analysis = response.get("analysis", {}) or {}
    return {
        "asset_id": response.get("asset_id"),
        "anchor_tx": response.get("anchor_tx"),
        "x402_endpoint": response.get("x402_endpoint"),
        "tags": list(analysis.get("tags", []) or []),
        "category": analysis.get("category"),
        "originality_score": analysis.get("originality_score"),
        "recommended_min_price_usdc": analysis.get("recommended_min_price_usdc"),
        "degraded": bool(analysis.get("degraded", False)),
    }
