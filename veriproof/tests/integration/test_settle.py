"""SPEC-004 integration — POST /api/v1/ip/{asset_id}/settle (R1..R16, AC-1..AC-12).

Drives the full view through Django's test client. The view delegates to the
``SettlementService.settle_pipeline`` SSOT; tests inject a SettlementService
wired with fakes via the ``apps.settlement.views_api.get_settlement_service``
DI seam.

TDD list (7):
- test_settle_valid_payment_grants_license (AC-1)
- test_settle_insufficient_amount_400 (AC-2)
- test_settle_wrong_mint_400 (AC-3)
- test_settle_duplicate_tx_returns_existing (AC-4)
- test_settle_certificate_failure_keeps_license (AC-10, R16)
- test_settle_mirrors_firestore_and_bigquery (AC-11)
- test_settle_records_events (AC-12)
"""
from __future__ import annotations

import decimal
import uuid

import pytest

from tests.conftest import VALID_WALLET

SETTLE_TEMPLATE = "/api/v1/ip/{asset_id}/settle"
_BUYER = VALID_WALLET
_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"
_AGENT_HEADERS = {"X-Agent-Protocol": "x402", "Accept": "application/json"}


# --- DI seam helpers --------------------------------------------------------


class _RecordingRecorder:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def record(self, type, payload, asset=None, session=None):
        self.calls.append((type, payload or {}))
        from apps.common.models import AgentEvent

        AgentEvent.objects.create(
            type=type, payload=payload or {}, asset=asset, session=session
        )
        return None


def _settlement_service(*, fail_issue_cert=False, valid=True, fail_amount=None):
    """Build a SettlementService with fakes; controls verify validity + cert."""
    from apps.settlement.services import SettlementService
    from services._types import PaymentVerification
    from services.license_service import LicenseService
    from tests.fakes import (
        FakeBigQuery,
        FakeFirestore,
        FakeRoyaltyService,
        FakeSolanaService,
    )

    recorder = _RecordingRecorder()
    solana = FakeSolanaService(fail_issue_cert=fail_issue_cert)
    if not valid:
        solana.verification = PaymentVerification(
            is_valid=False, amount=fail_amount or decimal.Decimal("1.0"),
            sender="x", slot=1, commitment="confirmed",
        )
    return SettlementService(
        solana=solana,
        license_service=LicenseService(event_recorder=recorder),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(),
        event_recorder=recorder,
        usdc_mint=_USDC_MINT,
    )


def _patch_settlement(monkeypatch, svc):
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service", lambda: svc
    )


# === AC-1: valid payment ====================================================


@pytest.mark.django_db
def test_settle_valid_payment_grants_license(client, monkeypatch):
    """AC-1: valid payment -> 200 SUCCESS, License granted, cert_tx + download_url."""
    from apps.settlement.models import License
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_usdc=decimal.Decimal("1.5"),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service()
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "tx_signature": "tx_settle_valid_001",
            "buyer_wallet": _BUYER,
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["certificate_tx"]
    assert body["download_url"].startswith("/files/")
    assert body["download_expires_at"]
    # License persisted with the payment tx as idempotency key.
    assert License.objects.filter(payment_tx_sig="tx_settle_valid_001").count() == 1


# === AC-2 / AC-3: invalid settlement -> 400 =================================


@pytest.mark.django_db
def test_settle_insufficient_amount_400(client, monkeypatch):
    """AC-2: verify returns is_valid=False (insufficient amount) -> 400."""
    from apps.settlement.models import License
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service(valid=False)
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"tx_signature": "tx_short_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_settlement"
    # R3: no license granted on invalid settlement.
    assert License.objects.filter(payment_tx_sig="tx_short_001").count() == 0


@pytest.mark.django_db
def test_settle_wrong_mint_400(client, monkeypatch):
    """AC-3: verify returns is_valid=False (wrong mint) -> 400 invalid_settlement."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service(valid=False)
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"tx_signature": "tx_wrongmint_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_settlement"


# === AC-4: duplicate tx idempotency =========================================


@pytest.mark.django_db
def test_settle_duplicate_tx_returns_existing(client, monkeypatch):
    """AC-4: re-submitting the same tx_signature returns the existing License."""
    from apps.settlement.models import License
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service()
    _patch_settlement(monkeypatch, svc)

    # First settle.
    r1 = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"tx_signature": "tx_dup_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert r1.status_code == 200

    # Replay the SAME tx_signature.
    r2 = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"tx_signature": "tx_dup_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert r2.status_code == 200
    # Exactly one License row — no duplicate.
    assert License.objects.filter(payment_tx_sig="tx_dup_001").count() == 1


# === AC-10 / R16: certificate failure keeps license =========================


@pytest.mark.django_db
def test_settle_certificate_failure_keeps_license(client, monkeypatch):
    """AC-10 / R16: cert issuance failure -> license kept, certificate_tx None."""
    from apps.settlement.models import License
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service(fail_issue_cert=True)
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"tx_signature": "tx_certfail_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["certificate_tx"] is None
    license = License.objects.get(payment_tx_sig="tx_certfail_001")
    assert license.certificate_tx_sig is None


# === AC-11: Firestore + BigQuery mirrored ===================================


@pytest.mark.django_db
def test_settle_mirrors_firestore_and_bigquery(client, monkeypatch):
    """AC-11: on success, Firestore status=LICENSED + BigQuery transactions insert."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service()
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"tx_signature": "tx_mirror_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 200

    # Reach into the injected fakes to assert they were called.
    fs = svc.firestore
    bq = svc.bigquery
    asset_status_calls = [
        c for c in fs.calls
        if c[0] == "set" and c[1]["collection"] == "asset_status"
    ]
    assert len(asset_status_calls) == 1
    assert asset_status_calls[0][1]["data"]["status"] == "LICENSED"
    tx_inserts = [
        c for c in bq.calls
        if c[0] == "insert" and c[1]["table"] == "transactions"
    ]
    assert len(tx_inserts) == 1
    assert tx_inserts[0][1]["row"]["payment_tx_sig"] == "tx_mirror_001"


# === AC-12: events recorded =================================================


@pytest.mark.django_db
def test_settle_records_events(client, monkeypatch):
    """AC-12: PAYMENT_VERIFIED (grant) + CERT_ISSUED (pipeline) recorded."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service()
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"tx_signature": "tx_events_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 200

    types = [t for t, _ in svc.event_recorder.calls]
    assert "PAYMENT_VERIFIED" in types
    assert "CERT_ISSUED" in types


# === edge cases =============================================================


@pytest.mark.django_db
def test_settle_unknown_asset_404(client, monkeypatch):
    """Unknown asset_id -> 404."""
    svc = _settlement_service()
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(uuid.uuid4())),
        data={"tx_signature": "tx_404", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_settle_missing_tx_signature_422(client, monkeypatch):
    """Missing tx_signature -> 422 (cannot settle without a payment proof)."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    svc = _settlement_service()
    _patch_settlement(monkeypatch, svc)

    response = client.post(
        SETTLE_TEMPLATE.format(asset_id=str(asset.id)),
        data={"buyer_wallet": _BUYER},  # no tx_signature
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 422
