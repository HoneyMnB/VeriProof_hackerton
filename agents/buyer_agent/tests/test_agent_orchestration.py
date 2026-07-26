"""구매자 에이전트가 판매자에게 제어권을 넘기지 않는지 검증한다."""

import asyncio

from google.adk.tools.agent_tool import AgentTool

from agents.buyer_agent.agent import root_agent
from agents.buyer_agent.tools import SellerAgentTool


def test_seller_agent_is_exposed_as_a_tool_without_transfer():
    """판매자는 원격 도구로만 호출하고 구매 흐름은 구매자가 계속 소유한다."""
    seller_tools = [
        tool
        for tool in root_agent.tools
        if isinstance(tool, AgentTool)
        and tool.name == "veriproof_seller_agent"
    ]

    assert len(seller_tools) == 1
    assert root_agent.sub_agents == []


def test_empty_seller_response_is_reported_as_unavailable(monkeypatch):
    """A2A 연결 오류를 자산 검색 결과 없음으로 오인하지 않는다."""

    async def return_empty_result(self, *, args, tool_context):
        return ""

    monkeypatch.setattr(AgentTool, "run_async", return_empty_result)
    seller_tool = next(
        tool
        for tool in root_agent.tools
        if isinstance(tool, SellerAgentTool)
    )

    result = asyncio.run(
        seller_tool.run_async(args={"request": "sea image"}, tool_context=None)
    )

    assert result.startswith("seller_agent_unavailable:")
