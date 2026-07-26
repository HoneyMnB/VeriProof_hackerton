"""공식 x402 SVM 클라이언트를 사용하는 구매자 자율 결제 서비스."""

import os
from collections.abc import Callable
from typing import Any

import httpx
from x402 import x402Client
from x402.http.clients.httpx import x402HttpxClient
from x402.http.utils import decode_payment_response_header
from x402.mechanisms.svm.exact import ExactSvmClientScheme
from x402.mechanisms.svm.signers import KeypairSigner

from .policy import (
    AutonomousPaymentError,
    AutonomousPaymentPolicy,
    PaymentConfigurationError,
    PaymentExecutionError,
)

HttpClientFactory = Callable[[x402Client], httpx.AsyncClient]


class AutonomousX402Buyer:
    """위임 정책을 통과한 결제를 서명하고 판매자에게 재요청한다."""

    def __init__(
        self,
        policy: AutonomousPaymentPolicy | None = None,
        private_key: str | None = None,
        rpc_url: str | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self.policy = policy or AutonomousPaymentPolicy.from_environment()
        self._private_key = (
            private_key
            if private_key is not None
            else os.environ.get("BUYER_WALLET_SECRET_KEY", "")
        ).strip()
        self._rpc_url = (
            rpc_url
            if rpc_url is not None
            else os.environ.get(
                "SOLANA_RPC_URL", "https://api.devnet.solana.com"
            )
        ).strip()
        self._http_client_factory = http_client_factory

    async def purchase(
        self,
        resource_url: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """402 응답을 정책 검증하고 자동 서명·재요청하여 정산을 완료한다."""
        protocol, buyer_wallet = self._build_protocol()
        client_factory = self._http_client_factory or self._default_client

        try:
            async with client_factory(protocol) as client:
                response = await client.get(
                    resource_url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Agent-Protocol": "x402",
                    },
                )
        except AutonomousPaymentError:
            raise
        except Exception as exc:
            raise PaymentExecutionError(
                f"x402 결제 요청을 완료하지 못했습니다: {exc}"
            ) from exc

        body = self._response_body(response)
        payment_response_header = response.headers.get("PAYMENT-RESPONSE")
        if response.status_code != 200 or not payment_response_header:
            raise PaymentExecutionError(
                f"판매자가 결제를 확정하지 않았습니다 (HTTP {response.status_code})."
            )

        try:
            payment_response = decode_payment_response_header(
                payment_response_header
            )
        except Exception as exc:
            raise PaymentExecutionError(
                "판매자의 PAYMENT-RESPONSE를 해석할 수 없습니다."
            ) from exc
        if not payment_response.success:
            reason = (
                payment_response.error_message
                or payment_response.error_reason
                or "알 수 없는 정산 오류"
            )
            raise PaymentExecutionError(f"x402 정산이 실패했습니다: {reason}")

        return {
            "status": "purchased",
            "http_status": response.status_code,
            "buyer_wallet": buyer_wallet,
            "body": body,
            "payment_response": payment_response.model_dump(
                by_alias=True,
                exclude_none=True,
            ),
            "payment_response_header": payment_response_header,
        }

    def _build_protocol(self) -> tuple[x402Client, str]:
        """개인키를 SDK 서명자로 만들고 정책 선택기를 등록한다."""
        if not self.policy.enabled:
            # 비활성 상태에서는 개인키 유효성보다 정책 거절을 우선한다.
            self.policy.select(2, [])
        if not self._private_key:
            raise PaymentConfigurationError(
                "BUYER_WALLET_SECRET_KEY가 설정되지 않았습니다."
            )
        if not self._rpc_url:
            raise PaymentConfigurationError("SOLANA_RPC_URL이 비어 있습니다.")

        try:
            signer = KeypairSigner.from_base58(self._private_key)
        except Exception as exc:
            raise PaymentConfigurationError(
                "BUYER_WALLET_SECRET_KEY가 유효한 Solana Base58 개인키가 아닙니다."
            ) from exc

        protocol = x402Client(payment_requirements_selector=self.policy.select)
        protocol.register(
            self.policy.network,
            ExactSvmClientScheme(signer, rpc_url=self._rpc_url),
        )
        return protocol, signer.address

    @staticmethod
    def _default_client(protocol: x402Client) -> httpx.AsyncClient:
        """402 재시도 기능이 연결된 공식 httpx 클라이언트를 생성한다."""
        return x402HttpxClient(protocol, timeout=60)

    @staticmethod
    def _response_body(response: httpx.Response) -> Any:
        """JSON 응답은 객체로, 그 외 응답은 제한된 문자열로 반환한다."""
        try:
            return response.json()
        except ValueError:
            return response.text[:2000]
