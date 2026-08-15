"""공식 x402 V2 결제 요구·검증·정산을 담당하는 서비스."""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_response_header,
)
from x402.mechanisms.svm.exact import ExactSvmServerScheme
from x402.schemas import AssetAmount, ResourceConfig, ResourceInfo
from x402.server import x402ResourceServerSync

USDC_DECIMALS = 6
X402_EXACT_SCHEME = "exact"
SOLANA_DEVNET_CAIP2 = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


class X402ProtocolError(RuntimeError):
    """x402 요청을 처리할 수 없을 때 발생한다."""


class X402PaymentInvalid(X402ProtocolError):
    """제출된 x402 결제 서명이 요구 조건을 충족하지 않을 때 발생한다."""


@dataclass(frozen=True)
class X402Challenge:
    """클라이언트에 전달할 공식 x402 V2 결제 조건."""

    headers: dict[str, str]
    body: dict[str, Any]
    payment_required: Any


@dataclass(frozen=True)
class X402Settlement:
    """Facilitator가 검증하고 체인에 정산한 결제 결과."""

    transaction: str
    payer: str
    network: str
    response_header: str


class X402ProtocolService:
    """공식 SDK와 Facilitator를 이용해 x402 V2 Exact 결제를 처리한다."""

    def __init__(
        self,
        *,
        facilitator_url: str,
        network: str = SOLANA_DEVNET_CAIP2,
        server: Any | None = None,
    ) -> None:
        self.network = network
        if server is not None:
            self.server = server
            return

        facilitator = HTTPFacilitatorClientSync(
            FacilitatorConfig(url=facilitator_url)
        )
        self.server = x402ResourceServerSync(facilitator)
        self.server.register(network, ExactSvmServerScheme())
        # SVM 결제 생성에 필요한 feePayer를 Facilitator에서 가져온다.
        self.server.initialize()

    def build_challenge(
        self,
        *,
        resource_url: str,
        description: str,
        pay_to: str,
        amount_usdc: decimal.Decimal,
        usdc_mint: str,
        memo: str,
    ) -> X402Challenge:
        """자산별 결제 조건을 PAYMENT-REQUIRED 헤더와 JSON 본문으로 만든다."""
        atomic_amount = self._to_atomic_usdc(amount_usdc)
        requirements = self.server.build_payment_requirements(
            ResourceConfig(
                scheme=X402_EXACT_SCHEME,
                pay_to=pay_to,
                price=AssetAmount(
                    amount=atomic_amount,
                    asset=usdc_mint,
                    extra={"memo": memo},
                ),
                network=self.network,
                max_timeout_seconds=300,
            )
        )
        payment_required = self.server.create_payment_required_response(
            requirements,
            ResourceInfo(
                url=resource_url,
                description=description,
                mime_type="application/json",
                service_name="VeriProof",
                tags=["license", "image", "solana"],
            ),
            "Payment required",
        )
        encoded = encode_payment_required_header(payment_required)
        return X402Challenge(
            headers={
                "PAYMENT-REQUIRED": encoded,
                "Access-Control-Expose-Headers": "PAYMENT-REQUIRED",
            },
            body=payment_required.model_dump(
                by_alias=True,
                exclude_none=True,
            ),
            payment_required=payment_required,
        )

    def verify_and_settle(
        self,
        *,
        payment_signature: str,
        challenge: X402Challenge,
    ) -> X402Settlement:
        """PAYMENT-SIGNATURE를 검증한 뒤 Facilitator를 통해 정산한다."""
        try:
            payload = decode_payment_signature_header(payment_signature)
        except Exception as exc:  # SDK의 Base64·JSON·스키마 오류를 단일 경계로 변환한다.
            raise X402PaymentInvalid("PAYMENT-SIGNATURE 형식이 올바르지 않습니다.") from exc

        requirements = self.server.find_matching_requirements(
            challenge.payment_required.accepts,
            payload,
        )
        if requirements is None:
            raise X402PaymentInvalid("제출된 결제가 현재 결제 조건과 일치하지 않습니다.")

        try:
            verification = self.server.verify_payment(payload, requirements)
            print("[verify_and_settle] verification:--------------------------------- ", verification)
        except Exception as exc:
            raise X402ProtocolError("x402 Facilitator 결제 검증 호출에 실패했습니다.") from exc
        if not verification.is_valid:
            print("[verify_and_settle] verification is not valid:--------------------------------- ", verification.invalid_reason)
            reason = verification.invalid_reason or "결제 검증에 실패했습니다."
            raise X402PaymentInvalid(reason)

        try:
            settlement = self.server.settle_payment(payload, requirements)
            print("[verify_and_settle] settlement:--------------------------------- ", settlement)
        except Exception as exc:
            print("[verify_and_settle] settlement error:--------------------------------- ", exc)
            raise X402ProtocolError("x402 Facilitator 결제 정산 호출에 실패했습니다.") from exc
        if not settlement.success:
            reason = settlement.error_reason or "결제 정산에 실패했습니다."
            raise X402PaymentInvalid(reason)
        if not settlement.transaction or not settlement.payer:
            raise X402ProtocolError("Facilitator 정산 응답에 거래 또는 구매자 정보가 없습니다.")

        return X402Settlement(
            transaction=settlement.transaction,
            payer=settlement.payer,
            network=str(settlement.network),
            response_header=encode_payment_response_header(settlement),
        )

    @staticmethod
    def _to_atomic_usdc(amount_usdc: decimal.Decimal) -> str:
        """USDC 금액을 6자리 최소 단위 문자열로 변환한다."""
        amount = decimal.Decimal(amount_usdc)
        if not amount.is_finite() or amount <= 0:
            raise ValueError("x402 결제 금액은 0보다 큰 유한한 값이어야 합니다.")
        atomic = amount * (decimal.Decimal(10) ** USDC_DECIMALS)
        if atomic != atomic.to_integral_value():
            raise ValueError("USDC 결제 금액은 소수점 6자리까지만 허용됩니다.")
        return str(int(atomic))


@lru_cache(maxsize=8)
def get_x402_protocol_service(
    facilitator_url: str,
    network: str,
) -> X402ProtocolService:
    """프로세스별로 초기화한 공식 x402 서버를 재사용한다."""
    return X402ProtocolService(
        facilitator_url=facilitator_url,
        network=network,
    )
