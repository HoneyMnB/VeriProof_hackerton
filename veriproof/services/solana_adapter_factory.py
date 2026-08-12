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

    from .kms_signer import get_kms_signer
    from .solana_service import SolanaService

    kms_key_name = getattr(settings, "KMS_KEY_NAME", "").strip()
    return SolanaService(
        rpc_url=getattr(settings, "SOLANA_RPC_URL", ""),
        sender_secret_key=_parse_secret_key(
            getattr(settings, "PLATFORM_ESCROW_SECRET_KEY", "")
        ),
        signer=get_kms_signer() if kms_key_name else None,
    )


def _parse_secret_key(raw: str | list[int] | tuple[int, ...] | None) -> list[int] | None:
    """Parse a Solana CLI array or Base58 keypair from settings."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (list, tuple)):
        return [int(item) for item in raw]
    text = str(raw).strip()
    if not text:
        return None
    if not text.startswith(("[", "(")):
        return _decode_base58_keypair(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            raise ValueError(
                "PLATFORM_ESCROW_SECRET_KEY must be a 64-integer array or Base58 keypair"
            ) from None
    if not isinstance(parsed, list):
        raise ValueError("PLATFORM_ESCROW_SECRET_KEY must be a 64-integer array or Base58 keypair")
    return [int(item) for item in parsed]


def _decode_base58_keypair(value: str) -> list[int]:
    """Decode a Base58-encoded Solana keypair into the signer byte array."""
    try:
        from solders.keypair import Keypair

        return list(bytes(Keypair.from_base58_string(value)))
    except (ImportError, ValueError) as exc:
        raise ValueError(
            "PLATFORM_ESCROW_SECRET_KEY must be a valid 64-byte Solana Base58 keypair"
        ) from exc
