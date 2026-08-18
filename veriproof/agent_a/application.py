"""Django와 함께 구동되는 공식 A2A/ADK 애플리케이션."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a2a.server.routes import create_agent_card_routes
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from django.conf import settings
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from starlette.applications import Starlette
from starlette.routing import Mount

from .agent import root_agent


def build_agent_card() -> AgentCard:
    """에이전트 A의 공개 A2A 1.0 탐색 문서를 생성한다."""
    endpoint = f"{settings.A2A_PUBLIC_BASE_URL}/a2a/"
    return AgentCard(
        name="VeriProof Seller Agent",
        description="Discovers registered works and fulfills settled licenses.",
        supported_interfaces=[
            AgentInterface(
                url=endpoint,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="discover-licensable-assets",
                name="Discover licensable assets",
                description=(
                    "Search registered public works and inspect their licensing terms."
                ),
                tags=["marketplace", "licensing", "SOL", "images"],
                examples=["Find a sea image available for licensing under 10 SOL."],
            ),
            AgentSkill(
                id="fulfill-settled-license",
                name="Fulfill settled license",
                description=(
                    "Returns the persisted download link and gasless receipt facts "
                    "only after a matching license settlement."
                ),
                tags=["marketplace", "license", "fulfillment", "USDC"],
            ),
        ],
    )


def build_application(django_application: object) -> Starlette:
    """공식 Agent Card/A2A 경로와 Django ASGI 앱을 결합한다."""
    agent_card = build_agent_card()
    a2a_application = to_a2a(root_agent, agent_card=agent_card)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        # 마운트된 애플리케이션에는 lifespan 이벤트가 자동 전달되지 않는다.
        # ADK는 이 lifespan 구간에서 공식 A2A 경로를 등록한다.
        async with a2a_application.router.lifespan_context(a2a_application):
            yield

    return Starlette(
        routes=[
            *create_agent_card_routes(agent_card),
            Mount("/a2a", app=a2a_application),
            Mount("/", app=django_application),
        ],
        lifespan=lifespan,
    )
