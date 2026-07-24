---
name: veriproof-spec007-contracts
description: SPEC-007 implementation contract decisions in VeriProof batch licensing that SPEC-008 must honor (BatchService SSOT, quote_batch never-raises + rule fallback, integer min-units total, per-item grant idempotency keys, partial-failure isolation, view location, recipient rule)
metadata:
  type: project
---

SPEC-007 (B2B micro-amount bulk licensing, scenario S2) landed with contract
decisions that downstream SPECs (008 escrow/royalty, plus any future batch
extensions) must honor. Non-obvious and not derivable from the architecture
doc alone.

**Why:** The architecture fixed the BatchOrder/BatchItem schema and the
`quote_batch` interface, but the batch SSOT location, the integer min-units
total summation, the per-item license idempotency key derivation, the
partial-failure isolation model, and the single-seller recipient assumption
all needed concrete decisions to satisfy R1..R10/AC-1..AC-10 testably offline.
SPEC-008 will break if it re-derives the recipient rule for batch or assumes
the batch settle calls RoyaltyService (it does NOT — royalty is out of MVP
scope for batch per §6 edge).

**How to apply:** Read before editing `apps/settlement/batch_services.py`,
`services/gemini_service.py` (`quote_batch`), `apps/ip/views_api.py`
(`batch_negotiate` / `batch_settle`), `tests/fakes.py` (`FakeLicenseService`),
or `tests/test_smoke.py`.

1. **Batch SSOT = `apps/settlement/batch_services.py::BatchService`** (NEW
   module). `quote_batch_order(buyer_agent_id, asset_ids, usage_type) ->
   (BatchOrder, items)` and `settle_batch_order(order_id, tx_signature,
   buyer_wallet=None) -> BatchSettleResult`. DI seam: tests monkeypatch
   `apps.ip.views_api.get_batch_service` (the view factory). Constructor
   defaults each dependency to its settings factory but accepts injected
   fakes (`gemini`, `solana`, `license_service`, `event_recorder`,
   `bigquery`, `usdc_mint`, `batch_max_items`, `micro_floor`). Do NOT add a
   second batch path — Workflows must reuse these methods.

2. **VIEW LOCATION: batch views live in `apps/ip/views_api.py`** as
   `batch_negotiate` / `batch_settle` (NOT `apps/settlement/views_api.py`).
   The URL routes were already wired in `apps/ip/urls.py` at scaffold time
   (`ip/batch/negotiate`, `ip/batch/settle`) pointing at
   `apps.ip.views_api.batch_*`. The task brief said `apps/settlement/views_api.py`
   but moving them would have required URL rewiring + risked breaking
   SPEC-001..006; the views are thin HTTP wrappers over the settlement-SSOT
   `BatchService` (mirrors how `settle_pipeline` SSOT sits in
   `apps/settlement/services.py` but is called from views). Flagged decision.

3. **`GeminiService.quote_batch(items, usage_type)` IMPLEMENTED**, never
   raises (R2/R8 pattern from `negotiate`). `items` is a list of
   `{asset_id, min_price_usdc}` dicts (the `_normalize_batch_item` helper also
   accepts IpAsset-like objects with `.id`/`.min_price_usdc`). Returns
   `list[BatchQuote]` in input order. Uses `gemini-3.5-flash-lite` with
   `BATCH_QUOTE_RESPONSE_SCHEMA` (forced `response_schema`, 3 retries). On no
   client / transport / parse failure / unknown asset_id -> rule fallback
   `unit = max(min_price_usdc, MICRO_FLOOR_USDC)`. Every unit_price is clamped
   to `>= max(min_price, MICRO_FLOOR)` (AC-2) and quantized to 6 decimals via
   `quantize_usdc`. `_batch_model_for_call()` defaults to
   `"gemini-3.5-flash-lite"`.

4. **Integer min-units total (R1/AC-1, architecture §8)**: `quote_batch_order`
   sums the per-unit prices as INTEGER min-units via `_to_min_units` (6 dp,
   `ROUND_HALF_EVEN`) then projects back via `_from_min_units`
   (`quantize_usdc`). 3 × 0.05 -> 150000 min-units -> `Decimal("0.150000").
   Do NOT sum Decimals directly (avoids 6-dp drift on micro-amount batches).

5. **Per-item license idempotency key** = `f"batch:{tx_signature}:{item.id}"`
   (item.id is the BatchItem UUID). Deterministic on replay -> LicenseService
   .grant short-circuits duplicates (R10 belt-and-suspenders). SPEC-008 must
   NOT change this key scheme or replay would double-license.

6. **TWO-LAYER idempotency on (order_id, tx_signature)** (R10/AC-10):
   (a) ORDER-LEVEL: if `order.payment_tx_sig == tx_signature` AND status in
   {settled, partial} -> `_rebuild_result(order)` returns immediately (no
   re-verify, no re-grant, no duplicate events). (b) LICENSE-LEVEL: per-item
   key above. A DIFFERENT tx on an already-settled order -> `ok=False,
   error="already_settled"` (rejected — would double-license). Unknown
   order_id -> `ok=False, error="not_found"`.

7. **Partial failure (R8/AC-8)**: each item's `LicenseService.grant` is
   wrapped in try/except; a failed item is appended to `failures` with
   `error=str(exc)`, `retry=True`, and the loop CONTINUES (successes are
   honored). If `failures` non-empty -> `order.status=PARTIAL`,
   `BatchSettleResult.status="partial"` (ok=True — the call succeeded, just
   not every item). `BatchSettleResult.successes`/`failures` carry
   `BatchItemResult(item_id, asset_id, license_id, download_token,
   download_url, error, retry)`. Failure injection in tests: extend
   `FakeLicenseService(fail_on_asset_ids={...})` (additive kwarg added in
   SPEC-007) OR for replay tests use a real-License wrapper
   (`_FailingRealLicense` in test_batch_services.py — needed because
   FakeLicenseService returns in-memory objects that can't persist the
   `BatchItem.license` FK).

8. **Recipient rule (§6 edge, single-seller MVP)**: `settle_batch_order`
   verifies the on-chain payment against `resolve_pay_to(items[0].asset)` —
   the FIRST item's seller. Multi-creator batch distribution (different
   wallet per item) is OUT of MVP scope; documented as a §6 edge, do NOT
   block. SPEC-008 escrow/royalty may revisit this. `buyer_wallet` for the
   grant defaults to the on-chain `verification.sender` (the payer); an
   optional body `buyer_wallet` overrides it.

9. **Payment verification (R5/AC-7)** delegates to
   `SolanaService.verify_usdc_payment(tx, expected_recipient,
   expected_amount=order.total_usdc, mint)` — the integer min-units compare
   is INSIDE SolanaService (SPEC-004 §3), so BatchService passes the Decimal
   total and does NOT re-implement the compare. `is_valid=False` ->
   `BatchSettleResult(ok=False, status="invalid", error="invalid_settlement")`
   -> view returns 400. Order stays `quoted` on rejection.

10. **R9 observability**: per-item `PAYMENT_VERIFIED` is fanned out by
    `LicenseService.grant` itself (first-grant only); `settle_batch_order`
    ADDS one order-level `BATCH_SETTLED` (all-success) or `BATCH_PARTIAL`
    event via the EventRecorder, AND inserts one BigQuery `transactions` row
    per successful item (via `self.bigquery.insert` with
    `batch_item_index`). The idempotent replay path fires NEITHER (no
    duplicate audit rows).

11. **Money wire serialization**: the §6.1 batch response serializes
    `total_usdc` / `unit_price_usdc` as `str(Decimal)` at 6 dp
    (`"0.150000"`, `"0.050000"`) — consistent with SPEC-003/005's money
    convention. Clients must `decimal.Decimal(body["total_usdc"])`, NOT
    assume a clean number.

12. **Smoke test progression** (`tests/test_smoke.py`):
    `test_service_stubs_raise_not_implemented` progression log gained the
    SPEC-007 line, and a new sanity check calls
    `GeminiService().quote_batch([...], "commercial")` (offline -> rule
    fallback, returns 1 quote). The ONLY remaining NotImplementedError stub
    is still `RoyaltyService.distribute` (SPEC-008). `batch_negotiate` /
    `batch_settle` were promoted from `_stub("SPEC-007")` to real view
    functions.

SPEC-007 coverage: `apps/settlement/batch_services.py` 92% (remaining misses
are defensive `except` branches, the empty-order `_invalid_verification`
guard, the `_resolve_mint`/`_effective_batch_max_items` settings fallbacks,
and the `get_batch_service` factory body — consistent with SPEC-004's
treatment), `services/gemini_service.py` 94%, total services+settlement 95%.
SPEC-007 suite: 16 tests (13 SPEC-listed + 3 R10 edge-cases: unknown order,
partial replay, different-tx-on-settled). Total suite: 253 passed (237
baseline + 16). See [[veriproof-spec004-contracts]] for the settlement
pipeline reused by batch, [[veriproof-spec002-contracts]] for `resolve_pay_to`
+ `LicenseService.grant`, [[veriproof-spec003-contracts]] for the
`quote_batch` never-raises + rule-fallback pattern inherited from
`negotiate`, and [[veriproof-scaffold-done]] for venv/env conventions.
