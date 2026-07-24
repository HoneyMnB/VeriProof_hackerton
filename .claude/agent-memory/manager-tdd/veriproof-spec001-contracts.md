---
name: veriproof-spec001-contracts
description: SPEC-001 implementation contract decisions in VeriProof services that SPEC-002..008 must honor (AnalysisResult.degraded, AnchorFailed, view DI seam, smoke-test progression)
metadata:
  type: project
---

SPEC-001 (IP registration & on-chain anchoring) landed with several contract
decisions that downstream SPECs (002-008) must honor. These are non-obvious
and not derivable from the architecture doc alone.

**Why:** The scaffold (SPEC-000) fixed interface signatures but several
behavioral contracts had to be extended/aligned to make R13/R14 testable
offline; future SPECs that touch the same services will break if they assume
the original scaffold shape.

**How to apply:** Read before editing `services/`, `apps/ip/views_api.py`,
`tests/fakes.py`, or `tests/test_smoke.py`.

1. **`AnalysisResult` extended** (`services/_types.py`): added
   `degraded: bool = False` and widened `category: str | None`. R13 requires
   `analysis.degraded=true` on Gemini failure — the scaffold dataclass had no
   such field. Backward compatible (default value).

2. **`AnchorFailed` exception** lives in `services/solana_service.py`.
   `SolanaService.anchor_hash` retries 3x then raises it; the register view
   catches `AnchorFailed` → `status="draft"`, `anchor_tx_sig=None`, HTTP 202
   (R14). `FakeSolanaService.fail_anchor=True` raises `AnchorFailed` (NOT
   RuntimeError) so the view's single `except AnchorFailed` covers both.

3. **View dependency-injection seam** (`apps/ip/views_api.py`): the register
   view calls `get_gemini_service()` / `get_solana_service()` /
   `get_storage_service()` / `get_event_recorder()` / `get_image_processor()`
   (imported into the view module). Integration tests monkeypatch
   `apps.ip.views_api.get_<svc>` to return fakes. Future SPEC views should
   follow the same pattern.

4. **Gemini never raises for genai failures** — it returns
   `AnalysisResult(degraded=True)` after 3 retries. The view ALSO wraps
   `analyze_image` in try/except so `FakeGeminiService(fail_analyze=True)`
   (which raises RuntimeError) is caught → degraded. Belt-and-suspenders.

5. **Smoke test progression** (`tests/test_smoke.py`):
   `test_service_stubs_raise_not_implemented` was edited to DROP the
   `pytest.raises(NotImplementedError)` assertions for ImageProcessor,
   GeminiService.analyze_image, SolanaService.anchor_hash, StorageService
   (now implemented in SPEC-001). KmsSigner/License/Royalty/X402 stub
   assertions remain. Each future SPEC must similarly drop its own service's
   assertion when implementing.

6. **Asset UUID pre-generated** in the register view (`uuid.uuid4()` before
   `IpAsset.save()`) so storage artifacts can be keyed by asset_id in a
   single save (thumbnail_url/watermark_url are non-nullable).

7. **Real cloud-SDK paths** (genai.Client, solana Client/Transaction, GCS
   storage.Client construction) are import-guarded and marked
   `# pragma: no cover` — the SDKs are intentionally NOT installed in the TDD
   venv; those branches run only in cloud integration.

SPEC-001 services coverage: 100% on image_processor, gemini_service,
solana_service, storage_service, event_recorder. See [[veriproof-scaffold-done]]
for the venv/env conventions.
