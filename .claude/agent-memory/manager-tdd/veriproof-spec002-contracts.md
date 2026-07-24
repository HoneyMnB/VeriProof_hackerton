---
name: veriproof-spec002-contracts
description: SPEC-002 implementation contract decisions in VeriProof services that SPEC-003/004/008 must honor (resolve_pay_to SSOT, X402Service methods, LicenseService.is_licensed DB-first, view DI seam)
metadata:
  type: project
---

SPEC-002 (x402 access interceptor & client classification) landed with several
contract decisions that downstream payment-flow SPECs (003 negotiate, 004
settle, 008 escrow/royalty) must honor. These are non-obvious and not
derivable from the architecture doc alone.

**Why:** The scaffold + SPEC-001 fixed interface signatures but the payment
recipient rule, the DB-first license check, and the x402 envelope shape all
needed concrete implementation decisions to satisfy R5b/AC-7/AC-8/AC-9
testably offline. SPEC-003/004/008 will break if they re-implement the
recipient rule or call on-chain verify before checking the DB.

**How to apply:** Read before editing `services/_payment.py`,
`services/x402_service.py`, `services/license_service.py`,
`apps/ip/views_api.py`, or `tests/test_smoke.py`.

1. **`resolve_pay_to(asset, escrow_pubkey=None)`** in
   `services/_payment.py` is the SINGLE SOURCE OF TRUTH for "who gets paid"
   (architecture §8). SPEC-003 (negotiate ACCEPT `pay_address`), SPEC-004
   (settle `expected_recipient`), SPEC-008 (escrow `expected_recipient`) MUST
   reuse it — do NOT duplicate the `parent_asset -> escrow else creator`
   rule. It checks `asset.parent_asset_id` (FK column) not `parent_asset` to
   avoid a DB query; for non-Django stand-ins it falls back to
   `getattr(asset, "parent_asset")`. When `escrow_pubkey is None` it reads
   `settings.PLATFORM_ESCROW_PUBKEY` lazily. Exported via
   `services.resolve_pay_to`.

2. **`X402Service.__init__` gained a 4th kwarg** `escrow_pubkey: str | None =
   None` (additive, backward-compatible — not a rename). The factory
   `get_x402_service()` reads `settings.PLATFORM_ESCROW_PUBKEY` and passes it
   in. `classify_client`, `build_payment_required`, `build_solana_pay_fallback`
   are implemented in SPEC-002. `parse_payment_submitted` (SPEC-004) and
   `build_ap2_mandate` (SPEC-003) REMAIN NotImplementedError stubs.

3. **`classify_client(request_or_headers)`** accepts either a Django
   HttpRequest (reads `.headers`) OR a plain dict. Header lookup is
   case-insensitive. Decision order: `X-Agent-Protocol: x402` -> agent;
   `Accept: application/json` -> agent; `Accept: text/html` -> browser;
   ambiguous (`*/*` / missing) -> **agent** (conservative access control per
   §6 edge note).

4. **`build_payment_required(asset)` returns `(headers, body)`** matching
   architecture §3.1 EXACTLY. `max_amount_required` == `asset.target_price_usdc`
   (NOT min_price). `pay_to` / `X-Solana-Pay-Address` set via `resolve_pay_to`.
   Body fields: `error`, `asset_id`, `preview_url` (= `asset.watermark_url`),
   `x402_version: "1"`, `accepts: [{scheme, network, mint, pay_to,
   max_amount_required}]`, `how_to_negotiate: {endpoint, method:"POST",
   required_payload:{buyer_agent_id,offer_usdc,usage_type}, settle_endpoint}`.

5. **`build_solana_pay_fallback(asset)`** returns a 200 body for browser
   clients with `solana_pay: {address, mint, amount_usdc (== target_price),
   label, uri}`. The `uri` format is
   `solana-pay:{pay_to}?amount={target}&spl-token={mint}`.

6. **`LicenseService.is_licensed(asset, tx_sig)`** is DB-first (R10 / AC-7):
   if a `License` row exists for `(asset, tx_sig)` return True with ZERO
   on-chain calls. Only when no DB license AND a tx_sig is present does it
   call `SolanaService.verify_usdc_payment` lazily. The solana seam is
   `services.license_service.get_solana_service` (imported at MODULE LEVEL so
   `monkeypatch.setattr` works — do NOT move it back to a lazy in-function
   import or the test seam breaks). `LicenseService.grant` REMAINS
   NotImplementedError (SPEC-004).

7. **View DI seam extended**: `apps/ip/views_api.get_asset` (the SPEC-002
   interceptor) calls `get_x402_service()` / `get_license_service()` /
   `get_storage_service()` / `get_event_recorder()` (all imported into the
   view module). Integration tests monkeypatch `apps.ip.views_api.get_<svc>`
   — same pattern as SPEC-001's register view. URL route
   `ip/<uuid:asset_id>` was already wired (SPEC-000) and now points at the
   real `get_asset` instead of the `_stub("SPEC-002")`.

8. **Interceptor scope**: implemented as a VIEW FUNCTION on
   `GET /api/v1/ip/{asset_id}` (NOT global middleware). The SSOT §6 edge note
   constrains interception to this path only; register/negotiate/settle pass
   through untouched. `settings.MIDDLEWARE` still has the placeholder comment
   for X402InterceptorMiddleware — left as-is (a thin middleware that
   short-circuits this path is an acceptable alternative, but the view
   approach was chosen for being cleaner + testable).

9. **Smoke test progression** (`tests/test_smoke.py`):
   `test_service_stubs_raise_not_implemented` was edited to DROP the
   `pytest.raises(NotImplementedError)` assertions for
   `LicenseService.is_licensed` and add explicit assertions for the two
   remaining X402 stubs (`parse_payment_submitted`, `build_ap2_mandate`) plus
   `LicenseService.grant`. The SPEC-001 implemented-method sanity checks were
   preserved.

10. **402 HTTP_402 event payload** recorded via `EventRecorder.record(
    "HTTP_402", {"asset_id": str(asset.id), "buyer_hint": <X-Buyer-Agent-Id
    header or "">}, asset=asset)`. SPEC-003/004 that record their own events
    should use the same `{asset_id, ...}` shape for BigQuery consistency.

SPEC-002 services coverage: 100% on `_payment`, `x402_service`,
`license_service`. See [[veriproof-spec001-contracts]] for the SPEC-001
contracts and [[veriproof-scaffold-done]] for venv/env conventions.
