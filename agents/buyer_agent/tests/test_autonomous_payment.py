"""해커톤 1단계 자율 결제 정책과 Buyer 도구 테스트."""

import asyncio

import httpx
import pytest
from solders.keypair import Keypair
from x402.http.utils import encode_payment_response_header
from x402.schemas import PaymentRequirements, SettleResponse

from agents.buyer_agent import tools
from agents.buyer_agent.payments.client import AutonomousX402Buyer
from agents.buyer_agent.payments.sol_client import (
    DEVNET_GENESIS_HASH,
    AutonomousSolBuyer,
)
from agents.buyer_agent.payments.policy import (
    DEVNET_NETWORK,
    DEVNET_USDC_MINT,
    AutonomousPaymentPolicy,
    PaymentConfigurationError,
    PaymentPolicyRejected,
)
from agents.buyer_agent.payment_approval import (
    PAYMENT_APPROVAL_STATE_KEY,
    PAYMENT_MODE_APPROVAL,
    PAYMENT_MODE_STATE_KEY,
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


def test_sol_buyer_accepts_only_published_devnet_sol_terms(monkeypatch):
    monkeypatch.setenv("BUYER_AUTONOMOUS_SOL_PAYMENT_ENABLED", "true")
    monkeypatch.setenv("BUYER_MAX_PAYMENT_SOL", "0.5")
    buyer = AutonomousSolBuyer(private_key="")
    terms = {
        "currency": "SOL",
        "network": DEVNET_NETWORK,
        "recipient": str(Keypair().pubkey()),
        "amount_sol": "0.5",
        "memo": "VERIPROOF:4882f2fc-b963-4f36-8c70-69a0e88d11d8:SOL",
    }

    assert buyer._validate_terms(terms) == 500_000_000
    terms["amount_sol"] = "0.500000001"
    with pytest.raises(PaymentPolicyRejected):
        buyer._validate_terms(terms)


def test_sol_buyer_rejects_non_devnet_terms(monkeypatch):
    monkeypatch.setenv("BUYER_AUTONOMOUS_SOL_PAYMENT_ENABLED", "true")
    monkeypatch.setenv("BUYER_MAX_PAYMENT_SOL", "1")
    buyer = AutonomousSolBuyer(private_key="")

    with pytest.raises(PaymentPolicyRejected):
        buyer._validate_terms(
            {
                "currency": "SOL",
                "network": "solana:mainnet",
                "recipient": str(Keypair().pubkey()),
                "amount_sol": "0.1",
                "memo": "VERIPROOF:asset:SOL",
            }
        )


def test_sol_buyer_accepts_the_real_devnet_genesis_hash(monkeypatch):
    buyer = AutonomousSolBuyer(private_key="")

    async def devnet_rpc(method, params):
        assert method == "getGenesisHash"
        return DEVNET_GENESIS_HASH

    monkeypatch.setattr(buyer, "_rpc", devnet_rpc)
    asyncio.run(buyer._ensure_devnet_rpc())


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


def test_x402_purchase_pauses_until_matching_user_approval(monkeypatch):
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"

    class FakeContext:
        state = {PAYMENT_MODE_STATE_KEY: PAYMENT_MODE_APPROVAL}

    class FakeBuyer:
        async def purchase(self, resource_url, params=None):
            return {"status": "purchased"}

    context = FakeContext()
    monkeypatch.setattr(tools, "AutonomousX402Buyer", FakeBuyer)

    paused = asyncio.run(
        tools.purchase_x402_asset(asset_id, tool_context=context)
    )

    assert paused == {
        "status": "approval_required",
        "asset_id": asset_id,
        "payment_method": "USDC_X402",
        "decision": "pending",
    }
    context.state[PAYMENT_APPROVAL_STATE_KEY] = {
        "asset_id": asset_id,
        "payment_method": "USDC_X402",
        "decision": "approved",
    }

    purchased = asyncio.run(
        tools.purchase_x402_asset(asset_id, tool_context=context)
    )

    assert purchased == {"status": "purchased"}
    assert context.state[PAYMENT_APPROVAL_STATE_KEY]["decision"] == "consumed"


def test_declined_sol_purchase_never_constructs_the_buyer(monkeypatch):
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"

    class FakeContext:
        state = {
            PAYMENT_MODE_STATE_KEY: PAYMENT_MODE_APPROVAL,
            PAYMENT_APPROVAL_STATE_KEY: {
                "asset_id": asset_id,
                "payment_method": "SOL_NATIVE",
                "decision": "declined",
            },
        }

    class UnexpectedBuyer:
        def __init__(self):
            raise AssertionError("payment buyer must not be constructed")

    monkeypatch.setattr(tools, "AutonomousSolBuyer", UnexpectedBuyer)

    result = asyncio.run(
        tools.purchase_sol_asset(asset_id, tool_context=FakeContext())
    )

    assert result["status"] == "payment_declined"


def test_purchase_reuses_the_asset_accepted_in_the_current_agent_session(
    monkeypatch,
):
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"
    accepted_session_id = "2f97091f-5c59-4d37-a4e0-b4c76b0215d2"

    class FakeContext:
        state = {}

    class FakeBuyer:
        received_params = None

        async def purchase(self, resource_url, params=None):
            self.received_params = params
            type(self).received_params = params
            return {"status": "purchased"}

    context = FakeContext()
    tools._remember_accepted_session(
        context,
        asset_id,
        {"status": "ACCEPT", "session_id": accepted_session_id},
    )
    monkeypatch.setattr(tools, "AutonomousX402Buyer", FakeBuyer)

    result = asyncio.run(
        tools.purchase_x402_asset(asset_id, tool_context=context)
    )

    assert result["status"] == "purchased"
    assert FakeBuyer.received_params == {"session_id": accepted_session_id}


def test_sol_purchase_reuses_the_accepted_negotiation_session(monkeypatch):
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"
    accepted_session_id = "2f97091f-5c59-4d37-a4e0-b4c76b0215d2"

    class FakeContext:
        state = {}

    class FakeBuyer:
        received_params = None

        async def purchase(self, resource_url, params=None):
            type(self).received_params = params
            return {"status": "purchased"}

    context = FakeContext()
    tools._remember_accepted_session(
        context,
        asset_id,
        {"status": "ACCEPT", "session_id": accepted_session_id},
    )
    monkeypatch.setattr(tools, "AutonomousSolBuyer", FakeBuyer)

    result = asyncio.run(tools.purchase_sol_asset(asset_id, tool_context=context))

    assert result["status"] == "purchased"
    assert FakeBuyer.received_params == {"session_id": accepted_session_id}


def test_accepted_negotiation_response_is_saved_in_agent_session(monkeypatch):
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"
    accepted_session_id = "2f97091f-5c59-4d37-a4e0-b4c76b0215d2"

    class FakeContext:
        state = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, **kwargs):
            return httpx.Response(
                200,
                json={"status": "ACCEPT", "session_id": accepted_session_id},
            )

    monkeypatch.setattr(tools.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    context = FakeContext()

    result = asyncio.run(
        tools.negotiate_license(
            asset_id,
            buyer_agent_id="buyer-1",
            offer_sol=0.5,
            tool_context=context,
        )
    )

    assert result["body"]["session_id"] == accepted_session_id
    assert tools._resolve_session_id(asset_id, "", context) == accepted_session_id


def test_explicit_session_takes_precedence_over_stored_agent_session(monkeypatch):
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"
    stored_session_id = "2f97091f-5c59-4d37-a4e0-b4c76b0215d2"
    explicit_session_id = "9f2188a8-41bf-4bc0-af26-baef7ce7104f"

    class FakeContext:
        state = {}

    context = FakeContext()
    tools._remember_accepted_session(
        context,
        asset_id,
        {"status": "ACCEPT", "session_id": stored_session_id},
    )

    assert tools._resolve_session_id(
        asset_id, explicit_session_id, context
    ) == explicit_session_id


def test_counter_offer_clears_a_previously_accepted_session(monkeypatch):
    asset_id = "4882f2fc-b963-4f36-8c70-69a0e88d11d8"
    accepted_session_id = "2f97091f-5c59-4d37-a4e0-b4c76b0215d2"

    class FakeContext:
        state = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, **kwargs):
            return httpx.Response(200, json={"status": "COUNTER_OFFER"})

    context = FakeContext()
    tools._remember_accepted_session(
        context,
        asset_id,
        {"status": "ACCEPT", "session_id": accepted_session_id},
    )
    monkeypatch.setattr(tools.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    asyncio.run(
        tools.negotiate_license(
            asset_id,
            buyer_agent_id="buyer-1",
            offer_sol=0.4,
            tool_context=context,
        )
    )

    assert tools._resolve_session_id(asset_id, "", context) == ""
