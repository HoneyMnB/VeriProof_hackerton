"""SPEC-003 unit tests ??GeminiService.negotiate (mocked genai client).

Covers the SPEC-003 짠5 unit TDD list for GeminiService.negotiate:
- test_negotiate_uses_response_schema (R4 / DD-7: response_schema enforced)
- test_negotiate_parses_status_price_reason (R5)
- test_negotiate_falls_back_on_parse_error (R8)
- test_negotiate_clamps_accept_below_min (R10)

The real ``google-genai`` SDK is NOT installed; these tests inject a stub
client mirroring ``client.models.generate_content(model, contents, config)``
so the service is exercised without network, like the asset-analysis tests.
"""
from __future__ import annotations

import decimal

import pytest
import json


# --- Stub client that mimics google-genai's ``.models.generate_content`` -----


class _StubResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubModels:
    """Records every call (incl. config) and returns a canned JSON payload."""

    def __init__(self, payload: dict | str, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.call_count = 0
        self.last_kwargs: dict = {}

    def generate_content(self, **kwargs):  # noqa: ANN003 (stub)
        self.call_count += 1
        self.last_kwargs = kwargs
        if self.fail:
            raise RuntimeError("stub genai negotiate failure")
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return _StubResponse(text)


class _StubClient:
    def __init__(self, payload, fail: bool = False) -> None:
        self.models = _StubModels(payload, fail=fail)


# === R4: response_schema enforced ===========================================


def test_negotiate_uses_response_schema():
    """The genai call is made with a config carrying response_schema (R4)."""
    from services.gemini_service import GeminiService

    payload = {"status": "ACCEPT", "price_sol": "2.00", "reason": "ok"}
    stub = _StubClient(payload)
    svc = GeminiService(client=stub)

    svc.negotiate(
        decimal.Decimal("1.0"),
        decimal.Decimal("3.0"),
        decimal.Decimal("2.0"),
        "commercial",
        [],
    )

    assert stub.models.call_count == 1
    config = stub.models.last_kwargs.get("config")
    assert config is not None
    # R4 / DD-7: a structured-output schema is forced on the model call.
    assert "response_schema" in config
    assert config.get("response_mime_type") == "application/json"
    # The reasoning model id is the one resolved for this call.
    assert stub.models.last_kwargs["model"] == "gemini-3.6-flash"


# === R5: parse {status, price_sol, reason} =================================


def test_negotiate_parses_status_price_reason():
    """A valid model JSON response is parsed into a NegotiationResult (R5)."""
    from services.gemini_service import GeminiService

    payload = {"status": "COUNTER_OFFER", "price_sol": "2.25", "reason": "mid"}
    svc = GeminiService(client=_StubClient(payload))

    result = svc.negotiate(
        decimal.Decimal("1.5"),
        decimal.Decimal("3.0"),
        decimal.Decimal("1.0"),
        "commercial",
        [],
    )

    assert result.status == "COUNTER_OFFER"
    assert result.price_sol == decimal.Decimal("2.25")
    assert result.reason == "mid"


def test_negotiate_ignores_extra_schema_fields():
    """Extra fields returned by the model are ignored (only 3 fields taken)."""
    from services.gemini_service import GeminiService

    payload = {
        "status": "ACCEPT",
        "price_sol": "2.0",
        "reason": "ok",
        "confidence": 0.9,  # outside-schema field
        "rationale": "ignored",
    }
    svc = GeminiService(client=_StubClient(payload))

    result = svc.negotiate(
        decimal.Decimal("1.0"),
        decimal.Decimal("3.0"),
        decimal.Decimal("2.0"),
        "commercial",
        [],
    )

    assert result.status == "ACCEPT"


# === R8: rule-based fallback on parse error / failure =======================


def test_negotiate_reports_error_on_parse_failure():
    """援ъ“???묐떟 ?ㅻ쪟??媛寃?洹쒖튃 fallback?쇰줈 ?泥댄븯吏 ?딅뒗??"""
    from services.gemini_service import GeminiResponseError, GeminiService

    # Garbage non-JSON response, retried 3 times then fallback.
    svc = GeminiService(client=_StubClient("not json {{{"))

    with pytest.raises(GeminiResponseError):
        svc.negotiate(decimal.Decimal("1.0"), decimal.Decimal("3.0"), decimal.Decimal("2.0"), "commercial", [])
    assert svc._client.models.call_count == 3


def test_negotiate_reports_error_on_transport_failure():
    """?꾩넚 ?ㅻ쪟???꾩쓽 ?묒긽 寃곌낵濡??泥댄븯吏 ?딅뒗??"""
    from services.gemini_service import GeminiResponseError, GeminiService

    svc = GeminiService(client=_StubClient({}, fail=True))

    with pytest.raises(GeminiResponseError):
        svc.negotiate(decimal.Decimal("1.5"), decimal.Decimal("3.0"), decimal.Decimal("1.0"), "commercial", [])
    assert svc._client.models.call_count == 3


def test_negotiate_reports_unavailable_when_no_client():
    """紐⑤뜽 誘몄꽕?뺤? 紐낆떆?곸씤 unavailable ?ㅻ쪟??"""
    from services.gemini_service import GeminiService, GeminiUnavailableError

    svc = GeminiService()  # no client, no keys

    with pytest.raises(GeminiUnavailableError):
        svc.negotiate(decimal.Decimal("1.0"), decimal.Decimal("3.0"), decimal.Decimal("0.5"), "commercial", [])


# === R10: clamp ACCEPT below min ============================================


def test_negotiate_clamps_accept_below_min():
    """Gemini ACCEPT with price < min is clamped UP to min (R10 creator guard)."""
    from services.gemini_service import GeminiService

    payload = {"status": "ACCEPT", "price_sol": "0.50", "reason": "low"}
    svc = GeminiService(client=_StubClient(payload))

    result = svc.negotiate(
        decimal.Decimal("1.0"),  # min
        decimal.Decimal("3.0"),
        decimal.Decimal("0.50"),
        "commercial",
        [],
    )

    assert result.status == "ACCEPT"
    # R10: clamped up to min_price, never below.
    assert result.price_sol >= decimal.Decimal("1.0")
    assert result.price_sol == decimal.Decimal("1.0")


def test_negotiate_rounds_usdc_to_six_decimals():
    """USDC prices are rounded to 6 decimal places."""
    from services.gemini_service import GeminiService

    # A price with more than 6 decimals must be quantised.
    payload = {"status": "ACCEPT", "price_sol": "2.1234567", "reason": "ok"}
    svc = GeminiService(client=_StubClient(payload))

    result = svc.negotiate(
        decimal.Decimal("1.0"),
        decimal.Decimal("3.0"),
        decimal.Decimal("2.1234567"),
        "commercial",
        [],
    )

    # Exactly 6 decimal places.
    assert result.price_sol.as_tuple().exponent == -9
