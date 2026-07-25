"""Native SOL transfer service."""
from __future__ import annotations

import decimal


from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

class CertificateIssueError(Exception):
    """Raised when a native SOL transfer cannot be submitted."""


class SolanaService:
    """Submit native SOL transfers through the configured Solana RPC endpoint."""

    LAMPORTS_PER_SOL = 1_000_000_000

    def __init__(self, rpc_url: str) -> None:
        self.rpc_url = rpc_url

    def transfer_sol(
        self,
        to_pubkey: str,
        sender_secret_key: list[int],
        amount_sol: decimal.Decimal,
    ) -> str:
        """발신 개인키로 수신 지갑에 네이티브 SOL을 전송한다.

        ``sender_secret_key``는 Solana CLI가 내보낸 64개 정수의 바이트 배열이어야
        하며, 트랜잭션 서명 과정에서만 사용하고 서비스 인스턴스에 저장하지 않는다.
        """
        if not self.rpc_url:
            raise CertificateIssueError("transfer_sol requires an RPC URL")
        if len(sender_secret_key) != 64:
            raise CertificateIssueError(
                "sender_secret_key must contain exactly 64 integers"
            )
        if any(
            not isinstance(item, int) or item < 0 or item > 255
            for item in sender_secret_key
        ):
            raise CertificateIssueError(
                "sender_secret_key values must be integers in 0..255"
            )
        if not amount_sol.is_finite() or amount_sol <= 0:
            raise CertificateIssueError("transfer_sol amount must be positive")

        lamports = (
            amount_sol * decimal.Decimal(self.LAMPORTS_PER_SOL)
        ).to_integral_exact(rounding=decimal.ROUND_DOWN)
        if lamports <= 0:
            raise CertificateIssueError("transfer_sol amount is below one lamport")

        try:
            keypair = Keypair.from_bytes(bytes(sender_secret_key))
            sender = keypair.pubkey()
            instruction = transfer(
                TransferParams(
                    from_pubkey=sender,
                    to_pubkey=Pubkey.from_string(to_pubkey),
                    lamports=int(lamports),
                )
            )
            client = Client(self.rpc_url)
            blockhash = client.get_latest_blockhash().value.blockhash
            transaction = Transaction([keypair], Message([instruction], sender), blockhash)
            response = client.send_transaction(transaction)
            return str(getattr(response, "value", response))
        except CertificateIssueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CertificateIssueError(f"transfer_sol failed: {exc}") from exc
