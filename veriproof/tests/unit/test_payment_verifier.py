"""Runtime payment verification is real-only."""
from __future__ import annotations


def test_payment_verifier_factory_returns_real_solana_service(settings):
    from services.payment_verifier import get_payment_verifier
    from services.solana_service import SolanaService

    settings.SOLANA_RPC_URL = "https://api.devnet.solana.com"
    settings.PLATFORM_ESCROW_SECRET_KEY = ""

    verifier = get_payment_verifier()

    assert isinstance(verifier, SolanaService)
