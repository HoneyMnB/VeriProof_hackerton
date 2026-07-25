"""마켓플레이스 판매자 에이전트의 ADK 정의."""

from django.conf import settings
from google.adk.agents import Agent

from .tools import get_licensable_asset, search_licensable_assets

root_agent = Agent(
    name="veriproof_seller_agent",
    model=settings.ADK_MODEL,
    description=(
        "VeriProof marketplace seller agent that discovers registered works "
        "and provides their public USDC licensing terms."
    ),
    instruction=(
        "You represent the VeriProof marketplace. Use the provided tools for "
        "all asset facts and prices. Never invent assets, prices, ownership, "
        "payment completion, or download access. Only return public watermarked "
        "previews. When the buyer chooses an asset, identify the asset and its "
        "published USDC terms; payment and settlement remain separate APIs."
    ),
    tools=[search_licensable_assets, get_licensable_asset],
)
