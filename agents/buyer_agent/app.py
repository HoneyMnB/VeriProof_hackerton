"""구매자 에이전트 B의 Cloud Run ASGI 진입점."""

import os
from urllib.parse import urlparse

from google.adk.a2a.utils.agent_to_a2a import to_a2a

from .agent import root_agent


def _public_endpoint() -> tuple[str, str, int]:
    """공개 기준 URL을 A2A 서버 구성에 필요한 값으로 분해한다."""
    value = os.environ.get(
        "BUYER_AGENT_PUBLIC_BASE_URL", "http://localhost:8001"
    ).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("BUYER_AGENT_PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname, parsed.port or default_port


protocol, host, port = _public_endpoint()
application = to_a2a(
    root_agent,
    protocol=protocol,
    host=host,
    port=port,
)
