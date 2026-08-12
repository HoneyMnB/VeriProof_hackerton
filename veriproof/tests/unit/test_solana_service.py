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


def test_submit_memo_uses_remote_ed25519_signer_without_secret_key(monkeypatch):
    import httpx
    from solders.hash import Hash
    from solders.keypair import Keypair

    from services.solana_service import SolanaService

    keypair = Keypair()

    class RemoteSigner:
        def public_key(self):
            return str(keypair.pubkey())

        def sign(self, message):
            return bytes(keypair.sign_message(message))

    requests = []

    def fake_post(url, *, json, timeout):
        requests.append(json)
        if json["method"] == "getLatestBlockhash":
            return _RpcResponse({"result": {"value": {"blockhash": str(Hash.default())}}})
        return _RpcResponse({"result": "kms_memo_signature"})

    monkeypatch.setattr(httpx, "post", fake_post)
    service = SolanaService(
        rpc_url="https://api.devnet.solana.com",
        signer=RemoteSigner(),
    )

    assert service.submit_memo("veriproof:kms:test") == "kms_memo_signature"
    assert [request["method"] for request in requests] == [
        "getLatestBlockhash",
        "sendTransaction",
    ]


def test_registration_certificate_submits_sha256_memo_without_original_data(monkeypatch):
    from services.solana_service import SolanaService

    service = SolanaService(
        rpc_url="https://api.devnet.solana.com",
        sender_secret_key=[1] * 64,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(service, "_request_latest_blockhash", lambda: "blockhash")

    def fake_build(memo_bytes, sender_secret_key, blockhash):
        captured["memo"] = memo_bytes.decode("utf-8")
        captured["secret_key"] = sender_secret_key
        captured["blockhash"] = blockhash
        return b"signed-memo-transaction"

    monkeypatch.setattr(service, "_build_signed_memo_transaction", fake_build)
    monkeypatch.setattr(service, "_submit_transaction", lambda transaction: "memo_sig")

    signature = service.issue_registration_certificate(
        "asset-1",
        "CreatorWallet111111111111111111111111111111",
        "a" * 64,
    )

    assert signature == "memo_sig"
    assert captured["memo"] == (
        "veriproof:registration:"
        "asset-1:CreatorWallet111111111111111111111111111111:"
        + ("a" * 64)
    )
    assert "http" not in captured["memo"]
    assert "original" not in captured["memo"]


def test_license_certificate_submits_sanitized_memo(monkeypatch):
    from services.solana_service import SolanaService

    service = SolanaService(
        rpc_url="https://api.devnet.solana.com",
        sender_secret_key=[1] * 64,
    )
    captured: dict[str, str] = {}

    monkeypatch.setattr(service, "_request_latest_blockhash", lambda: "blockhash")
    def fake_build(memo_bytes, sender_secret_key, blockhash):
        captured["memo"] = memo_bytes.decode("utf-8")
        return b"signed"

    monkeypatch.setattr(service, "_build_signed_memo_transaction", fake_build)
    monkeypatch.setattr(service, "_submit_transaction", lambda transaction: "cert_sig")

    signature = service.issue_certificate(
        "asset-1",
        "BuyerWallet11111111111111111111111111111111",
        "veriproof:cert:asset-1:https://private.example/original.png",
    )

    assert signature == "cert_sig"
    assert captured["memo"].startswith(
        "veriproof:license:asset-1:BuyerWallet11111111111111111111111111111111:"
    )
    assert "https://private.example" not in captured["memo"]
    assert "original.png" not in captured["memo"]


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


def test_verify_sol_payment_by_reference_checks_transfer_and_memo(monkeypatch):
    import httpx

    from services.solana_service import SolanaService

    requests: list[dict[str, object]] = []

    def fake_post(url: str, *, json: dict[str, object], timeout: int) -> _RpcResponse:
        requests.append(json)
        if json["method"] == "getSignaturesForAddress":
            return _RpcResponse({"result": [{"signature": "paid_sig", "err": None}]})
        assert json["method"] == "getTransaction"
        return _RpcResponse(
            {
                "result": {
                    "slot": 123,
                    "transaction": {
                        "message": {
                            "instructions": [
                                {
                                    "program": "system",
                                    "parsed": {
                                        "type": "transfer",
                                        "info": {
                                            "source": "Buyer11111111111111111111111111111111111",
                                            "destination": "Recipient1111111111111111111111111111111",
                                            "lamports": 1_250_000_000,
                                        },
                                    },
                                },
                                {
                                    "program": "spl-memo",
                                    "programId": SolanaService.MEMO_PROGRAM_ID,
                                    "parsed": "VERIPROOF:asset-1",
                                },
                            ]
                        }
                    },
                }
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    verification = service.verify_sol_payment_by_reference(
        reference="Reference11111111111111111111111111111111",
        expected_recipient="Recipient1111111111111111111111111111111",
        expected_amount=decimal.Decimal("1.25"),
        expected_memo="VERIPROOF:asset-1",
    )

    assert verification.is_valid is True
    assert verification.tx_signature == "paid_sig"
    assert verification.sender == "Buyer11111111111111111111111111111111111"
    assert verification.amount == decimal.Decimal("1.25")
    assert [request["method"] for request in requests] == [
        "getSignaturesForAddress",
        "getTransaction",
    ]


def test_verify_sol_payment_by_reference_rejects_wrong_memo(monkeypatch):
    import httpx

    from services.solana_service import SolanaService

    def fake_post(url: str, *, json: dict[str, object], timeout: int) -> _RpcResponse:
        if json["method"] == "getSignaturesForAddress":
            return _RpcResponse({"result": [{"signature": "wrong_memo_sig", "err": None}]})
        return _RpcResponse(
            {
                "result": {
                    "slot": 123,
                    "transaction": {
                        "message": {
                            "instructions": [
                                {
                                    "program": "system",
                                    "parsed": {
                                        "type": "transfer",
                                        "info": {
                                            "source": "Buyer",
                                            "destination": "Recipient",
                                            "lamports": 1_000_000_000,
                                        },
                                    },
                                },
                                {"program": "spl-memo", "parsed": "VERIPROOF:other"},
                            ]
                        }
                    },
                }
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    verification = service.verify_sol_payment_by_reference(
        reference="Reference11111111111111111111111111111111",
        expected_recipient="Recipient",
        expected_amount=decimal.Decimal("1"),
        expected_memo="VERIPROOF:asset-1",
    )

    assert verification.is_valid is False


def test_verify_usdc_payment_checks_mint_recipient_and_min_units(monkeypatch):
    import httpx

    from services.solana_service import SolanaService

    def fake_post(url: str, *, json: dict[str, object], timeout: int) -> _RpcResponse:
        assert json["method"] == "getTransaction"
        return _RpcResponse(
            {
                "result": {
                    "slot": 456,
                    "meta": {
                        "err": None,
                        "preTokenBalances": [
                            {
                                "mint": "USDCMint11111111111111111111111111111111",
                                "owner": "Buyer11111111111111111111111111111111111",
                                "uiTokenAmount": {"amount": "3000000"},
                            },
                            {
                                "mint": "USDCMint11111111111111111111111111111111",
                                "owner": "Seller1111111111111111111111111111111111",
                                "uiTokenAmount": {"amount": "0"},
                            },
                        ],
                        "postTokenBalances": [
                            {
                                "mint": "USDCMint11111111111111111111111111111111",
                                "owner": "Buyer11111111111111111111111111111111111",
                                "uiTokenAmount": {"amount": "750000"},
                            },
                            {
                                "mint": "USDCMint11111111111111111111111111111111",
                                "owner": "Seller1111111111111111111111111111111111",
                                "uiTokenAmount": {"amount": "2250000"},
                            },
                        ],
                    },
                    "transaction": {"message": {"instructions": []}},
                }
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    service = SolanaService(rpc_url="https://api.devnet.solana.com")

    verification = service.verify_usdc_payment(
        "usdc_sig",
        expected_recipient="Seller1111111111111111111111111111111111",
        expected_amount=decimal.Decimal("2.25"),
        mint="USDCMint11111111111111111111111111111111",
    )

    assert verification.is_valid is True
    assert verification.sender == "Buyer11111111111111111111111111111111111"
    assert verification.amount == decimal.Decimal("2.25")
    assert verification.slot == 456
    assert verification.tx_signature == "usdc_sig"
