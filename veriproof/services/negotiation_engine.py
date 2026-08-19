"""NegotiationEngine — orchestrates one autonomous negotiation round.

협상은 Gemini의 구조화 응답을 사용하되, 모델이 없거나 실패한 경우 가격·수락을
추정하지 않는다. 금액 하한과 지갑 주소 검증은 응답 후 이 모듈이 강제한다.
"""
from __future__ import annotations

import decimal
import logging
from typing import Any

from ._payment import resolve_pay_to
from ._types import NegotiationResult, quantize_sol, quantize_usdc

logger = logging.getLogger(__name__)

# SPEC-003 R9: default COUNTER round cap when settings.MAX_NEGOTIATION_ROUNDS
# is unset (matches architecture §1.4 default of 5).
_DEFAULT_MAX_ROUNDS = 5


class NegotiationUnavailableError(RuntimeError):
    """실제 협상 모델을 호출할 수 없을 때 발생한다."""


class NegotiationEngine:
    """Runs a negotiation round for an asset + session.

    Gemini 의존성은 주입 가능하며, 실제 경로에서는 모델 오류를 명시적으로
    호출자에게 전달한다.
    """

    def __init__(
        self,
        gemini: Any = None,
        max_rounds: int | None = None,
    ) -> None:
        self.gemini = gemini
        self.max_rounds = max_rounds

    # --- Architecture 4 method (SPEC-003) -----------------------------------
    def run_round(
        self,
        asset: Any,
        session: Any,
        offer_sol: decimal.Decimal,
        usage_type: str,
        currency: str = "SOL",
    ) -> NegotiationResult:
        """Execute one negotiation round.

        SPEC-003 R1..R10. Returns ACCEPT / COUNTER_OFFER / REJECT with a
        resolved ``pay_address`` on ACCEPT. See module docstring for the
        layered design.
        """
        max_rounds = self._resolve_max_rounds()
        rounds = list(getattr(session, "rounds", None) or [])
        min_price = asset.min_amount
        target_price = asset.target_amount
        if min_price is None or target_price is None:
            raise ValueError("native SOL negotiation prices are not configured")
        offer_meets_min = offer_sol >= min_price

        # 공개 원가를 제시한 구매자는 모델의 변동적 판단과 무관하게 구매할 수
        # 있어야 한다. 목록 가격은 협상의 최종 안전망이며, 초과 제안도 목록
        # 가격으로만 수락해 구매자에게 더 높은 가격을 청구하지 않는다.
        if offer_sol >= target_price:
            price = quantize_usdc(target_price) if currency == "USDC" else quantize_sol(target_price)
            return NegotiationResult(
                status="ACCEPT",
                price_sol=price,
                reason="공개 원가 제안을 수락합니다.",
                pay_address=resolve_pay_to(asset),
            )

        # R9 / AC-6: a below-min offer past the round cap REJECTs. A late offer
        # that meets min still ACCEPTs (creator-friendly; money is money).
        if not offer_meets_min and len(rounds) >= max_rounds:
            return NegotiationResult(
                status="REJECT",
                price_sol=None,
                reason="max rounds exceeded",
                pay_address=None,
            )

        if self.gemini is None:
            raise NegotiationUnavailableError("Gemini negotiation service is unavailable")
        try:
            raw = self.gemini.negotiate(
                min_price,
                target_price,
                offer_sol,
                usage_type,
                rounds,
                currency=currency,
            )
        except Exception as exc:  # noqa: BLE001 - external model adapter
            logger.error(
                "negotiation model failed asset_id=%s error=%s",
                getattr(asset, "id", None),
                exc,
            )
            raise NegotiationUnavailableError("Gemini negotiation could not be completed") from exc

        return self._finalize(raw, asset, currency=currency)

    # --- Internal helpers ----------------------------------------------------

    def _resolve_max_rounds(self) -> int:
        """Resolve the round cap: explicit ctor arg, else settings, else 5."""
        if self.max_rounds is not None:
            return self.max_rounds
        try:
            from django.conf import settings

            return int(getattr(settings, "MAX_NEGOTIATION_ROUNDS", _DEFAULT_MAX_ROUNDS))
        except Exception:  # noqa: BLE001 (settings unavailable in pure-unit ctx)
            return _DEFAULT_MAX_ROUNDS

    def _finalize(
        self,
        raw: NegotiationResult,
        asset: Any,
        *,
        currency: str,
    ) -> NegotiationResult:
        """Apply the creator-protection invariants shared by both paths.

        - R10: ACCEPT/COUNTER prices are clamped UP to ``min_price`` so a buggy
          model answer can never undercut the creator's floor.
        - pay_address is resolved via the shared ``resolve_pay_to`` SSOT on
          ACCEPT (§8); None for COUNTER/REJECT.
        - Native SOL prices are rounded to 9 decimals (lamport precision).
        """
        status = raw.status
        price = raw.price_sol
        reason = raw.reason
        min_price = asset.min_amount

        if status == "ACCEPT":
            if price is None or price < min_price:
                price = min_price
            pay_address = resolve_pay_to(asset)
        elif status == "COUNTER_OFFER":
            if price is None or price < min_price:
                price = min_price
            pay_address = None
        else:
            # REJECT (or any unknown status): no price, no recipient.
            pay_address = None

        if price is not None:
            price = quantize_usdc(price) if currency == "USDC" else quantize_sol(price)

        return NegotiationResult(
            status=status, price_sol=price, reason=reason, pay_address=pay_address
        )


def get_negotiation_engine() -> NegotiationEngine:
    """Factory: build a NegotiationEngine from current Django settings."""
    from django.conf import settings

    from .gemini_service import get_gemini_service

    return NegotiationEngine(
        gemini=get_gemini_service(),
        max_rounds=int(
            getattr(settings, "MAX_NEGOTIATION_ROUNDS", _DEFAULT_MAX_ROUNDS)
        ),
    )
