---
name: veriproof-spec003-contracts
description: SPEC-003 implementation contract decisions in VeriProof services that SPEC-004/008 must honor (NegotiationResult shape, NegotiationEngine layering, GeminiService.negotiate never-raises, view orchestration, build_ap2_mandate)
metadata:
  type: project
---

SPEC-003 (Gemini autonomous price negotiation) landed with contract decisions
that downstream payment-flow SPECs (004 settle, 008 escrow/royalty) must honor.
Non-obvious and not derivable from the architecture doc alone.

**Why:** The scaffold + SPEC-001/002 fixed interface signatures, but the
NegotiationResult value-object, the engine layering (Gemini vs rule fallback),
and the AP2 mandate shape all needed concrete decisions to make R4/R8/R9/R10
testable offline. SPEC-004/008 will break if they assume the original
NegotiationResult shape or re-derive the recipient rule.

**How to apply:** Read before editing `services/_types.py`,
`services/negotiation_engine.py`, `services/gemini_service.py`,
`services/x402_service.py`, `apps/negotiation/views_api.py`, or
`tests/test_smoke.py`.

1. **`NegotiationResult` extended** (`services/_types.py`): added
   `pay_address: str | None = None` and widened `price_usdc` to
   `decimal.Decimal | None` (None on REJECT). Backward-compatible (pay_address
   has a default; FakeGeminiService still constructs without it). SPEC-004/008
   that read negotiation results must handle `price_usdc=None` (REJECT case).
   `session_id` is added at the VIEW layer only, never on the dataclass.

2. **`quantize_usdc(value)` helper** added to `services/_types.py` (single
   source for USDC 6-decimal rounding, ROUND_HALF_UP). Both
   `GeminiService.negotiate` and `NegotiationEngine.run_round` import it — do
   NOT re-implement Decimal rounding in SPEC-004/007/008 money paths; reuse
   this. Exported via `services.quantize_usdc`.

3. **`NegotiationEngine.run_round` is LAYERED (Gemini-first with rule
   fallback)**, not pure-rule: when `self.gemini` is wired it calls
   `gemini.negotiate(...)` first, then applies session invariants. BOTH the
   Gemini result and the rule result flow through `_finalize()` which enforces
   R10 (clamp price up to min_price on ACCEPT/COUNTER) and resolves
   `pay_address` via `resolve_pay_to(asset)`. SPEC-004 must NOT call
   `NegotiationEngine.run_round` to re-derive recipient — it must reuse
   `resolve_pay_to` directly for `expected_recipient` (see
   [[veriproof-spec002-contracts]] §1).

4. **`GeminiService.negotiate` NEVER raises** (R8): no client -> immediate
   rule fallback; transport/parse failure -> 3 retries -> rule fallback.
   `_call_negotiate` passes `config={"response_mime_type": "application/json",
   "response_schema": NEGOTIATION_RESPONSE_SCHEMA}` (R4 forced schema). R10
   clamp applied in `_parse_negotiate_response` AND again in the engine's
   `_finalize` (belt-and-suspenders). Unknown model status -> `ValueError` ->
   caught by retry loop -> fallback.

5. **R9 max-rounds semantics**: the round cap (`settings.MAX_NEGOTIATION_ROUNDS`,
   default 5) gates ONLY the COUNTER path. A late offer that meets `min_price`
   still ACCEPTs even at/past the cap (creator-friendly — money is money).
   The check is `if not offer_meets_min and len(rounds) >= max_rounds: REJECT`.
   SPEC-004 must not assume REJECT blocks a good late offer.

6. **Counter price rule** (R3): midpoint of `[min_price, target_price]`,
   clamped to `>= min_price` (R10), quantized to 6 decimals. When
   `target <= min` (degenerate data), counter == min_price.

7. **View orchestration** (`apps/negotiation/views_api.py::negotiate`):
   delegates the round to `get_negotiation_engine().run_round(...)`; the view
   itself does NOT call resolve_pay_to (engine owns it). Session is
   created/resumed keyed by `(asset, buyer_agent_id)` via `get_or_create`
   (R1; separate sessions per buyer). DI seam: tests monkeypatch
   `apps.negotiation.views_api.get_negotiation_engine / get_event_recorder /
   get_x402_service` — same pattern as SPEC-001/002. URL route
   `ip/<uuid:asset_id>/negotiate` was already wired (SPEC-000).

8. **Round event type mapping** (R6/AC-9): ACCEPT->"ACCEPT",
   COUNTER_OFFER->"COUNTER", REJECT->"OFFER" (REJECT has no dedicated type in
   the {OFFER, COUNTER, ACCEPT} set). Event payload shape is
   `{asset_id, session_id, offer_usdc, status, price_usdc, reason}` for
   BigQuery consistency with the SPEC-002 HTTP_402 `{asset_id, ...}` shape.

9. **`X402Service.build_ap2_mandate(session, kind)` IMPLEMENTED** (R14/AC-10):
   returns `None` when `ap2_enabled` is falsy (no-op local/TDD default); returns
   a minimal VDC-shaped dict when enabled (`@context`, `type` CartMandate/
   PaymentMandate, `kind`, `asset_id`, `session_id`, `buyer_agent_id`,
   `amount_usdc` (= final_price or initial_offer), `mint`, `network`,
   `pay_to` via `resolve_pay_to`). UNSIGNED body only — KMS signing/issuance
   as a verifiable credential is a cloud concern. `parse_payment_submitted`
   REMAINS a NotImplementedError stub (SPEC-004).

10. **Money wire serialization**: the §6.2 response serializes `price_usdc`
    as a STRING (`str(Decimal)`), consistent with the register view's
    `str(analysis.recommended_min_price_usdc)`. SPEC-004/005 clients must
    parse with `decimal.Decimal(body["price_usdc"])`, not assume a JSON number.

11. **Smoke test progression** (`tests/test_smoke.py`):
    `test_service_stubs_raise_not_implemented` was edited to DROP the
    `pytest.raises(NotImplementedError)` assertions for
    `GeminiService.negotiate` and `X402Service.build_ap2_mandate` (both now
    implemented in SPEC-003), and added sanity checks that they don't raise
    (negotiate falls back to rule; build_ap2_mandate returns None when AP2 off).
    Remaining stub assertions: `KmsSigner.public_key`, `LicenseService.grant`,
    `RoyaltyService.distribute`, `SolanaService.verify_usdc_payment`,
    `X402Service.parse_payment_submitted`.

SPEC-003 services coverage: 97% on `negotiation_engine`, 97% on
`gemini_service`, 97% on `x402_service`. Remaining misses are defensive guards
for states unreachable offline. See [[veriproof-spec002-contracts]] for the
SPEC-002 contracts, [[veriproof-spec001-contracts]] for SPEC-001, and
[[veriproof-scaffold-done]] for venv/env conventions.
