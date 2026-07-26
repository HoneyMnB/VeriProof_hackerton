"""Write plain text to Solana devnet with the Memo program.

How to run from the repository root:

1. Fund a devnet payer wallet.
2. Fill in ``SENDER_SECRET_KEY`` with the 64-number array from Solana CLI.
3. Fill in ``MEMO_TEXT`` with the text you want to write.
4. Run:

       python veriproof/tests/solana/anchor_hash_devnet.py

This script submits a real devnet transaction. Keep private keys out of git.
"""
from __future__ import annotations

import sys
from pathlib import Path


RPC_URL = "https://api.devnet.solana.com"
SENDER_SECRET_KEY: list[int] = [85,124,63,244,71,79,22,223,161,116,144,217,27,244,168,183,42,102,14,236,190,31,60,233,253,56,110,123,68,228,80,93,175,245,208,15,177,139,2,153,107,31,179,140,0,253,157,183,118,163,51,35,21,231,182,234,70,247,210,13,215,67,219,133]
MEMO_TEXT = "veriproof:test:hello-devnet"
LOOKUP_TIMEOUT_SECONDS = 30
LOOKUP_INTERVAL_SECONDS = 2

APP_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_DIR))


def _require_runtime_dependencies() -> None:
    """Fail clearly before submitting when Solana dependencies are missing."""
    try:
        import httpx  # noqa: F401
        from solders.instruction import Instruction  # noqa: F401
        from solders.keypair import Keypair  # noqa: F401
        from solders.message import Message  # noqa: F401
        from solders.pubkey import Pubkey  # noqa: F401
        from solders.transaction import Transaction  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Install runtime dependencies first: "
            "python -m pip install -r veriproof/requirements.txt. "
            f"Missing import: {exc}"
        ) from exc


def _validate_script_settings() -> None:
    """Validate the constants at the top of this file before RPC submission."""
    if not RPC_URL:
        raise SystemExit("Set RPC_URL.")
    if len(SENDER_SECRET_KEY) != 64:
        raise SystemExit("Set SENDER_SECRET_KEY to exactly 64 integers.")
    if any(not isinstance(item, int) or item < 0 or item > 255 for item in SENDER_SECRET_KEY):
        raise SystemExit("SENDER_SECRET_KEY values must be integers in 0..255.")
    if not MEMO_TEXT.strip():
        raise SystemExit("Set MEMO_TEXT.")


def main() -> None:
    _validate_script_settings()
    _require_runtime_dependencies()

    from solders.keypair import Keypair

    from services.solana_service import SolanaService

    payer_wallet = str(Keypair.from_bytes(bytes(SENDER_SECRET_KEY)).pubkey())

    service = SolanaService(rpc_url=RPC_URL, sender_secret_key=SENDER_SECRET_KEY)
    signature = service.submit_memo(MEMO_TEXT)
    memo_texts = service.wait_for_memo_texts(
        signature,
        timeout_seconds=LOOKUP_TIMEOUT_SECONDS,
        interval_seconds=LOOKUP_INTERVAL_SECONDS,
    )

    print(f"RPC: {RPC_URL}")
    print(f"Payer: {payer_wallet}")
    print(f"Text: {MEMO_TEXT}")
    print(f"Signature: {signature}")
    print(f"Explorer: https://explorer.solana.com/tx/{signature}?cluster=devnet")
    if memo_texts:
        print("Memo:")
        for memo in memo_texts:
            print(f"  {memo}")
    else:
        print(
            "Memo lookup timed out. The transaction was submitted, but the RPC "
            "node did not return parsed memo data yet."
        )


if __name__ == "__main__":
    main()
