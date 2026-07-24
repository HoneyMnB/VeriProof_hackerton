"""SPEC-004 coverage — defensive branches + factory seams.

Covers the non-happy-path branches that the integration suites do not reach:
- SettlementService royalty degrade (real RoyaltyService with no solana backend
  records failed legs without aborting settlement; SPEC-008 R9).
- _resolve_mint fallback when no mint injected.
- get_settlement_service() factory.
- PubSubPublisher injected-client seam + disabled path.
- BigQuerySink / FirestoreMirror enabled-but-no-SDK degrade path.
- view edge cases (405, bad JSON, missing fields).
"""
from __future__ import annotations

import decimal

import pytest

from tests.conftest import VALID_WALLET

_BUYER = "BuyerWallet1111111111111111111111111111111111"


# === SettlementService defensive branches ===================================


@pytest.mark.django_db
def test_pipeline_royalty_without_solana_does_not_abort():
    """When the real RoyaltyService has no escrow solana backend wired (the
    default ``RoyaltyService()`` with ``solana=None``), distribute records each
    leg as ``status=failed`` (SPEC-008 R9 degrade) and the pipeline continues
    without aborting settlement. Replaces the former ``NotImplementedError``-
    stub swallow now that distribute is implemented in SPEC-008."""
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from services.royalty_service import RoyaltyService
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeBigQuery, FakeFirestore, FakeSolanaService
    from apps.ip.models import IpAsset

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    parent = IpAssetFactory(creator=creator, status=IpAsset.LISTED)
    child = IpAssetFactory(
        creator=creator, parent_asset=parent, royalty_share_bps=3000,
        target_price_usdc=decimal.Decimal("3.0"),
    )

    class _Rec:
        def record(self, *a, **k):
            from apps.common.models import AgentEvent

            AgentEvent.objects.create(
                type=a[0], payload=a[1] or {}, asset=a[2] if len(a) > 2 else None
            )

    svc = SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(event_recorder=_Rec()),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=RoyaltyService(),  # real service, no solana -> failed legs
        event_recorder=_Rec(),
        usdc_mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU",
    )

    result = svc.settle_pipeline(
        asset=child, session=None, tx_signature="tx_royalty_stub_001",
        buyer_wallet=_BUYER, expected_amount=decimal.Decimal("3.0"),
        usage_type="commercial",
    )
    assert result.ok is True  # settlement NOT aborted by royalty degrade


@pytest.mark.django_db
def test_pipeline_royalty_generic_exception_is_swallowed():
    """A generic royalty failure (not NIE) is also swallowed (pipeline robustness)."""
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeBigQuery, FakeFirestore, FakeRoyaltyService, FakeSolanaService
    from apps.ip.models import IpAsset

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    parent = IpAssetFactory(creator=creator, status=IpAsset.LISTED)
    child = IpAssetFactory(
        creator=creator, parent_asset=parent, royalty_share_bps=3000,
        target_price_usdc=decimal.Decimal("3.0"),
    )

    class _Rec:
        def record(self, *a, **k):
            pass

    svc = SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(event_recorder=_Rec()),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(fail_distribute=True),
        event_recorder=_Rec(),
        usdc_mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU",
    )

    result = svc.settle_pipeline(
        asset=child, session=None, tx_signature="tx_royalty_fail_001",
        buyer_wallet=_BUYER, expected_amount=decimal.Decimal("3.0"),
        usage_type="commercial",
    )
    assert result.ok is True


def test_resolve_mint_falls_back_to_settings():
    """_resolve_mint reads settings.USDC_MINT_ADDRESS when no mint injected."""
    from apps.settlement.services import SettlementService

    svc = SettlementService(usdc_mint=None)
    # _resolve_mint is a private method; exercise it directly.
    assert svc._resolve_mint()  # returns the settings default mint


def test_get_settlement_service_factory():
    """get_settlement_service() builds a wired service from settings."""
    from apps.settlement.services import SettlementService, get_settlement_service

    svc = get_settlement_service()
    assert isinstance(svc, SettlementService)
    assert svc.usdc_mint  # wired from settings


@pytest.mark.django_db
def test_pipeline_amount_falls_back_to_target_when_no_session():
    """_resolve_amount returns asset.target_price_usdc when session is None
    and no expected_amount supplied."""
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeBigQuery, FakeFirestore, FakeRoyaltyService, FakeSolanaService

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("2.25"),
    )

    class _Rec:
        def record(self, *a, **k):
            from apps.common.models import AgentEvent

            AgentEvent.objects.create(
                type=a[0], payload=a[1] or {}, asset=a[2] if len(a) > 2 else None
            )

    svc = SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(event_recorder=_Rec()),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(),
        event_recorder=_Rec(),
        usdc_mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU",
    )
    # No expected_amount -> pipeline resolves from asset.target_price_usdc.
    result = svc.settle_pipeline(
        asset=asset, session=None, tx_signature="tx_amount_fb_001",
        buyer_wallet=_BUYER, usage_type=None,
    )
    assert result.ok is True


# === PubSubPublisher injected-client seam ===================================


def test_pubsub_injected_client_seam_returns_msg_id():
    """PubSubPublisher with an injected client calls client.publish(topic, msg)."""
    from services.pubsub_publisher import PubSubPublisher

    class _Client:
        def __init__(self):
            self.calls = []

        def publish(self, topic, message):
            self.calls.append((topic, message))
            return "msg-id-xyz"

    client = _Client()
    publisher = PubSubPublisher(client=client)
    msg_id = publisher.publish("topic-x", {"a": 1})
    assert msg_id == "msg-id-xyz"
    assert client.calls == [("topic-x", {"a": 1})]


def test_pubsub_returns_none_when_disabled():
    """No project_id + no client -> publish returns None (sync-fallback signal)."""
    from services.pubsub_publisher import PubSubPublisher

    assert PubSubPublisher().publish("topic", {"x": 1}) is None


# === FirestoreMirror / BigQuerySink enabled-but-no-SDK degrade ==============


def test_firestore_enabled_but_no_client_degrades_silently():
    """Enabled flag set but no SDK/client -> set() returns None, no raise."""
    from services.firestore_mirror import FirestoreMirror

    fs = FirestoreMirror(enabled=True)
    # No google-cloud-firestore installed -> _get_client returns None -> no-op.
    assert fs.set("asset_status", "aid", {"status": "X"}) is None


def test_bigquery_dataset_set_but_no_client_degrades_silently():
    """Dataset set but no SDK/client -> insert() returns None, no raise."""
    from services.bigquery_sink import BigQuerySink

    bq = BigQuerySink(dataset="veriproof_analytics")
    assert bq.insert("transactions", {"x": 1}) is None


def test_firestore_injected_client_write_path():
    """Enabled + injected client -> collection().document().set() invoked."""
    from services.firestore_mirror import FirestoreMirror

    class _Doc:
        def __init__(self):
            self.set_calls = []

        def set(self, data):
            self.set_calls.append(data)

    class _Coll:
        def __init__(self, doc):
            self._doc = doc

        def document(self, doc_id):
            return self._doc

    class _Client:
        def __init__(self):
            self.doc = _Doc()

        def collection(self, name):
            return _Coll(self.doc)

    client = _Client()
    fs = FirestoreMirror(enabled=True, client=client)
    fs.set("asset_status", "aid", {"status": "LICENSED"})
    assert client.doc.set_calls == [{"status": "LICENSED"}]


def test_firestore_injected_client_failure_is_swallowed():
    """An injected client that raises -> set() swallows it (mirror must not abort)."""
    from services.firestore_mirror import FirestoreMirror

    class _BoomClient:
        def collection(self, name):
            raise RuntimeError("firestore down")

    fs = FirestoreMirror(enabled=True, client=_BoomClient())
    # Must not raise.
    assert fs.set("asset_status", "aid", {"x": 1}) is None


def test_bigquery_injected_client_write_path():
    """Dataset + injected client -> insert_rows_json invoked."""
    from services.bigquery_sink import BigQuerySink

    class _Client:
        def __init__(self):
            self.rows = []

        def insert_rows_json(self, table, rows):
            self.rows.append((table, rows))

    client = _Client()
    bq = BigQuerySink(dataset="veriproof_analytics", client=client)
    bq.insert("transactions", {"payment_tx_sig": "tx"})
    assert client.rows == [("veriproof_analytics.transactions", [{"payment_tx_sig": "tx"}])]


def test_bigquery_injected_client_failure_is_swallowed():
    """Injected client that raises -> insert() swallows it."""
    from services.bigquery_sink import BigQuerySink

    class _BoomClient:
        def insert_rows_json(self, table, rows):
            raise RuntimeError("bq down")

    bq = BigQuerySink(dataset="veriproof_analytics", client=_BoomClient())
    assert bq.insert("transactions", {"x": 1}) is None


# === View edge cases ========================================================


@pytest.mark.django_db
def test_settle_method_not_allowed_405(client, monkeypatch):
    """GET /settle -> 405."""
    from apps.settlement.services import SettlementService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET))
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service",
        lambda: SettlementService(usdc_mint="m"),
    )
    response = client.get(f"/api/v1/ip/{asset.id}/settle")
    assert response.status_code == 405


@pytest.mark.django_db
def test_settle_invalid_json_422(client, monkeypatch):
    """Malformed JSON body -> 422."""
    from apps.settlement.services import SettlementService
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET))
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service",
        lambda: SettlementService(usdc_mint="m"),
    )
    response = client.post(
        f"/api/v1/ip/{asset.id}/settle",
        data="not-json",
        content_type="application/json",
    )
    assert response.status_code == 422


@pytest.mark.django_db
def test_webhook_missing_asset_id_422(client, monkeypatch, settings):
    """Valid signature but no asset_id -> 422."""
    import hashlib
    import hmac
    import json

    settings.PAYSH_WEBHOOK_SECRET = "s"
    body = {"tx_signature": "t1"}  # no asset_id
    raw = json.dumps(body).encode()
    sig = hmac.new(b"s", raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/api/v1/paysh/webhook",
        data=raw,
        content_type="application/json",
        headers={"X-PaySh-Signature": sig},
    )
    assert response.status_code == 422


@pytest.mark.django_db
def test_webhook_method_not_allowed_405(client, settings):
    """GET /paysh/webhook -> 405."""
    settings.PAYSH_WEBHOOK_SECRET = "s"
    response = client.get("/api/v1/paysh/webhook")
    assert response.status_code == 405


@pytest.mark.django_db
def test_webhook_no_secret_rejects_401(client, settings):
    """R12 fail-closed: no PAYSH_WEBHOOK_SECRET configured -> 401."""
    settings.PAYSH_WEBHOOK_SECRET = ""
    response = client.post(
        "/api/v1/paysh/webhook",
        data='{"tx_signature":"t"}',
        content_type="application/json",
        headers={"X-PaySh-Signature": "anything"},
    )
    assert response.status_code == 401
