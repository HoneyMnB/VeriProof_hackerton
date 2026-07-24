---
name: veriproof-spec008-contracts
description: SPEC-008 implementation contract decisions for RoyaltyService.distribute (compute_split pure helper, integer min-units split, per-leg idempotency via RoyaltyDistribution status, partial-failure isolation, solana=None degrade, same-wallet combine, ROYALTY_SPLIT payload, smoke-test closing gate) — FINAL SPEC, all service stubs eliminated
metadata:
  type: project
---

SPEC-008 (2차 창작 로열티 자동 분배 / Scenario 3) landed as the FINAL SPEC.
`RoyaltyService.distribute` — the last remaining `services/` stub — is now
implemented. Zero `raise NotImplementedError` remain in `services/`; the
suite closing gate (`tests/test_smoke.py::test_all_services_implemented`)
asserts every architecture-§4 service method is implemented.

**Why:** The escrow royalty split had to (a) guarantee `original + secondary ==
total` with zero fractional loss across all bps values (including 3333/9999),
(b) never abort the settlement pipeline step F on a transfer failure, (c) stay
offline-testable via the existing FakeSolanaService.transfer_usdc seam, and
(d) keep the settle_pipeline call site (SPEC-004 R14b) unchanged. The
non-obvious decisions below bind any future change to royalty distribution.

**How to apply:** Read before editing `services/royalty_service.py`,
`apps/settlement/services.py` (step F), `tests/test_smoke.py`, or
`tests/unit/test_settlement_coverage.py`.

1. **`RoyaltyService.compute_split(price_usdc, bps)` is a pure `@staticmethod`**
   extracted so the integer-min-units math is unit-testable with NO db. It
   returns `(original, secondary)` Decimals quantized to 6dp. Formula:
   `total_min = int((price * 1_000_000).to_integral_value(ROUND_HALF_EVEN))`;
   `original_min = total_min * bps // 10000`; `secondary_min = total_min -
   original_min` (remainder always to secondary / the seller). Convert back via
   `/1_000_000` + `.quantize(Decimal("0.000001"))`. Constants `_USDC_DECIMALS=6`,
   `_QUANTUM`, `_MIN_PER_USDC` live at module scope. Do NOT recompute the split
   inline in `distribute` — call the helper.

2. **`RoyaltyService.__init__(solana=None, event_recorder=None)`** gained a 2nd
   kwarg `event_recorder` (additive, backward-compatible). `get_royalty_service()`
   now wires both `get_solana_service()` AND `get_event_recorder()`. distribute
   uses the injected recorder; if None it lazily fetches `get_event_recorder()`.

3. **`distribute(license) -> [RoyaltyDistribution]` contract**: reads
   `license.asset.parent_asset` (1-level only, R10 — grandparent chain ignored)
   + `license.asset.royalty_share_bps`. Recipients: original ->
   `parent.creator.wallet_address`; secondary -> `license.asset.creator.wallet_address`
   (the 2nd-creator/seller). Price base = `license.price_usdc` (falls back to
   `asset.target_price_usdc` if missing). NEVER re-raises on transfer failure
   (records `failed` legs instead) so settle_pipeline step F never aborts.

4. **Per-leg idempotency via RoyaltyDistribution status filter** (architecture
   8): `_settle_leg` first checks
   `RoyaltyDistribution.objects.filter(license=license, role=role,
   status=SETTLED).first()`; if present, returns it WITHOUT re-transferring (no
   double-pay on replay). Failed/pending legs are always re-attempted (retry
   path). There is NO unique constraint on the royalty rows — idempotency is
   query-based, distinct from License.payment_tx_sig unique.

5. **Partial-failure isolation (R9/AC-7)**: `_settle_leg` wraps
   `self.solana.transfer_usdc(wallet, amount)` in `except Exception` (broad —
   catches both the real `CertificateIssueError` and the fake `RuntimeError`).
   On failure: `status=FAILED`, `transfer_tx_sig` left None, `amount_usdc`
   still recorded (the intended split), `logger.warning`. The sibling leg is
   untouched. Tests inject per-wallet failure by subclassing FakeSolanaService
   (see `tests/unit/test_royalty_service.py::_PartialFailSolana`); the shared
   `FakeSolanaService.fail_transfer` flag fails ALL legs (still used for the
   whole-distribute-failure case elsewhere).

6. **`solana=None` degrade path**: when `RoyaltyService()` is built with no
   solana (e.g. the old stub-swallow test), `_settle_leg` records both legs as
   `FAILED` and does NOT raise. This is what lets
   `test_pipeline_royalty_without_solana_does_not_abort` (renamed from the old
   `test_pipeline_royalty_not_implemented_is_swallowed`) still pass with
   `result.ok is True` now that distribute is implemented.

7. **§6 same-wallet edge**: if `original_wallet == secondary_wallet`, the two
   shares collapse into ONE combined leg (role=SECONDARY, amount = original +
   secondary) and ONE transfer_usdc of the full total. Documented in the
   distribute docstring. Tested by `test_distribute_same_wallet_combines_into_single_transfer`.

8. **ROYALTY_SPLIT event payload shape (R7/AC-8)**:
   `{asset_id, license_id, parent_asset_id, royalty_share_bps, total_usdc
   (quantized 6dp string), legs: {original: {recipient_wallet, amount_usdc,
   transfer_tx_sig, status}, secondary: {...}}}`. `total_usdc` MUST be
   `.quantize(_QUANTUM)` before str-ifying — the raw `license.price_usdc`
   Decimal may carry fewer places (e.g. "10.0" vs canonical "10.000000").
   Fan-out failure is swallowed (logger.warning).

9. **Smoke-test closing gate RENAMED**: `test_service_stubs_raise_not_implemented`
   -> `test_all_services_implemented` in `tests/test_smoke.py`. The old
   `with pytest.raises(NotImplementedError): RoyaltyService().distribute(None)`
   block is replaced by a `RoyaltyService.compute_split(Decimal("10"), 3000)`
   sanity check (pure helper, no DB). The progression-log comment lists all 8
   SPECs. `services/__init__.py` module docstring also updated (no longer claims
   scaffold stubs).

10. **settle_pipeline call site UNCHANGED** (`apps/settlement/services.py` step
    F): still `self.royalty_service.distribute(granted)` inside
    `if asset.parent_asset_id is not None:`. Its `except NotImplementedError`
    branch is now dead code (distribute never raises NIE) but kept defensive
    alongside the generic `except Exception`. No edit needed.

SPEC-008 coverage: `services/royalty_service.py` 96% (3 uncovered lines are
defensive: the `price_usdc`-None fallback + the ROYALTY_SPLIT fan-out
except). Full suite: 283 tests (259 baseline + 24 SPEC-008) GREEN in 0.69s;
total `services`+`apps` coverage 94%. See [[veriproof-spec007-contracts]] for
the prior SPEC, [[veriproof-spec004-contracts]] for the settle_pipeline SSOT
this hooks into, and [[veriproof-scaffold-done]] for env conventions.
