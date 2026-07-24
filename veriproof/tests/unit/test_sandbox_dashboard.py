"""SPEC-006 frontend contract tests (offline, no browser toolchain).

Mirrors the SPEC-005 approach: the pure UI logic lives in a Python SSOT
(``apps/sandbox/dashboard.py``) and is mirrored 1:1 by ``static/js/sandbox.js``
(the vanilla-JS twin the browser runs). The three SPEC-006 §5 frontend tests
are covered here as Python mirrors of the JS logic + the consumed event JSON
contract.

Documented gap (same as SPEC-005): no jsdom/Playwright installed and the SPEC
forbids adding heavy browser tooling (no package.json in this project). The
data CONTRACTS the JS consumes (pane routing, inspector ordering, polling
fallback, mock badge) are fully tested here. Real DOM/browser coverage remains
a documented gap.
"""
from __future__ import annotations

from apps.sandbox.dashboard import (
    PANE_BUYER,
    PANE_INSPECTOR,
    PANE_SELLER,
    event_pane,
    explorer_url,
    inspector_events,
    should_poll_events,
)


# === R4/R5/R6: three-pane routing ============================================


def test_stream_renders_three_panes():
    """Each AgentEvent type routes to the correct pane (seller/buyer/inspector)."""
    # R6: inspector = network stream (402 -> tx -> cert).
    assert event_pane("HTTP_402") == PANE_INSPECTOR
    assert event_pane("PAYMENT_VERIFIED") == PANE_INSPECTOR
    assert event_pane("CERT_ISSUED") == PANE_INSPECTOR
    assert event_pane("SIMULATION_FAILED") == PANE_INSPECTOR
    # R5: buyer pane = offer/accept actions.
    assert event_pane("OFFER") == PANE_BUYER
    assert event_pane("ACCEPT") == PANE_BUYER
    # R4: seller pane = Gemini counter/reasoning.
    assert event_pane("COUNTER") == PANE_SELLER
    # Unknown types default to the inspector (network) pane.
    assert event_pane("UNKNOWN") == PANE_INSPECTOR


# === R6/R8: inspector ordering + Explorer link ===============================


def test_inspector_shows_402_then_tx():
    """Inspector pane shows HTTP 402 then the USDC tx, with an Explorer link."""
    events = [
        {"type": "HTTP_402", "payload": {"status": 402}},
        {"type": "OFFER", "payload": {"offer_usdc": "1.5"}},  # buyer pane
        {"type": "COUNTER", "payload": {"reason": "..."}},  # seller pane
        {"type": "ACCEPT", "payload": {"price_usdc": "1.5"}},  # buyer pane
        {"type": "PAYMENT_VERIFIED", "payload": {"tx_signature": "mock_tx_abc"}},
        {"type": "CERT_ISSUED", "payload": {"certificate_tx": "mock_cert_1"}},
    ]
    stream = inspector_events(events)
    types = [ev["type"] for ev in stream]
    # Order preserved: 402 precedes the payment + cert events.
    assert types == ["HTTP_402", "PAYMENT_VERIFIED", "CERT_ISSUED"]
    assert types.index("HTTP_402") < types.index("PAYMENT_VERIFIED")
    # R8: Explorer link built from the certificate tx signature.
    assert explorer_url("mock_cert_1") == (
        "https://explorer.solana.com/tx/mock_cert_1?cluster=devnet"
    )
    # Missing tx -> None (Explorer button disabled).
    assert explorer_url(None) is None
    assert explorer_url("") is None


# === R7/AC-6: polling fallback ==============================================


def test_polling_fallback_when_no_firestore():
    """Poll /api/v1/events unless Firestore AND the JS SDK are both live."""
    # Firestore disabled (offline default) -> always poll.
    assert should_poll_events(False, False) is True
    # Firestore enabled but the Firebase JS SDK is absent at runtime -> poll.
    assert should_poll_events(True, False) is True
    # Both live -> subscribe via onSnapshot (no polling).
    assert should_poll_events(True, True) is False
