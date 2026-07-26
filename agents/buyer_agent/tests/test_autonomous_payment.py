"""해커톤 1단계 자율 결제 정책과 Buyer 도구 테스트."""

import asyncio

import httpx
import pytest
from solders.keypair import Keypair
from x402.http.utils import encode_payment_response_header
from x402.schemas import PaymentRequirements, SettleResponse

from agents.buyer_agent import tools
from agents.buyer_agent.payments.client import AutonomousX402Buyer
from agents.buyer_agent.payments.policy import (
    DEVNET_NETWORK,
    DEVNET_USDC_MINT,
    AutonomousPaymentPolicy,
    PaymentConfigurationError,
    PaymentPolicyRejected,
)


def _requirement(
    *,
    amount: str = "500000",
    network: str = DEVNET_NETWORK,
    asset: str = DEVNET_USDC_MINT,
) -> PaymentRequirements:
    return PaymentRequirements(
        scheme="exact",
        network=network,
        asset=asset,
        amount=amount,
        payTo=str(Keypair().pubkey()),
        maxTimeoutSeconds=60,
    )


def test_environment_policy_selects_only_a_payment_within_the_limit(monkeypatch):
    monkeypatch.setenv("BUYER_AUTONOMOUS_PAYMENT_ENABLED", "true")
    monkeypatch.setenv("BUYER_MAX_PAYMENT_USDC", "0.5")
    monkeypatch.setenv("X402_NETWORK", DEVNET_NETWORK)
    monkeypatch.setenv("USDC_MINT_ADDRESS", DEVNET_USDC_MINT)
    policy = AutonomousPaymentPolicy.from_environment()

    selected = policy.select(2, [_requirement(amount="500000")])

    assert selected.amount == "500000"
    with pytest.raises(PaymentPolicyRejected):
        policy.select(2, [_requirement(amount="500001")])
    with pytest.raises(PaymentPolicyRejected):
        policy.select(2, [_requirement(asset=str(Keypair().pubkey()))])


def test_disabled_policy_rejects_before_signing():
    policy = AutonomousPaymentPolicy(
        enabled=False,
        network=DEVNET_NETWORK,
        asset=DEVNET_USDC_MINT,
        max_atomic_amount=0,
    )
    buyer = AutonomousX402Buyer(policy=policy, private_key="")

    with pytest.raises(PaymentPolicyRejected):
        buyer._build_protocol()


def test_enabled_policy_requires_a_valid_private_key():
    policy = AutonomousPaymentPolicy(
        enabled=True,
        network=DEVNET_NETWORK,
        asset=DEVNET_USDC_MINT,
        max_atomic_amount=1_000_000,
    )

    with pytest.raises(PaymentConfigurationError):
        AutonomousX402Buyer(policy=policy, private_key="")._build_protocol()


def test_buyer_reports_success_only_with_a_successful_payment_response():
    settlement = SettleResponse(
        success=True,
        payer=str(Keypair().pubkey()),
        transaction="devnet-transaction",
        network=DEVNET_NETWORK,
        amount="500000",
    )
    response = httpx.Response(
        200,
        headers={
            "PAYMENT-RESPONSE": encode_payment_response_header(settlement),
        },
        json={"license": {"status": "active"}},
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, url, **kwargs):
            return response

    policy = AutonomousPaymentPolicy(
        enabled=True,
        network=DEVNET_NETWORK,
        asset=DEVNET_USDC_MINT,
        max_atomic_amount=1_000_000,
    )
    private_key = str(Keypair())
    buyer = AutonomousX402Buyer(
        policy=policy,
        private_key=private_key,
        http_client_factory=lambda protocol: FakeClient(),
    )

    result = asyncio.run(
        buyer.purchase("http://seller.test/api/v1/ip/asset")
    )

    assert result["status"] == "purchased"
    assert result["payment_response"]["success"] is True
    assert result["payment_response"]["transaction"] == "devnet-transaction"


def test_agent_tool_does_not_accept_or_return_a_private_key(monkeypatch):
    class FakeBuyer:
        async def purchase(self, resource_url, params=None):
            return {
                "status": "purchased",
                "http_status": 200,
                "body": {"license": {"status": "active"}},
            }

    monkeypatch.setattr(tools, "AutonomousX402Buyer", FakeBuyer)
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"

    result = asyncio.run(tools.purchase_x402_asset(asset_id))

    assert result["status"] == "purchased"
    assert "private_key" not in result
