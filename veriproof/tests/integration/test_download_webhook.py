"""SPEC-004 integration — /files/{token} download + pay.sh webhook.

TDD list (6):
- test_download_valid_token_returns_original (AC-5)
- test_download_expired_token_403 (AC-6, freezegun)
- test_download_purged_original_410 (AC-7)
- test_paysh_webhook_bad_signature_401 (AC-8)
- test_paysh_webhook_valid_publishes_pubsub (AC-9)
- test_paysh_webhook_idempotent_on_replay (R17)
"""
from __future__ import annotations

import datetime
import decimal
import hashlib
import hmac
import json
import zipfile

import pytest
from freezegun import freeze_time

from tests.conftest import VALID_WALLET

_BUYER = "BuyerWallet1111111111111111111111111111111111"
_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"
_AGENT_HEADERS = {"X-Agent-Protocol": "x402", "Accept": "application/json"}
_WEBHOOK_URL = "/api/v1/paysh/webhook"
_DOWNLOAD_TEMPLATE = "/files/{token}"


# === DI helpers =============================================================


class _RecordingRecorder:
    def __init__(self):
        self.calls = []

    def record(self, type, payload, asset=None, session=None):
        self.calls.append((type, payload or {}))
        from apps.common.models import AgentEvent

        AgentEvent.objects.create(
            type=type, payload=payload or {}, asset=asset, session=session
        )


def _settlement_service():
    """A SettlementService with fakes (valid verify by default)."""
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from tests.fakes import (
        FakeBigQuery,
        FakeFirestore,
        FakeRoyaltyService,
        FakeSolanaService,
    )

    recorder = _RecordingRecorder()
    return SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(event_recorder=recorder),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(),
        event_recorder=recorder,
        usdc_mint=_USDC_MINT,
    )


def _webhook_body(*, tx_signature, asset_id, buyer_wallet=_BUYER, amount="3.0",
                  session_id=None):
    body = {
        "event": "payment.completed",
        "tx_signature": tx_signature,
        "asset_id": str(asset_id),
        "buyer_wallet": buyer_wallet,
        "amount_usdc": amount,
    }
    if session_id is not None:
        body["session_id"] = str(session_id)
    return body


def _sign(body: dict, secret: str) -> str:
    raw = json.dumps(body).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _signed_post(client, body: dict, secret: str, signature: str | None = "match"):
    raw = json.dumps(body).encode("utf-8")
    sig = _sign(body, secret) if signature == "match" else (signature or "deadbeef")
    return client.post(
        _WEBHOOK_URL,
        data=raw,
        content_type="application/json",
        headers={**_AGENT_HEADERS, "X-PaySh-Signature": sig},
    )


# === AC-5 / R9: valid token download ========================================


@pytest.mark.django_db
def test_download_valid_token_returns_original(client, monkeypatch):
    """AC-5: valid + unexpired token -> 200 with the original bytes."""
    from apps.settlement.models import License
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from services.storage_service import StorageService
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import (
        FakeBigQuery,
        FakeFirestore,
        FakeRoyaltyService,
        FakeSolanaService,
    )

    # Set up an asset + grant a license (real DB path) via the pipeline.
    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    recorder = _RecordingRecorder()
    import tempfile

    media_root = tempfile.mkdtemp()
    storage = StorageService(backend="local", media_root=media_root)
    # Seed a temporary original so read_temporary can serve it.
    storage.save_temporary(
        asset.id, b"ORIGINAL-PNG-BYTES", datetime.timedelta(seconds=60)
    )
    # Inject the storage into the download view's DI seam.
    monkeypatch.setattr("apps.settlement.views_api.get_storage_service", lambda: storage)

    svc = SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(event_recorder=recorder),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(),
        event_recorder=recorder,
        usdc_mint=_USDC_MINT,
    )
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service", lambda: svc
    )

    client.post(
        f"/api/v1/ip/{asset.id}/settle",
        data={"tx_signature": "tx_dl_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    license = License.objects.get(payment_tx_sig="tx_dl_001")

    response = client.get(_DOWNLOAD_TEMPLATE.format(token=license.download_token))

    assert response.status_code == 200
    assert response.content == b"ORIGINAL-PNG-BYTES"
    assert "attachment" in response["Content-Disposition"]


@pytest.mark.django_db
def test_download_multi_image_work_returns_one_archive(client, monkeypatch):
    """한 작품 라이선스 토큰으로 구성 이미지 전체를 하나의 ZIP으로 전달한다."""
    import io

    from apps.ip.models import AssetImage
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory
    from tests.fakes import FakeStorageService

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET))
    image = AssetImage.objects.create(
        asset=asset,
        position=1,
        file_name="detail.png",
        content_mime_type="image/png",
        content_sha256="b" * 64,
        watermark_url="memory://watermark/detail",
        original_url="memory://original/detail",
    )
    license = LicenseFactory(asset=asset, download_token="multi-image-token")
    storage = FakeStorageService()
    storage.temporary[asset.id] = b"PRIMARY"
    storage.temporary[image.id] = b"DETAIL"
    monkeypatch.setattr("apps.settlement.views_api.get_storage_service", lambda: storage)

    response = client.get(_DOWNLOAD_TEMPLATE.format(token=license.download_token))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("original-1") == b"PRIMARY"
        assert archive.read("detail.png") == b"DETAIL"


# === AC-6 / R10: expired token ==============================================


@pytest.mark.django_db
def test_download_expired_token_403(client, monkeypatch):
    """AC-6: token past its download_expires_at -> 403."""
    from apps.settlement.models import License
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from services.storage_service import StorageService
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeBigQuery, FakeFirestore, FakeRoyaltyService, FakeSolanaService
    import tempfile

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    recorder = _RecordingRecorder()
    media_root = tempfile.mkdtemp()
    storage = StorageService(backend="local", media_root=media_root)
    storage.save_temporary(asset.id, b"BYTES", datetime.timedelta(seconds=60))
    monkeypatch.setattr("apps.settlement.views_api.get_storage_service", lambda: storage)

    svc = SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(
            event_recorder=recorder, download_token_ttl_seconds=3600
        ),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(),
        event_recorder=recorder,
        usdc_mint=_USDC_MINT,
    )
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service", lambda: svc
    )

    # Grant at t0; then jump past the TTL.
    base_time = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    with freeze_time(base_time):
        client.post(
            f"/api/v1/ip/{asset.id}/settle",
            data={"tx_signature": "tx_exp_001", "buyer_wallet": _BUYER},
            content_type="application/json",
            headers=_AGENT_HEADERS,
        )
        license = License.objects.get(payment_tx_sig="tx_exp_001")
        token = license.download_token

    # Advance well past the 1h TTL.
    future = base_time + datetime.timedelta(hours=2)
    with freeze_time(future):
        response = client.get(_DOWNLOAD_TEMPLATE.format(token=token))

    assert response.status_code == 403
    assert response.json()["error"] == "expired_token"


# === AC-7 / R11: purged original ============================================


@pytest.mark.django_db
def test_download_purged_original_410(client, monkeypatch):
    """AC-7: valid token but original purged -> 410 Gone."""
    from apps.settlement.models import License
    from apps.settlement.services import SettlementService
    from services.license_service import LicenseService
    from services.storage_service import StorageService
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakeBigQuery, FakeFirestore, FakeRoyaltyService, FakeSolanaService
    import tempfile

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    recorder = _RecordingRecorder()
    media_root = tempfile.mkdtemp()
    storage = StorageService(backend="local", media_root=media_root)
    monkeypatch.setattr("apps.settlement.views_api.get_storage_service", lambda: storage)

    svc = SettlementService(
        solana=FakeSolanaService(),
        license_service=LicenseService(event_recorder=recorder),
        firestore=FakeFirestore(),
        bigquery=FakeBigQuery(),
        royalty_service=FakeRoyaltyService(),
        event_recorder=recorder,
        usdc_mint=_USDC_MINT,
    )
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service", lambda: svc
    )

    client.post(
        f"/api/v1/ip/{asset.id}/settle",
        data={"tx_signature": "tx_purge_001", "buyer_wallet": _BUYER},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    license = License.objects.get(payment_tx_sig="tx_purge_001")

    # Purge the original (no bytes available).
    storage.purge_original(asset.id)

    response = client.get(_DOWNLOAD_TEMPLATE.format(token=license.download_token))
    assert response.status_code == 410
    assert response.json()["error"] == "purged"


@pytest.mark.django_db
def test_download_unknown_token_403(client):
    """Unknown token (no License matches) -> 403."""
    response = client.get(_DOWNLOAD_TEMPLATE.format(token="nonexistent-token"))
    assert response.status_code == 403
    assert response.json()["error"] == "invalid_token"


# === AC-8 / R12: webhook signature =========================================


@pytest.mark.django_db
def test_paysh_webhook_bad_signature_401(client, monkeypatch, settings):
    """AC-8: signature mismatch -> 401 (no Pub/Sub publish attempted)."""
    from tests.fakes import FakePubSub

    settings.PAYSH_WEBHOOK_SECRET = "real-secret"
    publisher = FakePubSub()
    monkeypatch.setattr(
        "apps.settlement.views_api.get_pubsub_publisher", lambda: publisher
    )

    body = _webhook_body(tx_signature="tx_bad_sig", asset_id=uuid4())
    response = _signed_post(client, body, "real-secret", signature="wrong")

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_signature"
    # Pub/Sub MUST NOT be called when the signature is bad.
    assert publisher.published == []


# === AC-9 / R13: valid webhook publishes PubSub =============================


@pytest.mark.django_db
def test_paysh_webhook_valid_publishes_pubsub(client, monkeypatch, settings):
    """AC-9: valid signature -> 200 + Pub/Sub publish exactly once."""
    from tests.factories import CreatorFactory, IpAssetFactory
    from tests.fakes import FakePubSub

    settings.PAYSH_WEBHOOK_SECRET = "real-secret"
    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    publisher = FakePubSub()
    monkeypatch.setattr(
        "apps.settlement.views_api.get_pubsub_publisher", lambda: publisher
    )
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service", lambda: _settlement_service()
    )

    body = _webhook_body(tx_signature="tx_webhook_ok", asset_id=asset.id)
    response = _signed_post(client, body, "real-secret")

    assert response.status_code == 200
    # Pub/Sub publish called exactly once (R13).
    assert len(publisher.published) == 1
    topic, msg = publisher.published[0]
    assert topic == settings.PUBSUB_PAYMENTS_TOPIC
    assert msg["tx_signature"] == "tx_webhook_ok"


# === R17: webhook idempotent on replay ======================================


@pytest.mark.django_db
def test_paysh_webhook_idempotent_on_replay(client, monkeypatch, settings):
    """R17: replaying the same tx_signature -> no duplicate license.

    Pub/Sub disabled (publisher returns None) -> sync fallback runs the
    pipeline; the License.payment_tx_sig unique constraint enforces idempotency.
    """
    from apps.settlement.models import License
    from tests.factories import CreatorFactory, IpAssetFactory

    settings.PAYSH_WEBHOOK_SECRET = "real-secret"
    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        target_price_usdc=decimal.Decimal("3.0"),
    )
    monkeypatch.setattr(
        "apps.settlement.views_api.get_settlement_service", lambda: _settlement_service()
    )

    body = _webhook_body(tx_signature="tx_replay", asset_id=asset.id)

    # First delivery.
    r1 = _signed_post(client, body, "real-secret")
    assert r1.status_code == 200
    # Replayed delivery (same tx).
    r2 = _signed_post(client, body, "real-secret")
    assert r2.status_code == 200

    # Exactly one License — no duplicate on replay.
    assert License.objects.filter(payment_tx_sig="tx_replay").count() == 1


# === helpers ================================================================


def uuid4():
    import uuid

    return uuid.uuid4()
