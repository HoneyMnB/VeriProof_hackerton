"""Unit tests for the native SOL transfer service."""
from __future__ import annotations

import decimal

import pytest


class _RpcResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def test_transfer_sol_uses_json_rpc_without_solana_py(monkeypatch):
    import httpx
    from solders.hash import Hash
    from solders.keypair import Keypair

    from services.solana_service import SolanaService

    requests: list[dict[str, object]] = []
    blockhash = str(Hash.default())

    def fake_post(url: str, *, json: dict[str, object], timeout: int) -> _RpcResponse:
        requests.append(json)
        if json["method"] == "getLatestBlockhash":
            return _RpcResponse({"result": {"value": {"blockhash": blockhash}}})
        return _RpcResponse({"result": "solana_signature"})

    monkeypatch.setattr(httpx, "post", fake_post)
    keypair = Keypair()
    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    signature = service.transfer_sol(
        "ESCDLDSaqAkeGDgHxoafZCd7SYDNG2ZkUD3ueDpWgNTy",
        list(bytes(keypair)),
        decimal.Decimal("0.001"),
    )

    assert signature == "solana_signature"
    assert [request["method"] for request in requests] == [
        "getLatestBlockhash",
        "sendTransaction",
    ]


def test_transfer_sol_rejects_a_non_cli_secret_key_before_rpc_access():
    from services.solana_service import CertificateIssueError, SolanaService

    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    with pytest.raises(CertificateIssueError, match="exactly 64"):
        service.transfer_sol(
            "ESCDLDSaqAkeGDgHxoafZCd7SYDNG2ZkUD3ueDpWgNTy",
            [0],
            decimal.Decimal("0.001"),
        )


def test_transfer_sol_rejects_non_positive_amount_before_rpc_access():
    from services.solana_service import CertificateIssueError, SolanaService

    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    with pytest.raises(CertificateIssueError, match="must be positive"):
        service.transfer_sol(
            "ESCDLDSaqAkeGDgHxoafZCd7SYDNG2ZkUD3ueDpWgNTy",
            [0] * 64,
            decimal.Decimal("0"),
        )


def test_anchor_hash_submits_memo_transaction(monkeypatch):
    import httpx
    from solders.hash import Hash
    from solders.keypair import Keypair

    from services.solana_service import SolanaService

    requests: list[dict[str, object]] = []
    blockhash = str(Hash.default())

    def fake_post(url: str, *, json: dict[str, object], timeout: int) -> _RpcResponse:
        requests.append(json)
        if json["method"] == "getLatestBlockhash":
            return _RpcResponse({"result": {"value": {"blockhash": blockhash}}})
        return _RpcResponse({"result": "memo_signature"})

    monkeypatch.setattr(httpx, "post", fake_post)
    service = SolanaService(
        rpc_url="https://api.devnet.solana.com",
        sender_secret_key=list(bytes(Keypair())),
    )

    signature = service.anchor_hash("a" * 64, "CreatorWallet")

    assert signature == "memo_signature"
    assert [request["method"] for request in requests] == [
        "getLatestBlockhash",
        "sendTransaction",
    ]


def test_anchor_hash_rejects_invalid_hash_before_rpc_access():
    from services.solana_service import AnchorFailed, SolanaService

    service = SolanaService(
        rpc_url="https://api.devnet.solana.com",
        sender_secret_key=[0] * 64,
    )

    with pytest.raises(AnchorFailed, match="64-character hex"):
        service.anchor_hash("not-a-sha256", "CreatorWallet")


def test_get_memo_texts_reads_json_parsed_memo(monkeypatch):
    import httpx

    from services.solana_service import SolanaService

    def fake_post(url: str, *, json: dict[str, object], timeout: int) -> _RpcResponse:
        assert json["method"] == "getTransaction"
        return _RpcResponse(
            {
                "result": {
                    "transaction": {
                        "message": {
                            "instructions": [
                                {
                                    "program": "spl-memo",
                                    "programId": SolanaService.MEMO_PROGRAM_ID,
                                    "parsed": "veriproof:anchor:abc",
                                }
                            ]
                        }
                    }
                }
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    assert service.get_memo_texts("memo_signature") == ["veriproof:anchor:abc"]
