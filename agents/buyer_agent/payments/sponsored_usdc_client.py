"""KMS-signed, sponsor-paid USDC checkout for the autonomous Buyer Agent."""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import Transaction

from .kms_signer import KmsEd25519Signer
from .policy import (
    PaymentConfigurationError,
    PaymentExecutionError,
    PaymentPolicyRejected,
)

USDC_DECIMALS = 6
USDC_FACTOR = Decimal("1000000")
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


@dataclass(frozen=True)
class SponsoredUsdcPolicy:
    enabled: bool
    mint: str
    max_atomic_amount: int

    @classmethod
    def from_environment(cls) -> "SponsoredUsdcPolicy":
        enabled = os.environ.get(
            "BUYER_AUTONOMOUS_SPONSORED_USDC_ENABLED", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        raw_amount = os.environ.get("BUYER_MAX_SPONSORED_USDC", "0").strip()
        if not enabled:
            return cls(
                enabled=False,
                mint=os.environ.get("USDC_MINT_ADDRESS", "").strip(),
                max_atomic_amount=0,
            )
        try:
            amount = Decimal(raw_amount)
            atomic = amount * USDC_FACTOR
        except InvalidOperation as exc:
            raise PaymentConfigurationError(
                "BUYER_MAX_SPONSORED_USDC는 숫자여야 합니다."
            ) from exc
        if not amount.is_finite() or amount <= 0 or atomic != atomic.to_integral_value():
            raise PaymentConfigurationError(
                "BUYER_MAX_SPONSORED_USDC는 0보다 크고 소수점 6자리 이하여야 합니다."
            )
        return cls(
            enabled=enabled,
            mint=os.environ.get("USDC_MINT_ADDRESS", "").strip(),
            max_atomic_amount=int(atomic),
        )

    def verify_amount(self, amount_usdc: object) -> int:
        if not self.enabled:
            raise PaymentPolicyRejected(
                "BUYER_AUTONOMOUS_SPONSORED_USDC_ENABLED가 활성화되지 않았습니다."
            )
        try:
            amount = Decimal(str(amount_usdc))
            atomic = amount * USDC_FACTOR
        except InvalidOperation as exc:
            raise PaymentPolicyRejected("판매자의 USDC 금액이 올바르지 않습니다.") from exc
        if (
            not amount.is_finite()
            or amount <= 0
            or atomic != atomic.to_integral_value()
            or int(atomic) > self.max_atomic_amount
        ):
            raise PaymentPolicyRejected("요청된 USDC 금액이 위임 한도를 벗어났습니다.")
        return int(atomic)


class AutonomousSponsoredUsdcBuyer:
    """Signs only the canonical sponsored USDC intent issued by VeriProof."""

    def __init__(
        self,
        *,
        policy: SponsoredUsdcPolicy | None = None,
        private_key: str | None = None,
        rpc_url: str | None = None,
        kms_signer: KmsEd25519Signer | None = None,
        http_client_factory=httpx.AsyncClient,
    ) -> None:
        self.policy = policy or SponsoredUsdcPolicy.from_environment()
        self._private_key = (
            private_key
            if private_key is not None
            else os.environ.get("BUYER_WALLET_SECRET_KEY", "")
        ).strip()
        self._rpc_url = (
            rpc_url
            if rpc_url is not None
            else os.environ.get("SOLANA_RPC_URL", "https://api.devnet.solana.com")
        ).strip()
        key_name = os.environ.get("BUYER_KMS_KEY_NAME", "").strip()
        self._kms_signer = kms_signer or (KmsEd25519Signer(key_name) if key_name else None)
        self._http_client_factory = http_client_factory

    async def purchase(self, resource_url: str) -> dict[str, Any]:
        headers = self._agent_headers()
        async with self._http_client_factory(timeout=60) as client:
            intent_response = await client.post(resource_url, headers=headers, json={})
            intent = self._json_object(intent_response, "Agent payment intent response is invalid.")
            if intent_response.status_code != 201:
                raise PaymentExecutionError(self._detail(intent, "Agent payment intent is unavailable."))
            payer = await self._payer_pubkey()
            signed_transaction = await self._sign_intent(intent, payer)
            signature = await self._send_transaction(signed_transaction)
            settle_url = f"{resource_url.rstrip('/')}/settle"
            for _ in range(12):
                settle_response = await client.post(
                    settle_url,
                    headers=headers,
                    json={
                        "intent_id": intent.get("intent_id"),
                        "transaction_signature": signature,
                    },
                )
                body = self._json_object(settle_response, "Agent settlement response is invalid.")
                if settle_response.status_code == 200 and body.get("status") == "PAID":
                    return {"status": "purchased", "transaction": signature, "body": body}
                if settle_response.status_code != 202:
                    raise PaymentExecutionError(self._detail(body, "Agent payment settlement failed."))
                await asyncio.sleep(1)
        raise PaymentExecutionError("USDC 거래의 finalization 시간이 초과되었습니다.")

    async def _sign_intent(self, intent: dict[str, Any], payer: Pubkey) -> str:
        amount = self.policy.verify_amount(intent.get("amount_usdc"))
        if str(intent.get("currency")) != "USDC" or str(intent.get("usdc_mint")) != self.policy.mint:
            raise PaymentPolicyRejected("허용된 USDC mint의 결제 조건만 서명할 수 있습니다.")
        if str(intent.get("buyer_wallet")) != str(payer):
            raise PaymentPolicyRejected("결제 intent의 구매 지갑이 Buyer KMS 지갑과 다릅니다.")
        try:
            transaction = Transaction.from_bytes(base64.b64decode(str(intent["transaction"])))
            sponsor = Pubkey.from_string(str(intent["sponsor"]))
            recipient = Pubkey.from_string(str(intent["recipient_wallet"]))
            mint = Pubkey.from_string(str(intent["usdc_mint"]))
            memo = str(intent["memo"])
        except (KeyError, ValueError) as exc:
            raise PaymentPolicyRejected("판매자의 sponsor transaction이 올바르지 않습니다.") from exc
        self._verify_canonical_transaction(
            transaction,
            sponsor=sponsor,
            buyer=payer,
            recipient=recipient,
            mint=mint,
            amount=amount,
            memo=memo,
        )
        buyer_signature = await self._sign_message(transaction.message_data())
        signed = Transaction.populate(
            transaction.message,
            [transaction.signatures[0], Signature.from_bytes(buyer_signature)],
        )
        if not all(signed.verify_with_results()):
            raise PaymentExecutionError("sponsor 또는 Buyer KMS 서명을 검증하지 못했습니다.")
        return base64.b64encode(bytes(signed)).decode("ascii")

    def _verify_canonical_transaction(
        self,
        transaction: Transaction,
        *,
        sponsor: Pubkey,
        buyer: Pubkey,
        recipient: Pubkey,
        mint: Pubkey,
        amount: int,
        memo: str,
    ) -> None:
        try:
            from spl.token.constants import TOKEN_PROGRAM_ID
            from spl.token.instructions import (
                TransferCheckedParams,
                create_idempotent_associated_token_account,
                get_associated_token_address,
                transfer_checked,
            )
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise PaymentConfigurationError("sponsored USDC requires spl-token") from exc
        if len(transaction.signatures) != 2 or not memo.startswith("VERIPROOF:USDC:"):
            raise PaymentPolicyRejected("sponsor transaction의 서명 또는 memo가 올바르지 않습니다.")
        sender_ata = get_associated_token_address(buyer, mint, TOKEN_PROGRAM_ID)
        recipient_ata = get_associated_token_address(recipient, mint, TOKEN_PROGRAM_ID)
        instructions = [
            create_idempotent_associated_token_account(sponsor, recipient, mint, TOKEN_PROGRAM_ID),
            transfer_checked(
                TransferCheckedParams(
                    program_id=TOKEN_PROGRAM_ID,
                    source=sender_ata,
                    mint=mint,
                    dest=recipient_ata,
                    owner=buyer,
                    amount=amount,
                    decimals=USDC_DECIMALS,
                )
            ),
            Instruction(Pubkey.from_string(MEMO_PROGRAM_ID), memo.encode("utf-8"), []),
        ]
        expected = Message.new_with_blockhash(
            instructions, sponsor, transaction.message.recent_blockhash
        )
        if bytes(expected) != bytes(transaction.message) or not transaction.verify_with_results()[0]:
            raise PaymentPolicyRejected("Buyer KMS가 승인할 sponsor transaction이 아닙니다.")

    async def _payer_pubkey(self) -> Pubkey:
        if self._kms_signer is not None:
            return await asyncio.to_thread(self._kms_signer.public_key)
        return self._keypair().pubkey()

    async def _sign_message(self, message: bytes) -> bytes:
        if self._kms_signer is not None:
            return await asyncio.to_thread(self._kms_signer.sign, message)
        return bytes(self._keypair().sign_message(message))

    async def _send_transaction(self, encoded_transaction: str) -> str:
        if not self._rpc_url:
            raise PaymentConfigurationError("SOLANA_RPC_URL이 비어 있습니다.")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self._rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        encoded_transaction,
                        {"encoding": "base64", "preflightCommitment": "confirmed"},
                    ],
                },
            )
        payload = self._json_object(response, "Solana RPC response is invalid.")
        signature = payload.get("result")
        if response.status_code != 200 or payload.get("error") or not isinstance(signature, str):
            raise PaymentExecutionError("Solana RPC가 sponsor USDC 거래를 거절했습니다.")
        return signature

    def _agent_headers(self) -> dict[str, str]:
        token = os.environ.get("AGENT_SPONSORED_PAYMENT_TOKEN", "").strip()
        if not token:
            raise PaymentConfigurationError("AGENT_SPONSORED_PAYMENT_TOKEN이 설정되지 않았습니다.")
        return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    def _keypair(self) -> Keypair:
        if not self._private_key:
            raise PaymentConfigurationError("BUYER_WALLET_SECRET_KEY가 설정되지 않았습니다.")
        try:
            return Keypair.from_base58_string(self._private_key)
        except ValueError as exc:
            raise PaymentConfigurationError("BUYER_WALLET_SECRET_KEY가 유효한 Solana Base58 개인키가 아닙니다.") from exc

    @staticmethod
    def _json_object(response: httpx.Response, fallback: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise PaymentExecutionError(fallback) from exc
        if not isinstance(body, dict):
            raise PaymentExecutionError(fallback)
        return body

    @staticmethod
    def _detail(body: dict[str, Any], fallback: str) -> str:
        detail = body.get("detail")
        return detail if isinstance(detail, str) else fallback
