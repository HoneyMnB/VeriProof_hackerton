"""Factory for legacy non-transfer Solana workflows."""
from __future__ import annotations

from typing import Any


def get_solana_service() -> Any:
    """Return the local workflow adapter; real workflows are no longer provided."""
    from django.conf import settings

    adapter = getattr(settings, "SOLANA_ADAPTER", "mock").strip().lower()
    if adapter == "mock":
        from .mock_solana_service import LocalMockSolanaService

        return LocalMockSolanaService()
    raise RuntimeError(
        "Non-transfer Solana workflows were removed; use SolanaService.transfer_sol"
    )
