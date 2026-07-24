---
name: veriproof-spec004-contracts
description: SPEC-004 implementation contract decisions in VeriProof services that SPEC-005/007/008 must honor (settle_pipeline SSOT structure, PaymentVerification.commitment, SettlementResult shape, KmsSignerError, RoyaltyService stub-call-only, webhook HMAC + sync fallback, download token purge mapping)
metadata:
  type: project
---

SPEC-004 (Solana USDC settlement + license/certificate) landed with contract
decisions that downstream SPECs (005 transactions/listing, 007 batch, 008
royalty) must honor. These are non-obvious and not derivable from the
architecture doc alone.

**Why:** The pipeline SSOT had to be structured so the sync ``/settle`` view,
the pay.sh webhook sync fallback, and GCP Workflows all call the SAME service
method with no logic duplication. The recipient rule (§8), the integer
min-units amount compare, the decoupled certificate failure (R16), and the
idempotency key all needed concrete decisions testable offline. SPEC-005/007/008
will break if they re-derive these or assume the old stub shapes.

**How to apply:** Read before editing `apps/settlement/services.py`,
`apps/settlement/views_api.py`, `services/solana_service.py`,
`services/license_service.py`, `services/kms_signer.py`,
`services/x402_service.py`, `services/pubsub_publisher.py`,
`services/firestore_mirror.py`, `services/bigquery_sink.py`, or
`tests/test_smoke.py`.

1. **Settlement pipeline SSOT = `apps/settlement/services.py::SettlementService.settle_pipeline`**.
   This is the SINGLE settlement-logic path. The ``/settle`` view AND the
   pay.sh webhook sync fallback both call it. GCP Workflows calls the SAME
   service methods in the SAME order (verify -> grant -> cert -> firestore ->
   bigquery -> royalty[2nd-creation] -> CERT_ISSUED event). DI seam: tests
   monkeypatch `apps.settlement.views_api.get_settlement_service` (the view
   factory). The service constructor defaults each dependency to its settings
   factory but accepts injected fakes. Do NOT add a second settlement path.

2. **`SettlementResult` dataclass** (`apps/settlement/services.py`): `ok: bool`,
   `status: "SUCCESS"|"INVALID"`, `license`, `certificate_tx: str|None`,
   `download_url: str|None` (shaped `/files/{token}`), `download_expires_at`,
   `error: str|None`. Invalid verification -> `ok=False, error="invalid_settlement"`
   (R3) WITHOUT granting a license. Certificate failure (R16) -> `ok=True,
   certificate_tx=None` (license kept). Workflows/Views project this to the §6.3
   envelope.

3. **`PaymentVerification` extended** (`services/_types.py`): added
   `commitment: str | None = None`. Backward-compatible (SPEC-002 tests
   construct without it). `SolanaService.verify_usdc_payment` sets it from the
   on-chain observation so callers distinguish "unconfirmed" from "mismatch".
   Amount compared in **integer min-units (6 decimals)** via
   `SolanaService._to_min_units()` (architecture §8); `1.5 USDC == 1_500_000`.
   Commitment floor = `{confirmed, finalized}`.

4. **`SolanaService` verify client seam**: the injected client exposes
   `get_payment(tx_sig) -> dict|None` returning `{recipient, mint,
   amount_min_units(int), sender, slot, commitment}`. Real RPC parse
   (`_parse_rpc_payment`) is import-guarded `# pragma: no cover`. This mirrors
   anchor_hash's `send_memo` seam. `verify_usdc_payment` raises
   `VerifyUnavailable` (NEW exception in solana_service) when no client; returns
   `is_valid=False` for on-chain mismatches.

5. **`CertificateIssueError`** (NEW in solana_service.py): raised by
   `issue_certificate` AND `transfer_usdc` on failure. `FakeSolanaService` with
   `fail_issue_cert=True` raises the REAL `CertificateIssueError` (mirrors how
   `fail_anchor` raises the real `AnchorFailed`). The pipeline catches it (R16).
   `_signer_pubkey` now has a broad `except Exception -> AnchorFailed` so an
   unconfigured `KmsSigner` (which raises `KmsSignerError`, not
   `NotImplementedError`) still degrades anchor_hash cleanly.

6. **`KmsSignerError`** (NEW in kms_signer.py): raised at call time (not
   construction) when no key configured OR solders/base58 missing. Replaces the
   old `NotImplementedError`. Local-fallback derivation path is import-guarded
   (solders not in TDD env). Cloud KMS path is `# pragma: no cover`. SPEC-005+
   that touch signing MUST inject a fake signer via the `signer` ctor kwarg.

7. **`LicenseService.grant` IMPLEMENTED** (R4/R5/R7/R8/R15): idempotent on
   `payment_tx_sig` (unique) — `License.objects.filter(payment_tx_sig=...).first()`
   short-circuits, returns existing unchanged. First grant generates
   `download_token = secrets.token_urlsafe(24)` + `download_expires_at = now +
   DOWNLOAD_TOKEN_TTL_SECONDS`. Links `session` if provided. Records
   `PAYMENT_VERIFIED` event ONLY on first grant (not on idempotent replay).
   `LicenseService.__init__` gained 2nd kwarg `event_recorder=None` (additive);
   `get_license_service()` wires the real recorder.

8. **`X402Service.parse_payment_submitted` IMPLEMENTED**: parses a2a-x402 body
   into `SubmittedPayment`. Required: `tx_signature`, `buyer_wallet` (both str).
   Optional `amount_usdc` (str|number -> Decimal). Unknown fields preserved in
   `SubmittedPayment.extra`. Raises `InvalidPaymentSubmitted` (NEW in
   x402_service.py) on missing/non-object/non-numeric-amount. When no amount,
   defaults to `Decimal("0")` (settlement resolves the real expected amount
   from session/asset).

9. **`RoyaltyService.distribute` STAYS A STUB** (owned by SPEC-008). The
   pipeline CALLS it as step F ONLY when `asset.parent_asset_id is not None`
   (R14b). The pipeline swallows BOTH `NotImplementedError` (SPEC-008 not
   landed) AND generic Exception from distribute (settlement must not abort on
   royalty failure). Tests verify the CALL via `FakeRoyaltyService` (NEW in
   fakes.py) — assert called for 2nd-creation, NOT called for standalone.
   SPEC-008 must keep the `distribute(license) -> [RoyaltyDistribution]`
   signature the pipeline relies on.

10. **PubSub / Firestore / BigQuery implemented** (no-op-when-disabled):
    `PubSubPublisher.publish` returns `str|None` — **None signals "disabled"**
    so the webhook triggers the sync fallback. `FirestoreMirror.set` /
    `BigQuerySink.insert` return None when disabled OR when SDK missing
    (graceful degrade). Each has an injected-`client` seam:
    pubsub-client exposes `publish(topic, message)`, firestore-client exposes
    `collection().document().set()`, bigquery-client exposes
    `insert_rows_json(table, rows)`. Cloud SDK paths are `# pragma: no cover`.

11. **pay.sh webhook** (`apps/settlement/views_api.paysh_webhook`): R12 HMAC —
    `X-PaySh-Signature` hex vs `hmac.new(PAYSH_WEBHOOK_SECRET, body, sha256)`,
    constant-time compared via `hmac.compare_digest`. **Fail-closed: no secret
    configured -> 401** (never accept forged payloads). R13: valid sig ->
    publish to Pub/Sub + immediate 200 (non-blocking). When publish returns
    None (PubSub disabled/local) -> `_sync_fallback_settle` runs
    `settle_pipeline` directly (R17 idempotency via License.payment_tx_sig
    unique). R17: replayed tx -> no duplicate license.

12. **Download route** (`GET /files/{token}`): implemented in
    `apps/settlement/views_api.download` (License is a settlement model) but
    ROUTED from `apps/ip/urls_web.py` (architecture §6.5 root path, no
    /api/v1/ prefix). R9/R10/R11: unknown/expired token -> 403 (`invalid_token`
    / `expired_token`), purged original -> 410 (`purged`), valid -> streamed
    bytes via `StorageService.read_temporary` (NEW method on StorageService +
    FakeStorageService). `freezegun` drives the expiry test.

13. **Certificate route** (`GET /api/v1/ip/{asset_id}/certificate/{cert_id}`):
    implemented in `apps/ip/views_api.get_certificate` (was a `_stub`).
    `cert_id` == `License.certificate_tx_sig`. 200 payload = `{asset_id,
    certificate_tx, payment_tx_sig, buyer_wallet, usage_type, price_usdc,
    granted_at}` — **excludes original bytes + download_token**.

14. **Smoke test progression** (`tests/test_smoke.py`):
    `test_service_stubs_raise_not_implemented` now asserts ONLY
    `RoyaltyService.distribute` raises NotImplementedError (SPEC-008 owns it).
    All SPEC-004 methods have sanity checks instead: KmsSigner unconfigured ->
    `KmsSignerError`; X402 parse_payment_submitted({}) ->
    `InvalidPaymentSubmitted`; PubSubPublisher().publish(...) -> None. License
    `event_recorder` is now wired in `get_license_service()`.

SPEC-004 services coverage: solana_service 95%, license_service 94%,
kms_signer 90%, x402_service 96%, pubsub/firestore/bigquery 100%,
apps/settlement/services (pipeline) 93%, apps/settlement/views_api 85%.
Total suite: 205 tests (134 baseline + 71 SPEC-004). See
[[veriproof-spec003-contracts]] for SPEC-003, [[veriproof-spec002-contracts]]
for SPEC-002, [[veriproof-spec001-contracts]] for SPEC-001, and
[[veriproof-scaffold-done]] for venv/env conventions.
