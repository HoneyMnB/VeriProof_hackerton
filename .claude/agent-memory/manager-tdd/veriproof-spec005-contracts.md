---
name: veriproof-spec005-contracts
description: SPEC-005 implementation contract decisions for the IP library dashboard (apps/ip/dashboard.py pure SSOT, transactions merge shape, events polling boundary, frontend Python-mirror testing approach) that SPEC-006 sandbox + future UI work must honor
metadata:
  type: project
---

SPEC-005 (IP library + on-chain certificate dashboard, Page 1+2) landed with
contract decisions that SPEC-006 (sandbox — consumes the shared `/events`
endpoint) and any future frontend work must honor. These are non-obvious and
NOT derivable from the architecture doc alone.

**Why:** The dashboard mixes server-rendered Django templates with vanilla JS.
To keep the pure UI logic (Explorer URL, certificate QR payload, preview
toggle, polling-vs-Firestore decision, analysis-card render) testable OFFLINE
without a Node/jsdom toolchain (this project has NO package.json — see
[[veriproof-scaffold-done]]), the pure logic lives in ONE Python SSOT that the
JS mirrors 1:1. SPEC-006+ must reuse these contracts, not re-derive them, or
the offline test guarantee breaks.

**How to apply:** Read before editing `apps/ip/dashboard.py`,
`apps/ip/views_web.py`, `apps/ip/views_api.py` (transactions/asset_list/events),
`static/js/dashboard.js`, `static/js/library.js`, `static/js/workspace.js`, or
`templates/{workspace,library}.html`.

1. **Pure SSOT = `apps/ip/dashboard.py`** (100% covered). Contains: `explorer_url`,
   `build_certificate_payload`, `preview_src`, `should_poll_events`,
   `analysis_card_fields`. `static/js/dashboard.js` mirrors ALL of these on
   `window.VP` (same names in camelCase). When changing one, change BOTH. The
   pytest suite (`tests/unit/test_dashboard.py`) pins the contract.

2. **`explorer_url(anchor_tx_sig, cluster="devnet") -> str | None`** (R7/AC-6):
   returns `None` for empty/None `anchor_tx_sig` (draft assets — Explorer button
   DISABLED, architecture §6 edge). Format: `https://explorer.solana.com/tx/<sig>?cluster=<cluster>`.
   DEFAULT_CLUSTER = "devnet".

3. **`build_certificate_payload(asset, certificate_tx_sig=None) -> dict`** (R8/AC-7):
   duck-typed `asset` needs `.id`, `.image_sha256`, `.anchor_tx_sig`,
   `.creator.wallet_address`. Returns ONLY `{asset_id, image_sha256,
   anchor_tx_sig, certificate_tx_sig, creator_wallet, explorer_url}`. EXCLUDES
   original bytes/url AND CDN thumbnail/watermark urls (QR = proof data only).
   The view computes `certificate_tx_sig` from the LATEST License
   (`License.objects.filter(asset=asset).order_by("-granted_at").first()`).

4. **`GET /api/v1/ip/{asset_id}/transactions`** (R9/AC-8): implemented in
   `apps/ip/views_api.transactions` (was a `_stub`). Returns `{asset_id, items}`
   where items merge `License` + `AgentEvent`, **stable time-ascending** by
   `timestamp` (ISO-8601). Each item has `kind`: license entry =
   `{kind:"license", timestamp, license_id, buyer_wallet, price_usdc, usage_type,
   payment_tx_sig, certificate_tx_sig}`; event entry = `{kind:"event", timestamp,
   type, payload}`. 404 for unknown asset_id. NO original bytes/url in payload.

5. **`GET /api/v1/events?asset_id=&since=<iso8601>`** (R10/AC-9, SHARED with
   SPEC-006): implemented in `apps/ip/views_api.events` (was a `_stub` owned by
   SPEC-006). Returns `{items}` of AgentEvents. `since` filter is **strictly
   greater (`created_at__gt=since`)** — the incremental-polling boundary.
   Without `since`, returns all events for the asset oldest-first. `_parse_iso8601`
   handles trailing `Z`. `asset_id` is OPTIONAL (filter only if provided) so
   SPEC-006 can reuse for a global feed. `freezegun` drives the boundary test.

6. **`GET /api/v1/assets?creator=<wallet>`** (R11): implemented in
   `apps/ip/views_api.asset_list` (was a `_stub`). Returns `{items}` filtered by
   `creator__wallet_address`. Each item EXCLUDES original bytes/url (R8 edge).
   No `creator` param = all assets (unfiltered).

7. **`/library` web view** (`apps/ip/views_web.library`): accepts `creator` OR
   `wallet` query param (alias), renders ONLY that creator's assets
   (`IpAsset.objects.filter(creator__wallet_address=wallet)`). Pre-computes a
   per-asset card dict via `_asset_card(asset)` including `explorer_url` +
   `certificate` payload, injected into template context. §6 edge access control
   is wallet-param minimum (full wallet-sig/session deferred post-hackathon).
   Empty states: no wallet -> prompt; wallet + 0 assets -> `no-assets` marker.

8. **Frontend test strategy (NO browser tooling)**: the 4 SPEC §5 frontend tests
   are covered as (a) Python mirrors of the pure JS logic in
   `tests/unit/test_dashboard.py` (preview_src, should_poll_events,
   analysis_card_fields) + (b) an integration contract test
   (`test_dragdrop_register_response_renders_card`) that hits the REAL
   `/register` endpoint with fakes and feeds the JSON into `analysis_card_fields`.
   Documented gap: NO real DOM/browser test (no jsdom/Playwright installed and
   SPEC forbids installing heavy browser tooling). The data CONTRACTS the JS
   consumes are fully tested.

9. **`should_poll_events(firestore_enabled, firebase_sdk_present) -> bool`**
   (R10/AC-9): returns `not (firestore_enabled AND firebase_sdk_present)`.
   library.js wires this: Firestore `onSnapshot` ONLY when both true; otherwise
   `/api/v1/events` polling every 2s. Firebase JS SDK is OPTIONAL/guarded —
   `subscribeFirestore` try/catch falls back to polling if SDK absent at runtime
   (the offline default). Pure decision is the Python SSOT.

10. **Certificate QR fallback**: `static/js/dashboard.js::renderQr` renders a
    scannable text block (proof URL + asset_id + sha256 prefix) because the
    hackathon offline constraint forbids a vendored QR library / build step. An
    injected `window.VP.__encodeQr(payload)` override (progressive enhancement)
    takes precedence if a future vendored encoder is added.

SPEC-005 coverage: dashboard.py 100%, views_web.py 100%, views_api.py 92%
(uncovered lines are SPEC-001..004 error paths, not SPEC-005). Total suite:
226 tests (220 SPEC-001..005 + 6 smoke). See [[veriproof-spec004-contracts]]
for SPEC-004, [[veriproof-spec003-contracts]] for SPEC-003,
[[veriproof-spec002-contracts]] for SPEC-002, [[veriproof-spec001-contracts]]
for SPEC-001, and [[veriproof-scaffold-done]] for venv/env conventions
(`../.venv/bin/python manage.py ...` / `../.venv/bin/python -m pytest`).
