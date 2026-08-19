"""GeminiService — Google Gemini (gemini-3.1-flash-lite).

Architecture 4 contract. The ``google-genai`` SDK is import-guarded so this
module imports with the dependency absent; live calls happen only inside
method bodies. Tests swap in ``tests.fakes.FakeGeminiService``.
"""
from __future__ import annotations

import decimal
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._types import AnalysisResult, BatchQuote, NegotiationResult, quantize_sol, quantize_usdc

logger = logging.getLogger(__name__)

# 일시적인 전송 오류만 재시도하며 임의의 결과를 만들지 않는다.
ANALYZE_MAX_RETRIES = 3
NEGOTIATE_MAX_RETRIES = 3
# SPEC-003: allowed negotiation outcome statuses (response_schema enum).
NEGOTIATION_STATUSES = ("ACCEPT", "COUNTER_OFFER", "REJECT")
# SPEC-003 R4: structured-output JSON schema forced on the reasoning call.
NEGOTIATION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(NEGOTIATION_STATUSES)},
        "price": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["status", "price", "reason"],
}
BATCH_MAX_RETRIES = 3
# SPEC-007 R2: structured-output JSON schema forced on the batch pricing call.
BATCH_QUOTE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "unit_price_usdc": {"type": "number"},
                },
                "required": ["asset_id", "unit_price_usdc"],
            },
        }
    },
    "required": ["quotes"],
}
# 멀티모달 LLM(Gemini)이 실제 내용을 분석할 수 있는 형식. 저장 가능(STORABLE)
# 집합과 구분된다 — zip/tar 등은 저장은 되지만 LLM 분석 대상이 아니다.
LLM_ANALYZABLE_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "text/plain",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "video/mp4",
        "video/webm",
    }
)
VISION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "originality_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "recommended_min_price_usdc": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": [
        "tags",
        "category",
        "originality_score",
        "recommended_min_price_usdc",
        "description",
    ],
}
REGISTRATION_METADATA_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    },
    "required": ["reply", "title", "description", "tags"],
}

# 대화형 비서가 사용할 수 있는 변경 도구는 이 목록으로 제한한다. 자연어의
# 키워드를 코드가 추측해 실행하지 않고, Gemini의 구조화 계획도 서버에서 다시 검증한다.
CREATOR_ACTION_NAMES = (
    "none",
    "record_expense",
    "update_asset_terms",
    "prepare_registration",
    "analyze_attachment",
)
CREATOR_ACTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "action": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": list(CREATOR_ACTION_NAMES)},
                "amount_usdc": {"type": "number", "nullable": True},
                "memo": {"type": "string", "nullable": True},
                "asset_id": {"type": "string", "nullable": True},
                "min_price_usdc": {"type": "number", "nullable": True},
                "target_price_usdc": {"type": "number", "nullable": True},
                "visibility": {
                    "type": "string",
                    "enum": ["private", "public"],
                    "nullable": True,
                },
                "title": {"type": "string", "nullable": True},
            },
            "required": [
                "name",
                "amount_usdc",
                "memo",
                "asset_id",
                "min_price_usdc",
                "target_price_usdc",
                "visibility",
                "title",
            ],
        },
    },
    "required": ["reply", "action"],
}


@dataclass(frozen=True)
class CreatorActionPlan:
    """Gemini가 제안한 응답과 서버 검증 전 도구 계획이다."""

    reply: str
    action: dict[str, Any] | None


@dataclass(frozen=True)
class RegistrationMetadataSuggestion:
    """첨부 이미지에서 한 번의 모델 호출로 생성한 등록 초안 메타데이터."""

    reply: str
    title: str
    description: str
    tags: list[str]


class GeminiUnavailableError(RuntimeError):
    """실제 Gemini 호출을 수행할 수 없을 때 발생한다."""


class GeminiResponseError(RuntimeError):
    """Gemini가 유효한 구조화 응답을 반환하지 않을 때 발생한다."""


class GeminiService:
    """Isolates all Gemini vision/reasoning/batch I/O.

    The constructor stores config only; it MUST NOT open a live client at
    import time. The real ``google.genai`` client is created lazily inside
    methods (or via ``_get_client``) so the module imports offline.
    """

    def __init__(
        self,
        api_keys: str | None = None,
        vision_model: str | None = None,
        reasoning_model: str | None = None,
        batch_model: str | None = None,
        assistant_model: str | None = None,
        vertex_enabled: bool | None = None,
        vertex_project: str | None = None,
        vertex_location: str | None = None,
        client: Any = None,
    ) -> None:
        self.api_keys = api_keys
        self.vision_model = vision_model
        self.reasoning_model = reasoning_model
        self.batch_model = batch_model
        self.assistant_model = assistant_model
        self.vertex_enabled = vertex_enabled
        self.vertex_project = vertex_project
        self.vertex_location = vertex_location
        # Optional injected client (tests/fakes pass a stub here).
        self._client = client

    def analyze_asset(self, file_bytes: bytes, mime_type: str) -> AnalysisResult:
        """멀티모달 자산 분석 -> 태그·카테고리·독창성·최소가·설명.

        이미지 외에도 PDF/오디오/비디오/텍스트 등 Gemini가 이해하는 형식을
        전달된 mime 그대로 분석한다. 실패는 명시적으로 올린다.
        """
        client = self._get_client()
        if client is None:
            raise GeminiUnavailableError("Gemini vision client is unavailable")
        last_error: Exception | None = None
        for _ in range(ANALYZE_MAX_RETRIES):
            try:
                text = self._call_multimodal(client, file_bytes, mime_type)
                return self._parse_vision_response(text)
            except Exception as exc:  # noqa: BLE001 (SDK errors are broad)
                last_error = exc
                logger.warning("gemini analyze_asset attempt failed: %s", exc)
        raise GeminiResponseError(
            f"Gemini multimodal analysis failed after {ANALYZE_MAX_RETRIES} attempts"
        ) from last_error

    def negotiate(
        self,
        min_price: decimal.Decimal,
        target_price: decimal.Decimal,
        offer_sol: decimal.Decimal,
        usage_type: str,
        history: list[dict],
        *,
        currency: str = "SOL",
    ) -> NegotiationResult:
        """Reasoning negotiation -> ACCEPT/COUNTER_OFFER/REJECT + price.

        SPEC-003 R4/R5/R8/R10. The ``google-genai`` reasoning model is called
        with a forced ``response_schema`` so the answer is structured JSON.
        On any failure (no client, transport error, unparseable JSON) the call
        is retried up to ``NEGOTIATE_MAX_RETRIES`` times. 실패 시 가격이나
        조건을 추정하지 않고 명시적으로 실패한다.

        R10: an ACCEPT below ``min_price`` is clamped UP to ``min_price`` so the
        model cannot undercut the creator's floor.
        """
        client = self._get_client()
        if client is None:
            raise GeminiUnavailableError("Gemini negotiation client is unavailable")

        last_error: Exception | None = None
        for _ in range(NEGOTIATE_MAX_RETRIES):
            try:
                text = self._call_negotiate(
                    client, min_price, target_price, offer_sol, usage_type, history, currency
                )
                return self._parse_negotiate_response(text, min_price, target_price)
            except Exception as exc:  # noqa: BLE001 (SDK errors are broad)
                last_error = exc
                logger.warning("gemini negotiate attempt failed: %s", exc)

        raise GeminiResponseError(
            f"Gemini negotiation failed after {NEGOTIATE_MAX_RETRIES} attempts"
        ) from last_error

    def quote_batch(
        self, items: list[dict], usage_type: str
    ) -> list[BatchQuote]:
        """Batch structured-JSON pricing (3.5-flash-lite). SPEC-007 R1/R2.

        ``items`` is a list of ``{asset_id, min_price_usdc}`` dicts (Decimal or
        coercible). Returns one ``BatchQuote(asset_id, unit_price_usdc)`` per
        input item, in the SAME ORDER.

        구조화 호출은 최대 ``BATCH_MAX_RETRIES``회 시도한다. 실패 시 가격을
        임의 계산하지 않고 명시적으로 실패한다.
        """
        normalized = [self._normalize_batch_item(i) for i in items]

        client = self._get_client()
        if client is None:
            raise GeminiUnavailableError("Gemini batch-pricing client is unavailable")

        last_error: Exception | None = None
        for _ in range(BATCH_MAX_RETRIES):
            try:
                text = self._call_quote_batch(client, normalized, usage_type)
                return self._parse_batch_response(text, normalized)
            except Exception as exc:  # noqa: BLE001 (SDK errors are broad)
                last_error = exc
                logger.warning("gemini quote_batch attempt failed: %s", exc)

        raise GeminiResponseError(
            f"Gemini batch pricing failed after {BATCH_MAX_RETRIES} attempts"
        ) from last_error

    # --- Internal helpers (SPEC-007 batch) -----------------------------------

    @staticmethod
    def _normalize_batch_item(item: Any) -> dict:
        """Coerce a batch item to a ``{asset_id, min_price_usdc}`` dict.

        Accepts the canonical dict shape OR a Django IpAsset-like object
        (``.id`` / ``.min_price_usdc``). ``asset_id`` is stringified so JSON
        round-trips and dict lookups are stable.
        """
        if isinstance(item, dict):
            asset_id = item.get("asset_id")
            min_price = item.get("min_price_usdc")
        else:
            asset_id = getattr(item, "id", None)
            min_price = getattr(item, "min_price_usdc", None)
        try:
            min_price_dec = quantize_usdc(decimal.Decimal(str(min_price or "0")))
        except (decimal.InvalidOperation, ValueError, TypeError):
            min_price_dec = decimal.Decimal("0")
        return {"asset_id": str(asset_id), "min_price_usdc": min_price_dec}

    def _batch_model_for_call(self) -> str:
        """Resolve the model id for the batch pricing call."""
        return self.batch_model or "gemini-3.5-flash-lite"

    def _call_quote_batch(
        self, client: Any, items: list[dict], usage_type: str
    ) -> str:
        """Invoke the batch pricing model with a forced response_schema (R2)."""
        prompt = self._batch_prompt(items, usage_type)
        config = {
            "response_mime_type": "application/json",
            "response_schema": BATCH_QUOTE_RESPONSE_SCHEMA,
        }
        response = client.models.generate_content(
            model=self._batch_model_for_call(),
            contents=[prompt],
            config=config,
        )
        return getattr(response, "text", "") or ""

    @staticmethod
    def _batch_prompt(items: list[dict], usage_type: str) -> str:
        """Build the instruction string for the batch pricing call."""
        lines = "\n".join(
            f"- asset_id={it['asset_id']}, min_price_usdc={it['min_price_usdc']}"
            for it in items
        )
        return (
            "You are the seller's batch pricing agent for a portfolio of IP "
            "assets licensed in bulk to a single buyer. Price each asset "
            " competitively but NEVER below its min_price_usdc.\n"
            f"Usage type: {usage_type}.\n"
            f"Items ({len(items)}):\n{lines}\n"
            "Return JSON with exactly: quotes (array of {asset_id, "
            "unit_price_usdc}). Each unit_price_usdc is a positive number >= "
            "the item's min_price_usdc."
        )

    def _parse_batch_response(
        self, text: str, items: list[dict]
    ) -> list[BatchQuote]:
        """Parse the model JSON into per-item BatchQuotes, applying AC-2.

        입력 자산별로 정확히 하나의 견적이 있어야 한다. 누락·중복·형식 오류는
        재시도 후 호출자에게 실패로 전달한다.
        """
        from django.conf import settings

        floor = decimal.Decimal(str(getattr(settings, "MICRO_FLOOR_USDC", "0.05")))
        data = json.loads(text)
        raw_quotes = data.get("quotes")
        if not isinstance(raw_quotes, list):
            raise ValueError("quotes must be an array")
        by_id: dict[str, Any] = {}
        for rq in raw_quotes:
            if not isinstance(rq, dict):
                raise ValueError("each quote must be an object")
            quote_id = str(rq.get("asset_id"))
            if quote_id in by_id:
                raise ValueError(f"duplicate quote for asset_id={quote_id}")
            by_id[quote_id] = rq.get("unit_price_usdc")

        expected_ids = {item["asset_id"] for item in items}
        if set(by_id) != expected_ids:
            raise ValueError("quotes must match the requested asset ids exactly")

        results: list[BatchQuote] = []
        for it in items:
            asset_id = it["asset_id"]
            min_price = it["min_price_usdc"]
            unit = self._coerce_unit_price(by_id[asset_id], min_price, floor)
            results.append(BatchQuote(asset_id=asset_id, unit_price_usdc=unit))
        return results

    @staticmethod
    def _coerce_unit_price(
        raw: Any, min_price: decimal.Decimal, floor: decimal.Decimal
    ) -> decimal.Decimal:
        """모델 가격이 창작자 하한과 마이크로 결제 하한을 지키는지 검증한다."""
        try:
            price = decimal.Decimal(str(raw))
        except (decimal.InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("unit_price_usdc must be numeric") from exc
        if not price.is_finite() or price < min_price or price < floor:
            raise ValueError("unit_price_usdc violates the creator price floor")
        return quantize_usdc(price)

    # --- Internal helpers (SPEC-001) ----------------------------------------

    def _get_client(self) -> Any:
        """Return the injected client, or a lazily-built real genai client.

        The real ``google.genai`` SDK is imported HERE (not at module import).
        API 키 방식과 Vertex AI ADC 방식을 모두 지원한다. 자격증명이 없으면
        None을 반환해 호출자가 명확한 설정 오류를 응답하도록 한다.
        """
        if self._client is not None:
            return self._client
        try:
            from google import genai  # import-guarded (architecture 4)
        except ImportError:
            logger.info("google-genai not installed; Gemini client is unavailable")
            return None
        try:  # pragma: no cover
            if self.vertex_enabled and self.vertex_project and self.vertex_location:
                credential_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
                if not credential_file or Path(credential_file).is_file():
                    return genai.Client(
                        vertexai=True,
                        project=self.vertex_project,
                        location=self.vertex_location,
                    )
                logger.warning(
                    "Gemini Vertex credential file is unavailable; trying configured API key"
                )
            elif self.vertex_enabled:
                logger.warning("Gemini Vertex configuration incomplete; trying configured API key")
            api_key = (self.api_keys or "").split(",", maxsplit=1)[0].strip()
            if not api_key:
                logger.warning("Gemini API key is not configured")
                return None
            return genai.Client(api_key=api_key)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("gemini client construction failed: %s", exc)  # pragma: no cover
            return None  # pragma: no cover

    def connection_status(self) -> dict[str, Any]:
        """비밀값 없이 실제로 선택될 인증 경로를 보고한다."""
        api_key_configured = bool((self.api_keys or "").strip())
        credential_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        vertex_ready = bool(
            self.vertex_enabled
            and self.vertex_project
            and self.vertex_location
            and (not credential_file or Path(credential_file).is_file())
        )
        authentication = "vertex_adc" if vertex_ready else "api_key"
        return {
            "configured": vertex_ready or api_key_configured,
            "authentication": authentication if vertex_ready or api_key_configured else None,
        }

    def _call_multimodal(self, client: Any, file_bytes: bytes, mime_type: str) -> str:
        """전달된 mime 그대로 Part를 만들어 모델의 원문 응답을 반환한다.

        Uses the standard ``client.models.generate_content`` shape so the same
        code path serves both the real SDK and injected test stubs.
        """
        try:
            from google.genai import types
        except ImportError as exc:
            raise GeminiUnavailableError("google-genai is not installed") from exc
        prompt = (
            "Analyze this file and return JSON with keys: "
            "tags (array of strings), category (string), "
            "originality_score (integer 0-100), "
            "recommended_min_price_usdc (string decimal), "
            "description (a concise factual summary of the asset, for search)."
        )
        part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        response = client.models.generate_content(
            model=self._vision_model_for_call(),
            contents=[part, prompt],
            config={
                "response_mime_type": "application/json",
                "response_schema": VISION_RESPONSE_SCHEMA,
            },
        )
        return getattr(response, "text", "") or ""

    def _vision_model_for_call(self) -> str:
        """Resolve the model id for the vision call (has a sane default)."""
        return self.vision_model or "gemini-3.6-flash"

    def assist_creator(self, context: dict[str, Any], message: str) -> str:
        """검증된 창작자 데이터만 근거로 실제 Gemini 응답을 생성한다."""
        client = self._get_client()
        if client is None:
            raise GeminiUnavailableError("Gemini credentials are not configured")
        prompt = (
            "You are VeriProof AI's creator-rights assistant. Use only the "
            "verified workspace data below. Explain the current registration, "
            "x402 negotiation, settlement, and certificate pipeline clearly. "
            "Never claim a payment or on-chain action that is absent from data.\n"
            "Reply in Korean when the creator's message is in Korean; otherwise reply "
            "in the creator's language.\n"
            "Apply the creator-approved behavior instructions in Workspace data. "
            "When an action needs a user input or confirmation, state that exact "
            "next step instead of claiming it was completed.\n"
            f"Workspace data: {json.dumps(context, ensure_ascii=False)}\n"
            f"Creator question: {message}"
        )
        try:
            response = client.models.generate_content(
                model=self._assistant_model_for_call(), contents=[prompt]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("creator assistant call failed: %s", exc)
            raise GeminiResponseError("Gemini creator-assistant request failed") from exc
        answer = getattr(response, "text", "") or ""
        if not answer.strip():
            raise GeminiResponseError("Gemini creator-assistant returned an empty response")
        return answer.strip()

    def assist_with_attachments(
        self, context: dict[str, Any], message: str, files: list[tuple[bytes, str]]
    ) -> str:
        """첨부 파일을 멀티모달로 함께 전달해 창작자의 분석·의견 요청에 답한다."""
        client = self._get_client()
        if client is None:
            raise GeminiUnavailableError("Gemini credentials are not configured")
        try:
            from google.genai import types
        except ImportError as exc:
            raise GeminiUnavailableError("google-genai is not installed") from exc
        prompt = (
            "You are VeriProof AI's creator-rights assistant. The creator has "
            "attached one or more files and is asking about them. Analyze the "
            "attached file(s) and answer using only the attached content and the "
            "verified workspace data below. Never claim any payment or on-chain "
            "action that is absent from data.\n"
            "Reply in Korean when the creator's message is in Korean; otherwise reply "
            "in the creator's language.\n"
            f"Workspace data: {json.dumps(context, ensure_ascii=False)}\n"
            f"Creator message: {message}"
        )
        parts = [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in files]
        try:
            response = client.models.generate_content(
                model=self._assistant_model_for_call(), contents=[*parts, prompt]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("creator attachment-assist call failed: %s", exc)
            raise GeminiResponseError("Gemini attachment-assist request failed") from exc
        answer = getattr(response, "text", "") or ""
        if not answer.strip():
            raise GeminiResponseError("Gemini attachment-assist returned an empty response")
        return answer.strip()

    def suggest_registration_metadata(
        self, file_bytes: bytes, mime_type: str, message: str
    ) -> RegistrationMetadataSuggestion:
        """이미지 한 장으로 등록 제목·설명·태그를 단일 멀티모달 호출에서 생성한다."""
        client = self._get_client()
        if client is None:
            raise GeminiUnavailableError("Gemini credentials are not configured")
        try:
            from google.genai import types
        except ImportError as exc:
            raise GeminiUnavailableError("google-genai is not installed") from exc
        prompt = (
            "Generate metadata for licensing registration from the attached image. "
            "Return only the requested JSON. The title and description must describe "
            "only visible, supported image content; do not invent ownership, licensing "
            "terms, people, locations, brands, or events. Write the title, description, "
            "and every tag in Korean. The reply must be a short Korean confirmation "
            "that the registration draft is ready. Produce a concise title, a useful "
            "registration description, and no more than six specific discovery tags.\n"
            f"Creator message: {message}"
        )
        try:
            response = client.models.generate_content(
                model=self._vision_model_for_call(),
                contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": REGISTRATION_METADATA_RESPONSE_SCHEMA,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("registration metadata generation failed: %s", exc)
            raise GeminiResponseError("Gemini registration metadata generation failed") from exc
        return self._parse_registration_metadata(getattr(response, "text", "") or "")

    @staticmethod
    def _parse_registration_metadata(text: str) -> RegistrationMetadataSuggestion:
        try:
            data = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GeminiResponseError("Gemini registration metadata is not valid JSON") from exc
        reply = data.get("reply")
        title = data.get("title")
        description = data.get("description")
        tags = data.get("tags")
        if (
            not isinstance(reply, str)
            or not reply.strip()
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(description, str)
            or not description.strip()
        ):
            raise GeminiResponseError("Gemini registration metadata is incomplete")
        if not isinstance(tags, list) or len(tags) > 6:
            raise GeminiResponseError("Gemini registration metadata has invalid tags")
        normalized_tags = []
        for tag in tags:
            normalized = str(tag).strip().lstrip("#").strip()
            if not normalized:
                continue
            if normalized not in normalized_tags:
                normalized_tags.append(normalized)
        return RegistrationMetadataSuggestion(
            reply=reply.strip()[:300],
            title=title.strip()[:120],
            description=description.strip()[:3000],
            tags=normalized_tags[:6],
        )

    def plan_creator_action(
        self, context: dict[str, Any], message: str
    ) -> CreatorActionPlan:
        """자연어 요청을 응답과 제한된 도구 계획으로 분리해 생성한다.

        모델은 실행 권한이 없다. 이 메서드는 계획만 반환하며, 실제 변경은
        CreatorActionService가 소유권·입력값·DB 저장 결과를 검증한 뒤 수행한다.
        """
        client = self._get_client()
        if client is None:
            raise GeminiUnavailableError("Gemini credentials are not configured")
        prompt = self._creator_action_prompt(context, message)
        config = {
            "response_mime_type": "application/json",
            "response_schema": CREATOR_ACTION_RESPONSE_SCHEMA,
        }
        try:
            response = client.models.generate_content(
                model=self._assistant_model_for_call(), contents=[prompt], config=config
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("creator action planning call failed: %s", exc)
            raise GeminiResponseError("Gemini creator-action planning failed") from exc
        return self._parse_creator_action_plan(getattr(response, "text", "") or "")

    @staticmethod
    def _creator_action_prompt(context: dict[str, Any], message: str) -> str:
        """실행 권한과 다음 입력을 혼동하지 않도록 구조화 프롬프트를 만든다."""
        return (
            "You are VeriProof AI's creator-rights assistant. Use only the "
            "verified workspace data below. Explain registration, licensing, "
            "settlement, certificates, income, expenses, and sales clearly. "
            "Never claim that a payment, on-chain action, registration, or data "
            "change succeeded: the server independently executes and verifies it.\n"
            "Write reply in Korean when the creator's message is in Korean; otherwise "
            "write it in the creator's language.\n"
            "Return an action only for an explicit creator command, never for an "
            "informational question. Allowed action names: none, record_expense, "
            "update_asset_terms, prepare_registration, analyze_attachment. For "
            "record_expense require "
            "amount_usdc as a JSON number (without a currency suffix) and memo. "
            "For update_asset_terms require asset_id, "
            "min_price_usdc, target_price_usdc, and visibility. Use "
            "prepare_registration only to state that a file upload is still "
            "required; it does not register an asset. Use analyze_attachment when "
            "the creator asks you to analyze, review, describe, or give an opinion "
            "about a file listed in conversation_attachments; it needs no "
            "arguments. Never use analyze_attachment when conversation_attachments "
            "is empty. If required values are "
            "missing, use none and state exactly what is needed.\n"
            "Apply the creator-approved behavior instructions in Workspace data.\n"
            f"Workspace data: {json.dumps(context, ensure_ascii=False)}\n"
            f"Creator message: {message}"
        )

    @staticmethod
    def _parse_creator_action_plan(text: str) -> CreatorActionPlan:
        """응답 스키마를 다시 검사해 허용 목록 밖 계획을 실행하지 않는다."""
        try:
            data = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GeminiResponseError("Gemini creator plan is not valid JSON") from exc
        reply = data.get("reply")
        action = data.get("action")
        if not isinstance(reply, str) or not reply.strip() or not isinstance(action, dict):
            raise GeminiResponseError("Gemini creator plan has an invalid shape")
        name = action.get("name")
        if name not in CREATOR_ACTION_NAMES:
            raise GeminiResponseError("Gemini creator plan contains an invalid action")
        if name == "none":
            return CreatorActionPlan(reply=reply.strip(), action=None)
        argument_keys = {
            "record_expense": ("amount_usdc", "memo"),
            "update_asset_terms": (
                "asset_id",
                "min_price_usdc",
                "target_price_usdc",
                "visibility",
            ),
            "prepare_registration": ("title",),
            "analyze_attachment": (),
        }[name]
        arguments = {key: action.get(key) for key in argument_keys if action.get(key) is not None}
        return CreatorActionPlan(
            reply=reply.strip(), action={"name": name, "arguments": arguments}
        )

    # --- Internal helpers (SPEC-003 negotiation) ----------------------------

    def _reasoning_model_for_call(self) -> str:
        """Resolve the model id for the negotiation reasoning call."""
        return self.reasoning_model or "gemini-3.6-flash"

    def _assistant_model_for_call(self) -> str:
        """창작자 비서 대화에 사용할 모델 ID를 반환한다."""
        return self.assistant_model or "gemini-3.1-flash-lite"

    def _call_negotiate(
        self,
        client: Any,
        min_price: decimal.Decimal,
        target_price: decimal.Decimal,
        offer_sol: decimal.Decimal,
        usage_type: str,
        history: list[dict],
        currency: str,
    ) -> str:
        """Invoke the reasoning model with a forced response_schema (R4).

        Uses the same ``client.models.generate_content(model, contents, config)``
        shape as the vision path so injected test stubs work identically.
        """
        prompt = self._negotiation_prompt(
            min_price, target_price, offer_sol, usage_type, history, currency
        )
        config = {
            "response_mime_type": "application/json",
            "response_schema": NEGOTIATION_RESPONSE_SCHEMA,
        }
        response = client.models.generate_content(
            model=self._reasoning_model_for_call(),
            contents=[prompt],
            config=config,
        )
        return getattr(response, "text", "") or ""

    @staticmethod
    def _negotiation_prompt(
        min_price: decimal.Decimal,
        target_price: decimal.Decimal,
        offer_sol: decimal.Decimal,
        usage_type: str,
        history: list[dict],
        currency: str,
    ) -> str:
        """Build the instruction string for the negotiation reasoning call."""
        history_json = json.dumps(history or [])
        return (
            "You are the seller's autonomous pricing agent for an IP license. "
            "Given the creator's constraints and the buyer's offer, decide "
            "ACCEPT, COUNTER_OFFER, or REJECT.\n"
            f"Currency: {currency}.\n"
            f"Constraints: minimum price={min_price} {currency}, "
            f"target price={target_price} {currency}, usage_type={usage_type}.\n"
            f"Buyer offer: {offer_sol} {currency}.\n"
            f"Prior rounds: {history_json}\n"
            "Return JSON with exactly: status (ACCEPT|COUNTER_OFFER|REJECT), "
            "price (number, your proposed final/counter price in the stated currency), "
            "reason (short Korean string that uses only the stated currency). "
            "Never accept below the minimum price."
        )

    def _parse_negotiate_response(
        self,
        text: str,
        min_price: decimal.Decimal,
        target_price: decimal.Decimal,
    ) -> NegotiationResult:
        """Parse the model JSON into a NegotiationResult, applying R10.

        Only the three schema fields are taken (R5). Extra fields are ignored.
        Raises on malformed JSON so the retry/fallback loop handles it (R8).
        """
        data = json.loads(text)
        status = data.get("status")
        if status not in NEGOTIATION_STATUSES:
            # Unknown status -> let the fallback handle it.
            raise ValueError(f"unknown negotiation status: {status!r}")
        price_raw = data.get("price")
        price: decimal.Decimal | None
        try:
            price = decimal.Decimal(str(price_raw)) if price_raw is not None else None
        except (decimal.InvalidOperation, ValueError, TypeError):
            price = None
        reason = str(data.get("reason", ""))

        # R10: ACCEPT/COUNTER must never be below min_price (creator guard).
        if status in ("ACCEPT", "COUNTER_OFFER") and (price is None or price < min_price):
            price = min_price
        if price is not None:
            price = quantize_sol(price)

        return NegotiationResult(status=status, price_sol=price, reason=reason)

    def _parse_vision_response(self, text: str) -> AnalysisResult:
        """Parse the model JSON into an AnalysisResult (non-degraded)."""
        data = json.loads(text)
        return AnalysisResult(
            tags=list(data.get("tags", []) or []),
            category=data.get("category"),
            originality_score=int(data.get("originality_score", 0) or 0),
            recommended_min_price_usdc=decimal.Decimal(
                str(data.get("recommended_min_price_usdc", "0"))
            ),
            degraded=False,
            description=(data.get("description") or None),
        )


def get_gemini_service() -> GeminiService:
    """Factory: build a GeminiService from current Django settings."""
    from django.conf import settings

    return GeminiService(
        api_keys=getattr(settings, "GEMINI_API_KEYS", "") or None,
        vision_model=getattr(settings, "GEMINI_VISION_MODEL", None),
        reasoning_model=getattr(settings, "GEMINI_REASONING_MODEL", None),
        batch_model=getattr(settings, "GEMINI_BATCH_MODEL", None),
        assistant_model=getattr(settings, "GEMINI_ASSISTANT_MODEL", None),
        vertex_enabled=getattr(settings, "VERTEX_ENABLED", False),
        vertex_project=getattr(settings, "VERTEX_PROJECT", None),
        vertex_location=getattr(settings, "VERTEX_LOCATION", None),
    )
