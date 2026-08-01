"""SPEC-002 integration tests — GET /api/v1/ip/{asset_id} (x402 interceptor).

Drives the full view through Django's test client with the four external
services swapped to fakes via the ``get_*()`` factory seam in
``apps.ip.views_api`` (monkeypatched per-test). Every test stays offline.

Covers the SPEC-002 §5 integration TDD list (7 tests):
- unknown asset -> 404 (AC-1)
- agent without license -> 402 + headers + body (AC-2)
- agent with valid license -> 200 + download info (AC-3, is_licensed mocked True)
- browser without license -> 200 Solana Pay fallback (AC-4)
- 402 body contains USDC mint (AC-5)
- 402 records an HTTP_402 AgentEvent (AC-6)
- existing DB license skips on-chain verify (AC-7, mock call count == 0)
"""
from __future__ import annotations

import decimal

import pytest
from types import SimpleNamespace

from tests.conftest import VALID_WALLET

GET_TEMPLATE = "/api/v1/ip/{asset_id}"

_AGENT_HEADERS = {"X-Agent-Protocol": "x402", "Accept": "application/json"}
_BROWSER_HEADERS = {"Accept": "text/html"}

_ESCROW_PUBKEY = "EscrowWallet111111111111111111111111111111111"
_NETWORK = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


class _ProtocolStub:
    """통합 뷰 테스트에서 외부 Facilitator 호출을 제거하는 대역이다."""

    def build_challenge(self, **kwargs):
        from services.x402_protocol_service import X402Challenge

        amount = int(kwargs["amount_usdc"] * 1_000_000)
        body = {
            "x402Version": 2,
            "error": "Payment required",
            "resource": {"url": kwargs["resource_url"]},
            "accepts": [
                {
                    "scheme": "exact",
                    "network": _NETWORK,
                    "asset": kwargs["usdc_mint"],
                    "amount": str(amount),
                    "payTo": kwargs["pay_to"],
                    "maxTimeoutSeconds": 300,
                    "extra": {"feePayer": "Facilitator111"},
                }
            ],
        }
        return X402Challenge(
            headers={"PAYMENT-REQUIRED": "encoded-v2"},
            body=body,
            payment_required=SimpleNamespace(accepts=[]),
        )

    def verify_and_settle(self, **kwargs):
        from services.x402_protocol_service import X402Settlement

        return X402Settlement(
            transaction="x402-devnet-tx",
            payer=VALID_WALLET,
            network=_NETWORK,
            response_header="encoded-settlement",
        )


# --- Helpers ----------------------------------------------------------------


def _patch_view_services(
    monkeypatch,
    *,
    x402=None,
    license_service=None,
    storage=None,
    recorder=None,
):
    """Swap the view's service factories for fakes (DI seam).

    Mirrors the SPEC-001 ``_patch_services`` pattern in test_register.py.
    """
    if x402 is not None:
        monkeypatch.setattr("apps.ip.views_api.get_x402_service", lambda: x402)
    # License access now reads the persisted License record directly; the
    # arguments remain only for older call sites in this test module.
    if recorder is not None:
        monkeypatch.setattr("apps.ip.views_api.get_event_recorder", lambda: recorder)


def _real_x402(settings) -> "X402Service":  # type: ignore[name-defined]
    """Build a real X402Service wired with the test settings (offline)."""
    from services.x402_service import X402Service

    return X402Service(
        ap2_enabled=False,
        usdc_mint=settings.USDC_MINT_ADDRESS,
        escrow_pubkey=_ESCROW_PUBKEY,
        network=_NETWORK,
        protocol_service=_ProtocolStub(),
    )


# === AC-1: unknown asset -> 404 =============================================


@pytest.mark.django_db
def test_get_unknown_asset_404(client, monkeypatch):
    """AC-1 / R1: asset_id that does not exist -> 404."""
    import uuid

    _patch_view_services(monkeypatch, x402=_real_x402(_settings_stub()))
    response = client.get(
        GET_TEMPLATE.format(asset_id=str(uuid.uuid4())), headers=_AGENT_HEADERS
    )
    assert response.status_code == 404


# === AC-2: agent without license -> 402 =====================================


@pytest.mark.django_db
def test_agent_without_license_gets_402(client, monkeypatch):
    """AC-2 / R3 / R4: agent request, no license -> 402 with 4 headers + body."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET), visibility="public")

    # LicenseService returns False (no license).
    from services.license_service import LicenseService

    class _NotLicensed:
        def is_licensed(self, asset, tx_sig):
            return False

    _patch_view_services(
        monkeypatch, x402=_real_x402(_settings_stub()), license_service=_NotLicensed()
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)), headers=_AGENT_HEADERS
    )
    assert response.status_code == 402
    # R4: the four mandatory headers are all present.
    assert response.headers["PAYMENT-REQUIRED"] == "encoded-v2"
    assert response.headers["X-Agent-Protocol"] == "x402"
    assert response.headers["X-402-Negotiation-Endpoint"].endswith("/negotiate")
    assert response.headers["X-Payment-Mint"]  # non-empty
    # Body carries the a2a-x402 envelope.
    body = response.json()
    assert body["x402Version"] == 2
    assert body["accepts"][0]["scheme"] == "exact"


@pytest.mark.django_db
def test_unlicensed_private_asset_is_not_a_public_purchase_target(client, monkeypatch):
    """비공개 자산 UUID를 알아도 외부 구매 조건이나 미리보기를 얻을 수 없다."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET), visibility=IpAsset.PRIVATE
    )
    _patch_view_services(monkeypatch, x402=_real_x402(_settings_stub()))
    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)), headers=_AGENT_HEADERS
    )
    assert response.status_code == 404


# === AC-3: agent with valid license -> 200 ==================================


@pytest.mark.django_db
def test_agent_with_valid_license_gets_200(client, monkeypatch):
    """AC-3 / R2: 저장된 라이선스의 실제 다운로드 토큰만 반환한다."""
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        visibility="public",
        target_price_sol=decimal.Decimal("1.000000000"),
    )
    LicenseFactory(
        asset=asset,
        payment_tx_sig="valid_tx_001",
        download_token="valid-download-token",
    )

    _patch_view_services(
        monkeypatch,
        x402=_real_x402(_settings_stub()),
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)),
        headers={
            **_AGENT_HEADERS,
            "X-Solana-Tx-Sig": "valid_tx_001",
            "X-Forwarded-Proto": "https",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "LICENSED"
    assert body["download_url"] == "https://testserver/files/valid-download-token"


# === AC-4: browser without license -> Solana Pay fallback (200) =============


@pytest.mark.django_db
def test_browser_without_license_gets_solana_pay_fallback(client, monkeypatch):
    """AC-4 / R7: browser request, no license -> 200 Solana Pay Buy-It-Now."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        visibility="public",
        target_price_sol=decimal.Decimal("1.000000000"),
    )

    class _NotLicensed:
        def is_licensed(self, asset, tx_sig):
            return False

    _patch_view_services(
        monkeypatch, x402=_real_x402(_settings_stub()), license_service=_NotLicensed()
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)), headers=_BROWSER_HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["solana_pay"]["address"] == VALID_WALLET
    assert body["solana_pay"]["asset"] == "SOL"
    assert body["solana_pay"]["cluster"] == "devnet"
    assert body["solana_pay"]["rpc_url"]
    assert body["solana_pay"]["reference"]
    assert body["solana_pay"]["amount_sol"]
    assert "cluster=devnet" in body["solana_pay"]["uri"]
    assert "reference=" in body["solana_pay"]["uri"]
    assert "spl-token" not in body["solana_pay"]["uri"]


# === AC-5: 402 body contains USDC mint ======================================


@pytest.mark.django_db
def test_402_body_contains_usdc_mint(client, monkeypatch, settings):
    """AC-5: 402 body accepts[0].mint == settings.USDC_MINT_ADDRESS."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET), visibility="public")

    class _NotLicensed:
        def is_licensed(self, asset, tx_sig):
            return False

    _patch_view_services(
        monkeypatch, x402=_real_x402(settings), license_service=_NotLicensed()
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)), headers=_AGENT_HEADERS
    )
    assert response.status_code == 402
    body = response.json()
    assert body["accepts"][0]["asset"] == settings.USDC_MINT_ADDRESS
    assert body["accepts"][0]["amount"] == str(
        int(asset.target_price_usdc * 1_000_000)
    )


# === AC-6: 402 records an HTTP_402 event ====================================


@pytest.mark.django_db
def test_402_records_event(client, monkeypatch):
    """AC-6 / R9: a 402 response records exactly one HTTP_402 AgentEvent."""
    from apps.common.models import AgentEvent
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET), visibility="public")

    class _NotLicensed:
        def is_licensed(self, asset, tx_sig):
            return False

    _patch_view_services(
        monkeypatch, x402=_real_x402(_settings_stub()), license_service=_NotLicensed()
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)), headers=_AGENT_HEADERS
    )
    assert response.status_code == 402

    events = AgentEvent.objects.filter(asset_id=asset.id, type="HTTP_402")
    assert events.count() == 1
    payload = events.first().payload
    assert payload["asset_id"] == str(asset.id)


# === AC-7: existing DB license skips on-chain verify ========================


@pytest.mark.django_db
def test_existing_license_skips_onchain_verify(client, monkeypatch, settings):
    """AC-7 / R10: with a DB License for the tx_sig, Solana verify is NOT called.

    The view delegates to LicenseService.is_licensed; the real implementation
    MUST consult the DB first and short-circuit before any on-chain call. We
    wire a FakeSolanaService into ``services.license_service.get_solana_service``
    and assert its verify_usdc_payment call count stays at 0.
    """
    from apps.ip.models import IpAsset
    from services.license_service import LicenseService
    from tests.fakes import FakeSolanaService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW_PUBKEY

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    asset = IpAssetFactory(creator=creator, status=IpAsset.LISTED, visibility=IpAsset.PUBLIC)
    license = LicenseFactory(asset=asset, payment_tx_sig="tx_db_001")

    fake_solana = FakeSolanaService()
    # Wire the fake into BOTH the view's and LicenseService's solana seam.
    monkeypatch.setattr(
        "services.license_service.get_solana_service", lambda: fake_solana
    )
    _patch_view_services(
        monkeypatch,
        x402=_real_x402(settings),
        license_service=LicenseService(),  # real service; DB short-circuit
        storage=None,
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)),
        headers={**_AGENT_HEADERS, "X-Solana-Tx-Sig": license.payment_tx_sig},
    )
    assert response.status_code == 200
    # R10: the on-chain verify was skipped because the DB License pre-existed.
    verify_calls = [c for c in fake_solana.calls if c[0] == "verify_usdc_payment"]
    assert len(verify_calls) == 0


@pytest.mark.django_db
def test_expired_license_requires_payment_again(client, monkeypatch, settings):
    """Expired ledger rows are preserved but no longer return LICENSED."""
    from django.utils import timezone
    from apps.ip.models import IpAsset
    from services.license_service import LicenseService
    from tests.fakes import FakeSolanaService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW_PUBKEY

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    asset = IpAssetFactory(creator=creator, status=IpAsset.LISTED, visibility=IpAsset.PUBLIC)
    license = LicenseFactory(
        asset=asset,
        payment_tx_sig="tx_expired_db_001",
        download_expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )

    fake_solana = FakeSolanaService()
    monkeypatch.setattr(
        "services.license_service.get_solana_service", lambda: fake_solana
    )
    _patch_view_services(
        monkeypatch,
        x402=_real_x402(settings),
        license_service=LicenseService(),
        storage=None,
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)),
        headers={**_AGENT_HEADERS, "X-Solana-Tx-Sig": license.payment_tx_sig},
    )

    assert response.status_code == 402
    assert response.json()["error"] == "Payment required"
    assert license.__class__.objects.filter(id=license.id).exists()
    verify_calls = [c for c in fake_solana.calls if c[0] == "verify_usdc_payment"]
    assert len(verify_calls) == 0


@pytest.mark.django_db
def test_payment_signature_retries_same_get_and_returns_payment_response(
    client,
    monkeypatch,
):
    """공식 결제 서명을 동일 GET에 제출하면 라이선스와 정산 헤더를 반환한다."""
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        visibility="public",
    )
    license = LicenseFactory(
        asset=asset,
        payment_tx_sig="x402-devnet-tx",
        buyer_wallet=VALID_WALLET,
        download_token="x402-download-token",
    )
    calls = []

    class _SettlementStub:
        def settle_pipeline(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=True, license=license)

    _patch_view_services(monkeypatch, x402=_real_x402(_settings_stub()))
    monkeypatch.setattr(
        "apps.ip.views_api.get_settlement_service",
        lambda: _SettlementStub(),
    )

    response = client.get(
        GET_TEMPLATE.format(asset_id=str(asset.id)),
        headers={
            **_AGENT_HEADERS,
            "PAYMENT-SIGNATURE": "signed-v2-payload",
        },
    )

    assert response.status_code == 200
    assert response.headers["PAYMENT-RESPONSE"] == "encoded-settlement"
    assert response.json()["download_url"] == "http://testserver/files/x402-download-token"
    assert calls[0]["payment_already_verified"] is True
    assert calls[0]["tx_signature"] == "x402-devnet-tx"


# --- Internal helper --------------------------------------------------------


class _SettingsStub:
    """Minimal stand-in exposing the two attributes _real_x402() reads."""

    def __init__(self) -> None:
        from django.conf import settings as _dj_settings

        self.USDC_MINT_ADDRESS = getattr(_dj_settings, "USDC_MINT_ADDRESS")


def _settings_stub() -> _SettingsStub:
    return _SettingsStub()
