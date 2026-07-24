"""Payment Recipient Resolution — single shared rule (architecture §8).

This is the SINGLE SOURCE OF TRUTH for "who gets paid" across the entire
payment flow. The rule is applied identically to:

- the HTTP 402 response (``accepts.pay_to`` / ``X-Solana-Pay-Address``, SPEC-002)
- negotiation ACCEPT (``pay_address``, SPEC-003)
- settlement verification (``expected_recipient``, SPEC-004/008)

Rule:
    pay_to = PLATFORM_ESCROW_PUBKEY   IF asset.parent_asset is set
                                            (2nd-creation -> escrow so S3
                                             royalty distribution can split)
    pay_to = asset.creator.wallet_address   ELSE
                                             (standalone -> buyer pays creator
                                              directly, P2P)

Keeping this in one place prevents the three downstream SPECs from drifting on
recipient semantics. Callers pass ``escrow_pubkey`` explicitly when they have
already resolved it from settings (e.g. X402Service stores it at construction);
passing ``None`` defers to ``settings.PLATFORM_ESCROW_PUBKEY`` lazily so the
function is also usable as a pure helper.
"""
from __future__ import annotations

from typing import Any


def resolve_pay_to(asset: Any, escrow_pubkey: str | None = None) -> str:
    """Return the payment recipient address for ``asset`` per architecture §8.

    A pure function of ``asset`` (plus the resolved escrow pubkey). Uses
    ``parent_asset_id`` (the FK column) rather than ``parent_asset`` to avoid a
    database lookup when the parent is not already loaded.

    Args:
        asset: anything exposing ``parent_asset_id`` (or ``parent_asset``) and
            ``creator.wallet_address``. A Django ``IpAsset`` satisfies this.
        escrow_pubkey: the platform escrow pubkey. If ``None`` it is read lazily
            from ``settings.PLATFORM_ESCROW_PUBKEY`` (defaulting to ``""``).

    Returns:
        The recipient Solana base58 pubkey.
    """
    parent_id = getattr(asset, "parent_asset_id", None)
    # Fall back to the related-object attr for non-Django stand-ins.
    if parent_id is None:
        parent_id = getattr(asset, "parent_asset", None)

    if parent_id is not None:
        if escrow_pubkey is None:
            from django.conf import settings

            escrow_pubkey = getattr(settings, "PLATFORM_ESCROW_PUBKEY", "")
        return escrow_pubkey

    return asset.creator.wallet_address
