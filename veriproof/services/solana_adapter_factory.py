"""Factory for real Solana workflows."""
from __future__ import annotations

import ast
import json
from typing import Any


def get_solana_service() -> Any:
    """Return the settings-backed real Solana service.

    There is no runtime mock branch here. Tests that need deterministic Solana
    behavior inject ``tests.fakes.FakeSolanaService`` directly at the service
    boundary.
    """
    from django.conf import settings

    from .solana_service import SolanaService

    return SolanaService(
        rpc_url=getattr(settings, "SOLANA_RPC_URL", ""),
        sender_secret_key=_parse_secret_key(
            getattr(settings, "PLATFORM_ESCROW_SECRET_KEY", "")
        ),
    )


def _parse_secret_key(raw: str | list[int] | tuple[int, ...] | None) -> list[int] | None:
    """Parse a Solana CLI 64-byte secret-key array from settings."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (list, tuple)):
        return [int(item) for item in raw]
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(text)
    if not isinstance(parsed, list):
        raise ValueError("PLATFORM_ESCROW_SECRET_KEY must be a JSON array of 64 integers")
    return [int(item) for item in parsed]
