"""SPEC-007 unit tests — batch quote + settle service logic.

Covers the TDD list (6):
- test_quote_batch_sums_total (R1 / AC-1, integer min-units total)
- test_quote_unit_respects_micro_floor (R2 / AC-2)
- test_quote_batch_fallback_on_model_failure (R2 / AC-9)
- test_batch_settle_requires_total_match (R5 / AC-7)
- test_batch_grant_all_items_on_success (R6 / R7 / AC-6)
- test_batch_partial_failure_reporting (R8 / AC-8)

The first three exercise GeminiService.quote_batch / BatchService.quote_batch_order
at the service layer; the last three exercise BatchService.settle_batch_order
with fakes injected via the constructor DI seam.
"""
from __future__ import annotations

import decimal
import json

import pytest


_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"
_BUYER = "BuyerWallet1111111111111111111111111111111111"


# --- Stub genai client (mirrors google-genai .models.generate_content) --------


class _StubResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubModels:
    def __init__(self, payload: dict, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.call_count = 0

    def generate_content(self, **kwargs):  # noqa: ANN003 (stub)
        self.call_count += 1
        if self.fail:
            raise RuntimeError("stub genai batch failure")
        return _StubResponse(json.dumps(self.payload))


class _StubClient:
    def __init__(self, payload: dict, fail: bool = False) -> None:
        self.models = _StubModels(payload, fail=fail)


# === R1 / AC-1: quote_batch_order sums total in integer min-units =============


@pytest.mark.django_db
def test_quote_batch_sums_total():
    """R1/AC-1: 3-item quote -> quoted order, 3 unit prices, total == sum.

    The total is accumulated in integer min-units (architecture §8) to avoid
    6-decimal rounding drift, then projected back to a USDC Decimal.
    """
    from apps.settlement.batch_services import BatchService
    from tests.factories import CreatorFactory, IpAssetFactory

    assets = [
        IpAssetFactory(
            creator=CreatorFactory(),
            min_price_usdc=decimal.Decimal("0.05"),
            target_price_usdc=decimal.Decimal("0.10"),
        )
        for _ in range(3)
    ]
    svc = BatchService(gemini=_FlatQuoteGemini(decimal.Decimal("0.05")))

    order, items = svc.quote_batch_order(
        buyer_agent_id="buyer-agent-1",
        asset_ids=[a.id for a in assets],
        usage_type="commercial",
    )

    assert order.status == "quoted"
    assert len(items) == 3
    # Each unit price is the quoted 0.05.
    assert all(it.unit_price_usdc == decimal.Decimal("0.05") for it in items)
    # Total is the integer min-units sum: 3 * 50000 = 150000 -> 0.15 USDC.
    assert order.total_usdc == decimal.Decimal("0.15")
    assert order.total_usdc.as_tuple().exponent == -6  # 6-decimal quantized


# === R2 / AC-2: unit_price respects MICRO_FLOOR ==============================


def test_quote_rejects_model_price_below_micro_floor():
    """모델이 가격 하한을 위반하면 임의 보정하지 않고 실패한다."""
    from services.gemini_service import GeminiResponseError, GeminiService
    # Model returns an below-floor price (0.01) for both items.
    payload = {
        "quotes": [
            {"asset_id": "a1", "unit_price_usdc": 0.01},
            {"asset_id": "a2", "unit_price_usdc": 0.02},
        ]
    }
    svc = GeminiService(client=_StubClient(payload))
    items = [
        {"asset_id": "a1", "min_price_usdc": decimal.Decimal("0.01")},
        {"asset_id": "a2", "min_price_usdc": decimal.Decimal("0.02")},
    ]

    with pytest.raises(GeminiResponseError):
        svc.quote_batch(items, usage_type="commercial")


# === R2 / AC-9: rule fallback when the model fails ===========================


def test_quote_batch_reports_unavailable_without_model_client():
    """모델 미설정 상태에서 가격을 규칙으로 계산하지 않는다."""
    from services.gemini_service import GeminiService, GeminiUnavailableError

    # No client at all -> immediate rule fallback path.
    svc = GeminiService()
    items = [
        # Above floor: unit should equal min_price.
        {"asset_id": "hi", "min_price_usdc": decimal.Decimal("0.30")},
        # Below floor: unit should be clamped UP to the floor.
        {"asset_id": "lo", "min_price_usdc": decimal.Decimal("0.01")},
    ]

    with pytest.raises(GeminiUnavailableError):
        svc.quote_batch(items, usage_type="commercial")

    # 전송 실패도 동일하게 명시적 오류다.
    failing = GeminiService(client=_StubClient({}, fail=True))
    from services.gemini_service import GeminiResponseError

    with pytest.raises(GeminiResponseError):
        failing.quote_batch(items, usage_type="commercial")


# === R5 / AC-7: settle requires the on-chain total to match ==================


@pytest.mark.django_db
def test_batch_settle_requires_total_match():
    """AC-7: on-chain payment total != BatchOrder.total_usdc -> ok=False (400)."""
    from apps.settlement.batch_services import BatchService
    from services._types import PaymentVerification
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory())
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.15"))
    BatchItemFactory(order=order, asset=asset, unit_price_usdc=decimal.Decimal("0.05"))

    from tests.fakes import FakeSolanaService

    solana = FakeSolanaService()
    solana.verification = PaymentVerification(
        is_valid=False,
        amount=decimal.Decimal("0.10"),  # short payment
        sender=_BUYER,
        slot=1,
        commitment="confirmed",
    )
    svc = BatchService(solana=solana, usdc_mint=_USDC_MINT)

    result = svc.settle_batch_order(order_id=order.id, tx_signature="tx_short")

    assert result.ok is False
    assert result.error == "invalid_settlement"
    order.refresh_from_db()
    assert order.status == "quoted"  # unchanged — settlement rejected


# === R6 / R7 / AC-6: all items granted on success ============================


@pytest.mark.django_db
def test_batch_grant_all_items_on_success():
    """R6/R7/AC-6: valid payment -> every BatchItem gets a License + token."""
    from apps.settlement.batch_services import BatchService
    from apps.settlement.models import BatchOrder, License
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(3)]
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.15"))
    for a in assets:
        BatchItemFactory(order=order, asset=a, unit_price_usdc=decimal.Decimal("0.05"))

    from services.license_service import LicenseService
    from tests.fakes import FakeBigQuery, FakeSolanaService

    svc = BatchService(
        solana=FakeSolanaService(),
        license_service=LicenseService(),
        bigquery=FakeBigQuery(),
        usdc_mint=_USDC_MINT,
    )

    result = svc.settle_batch_order(order_id=order.id, tx_signature="tx_ok")

    assert result.ok is True
    assert result.status == "settled"
    assert len(result.successes) == 3
    assert len(result.failures) == 0
    # Each success carries a download token/url.
    for s in result.successes:
        assert s.download_token
        assert s.download_url.startswith("/files/")
    # Order transitioned to settled; 3 licenses persisted, each linked to its item.
    order.refresh_from_db()
    assert order.status == BatchOrder.SETTLED
    assert order.payment_tx_sig == "tx_ok"
    assert License.objects.filter(payment_tx_sig__startswith="batch:tx_ok").count() == 3
    # Every BatchItem now has a license FK.
    for item in order.items.all():
        assert item.license_id is not None


# === R8 / AC-8: partial failure reporting ====================================


@pytest.mark.django_db
def test_batch_partial_failure_reporting():
    """AC-8/R8: one item grant fails -> status=partial, success/fail split."""
    from apps.settlement.batch_services import BatchService
    from apps.settlement.models import BatchOrder
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(3)]
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.15"))
    for a in assets:
        BatchItemFactory(order=order, asset=a, unit_price_usdc=decimal.Decimal("0.05"))

    from tests.fakes import FakeBigQuery, FakeLicenseService, FakeSolanaService

    # Inject a license service that fails to grant for the 2nd asset.
    failing_license = FakeLicenseService(fail_on_asset_ids={assets[1].id})

    svc = BatchService(
        solana=FakeSolanaService(),
        license_service=failing_license,
        bigquery=FakeBigQuery(),
        usdc_mint=_USDC_MINT,
    )

    result = svc.settle_batch_order(order_id=order.id, tx_signature="tx_partial")

    assert result.ok is True  # the call itself succeeded (partial is not an error)
    assert result.status == "partial"
    assert len(result.successes) == 2
    assert len(result.failures) == 1
    assert result.failures[0].asset_id == str(assets[1].id)
    assert result.failures[0].error  # non-empty reason
    # Retry info is surfaced for the failed item.
    assert result.failures[0].retry is True
    order.refresh_from_db()
    assert order.status == BatchOrder.PARTIAL


# === R10 edge: idempotency & order-state guards ==============================


@pytest.mark.django_db
def test_batch_settle_unknown_order():
    """settle_batch_order on a missing order_id -> ok=False, error=not_found."""
    import uuid

    from apps.settlement.batch_services import BatchService
    from tests.fakes import FakeSolanaService

    svc = BatchService(solana=FakeSolanaService(), usdc_mint=_USDC_MINT)
    result = svc.settle_batch_order(order_id=uuid.uuid4(), tx_signature="tx_x")

    assert result.ok is False
    assert result.error == "not_found"


@pytest.mark.django_db
def test_batch_settle_idempotent_replay_partial():
    """R10: replaying the same tx on a PARTIAL order rebuilds the same split
    without re-granting (no new licenses, failures still reported)."""
    from apps.settlement.batch_services import BatchService
    from apps.settlement.models import BatchOrder, License
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(3)]
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.15"))
    for a in assets:
        BatchItemFactory(order=order, asset=a, unit_price_usdc=decimal.Decimal("0.05"))

    from services.license_service import LicenseService
    from tests.fakes import FakeBigQuery, FakeSolanaService

    # Real LicenseService (persists rows so the item.license FK survives) with a
    # per-asset failure injected for the 2nd asset.
    failing_license = _FailingRealLicense(
        fail_on_asset_ids={assets[1].id}
    )
    svc = BatchService(
        solana=FakeSolanaService(),
        license_service=failing_license,
        bigquery=FakeBigQuery(),
        usdc_mint=_USDC_MINT,
    )

    r1 = svc.settle_batch_order(order_id=order.id, tx_signature="tx_replay")
    assert r1.status == "partial"
    licenses_after_first = License.objects.count()

    # Replay the SAME (order_id, tx_signature).
    r2 = svc.settle_batch_order(order_id=order.id, tx_signature="tx_replay")
    assert r2.ok is True
    assert r2.status == "partial"
    assert len(r2.successes) == 2
    assert len(r2.failures) == 1
    # No new licenses created on replay.
    assert License.objects.count() == licenses_after_first
    order.refresh_from_db()
    assert order.status == BatchOrder.PARTIAL


@pytest.mark.django_db
def test_batch_settle_rejects_different_tx_on_settled():
    """A second, different tx_signature on an already-settled order is rejected
    (would otherwise double-license via different per-item keys)."""
    from apps.settlement.batch_services import BatchService
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(2)]
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.10"))
    for a in assets:
        BatchItemFactory(order=order, asset=a, unit_price_usdc=decimal.Decimal("0.05"))

    from services.license_service import LicenseService
    from tests.fakes import FakeBigQuery, FakeSolanaService

    svc = BatchService(
        solana=FakeSolanaService(),
        license_service=LicenseService(),
        bigquery=FakeBigQuery(),
        usdc_mint=_USDC_MINT,
    )

    first = svc.settle_batch_order(order_id=order.id, tx_signature="tx_one")
    assert first.ok is True and first.status == "settled"

    second = svc.settle_batch_order(order_id=order.id, tx_signature="tx_two")
    assert second.ok is False
    assert second.error == "already_settled"


# --- shared test helper ------------------------------------------------------


class _FlatQuoteGemini:
    """Minimal Gemini stand-in returning a flat unit price for every item.

    Avoids the genai stub machinery for the BatchService-level test; stands in
    for GeminiService.quote_batch contractually.
    """

    def __init__(self, unit_price: decimal.Decimal) -> None:
        self.unit_price = unit_price
        self.calls: list[tuple] = []

    def quote_batch(self, items: list[dict], usage_type: str):
        from services._types import BatchQuote

        self.calls.append((items, usage_type))
        return [
            BatchQuote(asset_id=i["asset_id"], unit_price_usdc=self.unit_price)
            for i in items
        ]


class _FailingRealLicense:
    """Real-LicenseService wrapper that injects a per-asset grant failure.

    Used by the partial-REPLAY test: successful grants persist real License
    rows (so the ``BatchItem.license`` FK survives for ``_rebuild_result``),
    while the nominated asset raises on every grant attempt.
    """

    def __init__(self, fail_on_asset_ids: set) -> None:
        from services.license_service import LicenseService

        self._inner = LicenseService()
        self.fail_on_asset_ids = set(fail_on_asset_ids or ())

    def grant(self, asset, buyer_wallet, price, usage_type, payment_tx, session=None):
        if getattr(asset, "id", None) in self.fail_on_asset_ids:
            raise RuntimeError(f"forced grant failure for asset {asset.id}")
        return self._inner.grant(
            asset, buyer_wallet, price, usage_type, payment_tx, session=session
        )

    def __getattr__(self, name):
        # Delegate anything else (e.g. is_licensed) to the real service.
        return getattr(self._inner, name)
