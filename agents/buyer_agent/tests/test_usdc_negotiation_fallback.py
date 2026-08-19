"""Bounded list-price fallback for sponsor-paid USDC negotiation."""

import asyncio

from agents.buyer_agent import tools


def test_rejection_retries_once_at_the_published_usdc_list_price(monkeypatch):
    responses = iter(
        [
            {"http_status": 200, "body": {"status": "REJECT", "currency": "USDC"}},
            {
                "http_status": 200,
                "body": {
                    "status": "ACCEPT",
                    "currency": "USDC",
                    "price_usdc": "1.000000",
                    "session_id": "12345678-1234-1234-1234-123456789012",
                },
            },
        ]
    )
    offers = []

    async def negotiate(asset_id, buyer_agent_id, offer_usdc, usage_type, tool_context):
        offers.append(offer_usdc)
        return next(responses)

    async def payment_terms(asset_id, session_id="", tool_context=None):
        assert session_id == ""
        assert tool_context is None
        return {
            "status": "payment_required",
            "payment_required": {
                "accepts": [{"asset": "test-usdc-mint", "amount": "1000000"}],
            },
        }

    monkeypatch.setenv("USDC_MINT_ADDRESS", "test-usdc-mint")
    monkeypatch.setattr(tools, "negotiate_usdc_license", negotiate)
    monkeypatch.setattr(tools, "get_x402_payment_terms", payment_terms)

    result = asyncio.run(
        tools.negotiate_usdc_with_list_price_fallback(
            "12345678-1234-1234-1234-123456789012",
            "buyer-1",
            0.9,
        )
    )

    assert offers == [0.9, 1.0]
    assert result["body"]["status"] == "ACCEPT"
    assert [attempt["offer_usdc"] for attempt in result["attempts"]] == ["0.9", "1.0"]
