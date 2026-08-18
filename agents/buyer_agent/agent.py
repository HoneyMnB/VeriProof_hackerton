"""Gemini/Vertex ADK 기반 구매자 에이전트 B 조정자."""

import os

from google.adk.agents import Agent

from .tools import (
    SellerAgentTool,
    get_sol_payment_terms,
    build_seller_agent,
    get_x402_payment_terms,
    negotiate_license,
    purchase_sponsored_usdc_asset,
    purchase_x402_asset,
    purchase_sol_asset,
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
        "for marketplace discovery, published licensing facts, and post-settlement "
        "fulfillment. The current UI payment mode is {buyer_payment_mode?}. When it "
        "is autonomous (Agent buys), it is an explicit delegation to complete the "
        "purchase: for any request to find, license, or buy an asset, first ask the "
        "seller to find the single best matching public asset. Next, send a second "
        "A2A message naming that exact asset_id and ask the seller to verify its "
        "published license terms; use the verified returned asset_id for the "
        "sponsor-paid USDC purchase. This creates two real Buyer-to-Seller and two "
        "real Seller-to-Buyer messages before payment. After payment, send a third "
        "A2A fulfillment request, so the complete journey has at least six actual "
        "Buyer/Seller messages. In autonomous mode, never ask "
        "the user to choose an asset, approve payment, or reconfirm the purchase. "
        "After a purchase returns status=purchased, call the "
        "seller again with that exact asset_id and transaction so it can return the "
        "verified download link and receipt. Never ask the seller to initiate payment. "
        "For a direct USDC purchase without Phantom, use "
        "purchase_sponsored_usdc_asset after the seller returns a public asset. "
        "That tool only signs the server-issued canonical USDC transaction "
        "when its amount is within the delegated policy. Do not convert prices "
        "between currencies or invent a price. Keep the existing SOL and x402 "
        "tools only when the user explicitly requests those payment methods. "
        "Never request, display, or store a private key in the conversation. "
        "When negotiate_license returns ACCEPT, preserve its body.session_id; "
        "the legacy Buyer tools reuse it for that asset when it is omitted. "
        "For the sponsor-paid USDC buy-now path, call "
        "purchase_sponsored_usdc_asset only within the configured delegated "
        "per-payment limit. If a payment tool rejects the payment, explain the policy or "
        "configuration reason and do not retry with changed terms. Do not claim "
        "that payment, settlement, or original-file delivery completed "
        "unless its payment tool returns status=purchased. A purchase request is "
        "authorization only within the configured delegated policy; if the policy "
        "or configuration rejects it, stop and explain why. For an explicit "
        "USDC x402 purchase, require status=purchased and a successful "
        "PAYMENT-RESPONSE. If a purchase tool returns approval_required, stop "
        "and tell the user that approval is required; do not retry or switch "
        "payment methods. If it returns payment_declined, stop the purchase. "
        "Only retry the same pending purchase after an explicit user approval. "
        "For every HTTP(S) link in a response, use named Markdown in the exact "
        "format [name](URL); never emit a bare URL."
    ),
    tools=[
        seller_agent_tool,
        get_x402_payment_terms,
        get_sol_payment_terms,
        negotiate_license,
        purchase_x402_asset,
        purchase_sol_asset,
        purchase_sponsored_usdc_asset,
    ],
)
