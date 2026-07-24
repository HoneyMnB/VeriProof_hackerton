"""LicenseService — grants licenses idempotently (architecture 4, 8).

Idempotency key: ``License.payment_tx_sig`` is UNIQUE, so re-submitting the
same verified tx returns the existing license. Shared by the sync ``/settle``
fallback and the GCP Workflows path (single logic SSOT).
"""
from __future__ import annotations

import datetime
import decimal
import logging
import secrets
from typing import Any

from .solana_service import get_solana_service

logger = logging.getLogger(__name__)


class LicenseService:
    """Creates License rows and answers license-holding queries."""

    def __init__(
        self,
        download_token_ttl_seconds: int | None = None,
        event_recorder: Any = None,
    ) -> None:
        self.download_token_ttl_seconds = download_token_ttl_seconds
        # SPEC-004 R15: optional EventRecorder for PAYMENT_VERIFIED fan-out.
        # Injected so tests can pass a recorder stand-in; the factory wires the
        # real one. ``None`` means "no fan-out" (kept testable in isolation).
        self.event_recorder = event_recorder

    # --- Architecture 4 methods (SPEC-004 implements grant) -----------------
    def grant(
        self,
        asset: Any,
        buyer_wallet: str,
        price: decimal.Decimal,
        usage_type: str,
        payment_tx: str,
        session: Any = None,
    ) -> Any:
        """Grant a License for ``asset`` to ``buyer_wallet``. SPEC-004 R4/R5/R7/R8.

        Idempotent on ``payment_tx`` (the unique ``License.payment_tx_sig``):
        a duplicate submission returns the existing License unchanged (R5/AC-4).
        On first grant it generates an expiring ``download_token`` + expiry
        (R7), links ``session`` when provided (R8), and fans out a
        ``PAYMENT_VERIFIED`` event (R15).
        """
        from apps.settlement.models import License

        # R5 / AC-4: idempotency short-circuit on the verified tx signature.
        existing = License.objects.filter(payment_tx_sig=payment_tx).first()
        if existing is not None:
            logger.info("grant idempotent hit for tx_sig=%s", payment_tx)
            return existing

        ttl_seconds = self._effective_ttl_seconds()
        now = datetime.datetime.now(datetime.timezone.utc)
        download_token = secrets.token_urlsafe(24)
        download_expires_at = now + datetime.timedelta(seconds=ttl_seconds)

        license = License.objects.create(
            asset=asset,
            session=session,
            buyer_wallet=buyer_wallet,
            price_usdc=price,
            usage_type=usage_type or "commercial",
            payment_tx_sig=payment_tx,
            certificate_tx_sig=None,
            download_token=download_token,
            download_expires_at=download_expires_at,
        )

        # R15: fan out PAYMENT_VERIFIED on FIRST grant only.
        self._record_payment_verified(asset, license, session)
        return license

    def _effective_ttl_seconds(self) -> int:
        """Resolve the download-token TTL, falling back to the Django default."""
        if self.download_token_ttl_seconds is not None:
            return int(self.download_token_ttl_seconds)
        from django.conf import settings

        return int(getattr(settings, "DOWNLOAD_TOKEN_TTL_SECONDS", 3600))

    def _record_payment_verified(
        self, asset: Any, license: Any, session: Any
    ) -> None:
        """R15: fan out PAYMENT_VERIFIED (best-effort; never aborts the grant)."""
        if self.event_recorder is None:
            return
        try:
            self.event_recorder.record(
                "PAYMENT_VERIFIED",
                {
                    "asset_id": str(getattr(asset, "id", "")),
                    "license_id": str(getattr(license, "id", "")),
                    "payment_tx_sig": getattr(license, "payment_tx_sig", ""),
                    "buyer_wallet": getattr(license, "buyer_wallet", ""),
                },
                asset=asset,
                session=session,
            )
        except Exception as exc:  # noqa: BLE001 (fan-out must not abort grant)
            logger.warning("PAYMENT_VERIFIED fan-out failed: %s", exc)

    def is_licensed(self, asset: Any, tx_sig: str) -> bool:
        """Return True if ``asset`` was licensed via ``tx_sig``. SPEC-002/004.

        SPEC-002 R10 / AC-7: the DB is the system of record. If a ``License``
        row exists for this ``(asset, tx_sig)`` we return True WITHOUT any
        on-chain call (cost + latency win). Only when no DB license is found
        AND a ``tx_sig`` is supplied do we lazily call
        ``SolanaService.verify_usdc_payment`` — the on-chain re-verification
        fallback that full settlement (SPEC-004) exercises.
        """
        from apps.settlement.models import License

        # R10: DB first. A hit short-circuits the on-chain call entirely.
        if (
            tx_sig
            and License.objects.filter(
                asset=asset, payment_tx_sig=tx_sig
            ).exists()
        ):
            return True

        # No tx_sig to verify against -> definitely not licensed here.
        if not tx_sig:
            return False

        # Fallback: on-chain re-verification (SPEC-004 owns the real path; the
        # SolanaService is obtained lazily so tests can swap it via the
        # ``services.license_service.get_solana_service`` seam).
        from django.conf import settings

        from ._payment import resolve_pay_to

        solana = get_solana_service()
        verification = solana.verify_usdc_payment(
            tx_sig,
            expected_recipient=resolve_pay_to(asset),
            expected_amount=asset.target_price_usdc,
            mint=getattr(settings, "USDC_MINT_ADDRESS"),
        )
        return bool(verification.is_valid)


def get_license_service() -> LicenseService:
    """Factory: build a LicenseService from current Django settings."""
    from django.conf import settings

    from .event_recorder import get_event_recorder

    return LicenseService(
        download_token_ttl_seconds=getattr(
            settings, "DOWNLOAD_TOKEN_TTL_SECONDS", 3600
        ),
        event_recorder=get_event_recorder(),
    )
