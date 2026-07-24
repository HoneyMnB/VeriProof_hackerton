"""SPEC-004 integration — settlement pipeline SSOT (R14/R14b/R15, AC-11/AC-12).

The pipeline function is the SINGLE SOURCE OF TRUTH for settlement: the
``/settle`` view and the pay.sh webhook sync fallback both call it, and GCP
Workflows calls the SAME service methods in the SAME order. This file drives
that contract directly (one level below the HTTP view).

TDD list (1):
- test_settlement_pipeline_runs_all_steps_in_order
  verify -> grant -> cert -> firestore(status=LICENSED) -> bigquery(transactions)
Plus R14b royalty branching for 2nd-creation vs standalone.
"""
from __future__ import annotations

import decimal

import pytest

from tests.conftest import VALID_WALLET
from tests.fakes import FakeBigQuery, FakeFirestore, FakeRoyaltyService

_BUYER = "BuyerWallet1111111111111111111111111111111111"
_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"


class _RecordingRecorder:
    """Persists AgentEvent to DB AND records every record() call for assertions."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def record(self, type, payload, asset=None, session=None):
        self.calls.append((type, payload or {}))
        from apps.common.models import AgentEvent

        AgentEvent.objects.create(
            type=type, payload=payload or {}, asset=asset, session=session
        )
        return None


def _pipeline(*, solana=None, firestore=None, bigquery=None, royalty=None,
              recorder=None):
    """Build a SettlementService with injected fakes (license_service stays real)."""
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from tests.fakes import FakeSolanaService

    return SettlementService(
        solana=solana or FakeSolanaService(),
        license_service=LicenseService(event_recorder=recorder),
        firestore=firestore or FakeFirestore(),
        bigquery=bigquery or FakeBigQuery(),
        royalty_service=royalty or FakeRoyaltyService(),
        event_recorder=recorder,
        usdc_mint=_USDC_MINT,
    )


# === R14 / AC-11 / AC-12: step order =========================================


@pytest.mark.django_db
def test_settlement_pipeline_runs_all_steps_in_order():
    """verify -> grant -> cert -> firestore(LICENSED) -> bigQuery -> CERT_ISSUED."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_usdc=decimal.Decimal("1.5"),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    recorder = _RecordingRecorder()
    fs, bq = FakeFirestore(), FakeBigQuery()
    royalty = FakeRoyaltyService()
    svc = _pipeline(firestore=fs, bigquery=bq, royalty=royalty, recorder=recorder)

    result = svc.settle_pipeline(
        asset=asset,
        session=None,
        tx_signature="tx_pipeline_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("1.5"),
        usage_type="commercial",
    )

    # Success envelope.
    assert result.ok is True
    assert result.status == "SUCCESS"
    assert result.certificate_tx is not None
    assert result.download_url.startswith("/files/")
    assert result.download_expires_at is not None

    # AC-12: PAYMENT_VERIFIED (from grant) + CERT_ISSUED (from pipeline) recorded.
    types = [t for t, _ in recorder.calls]
    assert "PAYMENT_VERIFIED" in types
    assert "CERT_ISSUED" in types

    # AC-11: Firestore mirrored status=LICENSED at asset_id.
    asset_status_calls = [
        c for c in fs.calls
        if c[0] == "set" and c[1]["collection"] == "asset_status"
    ]
    assert len(asset_status_calls) == 1
    assert asset_status_calls[0][1]["data"]["status"] == "LICENSED"

    # AC-11: BigQuery transactions row inserted with the payment tx.
    tx_inserts = [
        c for c in bq.calls
        if c[0] == "insert" and c[1]["table"] == "transactions"
    ]
    assert len(tx_inserts) == 1
    assert tx_inserts[0][1]["row"]["payment_tx_sig"] == "tx_pipeline_001"


# === R14b: royalty branching =================================================


@pytest.mark.django_db
def test_pipeline_calls_royalty_for_secondary_creation():
    """R14b: 2nd-creation (parent_asset set) -> RoyaltyService.distribute called."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    parent = IpAssetFactory(creator=creator, status=IpAsset.LISTED)
    child = IpAssetFactory(
        creator=creator,
        parent_asset=parent,
        royalty_share_bps=3000,
        target_price_usdc=decimal.Decimal("3.0"),
    )
    recorder = _RecordingRecorder()
    royalty = FakeRoyaltyService()
    svc = _pipeline(royalty=royalty, recorder=recorder)

    result = svc.settle_pipeline(
        asset=child,
        session=None,
        tx_signature="tx_secondary_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("3.0"),
        usage_type="commercial",
    )

    assert result.ok is True
    # R14b: distribute was called exactly once with the granted license.
    assert len(royalty.calls) == 1
    assert getattr(royalty.calls[0], "payment_tx_sig", None) == "tx_secondary_001"


@pytest.mark.django_db
def test_pipeline_skips_royalty_for_standalone_asset():
    """R14b: standalone asset (no parent) -> RoyaltyService NOT called."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    royalty = FakeRoyaltyService()
    svc = _pipeline(royalty=royalty, recorder=_RecordingRecorder())

    result = svc.settle_pipeline(
        asset=asset,
        session=None,
        tx_signature="tx_standalone_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("3.0"),
        usage_type="commercial",
    )

    assert result.ok is True
    assert royalty.calls == []  # NOT called for standalone


# === R16: certificate failure decoupling =====================================


@pytest.mark.django_db
def test_pipeline_cert_failure_keeps_license_and_null_cert():
    """R16 / AC-10: issue_certificate failure -> license kept, certificate_tx None."""
    from apps.settlement.models import License
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeSolanaService

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    solana = FakeSolanaService(fail_issue_cert=True)
    svc = _pipeline(solana=solana, recorder=_RecordingRecorder())

    result = svc.settle_pipeline(
        asset=asset,
        session=None,
        tx_signature="tx_certfail_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("3.0"),
        usage_type="commercial",
    )

    assert result.ok is True  # settlement still succeeds
    assert result.certificate_tx is None  # cert decoupled
    # License persists with certificate_tx_sig=None.
    license = License.objects.get(payment_tx_sig="tx_certfail_001")
    assert license.certificate_tx_sig is None


# === R3: invalid settlement ==================================================


@pytest.mark.django_db
def test_pipeline_invalid_verification_returns_invalid_result():
    """R3: verify returns is_valid=False -> result.ok=False, no license granted."""
    from services._types import PaymentVerification
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeSolanaService

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    solana = FakeSolanaService()
    solana.verification = PaymentVerification(
        is_valid=False, amount=decimal.Decimal("1.0"), sender="x", slot=1,
        commitment="confirmed",
    )
    svc = _pipeline(solana=solana, recorder=_RecordingRecorder())

    result = svc.settle_pipeline(
        asset=asset,
        session=None,
        tx_signature="tx_invalid_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("3.0"),
        usage_type="commercial",
    )

    assert result.ok is False
    assert result.status == "INVALID"
    assert result.error == "invalid_settlement"
    assert result.license is None
