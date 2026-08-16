"""Observable, same-origin chat stream for the Buyer Agent demo UI."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
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
            if _is_sensitive_key(key)
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
                    "text": _agent_message_text(call.args or {}),
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
            payloads.append(
                {
                    "type": "agent_message",
                    "from": "seller_agent",
                    "to": "buyer_agent",
                    "protocol": "A2A",
                    "text": _agent_message_text(output),
                }
            )
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
                {"type": "assistant_message", "text": "\n".join(texts)}
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

    state_delta: dict[str, Any] = {PAYMENT_MODE_STATE_KEY: payment_mode}
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
