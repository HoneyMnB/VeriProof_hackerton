"""Gemini/Vertex ADK 기반 구매자 에이전트 B 조정자."""

import os

from google.adk.agents import Agent

from .tools import build_seller_agent

seller_agent = build_seller_agent()

root_agent = Agent(
    name="veriproof_buyer_agent",
    model=os.environ.get("ADK_MODEL", "gemini-2.5-flash"),
    description=(
        "Buyer coordinator that discovers licensable works through remote "
        "A2A seller agents."
    ),
    instruction=(
        "Help the buyer find a suitable work. Delegate marketplace discovery "
        "and published licensing terms to veriproof_seller_agent through A2A. "
        "Do not claim that payment, settlement, or original-file delivery "
        "completed unless a real settlement response is supplied."
    ),
    sub_agents=[seller_agent],
)
