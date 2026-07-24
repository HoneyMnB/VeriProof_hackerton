"""Shared result value-objects for the service layer (architecture 4).

These plain dataclasses make the service INTERFACES self-documenting and give
fakes/tests concrete, immutable types to assert against. They carry no
behaviour — only the data returned by a service method.
"""
from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnalysisResult:
    """GeminiService.analyze_image result.

    Architecture 4: ``analyze_image(image_bytes) -> AnalysisResult(
        tags, category, originality_score, recommended_min_price_usdc)``.

    SPEC-001 R13 extension: ``degraded`` is True when the model call failed
    and rule-based defaults (tags=[], score=50) were substituted. The field
    carries a default so existing positional/kwargs construction still works.
    """

    tags: list[str]
    # SPEC-001 R13: category is None in the degraded fallback.
    category: str | None
    originality_score: int | None
    recommended_min_price_usdc: decimal.Decimal
    # SPEC-001 R13: True when rule-based defaults were used (Gemini failed).
    degraded: bool = False
    # 등록 시점 멀티모달 분석이 생성한 자산 설명(검색·발견용). 기본값을 두어
    # 기존 positional/kwargs 생성과 호환된다.
    description: str | None = None


@dataclass(frozen=True)
class NegotiationResult:
    """Negotiation outcome.

    Architecture 4: ``status in {ACCEPT, COUNTER_OFFER, REJECT}``,
    a counter/final price, and a human-readable reason. Returned by both
    ``GeminiService.negotiate`` and ``NegotiationEngine.run_round``.

    SPEC-003 extensions:
    - ``price_usdc`` is ``None`` on REJECT (no price is agreed).
    - ``pay_address`` is the resolved recipient on ACCEPT (creator wallet or
      platform escrow per architecture §8) and ``None`` for COUNTER/REJECT.
      The view layer adds ``session_id`` when projecting to the wire contract.
    """

    status: str
    price_usdc: decimal.Decimal | None
    reason: str
    pay_address: str | None = None


# USDC on-chain has 6 decimal places; all money math rounds here so the
# service layer never emits unquantised Decimals (architecture §8).
_USDC_QUANTUM = decimal.Decimal("0.000001")


def quantize_usdc(value: decimal.Decimal) -> decimal.Decimal:
    """Round a Decimal to USDC precision (6 decimals, half-up).

    Single source of truth for USDC rounding across the negotiation services so
    GeminiService.negotiate and NegotiationEngine.run_round cannot drift.
    """
    return value.quantize(_USDC_QUANTUM, rounding=decimal.ROUND_HALF_UP)


@dataclass(frozen=True)
class BatchQuote:
    """GeminiService.quote_batch per-item result.

    Architecture 4: ``BatchQuote(asset_id, unit_price_usdc)``.
    """

    asset_id: Any
    unit_price_usdc: decimal.Decimal


@dataclass(frozen=True)
class PaymentVerification:
    """SolanaService.verify_usdc_payment result.

    Architecture 4: ``PaymentVerification(is_valid, amount, sender, slot)``.
    ``amount`` is in USDC major units; ``slot`` is the confirmation slot.

    SPEC-004 extension: ``commitment`` carries the on-chain commitment level
    actually observed for the tx (``processed`` / ``confirmed`` / ``finalized``)
    so callers and observers can distinguish "unconfirmed" from "mismatch"
    rejections. Defaults to ``None`` so existing construction stays compatible.
    """

    is_valid: bool
    amount: decimal.Decimal
    sender: str
    slot: int
    commitment: str | None = None


@dataclass(frozen=True)
class SubmittedPayment:
    """X402Service.parse_payment_submitted result.

    Parsed from the a2a-x402 ``payment-submitted`` body. Fields mirror the
    settle request contract (architecture 6.3).
    """

    tx_signature: str
    buyer_wallet: str
    amount_usdc: decimal.Decimal
    extra: dict[str, Any] = field(default_factory=dict)
