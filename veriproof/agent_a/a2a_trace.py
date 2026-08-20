"""Transport the Seller's real ADK tool activity over its A2A response."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.a2a import _compat
from google.adk.a2a.converters.from_adk_event import convert_event_to_a2a_events

SELLER_TOOL_TRACE_METADATA_KEY = "veriproof.seller_tool_trace"
SELLER_TOOL_TRACE_STATE_KEY = "temp:veriproof.seller_tool_trace"

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "private",
    "secret",
    "signature",
    "token",
)
_MAX_COLLECTION_ITEMS = 30
_MAX_STRING_LENGTH = 4_000


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def safe_trace_value(value: Any, *, depth: int = 0) -> Any:
    """Make a bounded, JSON-safe public trace without credentials."""
    if depth > 8:
        return "[truncated]"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _is_sensitive_key(key)
            else safe_trace_value(item, depth=depth + 1)
            for key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [
            safe_trace_value(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_LENGTH:
            return value
        return f"{value[:_MAX_STRING_LENGTH]}… [truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_STRING_LENGTH]


def _reason(args: object) -> str | None:
    if not isinstance(args, dict) or not isinstance(args.get("execution_reason"), str):
        return None
    value = " ".join(args["execution_reason"].split())
    return value[:160] if value else None


def _input(args: object) -> Any:
    value = safe_trace_value(args)
    if isinstance(value, dict):
        value.pop("execution_reason", None)
    return value


class SellerToolTrace:
    """Correlates ADK function calls and responses for one A2A task."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add_event(self, event: Any) -> None:
        for call in event.get_function_calls():
            args = call.args or {}
            self.records.append(
                {
                    "type": "tool_call",
                    "tool": call.name or "unknown_tool",
                    "call_id": call.id,
                    "reason": _reason(args),
                    "input": _input(args),
                    "status": "called",
                }
            )
        for response in event.get_function_responses():
            self.records.append(
                {
                    "type": "tool_result",
                    "tool": response.name or "unknown_tool",
                    "call_id": response.id,
                    "output": safe_trace_value(response.response),
                    "status": "completed",
                }
            )


def _event_metadata(event: Any) -> dict[str, Any]:
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    if metadata is None:
        return {}
    try:
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(metadata, preserving_proto_field_name=True)
    except (TypeError, ValueError):
        return {}


def seller_trace_event_converter() -> Callable[..., list[Any]]:
    """Create an ADK event converter that adds trace to Seller output events."""
    traces: dict[str, SellerToolTrace] = {}
    artifacts_by_task: dict[str, dict[str, str]] = {}

    def convert(
        event: Any,
        _invocation_context: Any,
        task_id: str | None,
        context_id: str | None,
        part_converter: Any,
    ) -> list[Any]:
        trace_key = task_id or event.invocation_id
        trace = traces.setdefault(trace_key, SellerToolTrace())
        trace.add_event(event)
        a2a_events = convert_event_to_a2a_events(
            event,
            artifacts_by_task.setdefault(trace_key, {}),
            task_id,
            context_id,
            part_converter,
        )
        if trace.records:
            for a2a_event in a2a_events:
                target = getattr(a2a_event, "artifact", None)
                if target is None:
                    status = getattr(a2a_event, "status", None)
                    target = getattr(status, "message", None)
                if target is None:
                    continue
                metadata = _event_metadata(target)
                metadata[SELLER_TOOL_TRACE_METADATA_KEY] = trace.records
                _compat.set_struct_metadata(target, metadata)
        if event.is_final_response():
            traces.pop(trace_key, None)
            artifacts_by_task.pop(trace_key, None)
        return a2a_events

    return convert
