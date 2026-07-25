"""구매자 에이전트 B가 원격 A2A 상대를 구성할 때 사용하는 도우미."""

import os
from urllib.parse import urlparse

from google.adk.agents.remote_a2a_agent import RemoteA2aAgent


def get_seller_agent_card_url() -> str:
    """설정된 판매자 에이전트 A의 탐색 URL을 반환한다."""
    value = os.environ.get(
        "SELLER_AGENT_CARD_URL",
        "http://localhost:8000/.well-known/agent-card.json",
    ).strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("SELLER_AGENT_CARD_URL must be an absolute HTTP(S) URL")
    return value


def build_seller_agent() -> RemoteA2aAgent:
    """판매자 에이전트 A를 호출할 ADK 공식 원격 A2A 프록시를 생성한다."""
    return RemoteA2aAgent(
        name="veriproof_seller_agent",
        description=(
            "Remote VeriProof seller that discovers registered works and "
            "returns public USDC licensing terms."
        ),
        agent_card=get_seller_agent_card_url(),
    )
