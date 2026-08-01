"""SPEC-006 sandbox simulator — replays the full agent flow end-to-end.

``SandboxRunner.run_simulation`` orchestrates the complete buyer-agent journey
through the REAL view functions (architecture 6.1 / SPEC-006 R2):

    1. ``GET /api/v1/ip/{asset_id}`` (agent headers)  -> observe the 402 paywall
    2. ``POST /api/v1/ip/{asset_id}/negotiate``        -> buyer offer -> ACCEPT
    3. ``POST /api/v1/ip/{asset_id}/settle``           -> verified settlement

The runner is an ORCHESTRATOR + VISUALIZER only: it calls the SAME view
functions / services the production M2M paths use (via ``RequestFactory``), so
there is zero duplicated negotiate/settle logic. At each step it:

- observes the view's HTTP response (status / headers / body),
- relies on the views' own ``EventRecorder`` to persist ``AgentEvent`` rows
  (HTTP_402 / ACCEPT / PAYMENT_VERIFIED / CERT_ISSUED) to PostgreSQL — these
  are the events the polling fallback (``/api/v1/events``) serves, and
- pushes a richer display doc to the Firestore ``sandbox_feed`` collection
  (pane / message / detail) for the 3-pane live UI (R3).

이 모듈은 실제 서비스 팩토리만 호출한다. 테스트 더블은 테스트 코드에서만
팩토리 경계에 주입한다.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from apps.ip.dashboard import explorer_url

logger = logging.getLogger(__name__)


# === Result types ===========================================================


@dataclass
class SimulationResult:
    """Outcome of ``SandboxRunner.run_simulation`` (SPEC-006 R1/R2).

    ``steps`` is the ordered sandbox_feed doc list consumed by the 3-pane UI.
    ``ok=False`` means a real pipeline step failed and the flow aborted.
    """

    run_id: str
    asset_id: str
    ok: bool
    steps: list[dict] = field(default_factory=list)
    status: str = "SUCCESS"  # "SUCCESS" | "FAILED"
    session_id: str | None = None
    payment_tx_sig: str | None = None
    certificate_tx: str | None = None
    download_url: str | None = None
    error: str | None = None


# === The runner =============================================================


class SandboxRunner:
    """Orchestrates the buyer-agent flow through the real views (R2).

    Firestore와 이벤트 기록기는 관찰용이다. 결제·협상은 실제 HTTP 경로의
    서비스 팩토리를 사용하며, 앱 코드에는 테스트용 블록체인 구현이 없다.
    """

    def __init__(
        self,
        *,
        firestore: Any = None,
        event_recorder: Any = None,
        request_factory: Any = None,
    ) -> None:
        from services.event_recorder import get_event_recorder
        from services.firestore_mirror import get_firestore_mirror

        self.firestore = (
            firestore if firestore is not None else get_firestore_mirror()
        )
        self.event_recorder = (
            event_recorder if event_recorder is not None else get_event_recorder()
        )
        # RequestFactory builds HttpRequest objects we hand to the view funcs
        # directly (no middleware / nested-Client overhead).
        if request_factory is None:  # pragma: no cover (import only)
            from django.test import RequestFactory

            request_factory = RequestFactory()
        self._factory = request_factory

    # --- Public API ---------------------------------------------------------

    def run_simulation(
        self,
        *,
        asset_id: str,
        buyer_agent_id: str,
        initial_offer_sol: Any,
        usage_type: str,
        payment_tx_sig: str,
        buyer_wallet: str,
    ) -> SimulationResult:
        """Run the 402 -> negotiate -> settle flow. R1/R2/R3/R9/R10."""
        from apps.ip.models import IpAsset

        run_id = uuid.uuid4().hex
        asset_uuid = uuid.UUID(str(asset_id))
        asset = IpAsset.objects.filter(id=asset_uuid).first()
        if asset is None:
            # Missing asset -> fail fast (the /sandbox/run view maps this to 404).
            state = _RunState(run_id=run_id, asset_id=str(asset_id))
            self._push(state, "failure", "inspector", "SIMULATION_FAILED",
                       "asset not found", {"status": 404}, failed=True)
            return SimulationResult(
                run_id=run_id, asset_id=str(asset_id), ok=False,
                steps=state.steps, status="FAILED",
                error="asset_not_found",
            )

        state = _RunState(
            run_id=run_id,
            asset_id=str(asset_id),
            submitted_payment_tx_sig=payment_tx_sig,
            buyer_wallet=buyer_wallet,
        )

        try:
            self._step_get(asset, asset_uuid, state)
            if state.aborted:
                return self._result(state, session_id=None)
            self._step_negotiate(
                asset, asset_uuid, buyer_agent_id,
                initial_offer_sol, usage_type, state,
            )
            if state.aborted:
                return self._result(state, session_id=None)
            self._step_settle(asset, asset_uuid, state)
        except Exception as exc:  # noqa: BLE001 - show an actual pipeline failure
            logger.error("sandbox simulation crashed asset_id=%s error=%s", asset_id, exc)
            self._fail(state, "inspector", "SIMULATION_FAILED",
                       f"simulation crashed: {exc}", {"error": str(exc)},
                       asset=asset, stage="crash")
        return self._result(state, session_id=state.session_id)

    # --- Steps --------------------------------------------------------------

    def _step_get(self, asset, asset_uuid, state):
        """Step 1: agent GET -> observe native SOL payment terms."""
        from apps.ip.views_api import get_agent_sol_payment_terms

        request = self._factory.get(
            f"/api/v1/ip/{asset.id}/agent-sol-payment",
            HTTP_X_AGENT_PROTOCOL="x402",
            HTTP_ACCEPT="application/json",
            HTTP_X_BUYER_AGENT_ID="sandbox-buyer",
        )
        resp = get_agent_sol_payment_terms(request, asset_id=asset_uuid)
        if resp.status_code == 404:
            self._fail(state, "inspector", "SIMULATION_FAILED",
                       "asset not found during GET", {"status": 404},
                       asset=asset, stage="get")
            return
        body = _decode_json(resp)
        headers = {k: v for k, v in resp.items()}
        # R3: push the seller-published SOL terms to the inspector pane.
        self._emit(state, "http_402", "inspector", "HTTP_402",
                   "native SOL payment terms",
                   {"status": resp.status_code, "headers": headers, "body": body})

    def _step_negotiate(
        self, asset, asset_uuid, buyer_agent_id,
        offer_sol, usage_type, state,
    ):
        """Step 2: buyer posts an offer -> ACCEPT / COUNTER / REJECT."""
        import json as _json

        from apps.negotiation.views_api import negotiate

        body_str = _json.dumps({
            "buyer_agent_id": buyer_agent_id,
            "offer_sol": str(offer_sol),
            "usage_type": usage_type,
        })
        request = self._factory.post(
            f"/api/v1/ip/{asset.id}/negotiate",
            data=body_str,
            content_type="application/json",
            HTTP_X_AGENT_PROTOCOL="x402",
            HTTP_ACCEPT="application/json",
        )
        resp = negotiate(request, asset_id=asset_uuid)
        body = _decode_json(resp)

        # R5: the buyer's offer action.
        self._emit(state, "offer", "buyer", "OFFER",
                   f"buyer offers {offer_sol} SOL",
                   {"offer_sol": str(offer_sol), "usage_type": usage_type})

        status = body.get("status")
        if status == "COUNTER_OFFER":
            # R4: seller (Gemini) counters.
            self._emit(state, "counter", "seller", "COUNTER",
                       f"seller counters {body.get('price_sol')} SOL",
                       {"price_sol": body.get("price_sol"),
                        "reason": body.get("reason")})
        if status != "ACCEPT":
            # R9: negotiation did not accept -> abort before settle.
            self._fail(state, "inspector", "SIMULATION_FAILED",
                       f"negotiation not accepted ({status})",
                       {"status": status, "reason": body.get("reason")},
                       asset=asset, stage="negotiate")
            return

        # R5: buyer accepts the deal.
        self._emit(state, "accept", "buyer", "ACCEPT",
                   f"deal at {body.get('price_sol')} SOL",
                   {"price_sol": body.get("price_sol"),
                    "pay_address": body.get("pay_address")})
        state.session_id = body.get("session_id")
        state.final_price = body.get("price_sol")

    def _step_settle(self, asset, asset_uuid, state):
        """Step 3: 제출된 실제 체인 거래를 검증하고 라이선스를 정산한다."""
        import json as _json

        from apps.ip.views_api import settle_agent_sol_payment

        body_str = _json.dumps({
            "tx_signature": state.submitted_payment_tx_sig,
            "buyer_wallet": state.buyer_wallet,
        })
        request = self._factory.post(
            f"/api/v1/ip/{asset.id}/agent-sol-payment/settle?session_id={state.session_id}",
            data=body_str,
            content_type="application/json",
            HTTP_X_AGENT_PROTOCOL="x402",
            HTTP_ACCEPT="application/json",
        )
        started = time.monotonic()
        request.GET = request.GET.copy()
        request.GET["session_id"] = state.session_id
        resp = settle_agent_sol_payment(request, asset_id=asset_uuid)
        duration_ms = int((time.monotonic() - started) * 1000)
        body = _decode_json(resp)

        if resp.status_code != 200:
            self._fail(state, "inspector", "SIMULATION_FAILED",
                       f"settlement failed ({resp.status_code})",
                       {"status": resp.status_code, "body": body},
                       asset=asset, stage="settle")
            return

        # PAYMENT_VERIFIED + CERT_ISSUED AgentEvents are recorded by the pipeline
        # itself; here we enrich the inspector stream with the confirmation.
        cert_tx = body.get("certificate_tx")
        state.payment_tx_sig = state.submitted_payment_tx_sig
        state.certificate_tx = cert_tx
        state.download_url = body.get("download_url")
        self._emit(state, "payment_verified", "inspector", "PAYMENT_VERIFIED",
                   f"payment verified ({state.submitted_payment_tx_sig})",
                   {"tx_signature": state.submitted_payment_tx_sig, "duration_ms": duration_ms})
        self._emit(state, "cert_issued", "inspector", "CERT_ISSUED",
                   "certificate issued",
                   {"certificate_tx": cert_tx,
                    "explorer_url": explorer_url(cert_tx),
                    "download_url": body.get("download_url"),
                    "duration_ms": duration_ms})

    # --- Feed + failure helpers --------------------------------------------

    def _push(
        self, state, step, pane, type_, message, detail, *, failed=False,
    ) -> dict:
        """Build, persist (sandbox_feed), and append one display doc (R3).

        The per-run ``state.seq`` counter gives every doc a stable ascending
        id (used as the Firestore doc id) so the UI preserves insertion order.
        """
        state.seq += 1
        doc = {
            "run_id": state.run_id,
            "asset_id": str(state.asset_id),
            "seq": state.seq,
            "step": step,
            "pane": pane,
            "type": type_,
            "message": message,
            "failed": failed,
            "ts": timezone.now().isoformat(),
            "detail": detail or {},
        }
        self.firestore.set(
            "sandbox_feed", f"{state.run_id}_{state.seq:03d}", doc
        )
        state.steps.append(doc)
        return doc

    def _emit(self, state, step, pane, type_, message, detail) -> dict:
        """Push one non-failure display doc to sandbox_feed (R3)."""
        return self._push(state, step, pane, type_, message, detail)

    def _fail(self, state, pane, type_, message, detail, *, asset, stage):
        """R9: record a failure to both the feed and an AgentEvent, then abort."""
        self._push(
            state, "failure", pane, type_, message, detail, failed=True,
        )
        state.error = (
            detail.get("status", stage) if isinstance(detail, dict) else stage
        )
        state.aborted = True
        try:
            self.event_recorder.record(
                "SIMULATION_FAILED",
                {"run_id": state.run_id, "asset_id": state.asset_id,
                 "stage": stage, "detail": detail},
                asset=asset,
            )
        except Exception as exc:  # noqa: BLE001 (failure logging must not abort)
            logger.warning("SIMULATION_FAILED event record failed: %s", exc)

    def _result(self, state, *, session_id) -> SimulationResult:
        return SimulationResult(
            run_id=state.run_id,
            asset_id=state.asset_id,
            ok=not state.aborted,
            steps=list(state.steps),
            status="FAILED" if state.aborted else "SUCCESS",
            session_id=session_id,
            payment_tx_sig=state.payment_tx_sig,
            certificate_tx=state.certificate_tx,
            download_url=state.download_url,
            error=str(state.error) if state.aborted else None,
        )


# === Internal run state =====================================================


@dataclass
class _RunState:
    """Mutable per-run bookkeeping (seq counter + observed outputs)."""

    run_id: str
    asset_id: str
    steps: list[dict] = field(default_factory=list)
    seq: int = 0
    aborted: bool = False
    error: Any = None
    session_id: str | None = None
    final_price: Any = None
    submitted_payment_tx_sig: str = ""
    buyer_wallet: str = ""
    payment_tx_sig: str | None = None
    certificate_tx: str | None = None
    download_url: str | None = None


# === Helpers ================================================================


def _decode_json(resp) -> dict:
    """Best-effort JSON decode of a JsonResponse body."""
    import json as _json

    try:
        return _json.loads(resp.content) if resp.content else {}
    except (ValueError, _json.JSONDecodeError):
        return {}


# === Factory ================================================================


def get_sandbox_runner() -> SandboxRunner:
    """Factory: build a SandboxRunner from current Django settings.

    DI seam for the ``/sandbox/run`` view — tests monkeypatch
    ``apps.sandbox.views_api.get_sandbox_runner`` to inject fakes.
    """
    return SandboxRunner()
