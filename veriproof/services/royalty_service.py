"""RoyaltyService — escrow royalty split for secondary creations (architecture 4, 5.1 S3).

Computes the split (original creator + secondary creator) from the parent
``royalty_share_bps`` and performs the escrow ``transfer_usdc`` payouts,
recording one ``RoyaltyDistribution`` per leg.

SPEC-008 contract:
- R5/R8: split computed in integer min-units (6 decimals) so
  ``original + secondary == total`` EXACTLY (zero loss); remainder -> secondary.
- R6/R7: each leg transfers via ``SolanaService.transfer_usdc`` (KMS-signed from
  escrow) and records a ``RoyaltyDistribution`` with ``status="settled"``.
- R9: a failed leg is recorded ``status="failed"`` (no tx_sig); the sibling leg
  is preserved and distribute does NOT re-raise (settlement must not abort).
- R10: MVP distributes the direct 1-level parent only (parent's parent ignored).
- Architecture 8 idempotency: already-settled legs are not re-transferred.
"""
from __future__ import annotations

import decimal
import logging
from typing import Any

logger = logging.getLogger(__name__)

# USDC has 6 decimal places (architecture 8). Splitting in integer min-units
# avoids float drift and guarantees original + secondary == total exactly.
_USDC_DECIMALS = 6
_QUANTUM = decimal.Decimal(10) ** -_USDC_DECIMALS  # 0.000001
_MIN_PER_USDC = decimal.Decimal(10) ** _USDC_DECIMALS  # 1_000_000


class RoyaltyService:
    """Distributes escrow funds to royalty recipients."""

    def __init__(self, solana: Any = None, event_recorder: Any = None) -> None:
        # SolanaService (or fake) used for transfer_usdc. EventRecorder (or fake)
        # used for the ROYALTY_SPLIT fan-out; None defers to get_event_recorder().
        self.solana = solana
        self.event_recorder = event_recorder

    # --- Pure split math (R5/R8/AC-4/AC-6) ----------------------------------
    @staticmethod
    def compute_split(
        price_usdc: decimal.Decimal, royalty_share_bps: int
    ) -> tuple[decimal.Decimal, decimal.Decimal]:
        """Split ``price_usdc`` into (original, secondary) Decimal shares.

        R5: ``original = total * bps/10000``, ``secondary = total - original``.
        R8: computed in 6-decimal integer min-units so ``original + secondary``
        equals ``total`` EXACTLY with zero fractional loss. The integer remainder
        of the division accrues to secondary (the seller / 2nd creator) per
        architecture §6 edge.
        """
        total_min = int(
            (decimal.Decimal(price_usdc) * _MIN_PER_USDC).to_integral_value(
                rounding=decimal.ROUND_HALF_EVEN
            )
        )
        bps = int(royalty_share_bps)
        original_min = total_min * bps // 10000
        secondary_min = total_min - original_min
        original = (decimal.Decimal(original_min) / _MIN_PER_USDC).quantize(_QUANTUM)
        secondary = (decimal.Decimal(secondary_min) / _MIN_PER_USDC).quantize(_QUANTUM)
        return original, secondary

    # --- Architecture 4 method (SPEC-008) ------------------------------------
    def distribute(self, license: Any) -> list[Any]:
        """Split the license proceeds and transfer each leg on-chain.

        Returns the list of created (or idempotently reused) RoyaltyDistribution
        rows. Partial failure (R9) leaves the failed leg in ``status="failed"``
        while the sibling stays ``settled``; this method does NOT re-raise so the
        settlement pipeline (step F) never aborts on a royalty issue.
        """
        from apps.settlement.models import RoyaltyDistribution

        asset = license.asset
        # R10: MVP 1-level only. parent_asset is the direct progenitor; any
        # grandparent chain is an extension (marked out of scope here).
        parent = asset.parent_asset
        bps = asset.royalty_share_bps or 0

        price = getattr(license, "price_usdc", None)
        if price is None:
            price = asset.target_price_usdc
        original_share, secondary_share = self.compute_split(price, bps)

        original_wallet = parent.creator.wallet_address
        secondary_wallet = asset.creator.wallet_address  # the 2nd-creator / seller.

        # §6 edge: identical wallet -> a single combined transfer of the full
        # total avoids burning a second on-chain tx for a self-deal.
        if original_wallet == secondary_wallet:
            legs = [
                (
                    RoyaltyDistribution.SECONDARY,
                    secondary_wallet,
                    original_share + secondary_share,
                )
            ]
        else:
            legs = [
                (RoyaltyDistribution.ORIGINAL, original_wallet, original_share),
                (RoyaltyDistribution.SECONDARY, secondary_wallet, secondary_share),
            ]

        records: list[Any] = []
        for role, wallet, amount in legs:
            records.append(self._settle_leg(license, role, wallet, amount))

        self._record_royalty_split(license, asset, bps, price, legs, records)
        return records

    # --- Helpers --------------------------------------------------------------

    def _settle_leg(
        self,
        license: Any,
        role: str,
        wallet: str,
        amount: decimal.Decimal,
    ) -> Any:
        """Transfer one leg and persist its RoyaltyDistribution row.

        Architecture 8 idempotency: an already-settled row for this license+role
        is returned as-is (no re-transfer, no double-pay on replay). Failed or
        pending legs are always re-attempted so the retry path (R9) can recover
        them without touching the successful sibling.
        """
        from apps.settlement.models import RoyaltyDistribution

        existing = RoyaltyDistribution.objects.filter(
            license=license, role=role, status=RoyaltyDistribution.SETTLED
        ).first()
        if existing is not None:
            return existing

        rec = RoyaltyDistribution(
            license=license,
            recipient_wallet=wallet,
            role=role,
            amount_usdc=amount,
            status=RoyaltyDistribution.PENDING,
        )
        if self.solana is None:
            # No escrow backend configured (e.g. RoyaltyService() with no solana
            # injected): record the leg as failed rather than crash. R9.
            logger.warning(
                "royalty transfer skipped: no solana backend (license=%s role=%s)",
                getattr(license, "id", None),
                role,
            )
            rec.status = RoyaltyDistribution.FAILED
            rec.save()
            return rec
        try:
            tx_sig = self.solana.transfer_usdc(wallet, amount)
            rec.transfer_tx_sig = tx_sig
            rec.status = RoyaltyDistribution.SETTLED
        except Exception as exc:  # noqa: BLE001 (RPC + signer errors are broad)
            # R9: record the failure; a future retry worker scans status=failed.
            # Never abort the sibling leg.
            logger.warning(
                "royalty transfer failed (license=%s, role=%s): %s",
                getattr(license, "id", None),
                role,
                exc,
            )
            rec.status = RoyaltyDistribution.FAILED
        rec.save()
        return rec

    def _record_royalty_split(
        self,
        license: Any,
        asset: Any,
        bps: int,
        total: decimal.Decimal,
        legs: list[tuple[str, str, decimal.Decimal]],
        records: list[Any],
    ) -> None:
        """R7 / AC-8: fan out a ROYALTY_SPLIT event summarising the distribution."""
        recorder = self.event_recorder
        if recorder is None:
            from services.event_recorder import get_event_recorder

            recorder = get_event_recorder()
        leg_summary: dict[str, dict] = {}
        for (role, wallet, amount), rec in zip(legs, records):
            leg_summary[role] = {
                "recipient_wallet": wallet,
                "amount_usdc": str(amount),
                "transfer_tx_sig": getattr(rec, "transfer_tx_sig", None),
                "status": getattr(rec, "status", None),
            }
        payload = {
            "asset_id": str(getattr(asset, "id", "")),
            "license_id": str(getattr(license, "id", "")),
            "parent_asset_id": str(getattr(asset, "parent_asset_id", "") or ""),
            "royalty_share_bps": bps,
            # Canonical 6dp USDC string (the raw price may carry fewer places).
            "total_usdc": str(total.quantize(_QUANTUM)),
            "legs": leg_summary,
        }
        try:
            recorder.record("ROYALTY_SPLIT", payload, asset=asset)
        except Exception as exc:  # noqa: BLE001 (fan-out must not abort)
            logger.warning("ROYALTY_SPLIT event recording failed: %s", exc)


def get_royalty_service() -> RoyaltyService:
    """Factory: build a RoyaltyService with SolanaService + EventRecorder wired."""
    from .event_recorder import get_event_recorder
    from .solana_service import get_solana_service

    return RoyaltyService(
        solana=get_solana_service(),
        event_recorder=get_event_recorder(),
    )
