"""Observable, same-origin chat stream for the Buyer Agent demo UI."""

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from .agent import root_agent
from .payment_approval import (
    PAYMENT_APPROVAL_STATE_KEY,
    PAYMENT_MODE_APPROVAL,
    PAYMENT_MODE_AUTONOMOUS,
    PAYMENT_MODE_INSTRUCTION_STATE_KEY,
    PAYMENT_MODE_STATE_KEY,
    PAYMENT_MODES,
)

logger = logging.getLogger(__name__)

_APP_NAME = root_agent.name or "veriproof_buyer_agent"
_MAX_MESSAGE_LENGTH = 2_000
_MAX_COLLECTION_ITEMS = 30
_MAX_STRING_LENGTH = 4_000
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "private",
    "secret",
    "signature",
    "token",
)
_RECEIPT_LABELS = (
    ("asset_id", "작품 ID"),
    ("asset_title", "작품명"),
    ("amount_usdc", "결제 금액"),
    ("network_fee", "네트워크 수수료"),
    ("license_id", "라이선스 ID"),
    ("transaction_signature", "트랜잭션 서명"),
    ("download_expires_at", "다운로드 기한"),
)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(https?://[^\s)]+\)")
_BARE_URL_PATTERN = re.compile(r"https?://[^\s<>\[\]()]+")

_session_service = InMemorySessionService()
_runner = Runner(
    app_name=_APP_NAME,
    agent=root_agent,
    session_service=_session_service,
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def safe_tool_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-safe value with credential-like fields redacted."""
    if depth > 8:
        return "[truncated]"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _is_sensitive_key(key) and str(key) != "transaction_signature"
            else safe_tool_value(item, depth=depth + 1)
            for key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [
            safe_tool_value(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_LENGTH:
            return value
        return f"{value[:_MAX_STRING_LENGTH]}… [truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_STRING_LENGTH]


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _agent_message_text(value: Any) -> str:
    safe_value = safe_tool_value(value)
    if isinstance(safe_value, str):
        return safe_value
    if isinstance(safe_value, dict):
        for key in ("request", "result", "response", "output"):
            candidate = safe_value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return json.dumps(safe_value, ensure_ascii=False, indent=2)


def _linkify_bare_urls(text: str) -> str:
    """Keep named Markdown links and convert every remaining HTTP(S) URL."""
    def linkify_segment(segment: str) -> str:
        def replace_url(match: re.Match[str]) -> str:
            raw_url = match.group(0)
            url = raw_url.rstrip(".,;:!?")
            suffix = raw_url[len(url) :]
            hostname = urlparse(url).netloc
            return f"[{hostname}]({url}){suffix}" if hostname else raw_url

        return _BARE_URL_PATTERN.sub(replace_url, segment)

    parts: list[str] = []
    cursor = 0
    for match in _MARKDOWN_LINK_PATTERN.finditer(text):
        parts.append(linkify_segment(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(linkify_segment(text[cursor:]))
    return "".join(parts)


def _receipt_values(message: str) -> dict[str, str] | None:
    """Extract the complete seller receipt whether it arrived as lines or one line."""
    label_pattern = "|".join(re.escape(label) for _, label in _RECEIPT_LABELS)
    matches = list(re.finditer(rf"({label_pattern})\s*:\s*", message))
    if len(matches) != len(_RECEIPT_LABELS):
        return None

    labels = {label: key for key, label in _RECEIPT_LABELS}
    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(message)
        value = message[match.end() : next_start]
        value = re.sub(r"\s*\[[^\]]+\]\(https?://[^\s)]+\)\s*$", "", value)
        value = value.strip().rstrip("-* ").strip().strip("`")
        if not value or match.group(1) not in labels:
            return None
        key = labels[match.group(1)]
        if key in values:
            return None
        values[key] = value
    return values if len(values) == len(_RECEIPT_LABELS) else None


def _receipt_markdown(message: str) -> str:
    """Normalize only a complete receipt; all other seller text remains untouched."""
    values = _receipt_values(message)
    if values is None:
        return message

    lines = ["## 구매 완료", ""]
    code_keys = {"asset_id", "license_id", "transaction_signature"}
    for key, label in _RECEIPT_LABELS:
        value = values[key]
        formatted_value = f"`{value}`" if key in code_keys else value
        lines.append(f"- **{label}**: {formatted_value}")
    download_match = re.search(r"\[[^\]]+\]\((https?://[^\s)]+)\)", message)
    if download_match:
        lines.extend(("", f"[원본 다운로드]({download_match.group(1)})"))
    return "\n".join(lines)


def _seller_message_text(value: Any) -> str:
    message = _agent_message_text(value)
    rendered = _receipt_markdown(message) if isinstance(value, str) else message
    return _linkify_bare_urls(rendered)


def _delivery_payload(value: Any) -> dict[str, str] | None:
    """Accept only the seller's complete, display-safe fulfillment contract."""
    safe_value = safe_tool_value(value)
    if isinstance(safe_value, str):
        try:
            safe_value = json.loads(safe_value)
        except json.JSONDecodeError:
            fenced_json = safe_value.strip()
            if fenced_json.startswith("```json") and fenced_json.endswith("```"):
                try:
                    safe_value = json.loads(
                        fenced_json.removeprefix("```json").removesuffix("```").strip()
                    )
                except json.JSONDecodeError:
                    return None
            else:
                return _markdown_delivery_payload(safe_value)
    if not isinstance(safe_value, dict):
        return None
    candidate = safe_value.get("delivery", safe_value)
    if not isinstance(candidate, dict):
        return None

    required = (
        "asset_id",
        "asset_title",
        "license_id",
        "transaction_signature",
        "amount_usdc",
        "currency",
        "network_fee_usdc",
        "fee_sponsor",
        "download_url",
    )
    if any(not isinstance(candidate.get(key), str) for key in required):
        return None
    try:
        UUID(candidate["asset_id"])
        UUID(candidate["license_id"])
    except (ValueError, TypeError):
        return None
    parsed_url = urlparse(candidate["download_url"])
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return None
    if candidate["currency"] != "USDC" or candidate["network_fee_usdc"] != "0":
        return None

    return {
        key: candidate[key]
        for key in (*required, "download_expires_at")
        if isinstance(candidate.get(key), str)
    }


def _markdown_delivery_payload(message: str) -> dict[str, str] | None:
    """Extract a complete receipt only from the Seller's labelled Markdown contract."""
    values = _receipt_values(message)
    if values is None:
        return None

    download_match = re.search(r"\[[^\]]+\]\((https?://[^\s)]+)\)", message)
    fee_match = re.fullmatch(
        r"`?0\s+USDC`?\s*·\s*VeriProof 부담",
        values["network_fee"],
    )
    amount_match = re.fullmatch(r"(.+?)\s+(USDC)", values["amount_usdc"])
    if not download_match or not fee_match or not amount_match:
        return None

    return {
        "asset_id": values["asset_id"],
        "asset_title": values["asset_title"],
        "amount_usdc": amount_match.group(1),
        "currency": amount_match.group(2),
        "license_id": values["license_id"],
        "transaction_signature": values["transaction_signature"],
        "network_fee_usdc": "0",
        "fee_sponsor": "VeriProof",
        "download_url": download_match.group(1),
        "download_expires_at": values["download_expires_at"],
    }


def _event_payloads(event) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for call in event.get_function_calls():
        if call.name == "veriproof_seller_agent":
            payloads.append(
                {
                    "type": "agent_message",
                    "from": "buyer_agent",
                    "to": "seller_agent",
                    "protocol": "A2A",
                    "call_id": call.id,
                    "text": _linkify_bare_urls(_agent_message_text(call.args or {})),
                }
            )
        payloads.append(
            {
                "type": "tool_call",
                "tool": call.name or "unknown_tool",
                "call_id": call.id,
                "input": safe_tool_value(call.args or {}),
            }
        )
    for response in event.get_function_responses():
        output = safe_tool_value(response.response)
        payloads.append(
            {
                "type": "tool_result",
                "tool": response.name or "unknown_tool",
                "call_id": response.id,
                "output": output,
            }
        )
        if response.name == "veriproof_seller_agent":
            delivery = _delivery_payload(output)
            payloads.append(
                {
                    "type": "agent_message",
                    "from": "seller_agent",
                    "to": "buyer_agent",
                    "protocol": "A2A",
                    "call_id": response.id,
                    "text": _seller_message_text(output),
                    "delivery": delivery,
                }
            )
            if delivery is not None:
                payloads.append({"type": "license_delivery", "delivery": delivery})
        if isinstance(output, dict) and output.get("status") == "approval_required":
            payloads.append(
                {
                    "type": "payment_approval_required",
                    "asset_id": output.get("asset_id"),
                    "payment_method": output.get("payment_method"),
                }
            )

    if event.author == root_agent.name and event.is_final_response():
        texts = [
            part.text
            for part in (event.content.parts if event.content else [])
            if part.text and not part.thought
        ]
        if texts:
            payloads.append(
                {
                    "type": "assistant_message",
                    "text": _linkify_bare_urls("\n".join(texts)),
                }
            )
    return payloads


async def _ensure_session(session_id: str) -> None:
    user_id = f"demo:{session_id}"
    existing = await _session_service.get_session(
        app_name=_APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if existing is None:
        await _session_service.create_session(
            app_name=_APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )


async def _stream_agent(
    message: str,
    session_id: str,
    state_delta: dict[str, Any],
) -> AsyncIterator[str]:
    user_id = f"demo:{session_id}"
    yield _sse({"type": "session", "session_id": session_id})
    yield _sse({"type": "status", "status": "working"})
    try:
        async for event in _runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user", parts=[types.Part(text=message)]
            ),
            state_delta=state_delta,
        ):
            for payload in _event_payloads(event):
                yield _sse(payload)
        yield _sse({"type": "status", "status": "completed"})
    except Exception:
        logger.exception("Buyer Agent demo invocation failed")
        yield _sse(
            {
                "type": "error",
                "message": "Buyer Agent could not complete this request.",
            }
        )


async def stream_demo_chat(request: Request):
    """Validate a chat turn and stream observable ADK execution events."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, str) or not message.strip():
        return JSONResponse({"error": "message is required."}, status_code=400)
    message = message.strip()
    if len(message) > _MAX_MESSAGE_LENGTH:
        return JSONResponse(
            {"error": f"message must be {_MAX_MESSAGE_LENGTH} characters or fewer."},
            status_code=400,
        )

    requested_session_id = payload.get("session_id")
    if requested_session_id:
        try:
            session_id = str(UUID(str(requested_session_id)))
        except (TypeError, ValueError, AttributeError):
            return JSONResponse({"error": "session_id must be a UUID."}, status_code=400)
    else:
        session_id = str(uuid4())

    await _ensure_session(session_id)
    payment_mode = payload.get("payment_mode", PAYMENT_MODE_AUTONOMOUS)
    if payment_mode not in PAYMENT_MODES:
        return JSONResponse({"error": "Invalid payment_mode."}, status_code=400)

    state_delta: dict[str, Any] = {
        PAYMENT_MODE_STATE_KEY: payment_mode,
        PAYMENT_MODE_INSTRUCTION_STATE_KEY: payment_mode,
    }
    payment_decision = payload.get("payment_decision")
    if payment_decision is not None:
        if payment_mode != PAYMENT_MODE_APPROVAL:
            return JSONResponse(
                {"error": "Payment decisions require approval mode."},
                status_code=400,
            )
        if not isinstance(payment_decision, dict):
            return JSONResponse(
                {"error": "Invalid payment_decision."}, status_code=400
            )
        decision = payment_decision.get("decision")
        payment_method = payment_decision.get("payment_method")
        try:
            asset_id = str(UUID(str(payment_decision.get("asset_id"))))
        except (TypeError, ValueError, AttributeError):
            return JSONResponse(
                {"error": "Payment decision asset_id must be a UUID."},
                status_code=400,
            )
        if decision not in {"approved", "declined"} or payment_method not in {
            "USDC_X402",
            "SOL_NATIVE",
        }:
            return JSONResponse(
                {"error": "Invalid payment decision."}, status_code=400
            )

        session = await _session_service.get_session(
            app_name=_APP_NAME,
            user_id=f"demo:{session_id}",
            session_id=session_id,
        )
        pending = session.state.get(PAYMENT_APPROVAL_STATE_KEY, {})
        if not (
            isinstance(pending, dict)
            and pending.get("decision") == "pending"
            and pending.get("asset_id") == asset_id
            and pending.get("payment_method") == payment_method
        ):
            return JSONResponse(
                {"error": "No matching payment approval is pending."},
                status_code=409,
            )

        state_delta[PAYMENT_APPROVAL_STATE_KEY] = {
            "asset_id": asset_id,
            "payment_method": payment_method,
            "decision": decision,
        }
        if decision == "approved":
            message = (
                f"The user approved the pending {payment_method} purchase for "
                f"asset {asset_id}. Continue that exact purchase now."
            )
        else:
            message = (
                f"The user declined the pending {payment_method} purchase for "
                f"asset {asset_id}. Stop the purchase and confirm that no "
                "payment was executed."
            )

    return StreamingResponse(
        _stream_agent(message, session_id, state_delta),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
