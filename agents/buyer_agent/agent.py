"""Gemini/Vertex ADK 기반 구매자 에이전트 B 조정자."""

import os

from google.adk.agents import Agent

from .tools import (
    SellerAgentTool,
    build_seller_agent,
    get_x402_payment_terms,
    negotiate_license,
    purchase_x402_asset,
)

seller_agent_tool = SellerAgentTool(agent=build_seller_agent())

root_agent = Agent(
    name="veriproof_buyer_agent",
    model=os.environ.get("ADK_MODEL", "gemini-2.5-flash"),
    description=(
        "Buyer coordinator that discovers licensable works through remote "
        "A2A seller agents."
    ),
    instruction=(
        "You are the buyer-side coordinator and must retain control of the "
        "entire purchase workflow. Call veriproof_seller_agent only as a tool "
        "for marketplace discovery and published licensing facts. Send that "
        "tool only the discovery or licensing subtask, never a request to buy, "
        "pay, or deliver the original file. After it returns, continue the "
        "workflow yourself. Use get_x402_payment_terms and negotiate_license "
        "for payment terms. "
        "Never request, display, or store a private key in the conversation. "
        "After a license and final price are selected, call purchase_x402_asset "
        "to pay autonomously only within the configured delegated per-payment "
        "limit. If that tool rejects the payment, explain the policy or "
        "configuration reason and do not retry with changed terms. Do not claim "
        "that payment, settlement, or original-file delivery completed unless "
        "purchase_x402_asset returns status=purchased with a successful "
        "PAYMENT-RESPONSE."
    ),
    tools=[
        seller_agent_tool,
        get_x402_payment_terms,
        negotiate_license,
        purchase_x402_asset,
    ],
)
