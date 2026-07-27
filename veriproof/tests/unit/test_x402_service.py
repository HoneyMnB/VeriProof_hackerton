"""SPEC-002 unit tests — X402Service classify/builders + payment-recipient helper.

Covers the SPEC-002 §5 unit TDD list (8 tests):
- classify_client: agent-by-x402-header, agent-by-accept-json, browser-by-accept-html
- build_payment_required: headers present (R4), body schema (R5),
  max_amount_required == target_price_usdc
- Payment Recipient Resolution (R5b / AC-8 / AC-9): standalone -> creator,
  secondary (parent_asset) -> PLATFORM_ESCROW_PUBKEY.

All tests are pure (no network, no DB): ``asset`` is a SimpleNamespace and the
X402Service is constructed with explicit config so no Django settings are read.
"""
from __future__ import annotations

import decimal
from types import SimpleNamespace

import pytest

_USDC_MINT = "USDCMINT111111111111111111111111111111111"
_ESCROW = "EscrowWallet111111111111111111111111111111111"
_CREATOR_WALLET = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_NETWORK = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


class _ProtocolStub:
    """네트워크 없이 x402 서비스의 도메인 매핑만 검증하는 대역이다."""

    def build_challenge(self, **kwargs):
        from services.x402_protocol_service import X402Challenge

        amount = int(decimal.Decimal(kwargs["amount_usdc"]) * decimal.Decimal("1000000"))
        body = {
            "x402Version": 2,
            "error": "Payment required",
            "resource": {
                "url": kwargs["resource_url"],
                "description": kwargs["description"],
                "mimeType": "application/json",
            },
            "accepts": [
                {
                    "scheme": "exact",
                    "network": _NETWORK,
                    "asset": kwargs["usdc_mint"],
                    "amount": str(amount),
                    "payTo": kwargs["pay_to"],
                    "maxTimeoutSeconds": 300,
                    "extra": {"feePayer": "Facilitator111", "memo": kwargs["memo"]},
                }
            ],
        }
        return X402Challenge(
            headers={"PAYMENT-REQUIRED": "encoded-v2"},
            body=body,
            payment_required=SimpleNamespace(accepts=[]),
        )


# --- Fixtures (plain objects; no DB) -----------------------------------------


def _make_asset(
    *,
    wallet: str = _CREATOR_WALLET,
    parent_id: str | None = None,
    target_price: str = "1.50",
    asset_id: str = "asset-uuid-1",
    watermark_url: str = "https://cdn.test/wm-1.png",
) -> SimpleNamespace:
    """Build a lightweight asset stand-in for pure unit tests.

    Mirrors the IpAsset attributes consumed by X402Service + resolve_pay_to:
    ``id``, ``creator.wallet_address``, ``parent_asset_id``, ``target_price_usdc``,
    ``watermark_url``.
    """
    creator = SimpleNamespace(wallet_address=wallet)
    return SimpleNamespace(
        id=asset_id,
        creator=creator,
        # Expose both the FK id and the attr for robustness.
        parent_asset_id=parent_id,
        parent_asset=parent_id,
        target_price_usdc=decimal.Decimal(target_price),
        watermark_url=watermark_url,
    )


def _svc() -> "X402Service":  # type: ignore[name-defined]  # forward ref
    """Build an X402Service with explicit offline config (no settings read)."""
    from services.x402_service import X402Service

    return X402Service(
        ap2_enabled=False,
        usdc_mint=_USDC_MINT,
        escrow_pubkey=_ESCROW,
        network=_NETWORK,
        protocol_service=_ProtocolStub(),
    )


# === classify_client (R6 / R7) ===============================================


def test_classify_agent_by_x_agent_protocol_header():
    """R6: ``X-Agent-Protocol: x402`` header -> agent (regardless of Accept)."""
    svc = _svc()
    assert svc.classify_client({"X-Agent-Protocol": "x402"}) == "agent"
    # Header present alongside a browser-style Accept still wins as agent.
    assert svc.classify_client(
        {"X-Agent-Protocol": "x402", "Accept": "text/html"}
    ) == "agent"


def test_classify_agent_by_accept_json():
    """R6: ``Accept: application/json`` (no x402 header) -> agent."""
    svc = _svc()
    assert svc.classify_client({"Accept": "application/json"}) == "agent"


def test_classify_browser_by_accept_html():
    """R7: ``Accept: text/html`` without agent signals -> browser."""
    svc = _svc()
    assert svc.classify_client({"Accept": "text/html"}) == "browser"


def test_classify_ambiguous_defaults_to_agent():
    """SPEC §6 edge: ambiguous ``Accept: */*`` defaults to agent (conservative)."""
    svc = _svc()
    assert svc.classify_client({"Accept": "*/*"}) == "agent"
    # Missing Accept entirely also defaults to agent.
    assert svc.classify_client({}) == "agent"


def test_classify_handles_case_insensitive_header_keys():
    """Plain-dict headers with non-standard casing are matched case-insensitively.

    Real Django ``request.headers`` is already case-insensitive; this covers
    the plain-dict scan path used when callers pass a raw dict.
    """
    svc = _svc()
    # Lower-cased header key still detected as agent.
    assert svc.classify_client({"x-agent-protocol": "x402"}) == "agent"
    assert svc.classify_client({"accept": "text/html"}) == "browser"


def test_classify_degrades_to_agent_when_headers_missing():
    """A None / header-less input degrades to the agent default (SPEC §6)."""
    svc = _svc()
    assert svc.classify_client(None) == "agent"


# === build_payment_required (R3 / R4 / R5) ===================================


def test_build_payment_required_headers_present():
    """R4 / AC-2: 402 response carries the four mandatory x402 headers."""
    svc = _svc()
    asset = _make_asset()
    headers, _body = svc.build_payment_required(asset)

    assert headers["PAYMENT-REQUIRED"] == "encoded-v2"
    assert headers["X-Agent-Protocol"] == "x402"
    assert headers["X-402-Negotiation-Endpoint"] == "/api/v1/ip/asset-uuid-1/negotiate"
    # R5b: standalone asset -> creator wallet.
    assert headers["X-Solana-Pay-Address"] == _CREATOR_WALLET
    assert headers["X-Payment-Mint"] == _USDC_MINT


def test_build_payment_required_body_schema():
    """R5 / AC-5: body carries accepts[] + how_to_negotiate + preview_url."""
    svc = _svc()
    asset = _make_asset()
    _headers, body = svc.build_payment_required(asset)

    assert body["error"] == "Payment required"
    assert body["x402Version"] == 2
    assert body["resource"]["url"] == "/api/v1/ip/asset-uuid-1"

    accepts = body["accepts"]
    assert isinstance(accepts, list) and len(accepts) == 1
    accept = accepts[0]
    assert accept["scheme"] == "exact"
    assert accept["network"] == _NETWORK
    assert accept["asset"] == _USDC_MINT
    assert accept["payTo"] == _CREATOR_WALLET


def test_build_payment_required_uses_target_price_as_max_amount():
    """max_amount_required MUST equal the asset's target_price_usdc."""
    svc = _svc()
    asset = _make_asset(target_price="2.75")
    _headers, body = svc.build_payment_required(asset)

    accept = body["accepts"][0]
    # Compare as Decimals (the body serialises as str of the decimal).
    assert accept["amount"] == "2750000"


# === Payment Recipient Resolution (R5b / AC-8 / AC-9) ========================


def test_pay_to_is_creator_for_standalone_asset():
    """AC-8: standalone asset (no parent) -> pay_to == creator.wallet_address.

    Verified both via the shared helper and through build_payment_required's
    headers/body to prove the SSOT rule is applied everywhere.
    """
    from services._payment import resolve_pay_to

    asset = _make_asset()  # parent_id=None

    # Direct helper check.
    assert resolve_pay_to(asset, escrow_pubkey=_ESCROW) == _CREATOR_WALLET

    # Builder applies the same rule.
    svc = _svc()
    headers, body = svc.build_payment_required(asset)
    assert headers["X-Solana-Pay-Address"] == _CREATOR_WALLET
    assert body["accepts"][0]["payTo"] == _CREATOR_WALLET


def test_pay_to_is_escrow_for_secondary_asset():
    """AC-9 / R5b: 2nd-creation (parent_asset set) -> pay_to == escrow pubkey.

    The escrow routing is required for S3 royalty distribution downstream.
    """
    from services._payment import resolve_pay_to

    secondary = _make_asset(parent_id="parent-uuid-9")

    # Direct helper check.
    assert resolve_pay_to(secondary, escrow_pubkey=_ESCROW) == _ESCROW

    # Builder applies the same rule.
    svc = _svc()
    headers, body = svc.build_payment_required(secondary)
    assert headers["X-Solana-Pay-Address"] == _ESCROW
    assert body["accepts"][0]["payTo"] == _ESCROW


def test_resolve_pay_to_reads_settings_when_escrow_not_given(settings):
    """When ``escrow_pubkey`` is None, the helper reads settings lazily.

    This is the path used by SPEC-003/004/008 callers that do not hold a
    pre-resolved escrow pubkey (the SSOT helper then consults
    ``settings.PLATFORM_ESCROW_PUBKEY``).
    """
    from services._payment import resolve_pay_to

    settings.PLATFORM_ESCROW_PUBKEY = "SettingsEscrow111111111111111111111111111"
    secondary = _make_asset(parent_id="parent-uuid-9")

    assert resolve_pay_to(secondary) == "SettingsEscrow111111111111111111111111111"
    # Standalone asset still ignores escrow entirely.
    assert resolve_pay_to(_make_asset()) == _CREATOR_WALLET


def test_get_x402_service_factory_reads_settings(settings):
    """The factory wires X402Service from Django settings."""
    from services.x402_service import X402Service, get_x402_service

    settings.USDC_MINT_ADDRESS = "FactoryMint111111111111111111111111111111111"
    settings.PLATFORM_ESCROW_PUBKEY = "FactoryEscrow11111111111111111111111111111"
    settings.AP2_ENABLED = False

    svc = get_x402_service()
    assert isinstance(svc, X402Service)
    assert svc.usdc_mint == "FactoryMint111111111111111111111111111111111"
    assert svc.escrow_pubkey == "FactoryEscrow11111111111111111111111111111"
    assert svc.ap2_enabled is False


# === Solana Pay browser fallback (R7) ========================================


def test_build_solana_pay_fallback_uses_target_price_as_native_sol():
    """R7 / AC-4: browser fallback is a SOL Buy-It-Now transfer request."""
    svc = _svc()
    asset = _make_asset(target_price="3.25")
    body = svc.build_solana_pay_fallback(asset)

    # The fixed Buy-It-Now amount equals the catalog target price.
    assert decimal.Decimal(str(body["solana_pay"]["amount_sol"])) == decimal.Decimal(
        "3.25"
    )
    # Address routed via the same SSOT helper (standalone -> creator).
    assert body["solana_pay"]["address"] == _CREATOR_WALLET
    assert body["solana_pay"]["asset"] == "SOL"
    assert body["solana_pay"]["cluster"] == "devnet"
    assert body["solana_pay"]["rpc_url"] == "https://api.devnet.solana.com"
    assert body["solana_pay"]["reference"]
    assert "mint" not in body["solana_pay"]
    # Native SOL transfer requests use the official solana:<recipient> URL.
    assert body["solana_pay"]["uri"].startswith(f"solana:{_CREATOR_WALLET}?")
    assert "cluster=devnet" in body["solana_pay"]["uri"]
    assert "reference=" in body["solana_pay"]["uri"]
    assert "spl-token" not in body["solana_pay"]["uri"]


# === LicenseService.is_licensed DB short-circuit (R10 / AC-7) ================
# (Lives in tests/unit but needs the DB; kept here as a service-level check.)


@pytest.mark.django_db
def test_is_licensed_db_short_circuits_onchain_verify(monkeypatch):
    """AC-7 / R10: a pre-existing License row -> True with zero solana calls.

    The solana fake is wired in but its verify_usdc_payment MUST NOT be invoked
    when the DB already has a License for (asset, tx_sig).
    """
    from apps.ip.models import IpAsset
    from apps.settlement.models import License
    from services.license_service import LicenseService
    from tests.fakes import FakeSolanaService
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    creator = CreatorFactory(wallet_address=_CREATOR_WALLET)
    asset = IpAssetFactory(creator=creator, status=IpAsset.LISTED)
    license = LicenseFactory(asset=asset, payment_tx_sig="tx_existing_001")

    fake_solana = FakeSolanaService()
    monkeypatch.setattr(
        "services.license_service.get_solana_service", lambda: fake_solana
    )

    result = LicenseService().is_licensed(asset, license.payment_tx_sig)

    assert result is True
    # R10: zero on-chain verify calls when the DB license pre-exists.
    verify_calls = [c for c in fake_solana.calls if c[0] == "verify_usdc_payment"]
    assert len(verify_calls) == 0
