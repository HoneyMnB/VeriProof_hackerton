"""Request-scoped server-side events for the observable demo stream."""

from __future__ import annotations

import asyncio
from typing import Any

_streams: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


def open_stream(stream_id: str) -> asyncio.Queue[dict[str, Any] | None]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _streams[stream_id] = queue
    return queue


def close_stream(stream_id: str) -> None:
    _streams.pop(stream_id, None)


def publish(stream_id: object, payload: dict[str, Any]) -> None:
    """Publish only to the in-process HTTP stream that owns this request."""
    if not isinstance(stream_id, str):
        return
    queue = _streams.get(stream_id)
    if queue is not None:
        queue.put_nowait(payload)
