"""Native Solana Devnet SOL payment client for the buyer agent."""

import asyncio
import base64
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from .policy import PaymentConfigurationError, PaymentExecutionError, PaymentPolicyRejected

DEVNET_NETWORK = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
# CAIP-2 네트워크 참조값과 RPC ``getGenesisHash`` 응답은 다른 식별자다.
# Native SOL 전송 전에는 RPC가 반환하는 실제 Devnet genesis hash를 확인한다.
DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
LAMPORTS_PER_SOL = Decimal("1000000000")
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


class AutonomousSolBuyer:
    """Pays only seller-published native SOL terms on Solana Devnet."""

    def __init__(self, *, private_key: str | None = None, rpc_url: str | None = None) -> None:
        self._private_key = (private_key if private_key is not None else os.environ.get("BUYER_WALLET_SECRET_KEY", "")).strip()
        self._rpc_url = (rpc_url if rpc_url is not None else os.environ.get("SOLANA_RPC_URL", "https://api.devnet.solana.com")).strip()

    async def purchase(self, resource_url: str) -> dict[str, Any]:
        terms_url = f"{resource_url.rstrip('/')}/agent-sol-payment"
        async with httpx.AsyncClient(timeout=60) as client:
            terms_response = await client.get(terms_url, headers={"Accept": "application/json"})
            if terms_response.status_code != 200:
                raise PaymentExecutionError(self._detail(terms_response, "SOL payment terms are unavailable."))
            terms = self._json_object(terms_response)
            amount = self._validate_terms(terms)
            payer = self._keypair()
            await self._ensure_devnet_rpc()
            signature = await self._send_transfer(
                payer,
                recipient=str(terms["recipient"]),
                lamports=amount,
                memo=str(terms["memo"]),
            )
            settle_response = await client.post(
                f"{terms_url}/settle",
                json={"tx_signature": signature, "buyer_wallet": str(payer.pubkey())},
            )
            if settle_response.status_code != 200:
                raise PaymentExecutionError(self._detail(settle_response, "SOL payment was sent but license settlement failed."))
            result = self._json_object(settle_response)
            if result.get("status") != "purchased":
                raise PaymentExecutionError("Seller did not confirm the SOL purchase.")
            return result

    def _validate_terms(self, terms: dict[str, Any]) -> int:
        if not self._enabled():
            raise PaymentPolicyRejected("BUYER_AUTONOMOUS_SOL_PAYMENT_ENABLED가 활성화되지 않았습니다.")
        if terms.get("currency") != "SOL" or terms.get("network") != DEVNET_NETWORK:
            raise PaymentPolicyRejected("Devnet native SOL 결제 조건만 허용됩니다.")
        try:
            Pubkey.from_string(str(terms["recipient"]))
            amount = Decimal(str(terms["amount_sol"]))
            lamports = amount * LAMPORTS_PER_SOL
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise PaymentPolicyRejected("판매자의 SOL 결제 조건이 올바르지 않습니다.") from exc
        maximum = self._max_lamports()
        if not amount.is_finite() or amount <= 0 or lamports != lamports.to_integral_value() or int(lamports) > maximum:
            raise PaymentPolicyRejected("요청된 SOL 금액이 위임 한도를 벗어났습니다.")
        memo = terms.get("memo")
        if not isinstance(memo, str) or not memo.startswith("VERIPROOF:") or len(memo.encode()) > 566:
            raise PaymentPolicyRejected("판매자의 SOL 결제 메모가 올바르지 않습니다.")
        return int(lamports)

    async def _send_transfer(self, payer: Keypair, *, recipient: str, lamports: int, memo: str) -> str:
        blockhash = await self._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
        try:
            recent_blockhash = Hash.from_string(blockhash["value"]["blockhash"])
            transaction = Transaction.new_signed_with_payer(
                [
                    transfer(TransferParams(from_pubkey=payer.pubkey(), to_pubkey=Pubkey.from_string(recipient), lamports=lamports)),
                    Instruction(Pubkey.from_string(MEMO_PROGRAM_ID), memo.encode(), []),
                ],
                payer.pubkey(),
                [payer],
                recent_blockhash,
            )
        except (KeyError, ValueError) as exc:
            raise PaymentExecutionError("Devnet blockhash 또는 SOL 결제 조건이 올바르지 않습니다.") from exc
        encoded = base64.b64encode(bytes(transaction)).decode("ascii")
        signature = await self._rpc("sendTransaction", [encoded, {"encoding": "base64", "preflightCommitment": "confirmed"}])
        if not isinstance(signature, str):
            raise PaymentExecutionError("Devnet이 SOL 거래 서명을 반환하지 않았습니다.")
        await self._confirm(signature)
        return signature

    async def _ensure_devnet_rpc(self) -> None:
        """Refuse to sign if the configured endpoint is not Solana Devnet."""
        genesis_hash = await self._rpc("getGenesisHash", [])
        if genesis_hash != DEVNET_GENESIS_HASH:
            raise PaymentConfigurationError("SOLANA_RPC_URL은 Solana Devnet RPC여야 합니다.")

    async def _confirm(self, signature: str) -> None:
        for _ in range(12):
            statuses = await self._rpc("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
            value = statuses.get("value", [None])[0] if isinstance(statuses, dict) else None
            if isinstance(value, dict) and value.get("err") is not None:
                raise PaymentExecutionError("Devnet SOL 거래가 실패했습니다.")
            if isinstance(value, dict) and value.get("confirmationStatus") in {"confirmed", "finalized"}:
                return
            await asyncio.sleep(1)
        raise PaymentExecutionError("Devnet SOL 거래 확인 시간이 초과되었습니다.")

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        if not self._rpc_url:
            raise PaymentConfigurationError("SOLANA_RPC_URL이 비어 있습니다.")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self._rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if response.status_code != 200:
            raise PaymentExecutionError("Devnet RPC 요청에 실패했습니다.")
        payload = self._json_object(response)
        if payload.get("error"):
            raise PaymentExecutionError("Devnet RPC가 SOL 결제를 거절했습니다.")
        return payload.get("result")

    def _keypair(self) -> Keypair:
        if not self._private_key:
            raise PaymentConfigurationError("BUYER_WALLET_SECRET_KEY가 설정되지 않았습니다.")
        try:
            return Keypair.from_base58_string(self._private_key)
        except ValueError as exc:
            raise PaymentConfigurationError("BUYER_WALLET_SECRET_KEY가 유효한 Solana Base58 개인키가 아닙니다.") from exc

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PaymentExecutionError("서버 응답이 JSON이 아닙니다.") from exc
        if not isinstance(payload, dict):
            raise PaymentExecutionError("서버 응답 형식이 올바르지 않습니다.")
        return payload

    @staticmethod
    def _detail(response: httpx.Response, fallback: str) -> str:
        try:
            detail = response.json().get("detail")
            return detail if isinstance(detail, str) else fallback
        except (ValueError, AttributeError):
            return fallback

    @staticmethod
    def _enabled() -> bool:
        return os.environ.get("BUYER_AUTONOMOUS_SOL_PAYMENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _max_lamports() -> int:
        raw = os.environ.get("BUYER_MAX_PAYMENT_SOL", "0").strip()
        try:
            value = Decimal(raw)
            lamports = value * LAMPORTS_PER_SOL
        except InvalidOperation as exc:
            raise PaymentConfigurationError("BUYER_MAX_PAYMENT_SOL는 숫자여야 합니다.") from exc
        if not value.is_finite() or value <= 0 or lamports != lamports.to_integral_value():
            raise PaymentConfigurationError("BUYER_MAX_PAYMENT_SOL는 0보다 크고 소수점 9자리 이하여야 합니다.")
        return int(lamports)
