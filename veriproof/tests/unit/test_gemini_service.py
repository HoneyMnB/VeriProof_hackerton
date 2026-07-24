"""GeminiService unit tests for structured assistant action planning."""
from __future__ import annotations

import json

import pytest

# --- Stub client that mimics google-genai's ``.models.generate_content`` -----


class _StubResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubModels:
    def __init__(self, payload: dict, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.call_count = 0

    def generate_content(self, **kwargs):  # noqa: ANN003 (stub)
        self.call_count += 1
        if self.fail:
            raise RuntimeError("stub genai failure")
        return _StubResponse(json.dumps(self.payload))


class _StubClient:
    def __init__(self, payload: dict, fail: bool = False) -> None:
        self.models = _StubModels(payload, fail=fail)


def test_creator_action_plan_accepts_only_allowlisted_structured_action():
    """구조화된 자연어 계획만 도구 실행 계층으로 넘긴다."""
    from services.gemini_service import GeminiService

    svc = GeminiService(
        client=_StubClient(
            {
                "reply": "I will ask the server to record this expense.",
                "action": {
                    "name": "record_expense",
                    "amount_usdc": 1.2,
                    "memo": "storage",
                    "asset_id": None,
                    "min_price_usdc": None,
                    "target_price_usdc": None,
                    "visibility": None,
                    "title": None,
                },
            }
        )
    )

    plan = svc.plan_creator_action({}, "Record 1.2 USDC for storage")

    assert plan.action == {
        "name": "record_expense",
        "arguments": {"amount_usdc": 1.2, "memo": "storage"},
    }


def test_creator_action_plan_rejects_unknown_action_even_if_model_returns_it():
    """Gemini 응답이 스키마를 우회해도 서버 허용 목록 밖 실행은 막는다."""
    from services.gemini_service import GeminiResponseError, GeminiService

    svc = GeminiService(
        client=_StubClient(
            {
                "reply": "Done",
                "action": {
                    "name": "send_payment",
                    "amount_usdc": None,
                    "memo": None,
                    "asset_id": None,
                    "min_price_usdc": None,
                    "target_price_usdc": None,
                    "visibility": None,
                    "title": None,
                },
            }
        )
    )

    with pytest.raises(GeminiResponseError):
        svc.plan_creator_action({}, "Pay now")


def test_gemini_get_client_returns_none_without_api_key():
    """No API key keeps the offline default even when the optional SDK is installed."""
    from services.gemini_service import GeminiService

    svc = GeminiService()
    assert svc._get_client() is None


def test_gemini_get_client_returns_injected_client():
    """An injected client always wins over lazy SDK construction."""
    from services.gemini_service import GeminiService

    stub = _StubClient({"tags": [], "category": None, "originality_score": 1,
                        "recommended_min_price_usdc": "0"})
    svc = GeminiService(api_keys="ignored", client=stub)
    # The injected client object itself is returned (identity check).
    assert svc._get_client() is stub


def test_gemini_factory_builds_service():
    """get_gemini_service() returns a GeminiService wired from settings."""
    from services.gemini_service import GeminiService, get_gemini_service

    svc = get_gemini_service()
    assert isinstance(svc, GeminiService)


def test_gemini_vision_model_defaults_when_unset():
    """_vision_model_for_call falls back to the default model id."""
    from services.gemini_service import GeminiService

    assert GeminiService()._vision_model_for_call() == "gemini-3.6-flash"
    assert (
        GeminiService(vision_model="custom-model")._vision_model_for_call()
        == "custom-model"
    )


def test_gemini_assistant_model_defaults_to_flash_lite():
    """창작자 비서는 독립적인 Flash Lite 기본 모델을 사용한다."""
    from services.gemini_service import GeminiService

    assert GeminiService()._assistant_model_for_call() == "gemini-3.1-flash-lite"
    assert (
        GeminiService(assistant_model="custom-assistant-model")._assistant_model_for_call()
        == "custom-assistant-model"
    )
