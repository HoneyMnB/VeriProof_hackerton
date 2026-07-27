"""Settlement pipeline — the SINGLE SOURCE OF TRUTH for settling a payment.

Architecture 2.1 / 8. Both the synchronous ``POST /api/v1/ip/{asset_id}/settle``
fallback AND the GCP Workflows async path call the SAME service methods in the
SAME order via ``SettlementService.settle_pipeline``:

    A. SolanaService.verify_usdc_payment(recipient, amount, mint)   [R1/R2/R3]
    B. LicenseService.grant(...)                                     [R4/R5/R7/R8]
    C. SolanaService.issue_certificate(...)  (failure -> cert=None) [R6/R16]
    D. FirestoreMirror.set(asset_status, status=LICENSED)           [R14/AC-11]
    E. BigQuerySink.insert(transactions, {...})                     [R14/AC-11]
    F. RoyaltyService.distribute(license)  (2nd-creation only)      [R14b]
    G. EventRecorder.record(CERT_ISSUED, ...)                       [R15/AC-12]

Because Workflows reuses these exact service methods, there is NO logic
duplication between the sync and async paths — only the invocation binder
differs (Django view vs Workflows YAML).
"""
from __future__ import annotations

import datetime
import decimal
import logging
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from services._payment import resolve_pay_to
from services.bigquery_sink import get_bigquery_sink
from services.event_recorder import get_event_recorder
from services.firestore_mirror import get_firestore_mirror
from services.license_service import get_license_service
from services.payment_verifier import get_payment_verifier
from services.royalty_service import get_royalty_service
from services.solana_adapter_factory import get_solana_service
from services.solana_service import CertificateIssueError

logger = logging.getLogger(__name__)


@dataclass
class SettlementResult:
    """Outcome of ``SettlementService.settle_pipeline``.

    ``ok=False`` with ``error="invalid_settlement"`` signals R3 (the caller
    returns HTTP 400). ``ok=True`` carries the success envelope (§6.3):
    certificate tx, ``/files/{token}`` download URL, and expiry.
    """

    ok: bool
    status: str  # "SUCCESS" | "INVALID"
    license: Any = None
    certificate_tx: str | None = None
    download_url: str | None = None
    download_expires_at: datetime.datetime | None = None
    error: str | None = None


class SettlementService:
    """Orchestrates the verify->grant->cert->mirror->log settlement pipeline."""

    def __init__(
        self,
        solana: Any = None,
        license_service: Any = None,
        firestore: Any = None,
        bigquery: Any = None,
        royalty_service: Any = None,
        event_recorder: Any = None,
        payment_verifier: Any = None,
        usdc_mint: str | None = None,
    ) -> None:
        # Each dependency defaults to its settings-backed factory so the view
        # can construct the service with zero args, while tests inject fakes.
        self.solana = solana if solana is not None else get_solana_service()
        # 로컬 목업과 실체인 검증을 바꾸는 유일한 의존성 경계다. 테스트에서
        # solana fake를 넘기면 같은 fake를 검증기로 사용해 기존 계약을 유지한다.
        self.payment_verifier = (
            payment_verifier
            if payment_verifier is not None
            else (solana if solana is not None else get_payment_verifier())
        )
        self.license_service = (
            license_service if license_service is not None else get_license_service()
        )
        self.firestore = firestore if firestore is not None else get_firestore_mirror()
        self.bigquery = bigquery if bigquery is not None else get_bigquery_sink()
        self.royalty_service = (
            royalty_service if royalty_service is not None else get_royalty_service()
        )
        self.event_recorder = (
            event_recorder if event_recorder is not None else get_event_recorder()
        )
        self.usdc_mint = usdc_mint

    # --- The pipeline SSOT --------------------------------------------------

    def settle_pipeline(
        self,
        *,
        asset: Any,
        session: Any,
        tx_signature: str,
        buyer_wallet: str,
        expected_amount: decimal.Decimal | None = None,
        usage_type: str | None = None,
        payment_already_verified: bool = False,
        buyer_user: Any = None,
        payment_currency: str = "USDC",
    ) -> SettlementResult:
        """Run the full settlement pipeline. R1/R2/R3/R4/R5/R6/R14/R14b/R15/R16.

        Returns a ``SettlementResult``. On invalid verification (R3) returns
        ``ok=False`` WITHOUT granting a license. Certificate issuance failure
        (R16) is decoupled: the license is kept and ``certificate_tx`` is None.
        """
        mint = self._resolve_mint()
        amount = expected_amount if expected_amount is not None else (
            self._resolve_amount(asset, session)
        )
        effective_usage = usage_type or self._resolve_usage(asset, session)

        if payment_currency not in {"USDC", "SOL"}:
            raise ValueError("unsupported payment currency")
        # A. Verify the on-chain payment. Recipient comes from the shared
        # resolve_pay_to SSOT so all settlement routes agree on the payee.
        recipient = resolve_pay_to(asset)
        if not payment_already_verified:
            if payment_currency == "SOL":
                verification = self.payment_verifier.verify_sol_payment_transaction(
                    signature=tx_signature,
                    expected_recipient=recipient,
                    expected_lamports=self.solana._amount_to_lamports(amount),
                    expected_memo=f"VERIPROOF:{asset.id}:SOL",
                )
            else:
                verification = self.payment_verifier.verify_usdc_payment(
                    tx_signature,
                    expected_recipient=recipient,
                    expected_amount=amount,
                    mint=mint,
                )
            if not verification.is_valid:
                # R3 / AC-2 / AC-3: invalid -> 400, no license granted.
                return SettlementResult(
                    ok=False, status="INVALID", error="invalid_settlement"
                )

        # B. Grant the license (idempotent on payment_tx_sig). R4/R5/R7/R8.
        # PAYMENT_VERIFIED is fanned out inside grant (R15).
        grant_kwargs = {
            "session": session,
            "buyer_user": buyer_user,
        }
        if payment_currency == "SOL":
            grant_kwargs["payment_currency"] = "SOL"
        granted = self.license_service.grant(
            asset,
            buyer_wallet,
            amount,
            effective_usage,
            tx_signature,
            **grant_kwargs,
        )

        # C. Issue the certificate Memo (R6). Failure is decoupled (R16): the
        # license survives, certificate_tx_sig stays None.
        certificate_tx = self._issue_certificate(asset, granted, buyer_wallet)

        # D. Firestore real-time mirror (R14 / AC-11). No-op when disabled.
        self.firestore.set(
            "asset_status",
            str(asset.id),
            {
                "status": "LICENSED",
                "asset_id": str(asset.id),
                "buyer_wallet": buyer_wallet,
                "payment_tx_sig": tx_signature,
                "certificate_tx_sig": certificate_tx,
                "updated_at": timezone.now().isoformat(),
            },
        )

        # E. BigQuery audit ledger (R14 / AC-11). No-op when disabled.
        self.bigquery.insert(
            "transactions",
            {
                "tx_time": timezone.now().isoformat(),
                "asset_id": str(asset.id),
                "buyer_wallet": buyer_wallet,
                "price_usdc": str(amount),
                **(
                    {
                        "price_sol": str(amount),
                        "payment_currency": "SOL",
                    }
                    if payment_currency == "SOL"
                    else {}
                ),
                "payment_tx_sig": tx_signature,
                "certificate_tx_sig": certificate_tx,
                "usage_type": effective_usage,
            },
        )

        # F. Royalty distribution for 2nd-creation only (R14b). RoyaltyService
        # is owned by SPEC-008; here we only CALL it as a pipeline step.
        if getattr(asset, "parent_asset_id", None) is not None:
            try:
                self.royalty_service.distribute(granted)
            except NotImplementedError:
                # SPEC-008 not yet landed: log + continue (do not abort settle).
                logger.info(
                    "RoyaltyService.distribute is a stub (SPEC-008); "
                    "skipping royalty split for asset %s",
                    asset.id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("royalty distribution failed: %s", exc)

        # G. CERT_ISSUED event fan-out (R15 / AC-12).
        self._record_cert_issued(asset, session, granted, certificate_tx)

        download_expires_at = getattr(granted, "download_expires_at", None)
        return SettlementResult(
            ok=True,
            status="SUCCESS",
            license=granted,
            certificate_tx=certificate_tx,
            download_url=f"/files/{getattr(granted, 'download_token', '')}",
            download_expires_at=download_expires_at,
        )

    # --- Pipeline helpers ---------------------------------------------------

    def _resolve_mint(self) -> str:
        if self.usdc_mint:
            return self.usdc_mint
        from django.conf import settings

        return settings.USDC_MINT_ADDRESS

    @staticmethod
    def _resolve_amount(asset: Any, session: Any) -> decimal.Decimal:
        """Prefer the negotiated final price; fall back to the target price."""
        final = getattr(session, "final_price_usdc", None)
        if final is not None:
            return final
        return asset.target_price_usdc

    @staticmethod
    def _resolve_usage(asset: Any, session: Any) -> str:
        usage = getattr(session, "usage_type", None)
        if usage:
            return usage
        return "commercial"

    def _issue_certificate(
        self, asset: Any, license: Any, buyer_wallet: str
    ) -> str | None:
        """Issue the on-chain certificate; on failure (R16) return None.

        The license row is updated in place when issuance succeeds so
        ``certificate_tx_sig`` is persisted for later certificate retrieval.
        """
        memo = f"veriproof:cert:{asset.id}"
        try:
            certificate_tx = self.solana.issue_certificate(
                asset.id, buyer_wallet, memo
            )
        except CertificateIssueError as exc:
            logger.warning(
                "certificate issuance failed (license kept): %s", exc
            )
            return None
        # Persist the certificate tx onto the license (best-effort).
        try:
            license.certificate_tx_sig = certificate_tx
            update_fields = ["certificate_tx_sig"]
            license.save(update_fields=update_fields)
        except Exception as exc:  # noqa: BLE001 (FakeLicenseService has no save)
            logger.debug("license.save(certificate_tx) skipped: %s", exc)
        return certificate_tx

    def _record_cert_issued(
        self, asset, session, license, certificate_tx
    ) -> None:
        """R15 / AC-12: fan out a CERT_ISSUED AgentEvent (best-effort)."""
        if self.event_recorder is None:
            return
        try:
            self.event_recorder.record(
                "CERT_ISSUED",
                {
                    "asset_id": str(getattr(asset, "id", "")),
                    "license_id": str(getattr(license, "id", "")),
                    "payment_tx_sig": getattr(license, "payment_tx_sig", ""),
                    "certificate_tx_sig": certificate_tx,
                },
                asset=asset,
                session=session,
            )
        except Exception as exc:  # noqa: BLE001 (fan-out must not abort)
            logger.warning("CERT_ISSUED fan-out failed: %s", exc)


def get_settlement_service() -> SettlementService:
    """Factory: build a SettlementService from current Django settings.

    This is the DI seam for the views: tests monkeypatch
    ``apps.settlement.views_api.get_settlement_service`` to inject fakes.
    """
    from django.conf import settings

    return SettlementService(
        usdc_mint=getattr(settings, "USDC_MINT_ADDRESS", None),
    )
