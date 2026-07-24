"""SPEC-002 unit tests — LicenseService.is_licensed (R10).

Covers the three branches of the DB-first short-circuit contract:
- DB license pre-exists -> True, zero on-chain calls (AC-7, also in the unit
  list at the end of test_x402_service.py).
- No tx_sig supplied -> False without hitting the DB license path's verify.
- No DB license, tx_sig supplied -> lazy on-chain verify via the
  ``services.license_service.get_solana_service`` seam (kept testable for
  SPEC-004 per the SPEC-002 task brief).

Plus the ``get_license_service`` factory wiring.
"""
from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_is_licensed_returns_false_without_tx_sig(monkeypatch):
    """No tx_sig -> False immediately (no DB license lookup can match)."""
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())

    # A solana fake is wired but MUST NOT be called when tx_sig is absent.
    from tests.fakes import FakeSolanaService

    fake_solana = FakeSolanaService()
    monkeypatch.setattr(
        "services.license_service.get_solana_service", lambda: fake_solana
    )

    assert LicenseService().is_licensed(asset, "") is False
    assert fake_solana.calls == []


@pytest.mark.django_db
def test_is_licensed_falls_back_to_onchain_verify(monkeypatch):
    """No DB license BUT tx_sig supplied -> lazy on-chain verify.

    SPEC-004 owns the real verification path; SPEC-002 only keeps the wiring
    testable. With FakeSolanaService's default valid result, is_licensed True.
    """
    from services.license_service import LicenseService
    from tests.fakes import FakeSolanaService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())

    fake_solana = FakeSolanaService()
    monkeypatch.setattr(
        "services.license_service.get_solana_service", lambda: fake_solana
    )

    result = LicenseService().is_licensed(asset, "tx_not_in_db_001")

    # FakeSolanaService.verify_usdc_payment returns is_valid=True by default.
    assert result is True
    verify_calls = [c for c in fake_solana.calls if c[0] == "verify_usdc_payment"]
    assert len(verify_calls) == 1
    # The verify call carried the tx signature through.
    assert verify_calls[0][1][0] == "tx_not_in_db_001"


@pytest.mark.django_db
def test_is_licensed_onchain_false_propagates(monkeypatch):
    """When the on-chain verify returns is_valid=False, is_licensed is False."""
    from services._types import PaymentVerification
    from services.license_service import LicenseService
    from tests.fakes import FakeSolanaService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())

    fake_solana = FakeSolanaService()
    fake_solana.verification = PaymentVerification(
        is_valid=False,
        amount=asset.target_price_usdc,
        sender="Someone111111111111111111111111111111111111",
        slot=1,
    )
    monkeypatch.setattr(
        "services.license_service.get_solana_service", lambda: fake_solana
    )

    assert LicenseService().is_licensed(asset, "tx_invalid_001") is False


def test_get_license_service_factory_reads_settings(settings):
    """The factory wires LicenseService with the configured download TTL."""
    from services.license_service import LicenseService, get_license_service

    settings.DOWNLOAD_TOKEN_TTL_SECONDS = 7200
    svc = get_license_service()
    assert isinstance(svc, LicenseService)
    assert svc.download_token_ttl_seconds == 7200
