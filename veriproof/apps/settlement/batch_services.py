"""Batch licensing SSOT — B2B micro-amount bulk licensing (SPEC-007, S2).

Architecture 2.1 / 8. The SINGLE source of truth for batch quote + settle. Both
``POST /api/v1/ip/batch/negotiate`` and ``POST /api/v1/ip/batch/settle`` delegate
here so there is no logic duplication between the view and any future async
(Workflows) path.

Flow::

    quote_batch_order(buyer_agent_id, asset_ids, usage_type) -> (BatchOrder, items)
      A. Validate items: empty / unknown asset_id / > BATCH_MAX_ITEMS
         -> BatchValidationError (422).                       [R3/R4/AC-3/4/5]
      B. GeminiService.quote_batch (structured Gemini pricing). [R2]
      C. Sum ``total_usdc`` in integer min-units (architecture §8).   [R1/AC-1]
      D. Persist ``BatchOrder(status=quoted)`` + one ``BatchItem`` per asset.

    settle_batch_order(order_id, tx_signature) -> BatchSettleResult
      A. Verify the on-chain payment total == ``BatchOrder.total_usdc``
         (integer min-units compare inside SolanaService).    [R5/AC-7]
      B. For each BatchItem: ``LicenseService.grant`` with a per-item
         idempotency key (``batch:{tx}:{item_id}``).         [R6/R10/AC-6/10]
      C. Partial grant failure -> ``status=partial`` + success/fail split. [R8/AC-8]
      D. Idempotent on ``(order_id, tx_signature)`` replay.            [R10]
      E. Per-item EventRecorder + BigQuery logging.                   [R9]

Money: all unit prices / totals are ``Decimal`` quantized to USDC's 6 decimals
via ``services._types.quantize_usdc``. Totals are accumulated as integer
min-units to avoid 6-decimal rounding drift (architecture §8 / SPEC-006 §6
edge).
"""
from __future__ import annotations

import decimal
import logging
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from services._payment import resolve_pay_to
from services.bigquery_sink import get_bigquery_sink
from services.event_recorder import get_event_recorder
from services.gemini_service import get_gemini_service
from services.license_service import get_license_service
from services.solana_adapter_factory import get_solana_service

logger = logging.getLogger(__name__)

# USDC on-chain has 6 decimal places; totals are accumulated as integer
# min-units to avoid float/Decimal drift on micro-amount sums. (architecture §8)
_USDC_DECIMALS = 6
_USDC_QUANTUM_UNITS = decimal.Decimal(10) ** _USDC_DECIMALS


class BatchValidationError(Exception):
    """Raised by ``quote_batch_order`` for R3/R4 validation failures.

    ``code`` is one of ``invalid_items`` / ``too_many_items``; ``invalid_ids``
    carries the offending asset_ids (empty for the empty/too-many cases).
    """

    def __init__(self, code: str, invalid_ids: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.invalid_ids = list(invalid_ids or ())


@dataclass
class BatchItemResult:
    """One item's settle outcome (success or failure)."""

    item_id: str
    asset_id: str
    license_id: str | None = None
    download_token: str | None = None
    download_url: str | None = None
    error: str | None = None
    retry: bool = False


@dataclass
class BatchSettleResult:
    """Outcome of ``settle_batch_order``.

    ``ok=False`` with ``error="invalid_settlement"`` signals AC-7 (the view
    returns HTTP 400). ``ok=True`` carries the per-item results; ``status`` is
    ``settled`` (all items granted) or ``partial`` (R8).
    """

    ok: bool
    status: str  # "settled" | "partial" | "invalid"
    order: Any = None
    successes: list[BatchItemResult] = field(default_factory=list)
    failures: list[BatchItemResult] = field(default_factory=list)
    error: str | None = None


class BatchService:
    """Orchestrates the batch quote -> settle pipeline (SPEC-007)."""

    def __init__(
        self,
        gemini: Any = None,
        solana: Any = None,
        license_service: Any = None,
        event_recorder: Any = None,
        bigquery: Any = None,
        usdc_mint: str | None = None,
        batch_max_items: int | None = None,
        micro_floor: decimal.Decimal | None = None,
    ) -> None:
        # Each dependency defaults to its settings-backed factory so the view
        # constructs the service with zero args; tests inject fakes.
        self.gemini = gemini if gemini is not None else get_gemini_service()
        self.solana = solana if solana is not None else get_solana_service()
        self.license_service = (
            license_service if license_service is not None else get_license_service()
        )
        self.event_recorder = (
            event_recorder if event_recorder is not None else get_event_recorder()
        )
        self.bigquery = bigquery if bigquery is not None else get_bigquery_sink()
        self.usdc_mint = usdc_mint
        self._batch_max_items = batch_max_items
        self._micro_floor = micro_floor

    # --- Batch quote (R1/R2/R3/R4) -------------------------------------------

    def quote_batch_order(
        self,
        buyer_agent_id: str,
        asset_ids: list,
        usage_type: str,
    ) -> tuple[Any, list[Any]]:
        """Quote a BatchOrder for ``asset_ids``. R1/R2/R3/R4 / AC-1..AC-5/AC-9.

        Returns ``(order, items)``. Raises ``BatchValidationError`` on the
        validation failures (the view maps to HTTP 422).
        """
        from apps.ip.models import IpAsset
        from apps.settlement.models import BatchItem, BatchOrder

        # R3 / AC-3: empty items list.
        if not asset_ids:
            raise BatchValidationError("invalid_items")

        # R4 / AC-5: item count cap.
        max_items = self._effective_batch_max_items()
        if len(asset_ids) > max_items:
            raise BatchValidationError("too_many_items")

        # R3 / AC-4: resolve asset_ids -> IpAssets, tracking unknown ids.
        asset_id_strs = [str(a) for a in asset_ids]
        found = {str(a.id): a for a in IpAsset.objects.filter(id__in=asset_id_strs)}
        invalid_ids = [aid for aid in asset_id_strs if aid not in found]
        if invalid_ids:
            raise BatchValidationError("invalid_items", invalid_ids)

        # R2: 자산별 단가는 Gemini 구조화 응답으로만 결정한다.
        item_payload = [
            {
                "asset_id": aid,
                "min_price_usdc": found[aid].min_price_usdc,
            }
            for aid in asset_id_strs
        ]
        quotes = self.gemini.quote_batch(item_payload, usage_type or "commercial")
        quote_by_id = {str(q.asset_id): q.unit_price_usdc for q in quotes}

        # R1 / AC-1: total accumulated as integer min-units (architecture §8).
        units = [self._to_min_units(quote_by_id.get(aid)) for aid in asset_id_strs]
        total_usdc = self._from_min_units(sum(units))

        order = BatchOrder.objects.create(
            buyer_agent_id=buyer_agent_id or "",
            total_usdc=total_usdc,
            status=BatchOrder.QUOTED,
        )
        items = []
        for aid in asset_id_strs:
            items.append(
                BatchItem.objects.create(
                    order=order,
                    asset=found[aid],
                    unit_price_usdc=quote_by_id.get(aid),
                )
            )
        return order, items

    # --- Batch settle (R5/R6/R7/R8/R9/R10) -----------------------------------

    def settle_batch_order(
        self,
        order_id: Any,
        tx_signature: str,
        buyer_wallet: str | None = None,
    ) -> BatchSettleResult:
        """Settle a BatchOrder against an on-chain payment. R5..R10 / AC-6..AC-10.

        ``buyer_wallet`` defaults to the payment's on-chain sender. Returns a
        ``BatchSettleResult`` (``ok=False`` + ``error="invalid_settlement"`` on
        payment mismatch per AC-7).
        """
        from apps.settlement.models import BatchOrder

        try:
            order = BatchOrder.objects.get(id=order_id)
        except BatchOrder.DoesNotExist:
            return BatchSettleResult(ok=False, status="invalid", error="not_found")

        # R10 / AC-10: idempotent on (order_id, tx_signature) replay. An order
        # already settled/partial with the SAME tx is returned as-is — no
        # duplicate licenses, no duplicate events.
        if (
            order.payment_tx_sig == tx_signature
            and order.status in (BatchOrder.SETTLED, BatchOrder.PARTIAL)
        ):
            return self._rebuild_result(order)

        # A second, different tx for an already-settled order is rejected: the
        # per-item license keys would otherwise double-license the assets.
        if order.status in (BatchOrder.SETTLED, BatchOrder.PARTIAL):
            return BatchSettleResult(
                ok=False, status="invalid", error="already_settled"
            )

        # R5 / AC-7: verify the on-chain total equals the quoted total. The
        # recipient comes from the first item's asset via resolve_pay_to (§6
        # edge: single-seller demo set is the MVP assumption; multi-creator
        # distribution is SPEC-008 escrow territory).
        items = list(order.items.select_related("asset", "asset__creator").order_by("id"))
        verification = self._verify_payment(order, items, tx_signature)
        if not verification.is_valid:
            return BatchSettleResult(
                ok=False, status="invalid", error="invalid_settlement"
            )

        effective_wallet = buyer_wallet or getattr(verification, "sender", "") or ""

        # R6 / R7 / R8 / AC-6 / AC-8: grant a license per item. Per-item
        # idempotency key = batch:{tx}:{item_id} — deterministic on replay, so
        # LicenseService.grant short-circuits duplicates (R10 belt-and-suspenders).
        successes: list[BatchItemResult] = []
        failures: list[BatchItemResult] = []
        for idx, item in enumerate(items):
            item_tx = f"batch:{tx_signature}:{item.id}"
            try:
                license = self.license_service.grant(
                    item.asset,
                    effective_wallet,
                    decimal.Decimal(item.unit_price_usdc),
                    "commercial",
                    item_tx,
                    session=None,
                )
            except Exception as exc:  # noqa: BLE001 (partial-failure isolation R8)
                logger.warning(
                    "batch grant failed for item %s: %s", item.id, exc
                )
                failures.append(
                    BatchItemResult(
                        item_id=str(item.id),
                        asset_id=str(item.asset_id),
                        error=str(exc) or "grant_failed",
                        retry=True,
                    )
                )
                continue

            # Link the license onto the item (best-effort; FakeLicenseService
            # stand-ins have no .save()).
            token = getattr(license, "download_token", None)
            try:
                item.license = license
                item.save(update_fields=["license"])
            except Exception as exc:  # noqa: BLE001
                logger.debug("batch item license link skipped: %s", exc)

            # R9: per-item BigQuery audit row.
            self._log_transaction(item, license, item_tx, effective_wallet, idx)

            successes.append(
                BatchItemResult(
                    item_id=str(item.id),
                    asset_id=str(item.asset_id),
                    license_id=str(getattr(license, "id", "")),
                    download_token=token,
                    download_url=f"/files/{token}" if token else None,
                )
            )

        # R7 / R8: transition the order. All success -> settled; any failure
        # -> partial (the successes are still honored).
        from apps.settlement.models import BatchOrder as _BO

        if failures:
            order.status = _BO.PARTIAL
            final_status = "partial"
        else:
            order.status = _BO.SETTLED
            final_status = "settled"
        order.payment_tx_sig = tx_signature
        order.save(update_fields=["status", "payment_tx_sig"])

        # R9: one order-level event (BATCH_SETTLED / BATCH_PARTIAL).
        self._record_order_event(order, final_status, len(successes), len(failures))

        return BatchSettleResult(
            ok=True,
            status=final_status,
            order=order,
            successes=successes,
            failures=failures,
        )

    # --- Helpers --------------------------------------------------------------

    def _verify_payment(self, order, items, tx_signature):
        """R5/AC-7: verify the batch payment total + recipient on-chain."""
        if not items:
            return _invalid_verification(decimal.Decimal("0"))
        recipient = resolve_pay_to(items[0].asset)
        return self.solana.verify_usdc_payment(
            tx_signature,
            expected_recipient=recipient,
            expected_amount=decimal.Decimal(order.total_usdc),
            mint=self._resolve_mint(),
        )

    def _resolve_mint(self) -> str:
        if self.usdc_mint:
            return self.usdc_mint
        from django.conf import settings

        return getattr(settings, "USDC_MINT_ADDRESS")

    def _log_transaction(
        self, item, license, item_tx, buyer_wallet, idx
    ) -> None:
        """R9: insert one BigQuery ``transactions`` row per settled item."""
        try:
            self.bigquery.insert(
                "transactions",
                {
                    "tx_time": timezone.now().isoformat(),
                    "asset_id": str(item.asset_id),
                    "buyer_wallet": buyer_wallet,
                    "price_usdc": str(item.unit_price_usdc),
                    "payment_tx_sig": item_tx,
                    "certificate_tx_sig": getattr(license, "certificate_tx_sig", None),
                    "usage_type": "commercial",
                    "batch_item_index": idx,
                },
            )
        except Exception as exc:  # noqa: BLE001 (audit logging must not abort)
            logger.warning("batch BigQuery insert failed for item %s: %s", item.id, exc)

    def _record_order_event(
        self, order, status: str, n_success: int, n_fail: int
    ) -> None:
        """R9: record a BATCH_SETTLED / BATCH_PARTIAL order-level event."""
        if self.event_recorder is None:
            return
        event_type = "BATCH_SETTLED" if status == "settled" else "BATCH_PARTIAL"
        try:
            self.event_recorder.record(
                event_type,
                {
                    "order_id": str(order.id),
                    "payment_tx_sig": order.payment_tx_sig,
                    "successes": n_success,
                    "failures": n_fail,
                    "total_usdc": str(order.total_usdc),
                },
            )
        except Exception as exc:  # noqa: BLE001 (fan-out must not abort)
            logger.warning("batch order event fan-out failed: %s", exc)

    def _rebuild_result(self, order) -> BatchSettleResult:
        """Reconstruct a BatchSettleResult from an already-settled order (R10).

        No re-grant, no re-verify, no duplicate events — just project the
        existing item/license links back into the result envelope.
        """
        from apps.settlement.models import BatchOrder

        successes: list[BatchItemResult] = []
        failures: list[BatchItemResult] = []
        for item in order.items.select_related("license").order_by("id"):
            if item.license_id is not None:
                token = getattr(item.license, "download_token", None)
                successes.append(
                    BatchItemResult(
                        item_id=str(item.id),
                        asset_id=str(item.asset_id),
                        license_id=str(item.license_id),
                        download_token=token,
                        download_url=f"/files/{token}" if token else None,
                    )
                )
            else:
                failures.append(
                    BatchItemResult(
                        item_id=str(item.id),
                        asset_id=str(item.asset_id),
                        error="grant_failed",
                        retry=True,
                    )
                )
        status = "settled" if order.status == BatchOrder.SETTLED else "partial"
        return BatchSettleResult(
            ok=True,
            status=status,
            order=order,
            successes=successes,
            failures=failures,
        )

    def _effective_batch_max_items(self) -> int:
        if self._batch_max_items is not None:
            return int(self._batch_max_items)
        from django.conf import settings

        return int(getattr(settings, "BATCH_MAX_ITEMS", 200))

    @staticmethod
    def _to_min_units(amount: decimal.Decimal) -> int:
        """USDC major -> integer min-units (6 decimals)."""
        return int(
            (decimal.Decimal(amount) * _USDC_QUANTUM_UNITS).to_integral_value(
                rounding=decimal.ROUND_HALF_EVEN
            )
        )

    @staticmethod
    def _from_min_units(units: int) -> decimal.Decimal:
        """Integer min-units -> USDC major Decimal, quantized to 6 decimals."""
        from services._types import quantize_usdc

        return quantize_usdc(decimal.Decimal(units) / _USDC_QUANTUM_UNITS)


def _invalid_verification(amount: decimal.Decimal):
    """Build an is_valid=False PaymentVerification for the empty-order edge."""
    from services._types import PaymentVerification

    return PaymentVerification(
        is_valid=False, amount=amount, sender="", slot=0, commitment=None
    )


def get_batch_service() -> BatchService:
    """Factory: build a BatchService from current Django settings.

    DI seam for the views: tests monkeypatch
    ``apps.ip.views_api.get_batch_service`` to inject fakes.
    """
    from django.conf import settings

    return BatchService(
        usdc_mint=getattr(settings, "USDC_MINT_ADDRESS", None),
    )
