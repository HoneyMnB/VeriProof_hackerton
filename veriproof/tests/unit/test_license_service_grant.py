"""SPEC-004 unit tests — LicenseService.grant (R4/R5/R7/R8, AC-4).

Covers the TDD list (3):
- test_grant_creates_license_with_payment_tx (R4)
- test_grant_is_idempotent_on_duplicate_tx (R5 / AC-4)
- test_grant_generates_expiring_download_token (R7)
Plus session linking (R8) and PAYMENT_VERIFIED event recording (R15).
"""
from __future__ import annotations

import datetime
import decimal

import pytest


@pytest.mark.django_db
def test_grant_creates_license_with_payment_tx(monkeypatch):
    """R4: a valid grant creates a License row storing payment_tx_sig."""
    from apps.settlement.models import License
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    # Inject a noop event recorder so grant() can call record() without DB sinks.
    recorder = _NoopRecorder()
    svc = LicenseService(event_recorder=recorder)

    license = svc.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_create_001",
    )

    assert isinstance(license, License)
    assert license.payment_tx_sig == "tx_create_001"
    assert license.asset_id == asset.id
    assert license.buyer_wallet == "BuyerWallet111111111111111111111111111111"
    assert license.price_usdc == decimal.Decimal("1.500000")
    # Exactly one License row for this tx.
    assert License.objects.filter(payment_tx_sig="tx_create_001").count() == 1


@pytest.mark.django_db
def test_grant_is_idempotent_on_duplicate_tx(monkeypatch):
    """R5 / AC-4: re-granting the same payment_tx returns the existing License."""
    from apps.settlement.models import License
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    svc = LicenseService(event_recorder=_NoopRecorder())

    first = svc.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_dup_001",
    )
    second = svc.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_dup_001",  # SAME tx -> idempotent
    )

    assert second.id == first.id
    assert License.objects.filter(payment_tx_sig="tx_dup_001").count() == 1


@pytest.mark.django_db
def test_distinct_payments_create_distinct_licenses_for_the_same_asset():
    """각 검증 결제는 같은 작품에 대해서도 별도 구매 License가 된다."""
    from django.contrib.auth.models import User

    from apps.settlement.models import License
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    first_buyer = User.objects.create_user("first@example.com", "first@example.com", "safe-password-123")
    second_buyer = User.objects.create_user("second@example.com", "second@example.com", "safe-password-123")
    service = LicenseService(event_recorder=_NoopRecorder())

    first = service.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_first_purchase_001",
        buyer_user=first_buyer,
    )
    second = service.grant(
        asset,
        buyer_wallet="BuyerWallet222222222222222222222222222222",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_second_purchase_001",
        buyer_user=second_buyer,
    )

    assert first.id != second.id
    assert License.objects.filter(asset=asset).count() == 2
    assert {first.buyer_user_id, second.buyer_user_id} == {first_buyer.id, second_buyer.id}


@pytest.mark.django_db
def test_grant_generates_expiring_download_token(monkeypatch):
    """R7: grant issues a random download_token + download_expires_at."""
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    svc = LicenseService(
        download_token_ttl_seconds=3600, event_recorder=_NoopRecorder()
    )

    license = svc.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_token_001",
    )

    assert license.download_token  # non-empty urlsafe string
    assert len(license.download_token) >= 16
    assert license.download_expires_at is not None
    # Expiry is roughly now + TTL (within a generous window).
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = license.download_expires_at - now
    assert datetime.timedelta(seconds=3500) <= delta <= datetime.timedelta(seconds=3700)


@pytest.mark.django_db
def test_default_grant_download_right_lasts_seven_days(settings):
    """A paid license grants download access for 7 days by default."""
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory

    settings.DOWNLOAD_TOKEN_TTL_SECONDS = 604800
    asset = IpAssetFactory(creator=CreatorFactory())

    license = LicenseService(event_recorder=_NoopRecorder()).grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_token_7_days_001",
    )

    delta = license.download_expires_at - datetime.datetime.now(datetime.timezone.utc)
    assert datetime.timedelta(days=7, seconds=-5) <= delta <= datetime.timedelta(days=7, seconds=5)


@pytest.mark.django_db
def test_grant_links_session_when_provided(monkeypatch):
    """R8: when a session is passed, the License is linked to it."""
    from services.license_service import LicenseService
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        NegotiationSessionFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory())
    session = NegotiationSessionFactory(asset=asset)
    svc = LicenseService(event_recorder=_NoopRecorder())

    license = svc.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_session_001",
        session=session,
    )

    assert license.session_id == session.id


@pytest.mark.django_db
def test_grant_records_payment_verified_event(monkeypatch):
    """R15: grant fans out a PAYMENT_VERIFIED event on FIRST grant only."""
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    recorder = _NoopRecorder()
    svc = LicenseService(event_recorder=recorder)

    svc.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_event_001",
    )
    # First grant records exactly one PAYMENT_VERIFIED.
    types = [t for t, _ in recorder.calls]
    assert types.count("PAYMENT_VERIFIED") == 1

    # Idempotent re-grant does NOT record a second event.
    svc.grant(
        asset,
        buyer_wallet="BuyerWallet111111111111111111111111111111",
        price=decimal.Decimal("1.5"),
        usage_type="commercial",
        payment_tx="tx_event_001",
    )
    assert types is not [t for t, _ in recorder.calls]  # noqa: E721 (sanity)
    assert [t for t, _ in recorder.calls].count("PAYMENT_VERIFIED") == 1


class _NoopRecorder:
    """Minimal EventRecorder stand-in: records (type, payload) calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def record(self, type: str, payload: dict, asset=None, session=None):
        self.calls.append((type, payload))
        return None
