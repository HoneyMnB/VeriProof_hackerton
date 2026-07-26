"""Native SOL transfer service."""
from __future__ import annotations

import base64
import decimal
import re
import time
from typing import Any


class AnchorFailed(Exception):
    """Raised when an on-chain content anchor cannot be submitted or read."""


class CertificateIssueError(Exception):
    """Raised when a native SOL transfer cannot be submitted."""


class SolanaService:
    """Submit signed Solana devnet transactions through a JSON-RPC endpoint."""

    LAMPORTS_PER_SOL = 1_000_000_000
    MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
    MAX_MEMO_BYTES = 566
    SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

    def __init__(self, rpc_url: str, sender_secret_key: list[int] | None = None) -> None:
        self.rpc_url = rpc_url
        self.sender_secret_key = sender_secret_key

    def anchor_hash(
        self,
        image_sha256: str,
        creator_pubkey: str,
        sender_secret_key: list[int] | None = None,
    ) -> str:
        """Record a content hash on-chain with Solana's Memo program.

        The image bytes stay off-chain. Only the deterministic SHA-256 and the
        creator wallet are written to devnet so later systems can verify the
        registered image against the transaction memo.
        """
        if not self.SHA256_PATTERN.fullmatch(image_sha256):
            raise AnchorFailed("image_sha256 must be a 64-character hex string")
        if not creator_pubkey:
            raise AnchorFailed("creator_pubkey is required")

        memo = f"veriproof:anchor:{image_sha256.lower()}:{creator_pubkey}"
        try:
            return self.submit_memo(memo, sender_secret_key)
        except Exception as exc:  # noqa: BLE001 - normalized at service boundary
            if isinstance(exc, AnchorFailed):
                raise
            raise AnchorFailed(f"anchor_hash failed: {exc}") from exc

    def issue_registration_certificate(
        self,
        asset_id: Any,
        creator_pubkey: str,
        content_sha256: str,
        sender_secret_key: list[int] | None = None,
    ) -> str:
        """Record a registration certificate memo for an anchored asset."""
        if not asset_id:
            raise CertificateIssueError("asset_id is required")
        if not creator_pubkey:
            raise CertificateIssueError("creator_pubkey is required")
        if not self.SHA256_PATTERN.fullmatch(content_sha256):
            raise CertificateIssueError("content_sha256 must be a 64-character hex string")

        memo = f"veriproof:registration:{asset_id}:{creator_pubkey}:{content_sha256.lower()}"
        try:
            return self.submit_memo(memo, sender_secret_key)
        except Exception as exc:  # noqa: BLE001 - normalized at service boundary
            if isinstance(exc, CertificateIssueError):
                raise
            raise CertificateIssueError(
                f"issue_registration_certificate failed: {exc}"
            ) from exc

    def submit_memo(
        self,
        memo: str,
        sender_secret_key: list[int] | None = None,
    ) -> str:
        """Submit a signed Memo transaction and return its transaction signature."""
        secret_key = self._resolve_sender_secret_key(sender_secret_key, "submit_memo")
        memo_bytes = self._validate_memo(memo)
        try:
            blockhash = self._request_latest_blockhash()
            transaction = self._build_signed_memo_transaction(
                memo_bytes, secret_key, blockhash
            )
            return self._submit_transaction(transaction)
        except CertificateIssueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CertificateIssueError(f"submit_memo failed: {exc}") from exc

    def get_transaction(self, signature: str) -> dict[str, Any]:
        """Fetch a devnet transaction in jsonParsed form."""
        if not signature:
            raise AnchorFailed("transaction signature is required")
        return self._post_rpc(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )

    def get_memo_texts(self, signature: str) -> list[str]:
        """Return Memo-program text payloads found in a confirmed transaction."""
        payload = self.get_transaction(signature)
        result = payload.get("result")
        if result is None:
            return []
        if not isinstance(result, dict):
            raise AnchorFailed("Solana RPC getTransaction returned an invalid result")

        transaction = result.get("transaction", {})
        if not isinstance(transaction, dict):
            return []
        message = transaction.get("message", {})
        if not isinstance(message, dict):
            return []
        instructions = message.get("instructions", [])
        if not isinstance(instructions, list):
            return []

        memos: list[str] = []
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            parsed = instruction.get("parsed")
            if instruction.get("program") == "spl-memo" and isinstance(parsed, str):
                memos.append(parsed)
            elif (
                instruction.get("programId") == self.MEMO_PROGRAM_ID
                and isinstance(parsed, str)
            ):
                memos.append(parsed)
        return memos

    def wait_for_memo_texts(
        self,
        signature: str,
        *,
        timeout_seconds: float = 30,
        interval_seconds: float = 2,
    ) -> list[str]:
        """Poll getTransaction until Memo text is available or timeout expires."""
        deadline = time.monotonic() + timeout_seconds
        last_memos: list[str] = []

        while time.monotonic() < deadline:
            last_memos = self.get_memo_texts(signature)
            if last_memos:
                return last_memos
            time.sleep(interval_seconds)

        return last_memos

    def transfer_sol(
        self,
        to_pubkey: str,
        sender_secret_key: list[int],
        amount_sol: decimal.Decimal,
    ) -> str:
        """발신 개인키로 수신 지갑에 네이티브 SOL을 전송한다.

        ``sender_secret_key``는 Solana CLI가 내보낸 64개 정수의 바이트 배열이어야
        하며, 트랜잭션 서명 과정에서만 사용하고 서비스 인스턴스에 저장하지
        않는다.
        """
        try:
            lamports = self._validate_transfer_request(
                to_pubkey, sender_secret_key, amount_sol
            )
            blockhash = self._request_latest_blockhash()
            transaction = self._build_signed_transaction(
                to_pubkey, sender_secret_key, lamports, blockhash
            )
            return self._submit_transaction(transaction)
        except CertificateIssueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CertificateIssueError(f"transfer_sol failed: {exc}") from exc

    def _validate_transfer_request(
        self,
        to_pubkey: str,
        sender_secret_key: list[int],
        amount_sol: decimal.Decimal,
    ) -> int:
        """전송 요청을 검증하고 lamports 단위의 전송 금액을 계산한다."""
        if not self.rpc_url:
            raise CertificateIssueError("transfer_sol requires an RPC URL")
        if not to_pubkey:
            raise CertificateIssueError("transfer_sol requires a recipient public key")
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
        return int(lamports)

    def _resolve_sender_secret_key(
        self,
        sender_secret_key: list[int] | None,
        operation: str,
    ) -> list[int]:
        """Use the explicit secret key first, then the instance-level key."""
        secret_key = sender_secret_key if sender_secret_key is not None else self.sender_secret_key
        if secret_key is None:
            raise CertificateIssueError(f"{operation} requires a sender secret key")
        self._validate_secret_key(secret_key)
        return secret_key

    def _validate_secret_key(self, sender_secret_key: list[int]) -> None:
        """Validate a Solana CLI 64-byte secret-key array."""
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

    def _validate_memo(self, memo: str) -> bytes:
        """Validate Memo text before a transaction is built."""
        if not isinstance(memo, str) or not memo.strip():
            raise CertificateIssueError("memo must be a non-empty string")
        memo_bytes = memo.encode("utf-8")
        if len(memo_bytes) > self.MAX_MEMO_BYTES:
            raise CertificateIssueError(
                f"memo must be {self.MAX_MEMO_BYTES} bytes or fewer"
            )
        return memo_bytes

    def _request_latest_blockhash(self) -> str:
        """confirmed 상태의 최신 블록해시를 RPC에서 조회한다."""
        payload = self._post_rpc(
            "getLatestBlockhash", [{"commitment": "confirmed"}]
        )
        blockhash = payload.get("result", {}).get("value", {}).get("blockhash")
        if not isinstance(blockhash, str):
            raise CertificateIssueError("latest Solana blockhash is unavailable")
        return blockhash

    def _build_signed_transaction(
        self,
        to_pubkey: str,
        sender_secret_key: list[int],
        lamports: int,
        blockhash: str,
    ) -> Any:
        """수신자, 금액, 블록해시를 사용해 서명된 SOL 전송을 만든다."""
        try:
            from solders.hash import Hash
            from solders.keypair import Keypair
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.system_program import TransferParams, transfer
            from solders.transaction import Transaction
        except ImportError as exc:
            raise CertificateIssueError("solders is required for SOL transfer") from exc

        keypair = Keypair.from_bytes(bytes(sender_secret_key))
        sender = keypair.pubkey()
        instruction = transfer(
            TransferParams(
                from_pubkey=sender,
                to_pubkey=Pubkey.from_string(to_pubkey),
                lamports=lamports,
            )
        )
        return Transaction([keypair], Message([instruction], sender), Hash.from_string(blockhash))

    def _build_signed_memo_transaction(
        self,
        memo_bytes: bytes,
        sender_secret_key: list[int],
        blockhash: str,
    ) -> Any:
        """Build a signed Memo transaction paid by the sender keypair."""
        try:
            from solders.hash import Hash
            from solders.instruction import Instruction
            from solders.keypair import Keypair
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.transaction import Transaction
        except ImportError as exc:
            raise CertificateIssueError("solders is required for Memo transactions") from exc

        keypair = Keypair.from_bytes(bytes(sender_secret_key))
        sender = keypair.pubkey()
        instruction = Instruction(
            Pubkey.from_string(self.MEMO_PROGRAM_ID),
            memo_bytes,
            [],
        )
        return Transaction([keypair], Message([instruction], sender), Hash.from_string(blockhash))

    def _submit_transaction(self, transaction: Any) -> str:
        """서명된 트랜잭션을 RPC에 제출하고 트랜잭션 서명을 반환한다."""
        payload = self._post_rpc(
            "sendTransaction",
            [
                base64.b64encode(bytes(transaction)).decode("ascii"),
                {"encoding": "base64", "preflightCommitment": "confirmed"},
            ],
        )
        signature = payload.get("result")
        if not isinstance(signature, str):
            raise CertificateIssueError("Solana RPC did not return a transaction signature")
        return signature

    def _post_rpc(self, method: str, params: list[Any]) -> dict[str, Any]:
        """JSON-RPC 요청을 전송하고 RPC 오류를 서비스 오류로 변환한다."""
        try:
            import httpx
        except ImportError as exc:
            raise CertificateIssueError("httpx is required for SOL transfer") from exc

        response = httpx.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise CertificateIssueError(f"Solana RPC {method} returned an invalid response")
        if payload.get("error"):
            raise CertificateIssueError(f"Solana RPC {method} failed: {payload['error']}")
        return payload
