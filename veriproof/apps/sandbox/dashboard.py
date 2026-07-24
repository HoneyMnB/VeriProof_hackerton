"""SPEC-006 pure helpers — sandbox live-stream frontend contract logic.

Single source of truth for the sandbox UI pure logic (architecture 5.2 / 6.1 /
6.5). Consumed server-side by the sandbox views/template and mirrored 1:1 by
``static/js/sandbox.js`` (the vanilla-JS twin the browser runs). Keeping one
Python SSOT lets the offline pytest suite verify the data contracts the
frontend consumes without a browser toolchain (same approach as
``apps.ip.dashboard`` in SPEC-005).

Coverage:
- R4 / R5 / R6: ``event_pane`` -> routes an event type to a UI pane
  (seller | buyer | inspector).
- R6 / R8: ``inspector_events`` -> ordered inspector-pane stream used to
  render the network inspector (402 -> tx -> cert).
- R7 / AC-6: ``should_poll_events`` -> Firestore ``onSnapshot`` vs
  ``/api/v1/events`` polling fallback decision.
- R8: ``explorer_url`` -> re-exports the SPEC-005 SSOT for the Explorer link.
"""
from __future__ import annotations

# Pane identifiers shared with static/js/sandbox.js (R4/R5/R6).
PANE_SELLER = "seller"
PANE_BUYER = "buyer"
PANE_INSPECTOR = "inspector"

# AgentEvent types that belong to the bottom network inspector pane (R6).
_INSPECTOR_TYPES = frozenset(
    {"HTTP_402", "PAYMENT_VERIFIED", "CERT_ISSUED", "SIMULATION_FAILED"}
)
# Buyer-pane types: the purchasing AI's offer/accept actions (R5).
_BUYER_TYPES = frozenset({"OFFER", "ACCEPT"})
# Seller-pane types: the creator AI's counter/reasoning (R4).
_SELLER_TYPES = frozenset({"COUNTER"})


def event_pane(event_type: str) -> str:
    """Map an AgentEvent type to the UI pane (R4 seller / R5 buyer / R6 inspector).

    Pure function over the event type string. Unknown types default to the
    inspector (network) pane so new event kinds stay visible in the stream.
    """
    if event_type in _INSPECTOR_TYPES:
        return PANE_INSPECTOR
    if event_type in _BUYER_TYPES:
        return PANE_BUYER
    if event_type in _SELLER_TYPES:
        return PANE_SELLER
    return PANE_INSPECTOR


def inspector_events(events: list[dict]) -> list[dict]:
    """Filter an ordered event stream to the inspector pane, preserving order (R6).

    The inspector renders the live ``HTTP 402 -> USDC tx -> certificate`` stream.
    Input order is preserved so the 402 precedes the payment confirmation.
    """
    return [ev for ev in events if event_pane(ev.get("type", "")) == PANE_INSPECTOR]


def should_poll_events(firestore_enabled: bool, firebase_sdk_present: bool) -> bool:
    """Polling decision (R7 / AC-6).

    Poll ``/api/v1/events?since=`` every 2s UNLESS Firestore is enabled AND the
    Firebase JS SDK is present at runtime (then subscribe via ``onSnapshot``).
    Mirrors ``apps.ip.dashboard.should_poll_events`` (SPEC-005 R10).
    """
    return not (bool(firestore_enabled) and bool(firebase_sdk_present))


def explorer_url(tx_sig: str | None, cluster: str = "devnet") -> str | None:
    """Re-export the SPEC-005 SSOT for the inspector Explorer link (R8).

    Kept as a thin pass-through so the sandbox frontend has a single import
    surface while the canonical builder stays in ``apps.ip.dashboard``.
    """
    from apps.ip.dashboard import explorer_url as _impl

    return _impl(tx_sig, cluster=cluster)
