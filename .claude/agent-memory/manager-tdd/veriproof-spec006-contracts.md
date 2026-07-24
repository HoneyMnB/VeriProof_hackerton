---
name: veriproof-spec006-contracts
description: SPEC-006 implementation contract decisions for the sandbox simulator (SandboxRunner RequestFactory + view-func orchestration, MockSolanaService app-code backend, mock-mode factory patching, sandbox_feed vs AgentEvents split, event_pane SSOT) that SPEC-007/008 + future UI work must honor
metadata:
  type: project
---

SPEC-006 (Multi-Agent negotiation sandbox simulator, Page 3) landed with
contract decisions that downstream SPECs (007 batch, 008 royalty) and any
future UI/event-stream work must honor. These are non-obvious and NOT
derivable from the architecture doc alone.

**Why:** The sandbox is an ORCHESTRATOR + VISUALIZER, never a parallel
implementation of negotiate/settle. It replays the full agent flow
(`GET /ip/{id}` 402 → `POST /negotiate` → `POST /settle`) through the REAL
view functions so the SSOT contracts (settle_pipeline, NegotiationEngine,
resolve_pay_to) are exercised unchanged. Re-deriving any of these breaks the
"single settlement path" invariant from [[veriproof-spec004-contracts]].

**How to apply:** Read before editing `apps/sandbox/services.py`,
`apps/sandbox/views_api.py`, `apps/sandbox/dashboard.py`,
`static/js/sandbox.js`, `templates/sandbox.html`, or
`scripts/buyer_agent_sim.py`.

1. **`SandboxRunner.run_simulation` (apps/sandbox/services.py) is the SSOT
   orchestrator.** It calls the view FUNCTIONS directly via
   `django.test.RequestFactory` (NOT `django.test.Client`, NOT a parallel
   service copy): `apps.ip.views_api.get_asset`,
   `apps.negotiation.views_api.negotiate`, `apps.settlement.views_api.settle`.
   Each view runs its full logic (validation, session create, event recording,
   settle_pipeline). The runner only observes responses + enriches the stream.
   DI seam: tests inject fakes via the constructor (`solana=`,
   `negotiation_engine=`, `firestore=`); the `/sandbox/run` view uses the
   `get_sandbox_runner()` factory (monkeypatch
   `apps.sandbox.views_api.get_sandbox_runner`).

2. **Mock-mode factory patching (R10).** In `SANDBOX_MODE=mock` (default) the
   runner patches TWO view-level factories for the duration of the run via
   `contextlib.ExitStack` + `unittest.mock.patch` (NOT `mock.ExitStack` —
   that does NOT exist; use `contextlib.ExitStack`):
   - `apps.settlement.views_api.get_settlement_service` → `SettlementService(solana=<mock>)`
   - `apps.negotiation.views_api.get_negotiation_engine` → `NegotiationEngine(gemini=None)` (deterministic rule fallback)
   The `live` branch applies NO patches and uses the real factories (guarded;
   full Devnet wiring is out of scope). `mock.patch(target, new=lambda: obj)`
   replaces the module attr with a callable the view invokes.

3. **`MockSolanaService` is APP CODE** (apps/sandbox/services.py), NOT a test
   fake. It implements the real SolanaService interface
   (`verify_usdc_payment` / `issue_certificate` / `transfer_usdc`) returning
   deterministic valid results + `mock_*` signatures with NO network. It is
   the default the `get_sandbox_runner()` factory wires so the `/sandbox/run`
   endpoint runs fully offline. Tests inject `tests.fakes.FakeSolanaService`
   (recording) instead. Both implement the same interface. Do NOT import test
   fakes from app code.

4. **Event split: AgentEvents vs sandbox_feed.** The VIEWS' own
   `get_event_recorder()` persists the canonical `AgentEvent` rows to
   PostgreSQL (HTTP_402 from get_asset, ACCEPT/COUNTER/OFFER from negotiate,
   PAYMENT_VERIFIED + CERT_ISSUED from settle_pipeline). These are what the
   polling fallback `/api/v1/events?asset_id=&since=` serves (SHARED with
   SPEC-005 — see [[veriproof-spec005-contracts]] point 5). The runner
   ADDITIONALLY pushes richer display docs to the Firestore `sandbox_feed`
   collection (pane/message/mock badge/detail) for the live 3-pane UI. The
   runner does NOT re-record the canonical events (no duplication). On R9
   failure the runner records a `SIMULATION_FAILED` AgentEvent (novel type,
   varchar) so BOTH feeds surface the failure.

5. **`SimulationResult` dataclass**: `run_id`, `asset_id`, `ok`, `steps`
   (ordered sandbox_feed doc list), `status` ("SUCCESS"|"FAILED"),
   `session_id`, `payment_tx_sig`, `certificate_tx`, `download_url`, `error`,
   `mock`. `payment_tx_sig` is the deterministic mock sig
   (`mock_tx_<run_id[:8]>` in mock mode). The `/sandbox/run` view returns 200
   for BOTH success and surfaced-failure (the run executed; `ok`/`status`
   distinguish); 404 only for a missing asset; 422 for bad input.

6. **`apps/sandbox/dashboard.py` is the pure frontend SSOT** (100% covered),
   mirrored 1:1 by `static/js/sandbox.js` on `window.VP` (extends the SPEC-005
   VP namespace). Functions: `event_pane(type)` → "seller"|"buyer"|"inspector"
   (HTTP_402/PAYMENT_VERIFIED/CERT_ISSUED/SIMULATION_FAILED→inspector;
   OFFER/ACCEPT→buyer; COUNTER→seller; unknown→inspector), `inspector_events`,
   `should_poll_events(firestore_enabled, firebase_sdk_present)` (same formula
   as SPEC-005), `mock_badge(mock)`, `explorer_url` (re-export from
   `apps.ip.dashboard`). When changing one, change BOTH.

7. **Frontend test strategy (NO browser tooling)**: the 3 SPEC-006 §5 frontend
   tests are Python mirrors of the JS pure logic in
   `tests/unit/test_sandbox_dashboard.py` (event_pane routing, inspector
   ordering + explorer_url, polling fallback + mock badge). Same documented
   gap as SPEC-005: no jsdom/Playwright (no package.json). The data CONTRACTS
   the JS consumes are fully tested.

8. **`scripts/buyer_agent_sim.py`** is a standalone stdlib-only CLI (urllib,
   no requests/x402_a2a dependency) implementing the x402 3-step flow
   (GET→negotiate→settle) inline over HTTP against a running server. It is a
   demo/E2E driver, NOT imported by the app. `--mock` (default) sends a
   `mock_tx_*` signature; `--live` sends a real-looking sig (server must be
   SANDBOX_MODE=live). Exit codes: 0 success, 1 step failed, 2 network/usage.

9. **Failure handling (R9)**: any step non-success (negotiate non-ACCEPT
   incl. COUNTER/REJECT, settle non-200, view crash) → runner records
   `SIMULATION_FAILED` AgentEvent + a `step="failure"` sandbox_feed doc and
   ABORTS subsequent steps (partial-state visibility). The crash path
   wraps the whole GET→negotiate→settle sequence in try/except so a view
   exception becomes a surfaced failure, never a 500.

SPEC-006 coverage: apps.sandbox 94% (dashboard.py 100%, services.py 94%,
views_api.py 90%). Total suite: 243 tests (226 SPEC-001..005 + 1 + 16
SPEC-006... 226 baseline + 17 SPEC-006 new). See
[[veriproof-spec005-contracts]] for the shared `/events` endpoint,
[[veriproof-spec004-contracts]] for settle_pipeline SSOT, and
[[veriproof-scaffold-done]] for venv/env conventions
(`../.venv/bin/python manage.py ...` / `../.venv/bin/python -m pytest`).
