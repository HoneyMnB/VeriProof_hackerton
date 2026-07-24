"""SPEC-005 unit tests — pure helpers in ``apps/ip/dashboard.py``.

These helpers are the single source of truth for the on-chain proof / frontend
contract logic (architecture 6.1/6.5). They are consumed server-side by the
views/templates AND mirrored 1:1 by ``static/js/dashboard.js`` (the vanilla-JS
twin the browser runs).

The four "frontend" tests named in SPEC §5 (preview-toggle, polling-decision,
analysis-card render, dragdrop->register) are covered here as **Python mirrors
of the JS pure logic** plus an integration contract test
(``tests/integration/test_library_dashboard.py::test_dragdrop_register_response_renders_card``).

Why not jsdom/Playwright: this project has no ``package.json`` / Node toolchain
(installed deps are pure-Python; see pyproject.toml). Per the SPEC-005 task
instructions we keep the frontend tests offline and dependency-free by testing
the data contracts the frontend consumes and mirroring the small pure JS logic
in Python. Real DOM/browser coverage is a documented gap (see report).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

# --- R7 / AC-6: Explorer URL builder ----------------------------------------


def test_explorer_url_builder_devnet():
    """anchor_tx_sig -> Solana Explorer devnet URL; None -> None (draft)."""
    from apps.ip.dashboard import explorer_url

    assert (
        explorer_url("sig_abc")
        == "https://explorer.solana.com/tx/sig_abc?cluster=devnet"
    )
    # cluster override (architecture uses devnet by default).
    assert explorer_url("sig_abc", cluster="mainnet-beta") == (
        "https://explorer.solana.com/tx/sig_abc?cluster=mainnet-beta"
    )
    # Edge (6): draft status has no anchor_tx_sig -> Explorer disabled.
    assert explorer_url(None) is None
    assert explorer_url("") is None


# --- R8 / AC-7: Certificate payload (QR) excludes original -------------------


def test_certificate_payload_excludes_original():
    """QR payload carries ONLY on-chain/proof data — no original bytes/url."""
    from apps.ip.dashboard import build_certificate_payload

    asset = SimpleNamespace(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        image_sha256="a" * 64,
        anchor_tx_sig="anchor_sig_123",
        creator=SimpleNamespace(wallet_address="CreatorWalletxxxxxxxxxxxxxxxxxxxxxxxx"),
        # These MUST be ignored / excluded from the QR payload:
        original_url="https://secret/original.png",
        thumbnail_url="https://cdn/thumb.png",
        watermark_url="https://cdn/wm.png",
    )
    payload = build_certificate_payload(asset, certificate_tx_sig="cert_tx_456")
    # Proof data present.
    assert payload["asset_id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["image_sha256"] == "a" * 64
    assert payload["anchor_tx_sig"] == "anchor_sig_123"
    assert payload["certificate_tx_sig"] == "cert_tx_456"
    assert payload["creator_wallet"] == "CreatorWalletxxxxxxxxxxxxxxxxxxxxxxxx"
    assert payload["explorer_url"] == (
        "https://explorer.solana.com/tx/anchor_sig_123?cluster=devnet"
    )
    # Edge (6): NO original bytes / url / preview cdn url leaks into the QR.
    import json

    blob = json.dumps(payload)
    assert "original" not in blob
    assert "cdn/thumb" not in blob
    assert "cdn/wm" not in blob


def test_certificate_payload_without_license():
    """An asset with no License yet still yields a valid payload (cert=None)."""
    from apps.ip.dashboard import build_certificate_payload

    asset = SimpleNamespace(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        image_sha256="b" * 64,
        anchor_tx_sig="anchor_sig_999",
        creator=SimpleNamespace(wallet_address="WalletXxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
    )
    payload = build_certificate_payload(asset, certificate_tx_sig=None)
    assert payload["certificate_tx_sig"] is None
    assert payload["explorer_url"].endswith("?cluster=devnet")


def test_certificate_payload_draft_asset_disables_explorer():
    """Draft asset (anchor_tx_sig is None) -> explorer_url is None."""
    from apps.ip.dashboard import build_certificate_payload

    asset = SimpleNamespace(
        id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        image_sha256="c" * 64,
        anchor_tx_sig=None,
        creator=SimpleNamespace(wallet_address="WalletYyyyyyyyyyyyyyyyyyyyyyyyyyyy"),
    )
    payload = build_certificate_payload(asset, certificate_tx_sig=None)
    assert payload["anchor_tx_sig"] is None
    assert payload["explorer_url"] is None


# --- R6 / AC-5: Preview toggle (frontend pure mirror) -----------------------


def test_preview_toggle_switches_src():
    """Toggling switches watermark<->thumbnail; never the original url."""
    from apps.ip.dashboard import preview_src

    wm = "https://cdn/wm.png"
    th = "https://cdn/thumb.png"
    # Watermark shown by default.
    assert preview_src(wm, th, show_watermark=True) == wm
    # Toggle to thumbnail.
    assert preview_src(wm, th, show_watermark=False) == th
    # Contract: the helper only ever returns one of its two inputs, so the
    # original url can never be surfaced by construction.


# --- R10 / AC-9: Polling-vs-Firestore decision (frontend pure mirror) --------


def test_firestore_disabled_uses_polling():
    """FIRESTORE_ENABLED=false OR Firebase SDK absent -> fall back to polling."""
    from apps.ip.dashboard import should_poll_events

    # Firestore disabled -> always poll (architecture 5.2 fallback).
    assert should_poll_events(firestore_enabled=False, firebase_sdk_present=False) is True
    assert should_poll_events(firestore_enabled=False, firebase_sdk_present=True) is True
    # Firestore enabled BUT SDK missing in the browser -> still poll (guarded).
    assert should_poll_events(firestore_enabled=True, firebase_sdk_present=False) is True
    # Both enabled and present -> use onSnapshot, no polling.
    assert should_poll_events(firestore_enabled=True, firebase_sdk_present=True) is False


# --- R2 / AC-2: Analysis-card field extraction (frontend pure mirror) -------


def test_render_analysis_card_from_response():
    """The analysis-card render pulls tags/category/score/price from response."""
    from apps.ip.dashboard import analysis_card_fields

    asset_id = "11111111-1111-1111-1111-111111111111"
    response = {
        "asset_id": asset_id,
        "anchor_tx": "anchor_sig_xyz",
        "analysis": {
            "tags": ["photo", "nature"],
            "category": "photography",
            "originality_score": 87,
            "recommended_min_price_usdc": "1.50",
            "degraded": False,
        },
        "x402_endpoint": f"/api/v1/ip/{asset_id}",
        "watermark_url": "https://cdn/wm.png",
    }
    card = analysis_card_fields(response)
    assert card["tags"] == ["photo", "nature"]
    assert card["category"] == "photography"
    assert card["originality_score"] == 87
    assert card["recommended_min_price_usdc"] == "1.50"
    assert card["degraded"] is False
    # R4 / AC-3: completion card fields surfaced from the same response.
    assert card["anchor_tx"] == "anchor_sig_xyz"
    assert card["x402_endpoint"] == f"/api/v1/ip/{asset_id}"
    assert card["asset_id"] == asset_id


def test_analysis_card_fields_degraded_flag_propagated():
    """A degraded Gemini result surfaces a degraded flag for the UI badge."""
    from apps.ip.dashboard import analysis_card_fields

    card = analysis_card_fields(
        {
            "asset_id": "x",
            "anchor_tx": None,
            "analysis": {
                "tags": [],
                "category": None,
                "originality_score": 50,
                "recommended_min_price_usdc": "0.50",
                "degraded": True,
            },
            "x402_endpoint": "/api/v1/ip/x",
            "watermark_url": "https://cdn/wm.png",
        }
    )
    assert card["degraded"] is True
    assert card["anchor_tx"] is None  # completion card Explorer disabled
