"""환경 변수 위임 한도를 적용하는 해커톤용 자율 결제 정책."""

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from x402.schemas import PaymentRequirements

DEVNET_NETWORK = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
DEVNET_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
USDC_ATOMIC_FACTOR = Decimal(1000000)


class AutonomousPaymentError(RuntimeError):
    """자율 결제를 안전하게 중단해야 하는 경우의 기본 예외."""

    code = "autonomous_payment_error"


class PaymentConfigurationError(AutonomousPaymentError):
    """필수 지갑 또는 정책 설정이 올바르지 않은 경우."""

    code = "payment_configuration_error"


class PaymentPolicyRejected(AutonomousPaymentError):
    """판매자의 결제 요구가 구매자의 위임 정책을 벗어난 경우."""

    code = "payment_policy_rejected"


class PaymentExecutionError(AutonomousPaymentError):
    """서명 이후 정산 또는 판매자 응답 검증이 실패한 경우."""

    code = "payment_execution_error"


def _read_enabled(value: str) -> bool:
    """명시적인 true 값만 자율 결제 활성화로 인정한다."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_atomic_usdc(value: str) -> int:
    """USDC 문자열 금액을 6자리 최소 단위 정수로 변환한다."""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise PaymentConfigurationError(
            "BUYER_MAX_PAYMENT_USDC는 숫자여야 합니다."
        ) from exc

    atomic = amount * USDC_ATOMIC_FACTOR
    if not amount.is_finite() or amount <= 0 or atomic != atomic.to_integral_value():
        raise PaymentConfigurationError(
            "BUYER_MAX_PAYMENT_USDC는 0보다 크고 소수점 6자리 이하여야 합니다."
        )
    return int(atomic)


@dataclass(frozen=True)
class AutonomousPaymentPolicy:
    """허용 네트워크·토큰·거래당 최대 금액을 고정하는 정책."""

    enabled: bool
    network: str
    asset: str
    max_atomic_amount: int

    @classmethod
    def from_environment(cls) -> "AutonomousPaymentPolicy":
        """프로세스 환경에서 현재 위임 정책을 구성한다."""
        enabled = _read_enabled(
            os.environ.get("BUYER_AUTONOMOUS_PAYMENT_ENABLED", "false")
        )
        print("enabled", enabled)
        max_amount = os.environ.get("BUYER_MAX_PAYMENT_USDC", "0").strip()
        if enabled:
            max_atomic_amount = _to_atomic_usdc(max_amount)
        else:
            max_atomic_amount = 0
        return cls(
            enabled=enabled,
            network=os.environ.get("X402_NETWORK", DEVNET_NETWORK).strip(),
            asset=os.environ.get("USDC_MINT_ADDRESS", DEVNET_USDC_MINT).strip(),
            max_atomic_amount=max_atomic_amount,
        )

    def select(
        self,
        version: int,
        requirements: list[PaymentRequirements],
    ) -> PaymentRequirements:
        """서버 요구 중 위임 범위에 정확히 맞는 첫 결제 조건을 선택한다."""
        if not self.enabled:
            raise PaymentPolicyRejected(
                "BUYER_AUTONOMOUS_PAYMENT_ENABLED가 활성화되지 않았습니다."
            )
        if version != 2:
            raise PaymentPolicyRejected("x402 V2 결제 요구만 허용됩니다.")

        for requirement in requirements:
            try:
                amount = int(requirement.amount)
            except (TypeError, ValueError):
                continue
            if (
                requirement.scheme == "exact"
                and str(requirement.network) == self.network
                and requirement.asset == self.asset
                and 0 < amount <= self.max_atomic_amount
            ):
                return requirement

        raise PaymentPolicyRejected(
            "요청된 네트워크, USDC 토큰 또는 금액이 위임 정책을 벗어났습니다."
        )
