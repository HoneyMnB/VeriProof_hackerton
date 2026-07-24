"""SPEC-008 unit tests — RoyaltyService.distribute (R5/R6/R7/R8/R9, AC-4..AC-8).

Covers the §5 TDD unit list (5):
- test_split_computes_original_and_secondary (AC-4)
- test_split_uses_integer_min_units_no_loss (AC-6 / R8)
- test_distribute_transfers_to_both_wallets (AC-5)
- test_distribute_partial_failure_keeps_success (AC-7 / R9)
- test_distribute_records_royalty_events (AC-8)

The split math is exposed as the pure static helper ``RoyaltyService.compute_split``
so the two calculation tests run offline without a DB. ``distribute`` is the
side-effectful orchestrator (transfer + record + event) and is driven through
the established ``FakeSolanaService.transfer_usdc`` seam.
"""
from __future__ import annotations

import decimal

import pytest

_BUYER = "BuyerWallet1111111111111111111111111111111111"


# === Pure split math (AC-4 / AC-6 / R8) ======================================


def test_split_computes_original_and_secondary():
    """AC-4: total=10, bps=3000 -> original=3.0, secondary=7.0."""
    from services.royalty_service import RoyaltyService

    original, secondary = RoyaltyService.compute_split(
        decimal.Decimal("10"), 3000
    )
    assert original == decimal.Decimal("3.000000")
    assert secondary == decimal.Decimal("7.000000")


@pytest.mark.parametrize(
    "price,bps",
    [
        ("10", 3000),
        ("10", 3333),   # awkward divisor
        ("10", 9999),   # near-total to original, tiny remainder
        ("10", 1),      # minimum share
        ("1", 1),
        ("0.05", 3333), # sub-cent edge
        ("3", 3000),
        ("99.99", 7777),
        ("0.01", 5000), # single min-unit territory
    ],
)
def test_split_uses_integer_min_units_no_loss(price, bps):
    """AC-6 / R8: original + secondary == total EXACTLY via integer min-units.

    The split is computed in 6-decimal min-units so no fractional min-unit is
    ever lost. The remainder always goes to secondary (the seller / 2nd creator).
    """
    from services.royalty_service import RoyaltyService

    price_d = decimal.Decimal(price)
    original, secondary = RoyaltyService.compute_split(price_d, bps)

    # Decimal-level exactness (both legs quantized to 6dp, price is 6dp).
    assert original + secondary == price_d
    # Integer min-units exactness (architecture 8): zero remainder loss.
    to_min = lambda d: int((d * 1_000_000).to_integral_value())
    assert to_min(original) + to_min(secondary) == to_min(price_d)
    # Remainder归 secondary: original is floor(total * bps / 10000) in min-units.
    total_min = to_min(price_d)
    assert to_min(original) == total_min * bps // 10000
    assert to_min(secondary) == total_min - to_min(original)


# === distribute orchestrator (AC-5 / AC-7 / AC-8) ============================


@pytest.mark.django_db
def test_distribute_transfers_to_both_wallets():
    """AC-5: distribute transfers to original + secondary wallets, 2 settled rows.

    FakeSolanaService.transfer_usdc records each call so we assert the recipient
    wallets and amounts, then assert 2 RoyaltyDistribution rows with status=
    settled and a stored transfer_tx_sig.
    """
    from apps.settlement.models import RoyaltyDistribution
    from services.royalty_service import RoyaltyService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory
    from tests.fakes import FakeSolanaService

    parent_creator = CreatorFactory(wallet_address="OrigWallet1111111111111111111111111111111111")
    parent_asset = IpAssetFactory(creator=parent_creator)
    child_creator = CreatorFactory(wallet_address="SecWallet111111111111111111111111111111111111")
    child_asset = IpAssetFactory(
        creator=child_creator,
        parent_asset=parent_asset,
        royalty_share_bps=3000,
    )
    license = LicenseFactory(asset=child_asset, price_usdc=decimal.Decimal("10.0"))

    solana = FakeSolanaService()
    svc = RoyaltyService(solana=solana, event_recorder=_NoopRecorder())

    records = svc.distribute(license)

    # 2 transfer_usdc calls to the 2 distinct wallets with the split amounts.
    transfers = [c for c in solana.calls if c[0] == "transfer_usdc"]
    assert len(transfers) == 2
    by_wallet = {c[1][0]: c[1][1] for c in transfers}  # wallet -> amount
    assert by_wallet["OrigWallet1111111111111111111111111111111111"] == decimal.Decimal("3.000000")
    assert by_wallet["SecWallet111111111111111111111111111111111111"] == decimal.Decimal("7.000000")

    # 2 RoyaltyDistribution rows, both settled, each with a transfer tx sig.
    assert len(records) == 2
    statuses = {r.status for r in records}
    assert statuses == {RoyaltyDistribution.SETTLED}
    for r in records:
        assert r.transfer_tx_sig  # non-empty
    rows = RoyaltyDistribution.objects.filter(license=license)
    assert rows.count() == 2
    roles = {r.role for r in rows}
    assert roles == {RoyaltyDistribution.ORIGINAL, RoyaltyDistribution.SECONDARY}


@pytest.mark.django_db
def test_distribute_partial_failure_keeps_success():
    """AC-7 / R9: secondary transfer failure -> original stays settled,
    failed leg is status=failed with no tx_sig, settlement is not aborted.

    Failure is injected by routing the secondary wallet's transfer through a
    FakeSolanaService subclass that raises on that recipient only.
    """
    from apps.settlement.models import RoyaltyDistribution
    from services.royalty_service import RoyaltyService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory
    from tests.fakes import FakeSolanaService

    orig_wallet = "OrigWallet1111111111111111111111111111111111"
    sec_wallet = "SecWallet111111111111111111111111111111111111"
    parent_asset = IpAssetFactory(creator=CreatorFactory(wallet_address=orig_wallet))
    child_asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=sec_wallet),
        parent_asset=parent_asset,
        royalty_share_bps=3000,
    )
    license = LicenseFactory(asset=child_asset, price_usdc=decimal.Decimal("10.0"))

    class _PartialFailSolana(FakeSolanaService):
        """Fails transfer_usdc only when the recipient is the secondary wallet."""

        def transfer_usdc(self, to_pubkey, amount):
            if to_pubkey == sec_wallet:
                raise RuntimeError("forced secondary transfer failure")
            return super().transfer_usdc(to_pubkey, amount)

    svc = RoyaltyService(solana=_PartialFailSolana(), event_recorder=_NoopRecorder())

    records = svc.distribute(license)

    assert len(records) == 2
    by_role = {r.role: r for r in records}
    # Original leg survived (settled, with tx sig).
    assert by_role[RoyaltyDistribution.ORIGINAL].status == RoyaltyDistribution.SETTLED
    assert by_role[RoyaltyDistribution.ORIGINAL].transfer_tx_sig
    assert by_role[RoyaltyDistribution.ORIGINAL].amount_usdc == decimal.Decimal("3.000000")
    # Secondary leg failed (no tx sig, status=failed).
    assert by_role[RoyaltyDistribution.SECONDARY].status == RoyaltyDistribution.FAILED
    assert by_role[RoyaltyDistribution.SECONDARY].transfer_tx_sig in (None, "")
    assert by_role[RoyaltyDistribution.SECONDARY].amount_usdc == decimal.Decimal("7.000000")


@pytest.mark.django_db
def test_distribute_records_royalty_events():
    """AC-8: distribute fans out a ROYALTY_SPLIT event summarising the split."""
    from services.royalty_service import RoyaltyService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory
    from tests.fakes import FakeSolanaService

    parent_asset = IpAssetFactory(creator=CreatorFactory(wallet_address="OrigWallet1111111111111111111111111111111111"))
    child_asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address="SecWallet111111111111111111111111111111111111"),
        parent_asset=parent_asset,
        royalty_share_bps=3000,
    )
    license = LicenseFactory(asset=child_asset, price_usdc=decimal.Decimal("10.0"))

    recorder = _RecordingRecorder()
    svc = RoyaltyService(solana=FakeSolanaService(), event_recorder=recorder)

    svc.distribute(license)

    types = [t for t, _ in recorder.calls]
    assert "ROYALTY_SPLIT" in types
    payload = next(p for t, p in recorder.calls if t == "ROYALTY_SPLIT")
    # Payload summarises the distribution.
    assert payload["asset_id"] == str(child_asset.id)
    assert payload["license_id"] == str(license.id)
    assert payload["royalty_share_bps"] == 3000
    assert payload["total_usdc"] == "10.000000"
    assert set(payload["legs"].keys()) == {"original", "secondary"}
    assert payload["legs"]["original"]["amount_usdc"] == "3.000000"
    assert payload["legs"]["secondary"]["amount_usdc"] == "7.000000"


# === Same-wallet edge (§6) ===================================================


@pytest.mark.django_db
def test_distribute_same_wallet_combines_into_single_transfer():
    """§6 edge: original == secondary wallet -> single combined transfer.

    When the parent creator and the 2nd creator share a wallet, paying both
    legs separately would just burn an extra on-chain tx. We collapse the two
    shares into one transfer of the full total to that wallet.
    """
    from apps.settlement.models import RoyaltyDistribution
    from services.royalty_service import RoyaltyService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory
    from tests.fakes import FakeSolanaService

    same_wallet = "SameWallet11111111111111111111111111111111111"
    creator = CreatorFactory(wallet_address=same_wallet)
    parent_asset = IpAssetFactory(creator=creator)
    child_asset = IpAssetFactory(
        creator=creator,  # same creator -> same wallet
        parent_asset=parent_asset,
        royalty_share_bps=3000,
    )
    license = LicenseFactory(asset=child_asset, price_usdc=decimal.Decimal("10.0"))

    solana = FakeSolanaService()
    svc = RoyaltyService(solana=solana, event_recorder=_NoopRecorder())

    records = svc.distribute(license)

    transfers = [c for c in solana.calls if c[0] == "transfer_usdc"]
    # A single combined transfer of the full total to the shared wallet.
    assert len(transfers) == 1
    assert transfers[0][1][0] == same_wallet
    assert transfers[0][1][1] == decimal.Decimal("10.000000")
    # Exactly one RoyaltyDistribution row recorded for the combined leg.
    assert len(records) == 1
    assert records[0].status == RoyaltyDistribution.SETTLED
    assert RoyaltyDistribution.objects.filter(license=license).count() == 1


# === Idempotency (architecture 8) ============================================


@pytest.mark.django_db
def test_distribute_is_idempotent_on_replay():
    """Architecture 8 idempotency: replaying distribute for a settled license
    does NOT re-transfer (no double-pay). Already-settled legs are returned as-is.
    """
    from services.royalty_service import RoyaltyService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory
    from tests.fakes import FakeSolanaService

    parent_asset = IpAssetFactory(creator=CreatorFactory(wallet_address="OrigWallet1111111111111111111111111111111111"))
    child_asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address="SecWallet111111111111111111111111111111111111"),
        parent_asset=parent_asset,
        royalty_share_bps=3000,
    )
    license = LicenseFactory(asset=child_asset, price_usdc=decimal.Decimal("10.0"))

    solana = FakeSolanaService()
    svc = RoyaltyService(solana=solana, event_recorder=_NoopRecorder())

    first = svc.distribute(license)
    second = svc.distribute(license)  # replay

    # Only the 2 original transfers happened — no double-pay on replay.
    transfers = [c for c in solana.calls if c[0] == "transfer_usdc"]
    assert len(transfers) == 2
    assert {r.id for r in first} == {r.id for r in second}


# === Helpers =================================================================


class _NoopRecorder:
    """EventRecorder stand-in that swallows record() (no DB sinks needed)."""

    def record(self, *args, **kwargs):  # noqa: D401
        return None


class _RecordingRecorder:
    """Captures every record(type, payload, ...) call for assertions."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def record(self, type, payload, asset=None, session=None):
        self.calls.append((type, payload or {}))
        return None
