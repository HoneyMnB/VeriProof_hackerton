"""Manually send native SOL on Solana devnet.

How to run from the project root:

1. Edit ``RECIPIENT_PUBKEY`` below.
2. Edit ``SENDER_SECRET_KEY`` below to a funded devnet secret-key byte list
   such as the 64-number array in Solana CLI id.json.
3. Run:

       python veriproof/tests/solana/transfer_sol_devnet.py

This script takes no CLI args by design. It submits a real devnet transaction.
"""
from __future__ import annotations

import decimal
import sys
from pathlib import Path


RPC_URL = "https://api.devnet.solana.com"
SENDER_SECRET_KEY = [85,124,63,244,71,79,22,223,161,116,144,217,27,244,168,183,42,102,14,236,190,31,60,233,253,56,110,123,68,228,80,93,175,245,208,15,177,139,2,153,107,31,179,140,0,253,157,183,118,163,51,35,21,231,182,234,70,247,210,13,215,67,219,133]
RECIPIENT_PUBKEY = "ESCDLDSaqAkeGDgHxoafZCd7SYDNG2ZkUD3ueDpWgNTy"
AMOUNT_SOL = decimal.Decimal("0.001")


APP_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_DIR))


class _ListSecretSigner:
    """Local signer backed by a Solana CLI-style secret-key byte list."""

    def __init__(self, secret_key: list[int]) -> None:
        self._secret_key = secret_key
        self.keypair = self._load_keypair()


    def public_key(self) -> str:
        return str(self.keypair.pubkey())

    def _keypair_local(self):
        return self.keypair

    def _load_keypair(self):
        if len(self._secret_key) != 64:
            raise SystemExit("SENDER_SECRET_KEY must contain exactly 64 integers.")
        if any(not isinstance(item, int) or item < 0 or item > 255 for item in self._secret_key):
            raise SystemExit("SENDER_SECRET_KEY values must be integers in 0..255.")
        try:
            from solders.keypair import Keypair
        except ImportError as exc:
            raise SystemExit(f"solders is required to load SENDER_SECRET_KEY: {exc}") from exc
        bytes_secret_key = bytes(self._secret_key)
        keypair = Keypair.from_bytes(bytes_secret_key)
        print(f"Keypair: {keypair.pubkey()}")
        return keypair


def _require_solana_sdk() -> None:
    """Fail before building a transaction when the runtime SDK is incomplete."""
    try:
        import httpx  # noqa: F401
        from solders.keypair import Keypair  # noqa: F401
        from solders.message import Message  # noqa: F401
        from solders.pubkey import Pubkey  # noqa: F401
        from solders.system_program import TransferParams, transfer  # noqa: F401
        from solders.transaction import Transaction  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Install the Solana runtime dependencies in this Python environment: "
            "python -m pip install -r veriproof/requirements.txt. "
            f"Missing import: {exc}"
        ) from exc


def main() -> None:
    if RECIPIENT_PUBKEY == "REPLACE_WITH_DEVNET_RECIPIENT_PUBLIC_KEY":
        raise SystemExit("Set RECIPIENT_PUBKEY in this file before running.")
    if not SENDER_SECRET_KEY:
        raise SystemExit("Set SENDER_SECRET_KEY in this file before running.")
    if AMOUNT_SOL <= 0:
        raise SystemExit("AMOUNT_SOL must be positive.")

    _require_solana_sdk()

    from services.solana_service import SolanaService

    signer = _ListSecretSigner(SENDER_SECRET_KEY)
    service = SolanaService(rpc_url=RPC_URL, signer=signer)

    sender = signer.public_key()

    # print(f"RPC: {RPC_URL}")
    # print(f"From: {sender}")
    # print(f"To:   {RECIPIENT_PUBKEY}")
    # print(f"SOL:  {AMOUNT_SOL}")

    signature = service.transfer_sol(RECIPIENT_PUBKEY, AMOUNT_SOL)
    # print(f"Signature: {signature}")
    # print(f"Explorer: https://explorer.solana.com/tx/{signature}?cluster=devnet")


if __name__ == "__main__":
    main()
