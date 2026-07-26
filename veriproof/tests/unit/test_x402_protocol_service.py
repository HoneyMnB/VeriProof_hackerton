"""공식 x402 V2 헤더와 최소 단위 변환 계약을 검증한다."""

from __future__ import annotations

import decimal
from types import SimpleNamespace

import pytest
from x402.http.utils import (
    decode_payment_required_header,
    encode_payment_signature_header,
)
from x402.schemas import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
    SettleResponse,
)

from services.x402_protocol_service import (
    SOLANA_DEVNET_CAIP2,
    X402PaymentInvalid,
    X402ProtocolService,
)

_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"
_WALLET = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


class _ServerStub:
    """Facilitator 네트워크 호출 없이 SDK 모델 경계를 실행하는 대역이다."""

    def __init__(self) -> None:
        self.requirements = None
        self.valid = True

    def build_payment_requirements(self, config):
        self.requirements = PaymentRequirements(
            scheme=config.scheme,
            network=config.network,
            asset=config.price.asset,
            amount=config.price.amount,
            pay_to=config.pay_to,
            max_timeout_seconds=config.max_timeout_seconds,
            extra={"feePayer": _WALLET, **(config.price.extra or {})},
        )
        return [self.requirements]

    def create_payment_required_response(
        self,
        requirements,
        resource: ResourceInfo,
        error,
    ):
        return PaymentRequired(
            error=error,
            resource=resource,
            accepts=requirements,
        )

    def find_matching_requirements(self, requirements, payload):
        return requirements[0] if payload.accepted == requirements[0] else None

    def verify_payment(self, payload, requirements):
        return SimpleNamespace(
            is_valid=self.valid,
            invalid_reason=None if self.valid else "invalid_payment",
        )

    def settle_payment(self, payload, requirements):
        return SettleResponse(
            success=True,
            transaction="devnet-tx-1",
            payer=_WALLET,
            network=SOLANA_DEVNET_CAIP2,
            amount=requirements.amount,
        )


def _service(server: _ServerStub) -> X402ProtocolService:
    return X402ProtocolService(
        facilitator_url="https://facilitator.test",
        network=SOLANA_DEVNET_CAIP2,
        server=server,
    )


def test_build_challenge_uses_official_v2_header_and_atomic_usdc():
    server = _ServerStub()
    challenge = _service(server).build_challenge(
        resource_url="https://seller.test/api/v1/ip/asset-1",
        description="테스트 라이선스",
        pay_to=_WALLET,
        amount_usdc=decimal.Decimal("1.250001"),
        usdc_mint=_MINT,
        memo="veriproof:asset-1",
    )

    decoded = decode_payment_required_header(challenge.headers["PAYMENT-REQUIRED"])
    assert decoded.x402_version == 2
    assert decoded.accepts[0].scheme == "exact"
    assert decoded.accepts[0].network == SOLANA_DEVNET_CAIP2
    assert decoded.accepts[0].asset == _MINT
    assert decoded.accepts[0].amount == "1250001"
    assert decoded.accepts[0].pay_to == _WALLET
    assert decoded.accepts[0].extra["feePayer"] == _WALLET


def test_verify_and_settle_returns_standard_payment_response():
    server = _ServerStub()
    service = _service(server)
    challenge = service.build_challenge(
        resource_url="https://seller.test/api/v1/ip/asset-1",
        description="테스트 라이선스",
        pay_to=_WALLET,
        amount_usdc=decimal.Decimal("1"),
        usdc_mint=_MINT,
        memo="veriproof:asset-1",
    )
    payload = PaymentPayload(
        payload={"transaction": "signed-base64-transaction"},
        accepted=server.requirements,
        resource=challenge.payment_required.resource,
    )

    result = service.verify_and_settle(
        payment_signature=encode_payment_signature_header(payload),
        challenge=challenge,
    )

    assert result.transaction == "devnet-tx-1"
    assert result.payer == _WALLET
    assert result.response_header


def test_verify_and_settle_rejects_malformed_signature():
    server = _ServerStub()
    service = _service(server)
    challenge = service.build_challenge(
        resource_url="https://seller.test/api/v1/ip/asset-1",
        description="테스트 라이선스",
        pay_to=_WALLET,
        amount_usdc=decimal.Decimal("1"),
        usdc_mint=_MINT,
        memo="veriproof:asset-1",
    )

    with pytest.raises(X402PaymentInvalid):
        service.verify_and_settle(
            payment_signature="not-base64",
            challenge=challenge,
        )
