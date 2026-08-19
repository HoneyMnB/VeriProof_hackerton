"""SPEC-003 unit tests ??NegotiationEngine.run_round (rule-based, no Gemini).

Covers the SPEC-003 짠5 unit TDD list for the engine:
- test_accept_when_offer_ge_min (R2 / AC-1)
- test_counter_between_min_and_target_when_offer_below_min (R3 / AC-2)
- test_counter_price_never_below_min (R10 invariant)
- test_reject_after_max_rounds (R9 / AC-6)

Plus one extra test (test_run_round_applies_invariants_to_gemini_result) that
exercises the Gemini-wired branch + the shared invariant finaliser so the
Gemini-success path is covered too.

The engine is pure over (asset, session, offer_sol); no DB, no network. Asset
    and session are SimpleNamespace stand-ins exposing exactly the attributes the
    engine reads (min_amount, target_amount, parent_asset_id, creator,
rounds).
"""
from __future__ import annotations

import decimal
from types import SimpleNamespace

import pytest

from services._types import NegotiationResult
from services.negotiation_engine import NegotiationEngine

_CREATOR_WALLET = "CreatorWallet1111111111111111111111111111"
_ESCROW = "EscrowWallet11111111111111111111111111111111"


def _asset(*, min_price, target_price, parent=False):
    """Build a stand-alone asset stand-in for the pure engine tests."""
    return SimpleNamespace(
        parent_asset_id=_ESCROW if parent else None,
        parent_asset=_ESCROW if parent else None,
        min_amount=decimal.Decimal(str(min_price)),
        target_amount=decimal.Decimal(str(target_price)),
        creator=SimpleNamespace(wallet_address=_CREATOR_WALLET),
    )


def _session(rounds=None):
    """Build a session stand-in whose ``rounds`` list is mutable."""
    return SimpleNamespace(rounds=list(rounds or []))


class _NegotiationDouble:
    """?뚯뒪???꾩슜 紐⑤뜽 ?붾툝: ?ㅽ뻾 肄붾뱶?먮뒗 媛寃?洹쒖튃???먯? ?딅뒗??"""

    def negotiate(self, min_price, target_price, offer_sol, usage_type, history, *, currency="SOL"):
        if offer_sol >= min_price:
            return NegotiationResult("ACCEPT", offer_sol, "test acceptance")
        return NegotiationResult(
            "COUNTER_OFFER",
            (min_price + max(min_price, target_price)) / decimal.Decimal("2"),
            "test counter",
        )


def _engine() -> NegotiationEngine:
    return NegotiationEngine(gemini=_NegotiationDouble(), max_rounds=5)


# === R2 / AC-1: ACCEPT when offer >= min ====================================


def test_accept_when_offer_ge_min():
    """offer == min -> ACCEPT at the offer price, pay_address = creator."""
    asset = _asset(min_price="1.5", target_price="3.0")
    session = _session()

    result = _engine().run_round(
        asset, session, decimal.Decimal("2.0"), "commercial"
    )

    assert result.status == "ACCEPT"
    assert result.price_sol == decimal.Decimal("2.0")
    assert result.pay_address == _CREATOR_WALLET


def test_accept_when_offer_equals_min_boundary():
    """offer == min (boundary) -> ACCEPT (the rule is >=)."""
    asset = _asset(min_price="1.5", target_price="3.0")
    session = _session()

    result = _engine().run_round(
        asset, session, decimal.Decimal("1.5"), "commercial"
    )

    assert result.status == "ACCEPT"
    assert result.price_sol == decimal.Decimal("1.5")


def test_list_price_is_accepted_without_a_model_decision():
    """The published price is the deterministic final fallback for a buyer."""
    class _RejectingModel:
        def negotiate(self, *args, **kwargs):
            raise AssertionError("the list-price fallback must not call Gemini")

    asset = _asset(min_price="0.5", target_price="1.0")
    result = NegotiationEngine(gemini=_RejectingModel()).run_round(
        asset, _session(rounds=[{"status": "REJECT"}] * 5),
        decimal.Decimal("1.0"),
        "commercial",
        currency="USDC",
    )

    assert result.status == "ACCEPT"
    assert result.price_sol == decimal.Decimal("1.000000")
    assert result.reason == "공개 원가 제안을 수락합니다."


# === R3 / AC-2: COUNTER_OFFER in [min, target] when offer below min =========


def test_counter_between_min_and_target_when_offer_below_min():
    """offer < min -> COUNTER_OFFER, counter in [min, target], pay_address None."""
    asset = _asset(min_price="1.5", target_price="3.0")
    session = _session()

    result = _engine().run_round(
        asset, session, decimal.Decimal("1.0"), "commercial"
    )

    assert result.status == "COUNTER_OFFER"
    assert result.pay_address is None
    # R3: counter must lie inside [min_price, target_price].
    assert asset.min_amount <= result.price_sol <= asset.target_amount


# === R10 invariant: counter never below min =================================


def test_counter_price_never_below_min():
    """Even with a degenerate target (< min), the counter stays >= min (R10)."""
    asset = _asset(min_price="1.5", target_price="1.0")  # target < min (bad data)
    session = _session()

    result = _engine().run_round(
        asset, session, decimal.Decimal("0.5"), "commercial"
    )

    assert result.status == "COUNTER_OFFER"
    assert result.price_sol >= asset.min_amount


# === R9 / AC-6: REJECT after max rounds =====================================


def test_reject_after_max_rounds():
    """len(rounds) >= MAX_ROUNDS with offer below min -> REJECT."""
    asset = _asset(min_price="1.5", target_price="3.0")
    # 5 prior COUNTER rounds == MAX_NEGOTIATION_ROUNDS default.
    session = _session(rounds=[{"status": "COUNTER_OFFER"}] * 5)

    result = _engine().run_round(
        asset, session, decimal.Decimal("1.0"), "commercial"
    )

    assert result.status == "REJECT"
    assert result.reason == "max rounds exceeded"
    assert result.pay_address is None
    assert result.price_sol is None


def test_accept_still_honoured_at_max_rounds():
    """A late offer that meets min still ACCEPTs at the round cap (creator-friendly)."""
    asset = _asset(min_price="1.5", target_price="3.0")
    session = _session(rounds=[{"status": "COUNTER_OFFER"}] * 5)

    result = _engine().run_round(
        asset, session, decimal.Decimal("1.8"), "commercial"
    )

    assert result.status == "ACCEPT"


# === Gemini-wired branch + shared invariants ================================


def test_run_round_applies_invariants_to_gemini_result(monkeypatch):
    """A wired Gemini result flows through the same invariant finaliser.

    The fake returns an ACCEPT below min (a bad model answer); the engine MUST
    clamp it up to min_price (R10) and still resolve pay_address from the asset.
    """
    from tests.fakes import FakeGeminiService

    asset = _asset(min_price="1.5", target_price="3.0")

    fake = FakeGeminiService()
    # Sneaky Gemini: ACCEPT at a price BELOW the creator minimum.
    fake.negotiate_result = NegotiationResult(
        status="ACCEPT", price_sol=decimal.Decimal("0.80"), reason="model-low"
    )

    result = NegotiationEngine(gemini=fake, max_rounds=5).run_round(
        asset, _session(), decimal.Decimal("0.80"), "commercial"
    )

    assert result.status == "ACCEPT"
    # R10 clamp: never below min on ACCEPT.
    assert result.price_sol >= asset.min_amount
    assert result.pay_address == _CREATOR_WALLET
    # Gemini was actually consulted.
    assert any(c[0] == "negotiate" for c in fake.calls)


def test_run_round_reports_model_failure_without_price_fallback():
    """紐⑤뜽 ?ㅽ뙣 ???꾩쓽 ?섎씫쨌諛섎? 媛寃⑹쓣 留뚮뱾吏 ?딅뒗??"""
    from tests.fakes import FakeGeminiService
    from services.negotiation_engine import NegotiationUnavailableError

    asset = _asset(min_price="1.5", target_price="3.0")
    fake = FakeGeminiService(fail_negotiate=True)

    with pytest.raises(NegotiationUnavailableError):
        NegotiationEngine(gemini=fake, max_rounds=5).run_round(
            asset, _session(), decimal.Decimal("2.0"), "commercial"
        )


def test_accept_pay_address_routes_secondary_to_escrow(settings):
    """2nd-creation asset (parent_asset_id set) -> pay_address == escrow (짠8).

    The engine delegates to the shared ``resolve_pay_to`` SSOT, which reads
    ``settings.PLATFORM_ESCROW_PUBKEY`` lazily for parented assets; configure
    it here so the assertion is deterministic.
    """
    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW
    asset = _asset(min_price="1.5", target_price="3.0", parent=True)

    result = _engine().run_round(
        asset, _session(), decimal.Decimal("2.0"), "commercial"
    )

    assert result.status == "ACCEPT"
    assert result.pay_address == _ESCROW


def test_run_round_clamps_gemini_counter_below_min():
    """R10 invariant on the COUNTER path: a Gemini counter below min is clamped."""
    from tests.fakes import FakeGeminiService

    asset = _asset(min_price="1.5", target_price="3.0")
    fake = FakeGeminiService()
    fake.negotiate_result = NegotiationResult(
        status="COUNTER_OFFER",
        price_sol=decimal.Decimal("0.80"),  # below min
        reason="model-low-counter",
    )

    result = NegotiationEngine(gemini=fake, max_rounds=5).run_round(
        asset, _session(), decimal.Decimal("0.50"), "commercial"
    )

    assert result.status == "COUNTER_OFFER"
    assert result.price_sol >= asset.min_amount
    assert result.pay_address is None


def test_run_round_passes_gemini_reject_through():
    """A Gemini REJECT flows through the finaliser unchanged (no pay_address)."""
    from tests.fakes import FakeGeminiService

    asset = _asset(min_price="1.5", target_price="3.0")
    fake = FakeGeminiService()
    fake.negotiate_result = NegotiationResult(
        status="REJECT", price_sol=None, reason="model rejected"
    )

    result = NegotiationEngine(gemini=fake, max_rounds=5).run_round(
        asset, _session(), decimal.Decimal("0.10"), "commercial"
    )

    assert result.status == "REJECT"
    assert result.pay_address is None
    assert result.price_sol is None


def test_max_rounds_defaults_when_unset(monkeypatch):
    """max_rounds=None defers to a sane default (5), not an error."""
    asset = _asset(min_price="1.5", target_price="3.0")
    session = _session(rounds=[{"status": "COUNTER_OFFER"}] * 5)

    result = NegotiationEngine(gemini=_NegotiationDouble(), max_rounds=None).run_round(
        asset, session, decimal.Decimal("1.0"), "commercial"
    )

    assert result.status == "REJECT"


@pytest.mark.django_db
def test_factory_builds_engine(monkeypatch):
    """get_negotiation_engine() returns a wired engine (smoke)."""
    from services.negotiation_engine import get_negotiation_engine

    engine = get_negotiation_engine()
    assert engine.max_rounds is not None and engine.max_rounds >= 1
