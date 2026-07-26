"""Gemini/Vertex ADK 기반 구매자 에이전트 B 조정자."""

import os

from google.adk.agents import Agent

from .tools import (
    build_seller_agent,
    get_x402_payment_terms,
    negotiate_license,
    purchase_x402_asset,
)

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
        "Use get_x402_payment_terms and negotiate_license for payment terms. "
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
        get_x402_payment_terms,
        negotiate_license,
        purchase_x402_asset,
    ],
    sub_agents=[seller_agent],
)
