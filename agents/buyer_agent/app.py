"""구매자 에이전트 B의 Cloud Run ASGI 진입점."""

import os
from pathlib import Path
from urllib.parse import urlparse

from google.adk.a2a.utils.agent_to_a2a import to_a2a
from starlette.responses import FileResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from .agent import root_agent
from .demo import stream_demo_chat


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
a2a_application = to_a2a(
    root_agent,
    protocol=protocol,
    host=host,
    port=port,
)

_ui_directory = Path(__file__).with_name("ui")


async def _demo_page(request):
    return FileResponse(_ui_directory / "index.html")


async def _demo_redirect(request):
    return RedirectResponse("/demo/", status_code=307)


a2a_application.add_route("/demo", _demo_redirect, methods=["GET"])
a2a_application.add_route("/demo/", _demo_page, methods=["GET"])
a2a_application.add_route(
    "/demo/api/chat", stream_demo_chat, methods=["POST"]
)
a2a_application.mount(
    "/demo/assets",
    app=StaticFiles(directory=_ui_directory / "assets"),
    name="buyer-demo-assets",
)
application = a2a_application
