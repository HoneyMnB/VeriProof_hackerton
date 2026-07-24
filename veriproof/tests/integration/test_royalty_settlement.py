"""SPEC-008 integration — escrow settlement + royalty distribution (R4/R5/R6, AC-9).

Drives the full ``SettlementService.settle_pipeline`` with the REAL
``RoyaltyService`` wired in (SPEC-008) and a ``FakeSolanaService`` for the
verify/cert/transfer seams. Covers the §5 integration list:

- test_royalty_settlement_requires_escrow_recipient (AC-9)
- test_royalty_settlement_grants_license_then_distributes (R4 / R14b)
- test_royalty_settlement_end_to_end_split (mock RPC, 10 USDC -> 3/7)

The settlement pipeline SSOT (apps/settlement/services.py) already calls
``RoyaltyService.distribute(granted)`` as step F when ``asset.parent_asset_id``
is set (SPEC-004 R14b); these tests prove the now-implemented distribute runs
end-to-end through that call site.
"""
from __future__ import annotations

import decimal

import pytest

from tests.conftest import VALID_WALLET
from tests.fakes import FakeBigQuery, FakeFirestore, FakeSolanaService

_BUYER = "BuyerWallet1111111111111111111111111111111111"
_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"
_ESCROW = "EscrowWallet1111111111111111111111111111111111"
_ORIG_WALLET = "OrigWallet1111111111111111111111111111111111"
_SEC_WALLET = "SecWallet111111111111111111111111111111111111"


class _RecordingRecorder:
    """Persists AgentEvents to DB and captures record() calls for assertions."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def record(self, type, payload, asset=None, session=None):
        self.calls.append((type, payload or {}))
        from apps.common.models import AgentEvent

        AgentEvent.objects.create(
            type=type, payload=payload or {}, asset=asset, session=session
        )
        return None


def _pipeline_with_real_royalty(*, solana, recorder, escrow_solana=None):
    """Build a SettlementService whose RoyaltyService is the REAL implementation,
    wired with a FakeSolanaService for the transfer seam (architecture 8 idempotency
    + KMS signing are exercised by the transfer_usdc seam, not real RPC)."""
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from services.royalty_service import RoyaltyService

    royalty_solana = escrow_solana if escrow_solana is not None else solana
    return SettlementService(
        solana=solana,
        license_service=LicenseService(event_recorder=recorder),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=RoyaltyService(solana=royalty_solana, event_recorder=recorder),
        event_recorder=recorder,
        usdc_mint=_USDC_MINT,
    )


def _make_second_creation(*, price=decimal.Decimal("10.0"), bps=3000):
    """Build a parent + child IpAsset with the given royalty share."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    parent = IpAssetFactory(
        creator=CreatorFactory(wallet_address=_ORIG_WALLET),
        status=IpAsset.LISTED,
    )
    child = IpAssetFactory(
        creator=CreatorFactory(wallet_address=_SEC_WALLET),
        parent_asset=parent,
        royalty_share_bps=bps,
        target_price_usdc=price,
    )
    return parent, child


# === AC-9: escrow recipient verification ====================================


@pytest.mark.django_db
def test_royalty_settlement_requires_escrow_recipient(settings):
    """AC-9: a 2nd-creation asset settles against PLATFORM_ESCROW_PUBKEY, NOT the
    creator wallet. verify_usdc_payment is called with the escrow recipient, and
    distribute runs after the license is granted."""
    from apps.settlement.models import RoyaltyDistribution

    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW
    _, child = _make_second_creation()
    solana = FakeSolanaService()
    recorder = _RecordingRecorder()
    svc = _pipeline_with_real_royalty(solana=solana, recorder=recorder)

    result = svc.settle_pipeline(
        asset=child,
        session=None,
        tx_signature="tx_escrow_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("10.0"),
        usage_type="commercial",
    )

    assert result.ok is True
    # AC-9: the verify call used the ESCROW recipient (resolve_pay_to SSOT), not
    # the child creator wallet.
    verify_calls = [c for c in solana.calls if c[0] == "verify_usdc_payment"]
    assert len(verify_calls) == 1
    assert verify_calls[0][1][1] == _ESCROW  # expected_recipient == escrow

    # distribute ran (step F) after the grant: 2 RoyaltyDistribution rows exist.
    assert RoyaltyDistribution.objects.filter(license=result.license).count() == 2


# === R4 / R14b: grant-then-distribute =======================================


@pytest.mark.django_db
def test_royalty_settlement_grants_license_then_distributes(settings):
    """R4 / R14b: settlement grants the license first, THEN distributes royalties.
    The granted license is what distribute receives; royalty rows reference it."""
    from apps.settlement.models import License, RoyaltyDistribution

    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW
    _, child = _make_second_creation()
    solana = FakeSolanaService()
    recorder = _RecordingRecorder()
    svc = _pipeline_with_real_royalty(solana=solana, recorder=recorder)

    result = svc.settle_pipeline(
        asset=child,
        session=None,
        tx_signature="tx_grant_dist_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("10.0"),
        usage_type="commercial",
    )

    assert result.ok is True
    granted = License.objects.get(payment_tx_sig="tx_grant_dist_001")
    assert result.license.id == granted.id

    # Royalty rows reference the granted license and are settled.
    rows = list(RoyaltyDistribution.objects.filter(license=granted))
    assert len(rows) == 2
    assert {r.status for r in rows} == {RoyaltyDistribution.SETTLED}
    # A ROYALTY_SPLIT event was fanned out via the shared recorder.
    assert any(t == "ROYALTY_SPLIT" for t, _ in recorder.calls)


# === End-to-end split (mock RPC): 10 USDC -> 3 / 7 ===========================


@pytest.mark.django_db
def test_royalty_settlement_end_to_end_split(settings):
    """SPEC §5: 10 USDC at bps=3000 -> original 3.0 / secondary 7.0, 2 transfer
    tx sigs, both settled, license granted. Mock RPC via FakeSolanaService."""
    from apps.settlement.models import RoyaltyDistribution

    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW
    parent, child = _make_second_creation(bps=3000)
    solana = FakeSolanaService()
    recorder = _RecordingRecorder()
    svc = _pipeline_with_real_royalty(solana=solana, recorder=recorder)

    result = svc.settle_pipeline(
        asset=child,
        session=None,
        tx_signature="tx_e2e_split_001",
        buyer_wallet=_BUYER,
        expected_amount=decimal.Decimal("10.0"),
        usage_type="commercial",
    )

    assert result.ok is True
    assert result.status == "SUCCESS"

    # 2 escrow payouts: 3.0 to the original creator, 7.0 to the 2nd creator.
    transfers = [c for c in solana.calls if c[0] == "transfer_usdc"]
    assert len(transfers) == 2
    by_wallet = {c[1][0]: c[1][1] for c in transfers}
    assert by_wallet[_ORIG_WALLET] == decimal.Decimal("3.000000")
    assert by_wallet[_SEC_WALLET] == decimal.Decimal("7.000000")

    rows = list(RoyaltyDistribution.objects.filter(license=result.license))
    assert len(rows) == 2
    role_amounts = {r.role: r.amount_usdc for r in rows}
    assert role_amounts[RoyaltyDistribution.ORIGINAL] == decimal.Decimal("3.000000")
    assert role_amounts[RoyaltyDistribution.SECONDARY] == decimal.Decimal("7.000000")
    # Both legs settled with a transfer tx sig.
    assert all(r.status == RoyaltyDistribution.SETTLED for r in rows)
    assert all(r.transfer_tx_sig for r in rows)
