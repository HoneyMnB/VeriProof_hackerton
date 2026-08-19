"""Buyer Agent demo UI and A2A route integration tests."""

import json

from starlette.testclient import TestClient

from agents.buyer_agent.app import application
from agents.buyer_agent.demo import (
    _agent_message_text,
    _usdc_negotiation_attempts,
    _usdc_negotiation_result,
    _delivery_payload,
    _linkify_bare_urls,
    _seller_message_text,
    safe_tool_value,
)


def test_usdc_negotiation_result_projects_only_the_actual_seller_outcome():
    assert _usdc_negotiation_result(
        {
            "http_status": 200,
            "body": {
                "status": "COUNTER_OFFER",
                "currency": "USDC",
                "price_usdc": "0.500000",
                "reason": "minimum price is 0.500000 USDC",
            },
        }
    ) == {
        "status": "COUNTER_OFFER",
        "price_usdc": "0.500000",
        "reason": "minimum price is 0.500000 USDC",
    }


def test_usdc_negotiation_result_hides_the_internal_round_cap_reason():
    assert _usdc_negotiation_result(
        {
            "http_status": 200,
            "body": {
                "status": "REJECT",
                "currency": "USDC",
                "reason": "max rounds exceeded",
            },
        }
    ) == {"status": "REJECT"}


def test_usdc_negotiation_result_hides_a_reason_with_the_wrong_currency():
    assert _usdc_negotiation_result(
        {
            "http_status": 200,
            "body": {
                "status": "ACCEPT",
                "currency": "USDC",
                "price_usdc": "1.000000",
                "reason": "최소 가격 1.0 SOL을 충족하여 수락합니다.",
            },
        }
    ) == {"status": "ACCEPT", "price_usdc": "1.000000"}


def test_usdc_negotiation_attempts_include_the_real_list_price_fallback():
    assert _usdc_negotiation_attempts(
        {
            "attempts": [
                {
                    "offer_usdc": "0.9",
                    "result": {"body": {"status": "REJECT", "currency": "USDC"}},
                },
                {
                    "offer_usdc": "1.0",
                    "result": {
                        "body": {
                            "status": "ACCEPT",
                            "currency": "USDC",
                            "price_usdc": "1.000000",
                        }
                    },
                },
            ]
        }
    ) == [
        {"offer_usdc": "0.9", "outcome": {"status": "REJECT"}},
        {
            "offer_usdc": "1.0",
            "outcome": {"status": "ACCEPT", "price_usdc": "1.000000"},
        },
    ]


def test_demo_ui_is_served_without_replacing_a2a_routes():
    with TestClient(application) as client:
        demo = client.get("/demo/")
        stylesheet = client.get("/demo/assets/buyer-demo.css")
        agent_card = client.get("/.well-known/agent-card.json")
        redirect = client.get("/demo", follow_redirects=False)
        invalid_message = client.post("/demo/api/chat", json={"message": ""})
        invalid_session = client.post(
            "/demo/api/chat",
            json={"message": "Find an image", "session_id": "not-a-uuid"},
        )

    assert demo.status_code == 200
    assert "VeriProof · Buyer Agent" in demo.text
    assert '<details class="execution" open>' in demo.text
    assert 'template id="agentMessageTemplate"' in demo.text
    assert 'class="execution-label">Excution</span>' in demo.text
    assert stylesheet.status_code == 200
    assert agent_card.status_code == 200
    assert agent_card.json()["name"] == "veriproof_buyer_agent"
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/demo/"
    assert invalid_message.status_code == 400
    assert invalid_session.status_code == 400


def test_tool_trace_redacts_sensitive_values_recursively():
    value = {
        "asset_id": "asset-1",
        "headers": {"PAYMENT-SIGNATURE": "signed-payment"},
        "private_key": "never-display",
    }

    assert safe_tool_value(value) == {
        "asset_id": "asset-1",
        "headers": {"PAYMENT-SIGNATURE": "[redacted]"},
        "private_key": "[redacted]",
    }


def test_agent_message_uses_the_actual_a2a_request_text():
    assert _agent_message_text({"request": "Find a verified sea image"}) == (
        "Find a verified sea image"
    )


def test_bare_urls_are_rendered_as_named_markdown_links():
    message = "Preview: https://seller.test/assets/1. Existing [guide](https://docs.test)."

    assert _linkify_bare_urls(message) == (
        "Preview: [seller.test](https://seller.test/assets/1). "
        "Existing [guide](https://docs.test)."
    )


def test_delivery_payload_accepts_only_the_complete_gasless_seller_contract():
    delivery = {
        "asset_id": "bc065056-df26-456f-aefc-8d12f743d3e3",
        "asset_title": "Character",
        "license_id": "a565a220-f028-4b42-8ab2-cee2c95a5db8",
        "transaction_signature": "public-chain-transaction",
        "amount_usdc": "2.500000",
        "currency": "USDC",
        "network_fee_usdc": "0",
        "fee_sponsor": "VeriProof",
        "download_url": "https://seller.test/files/persisted-token",
    }

    assert _delivery_payload({"delivery": delivery}) == delivery
    assert _delivery_payload(f"```json\n{json.dumps(delivery)}\n```") == delivery
    assert _delivery_payload({**delivery, "network_fee_usdc": "0.01"}) is None


def test_delivery_payload_extracts_the_seller_markdown_receipt_without_json():
    message = """## 구매 완료
[원본 다운로드](https://seller.test/files/persisted-token)

- 작품 ID: `bc065056-df26-456f-aefc-8d12f743d3e3`
- 작품명: Character
- 결제 금액: 2.500000 USDC
- 네트워크 수수료: 0 USDC · VeriProof 부담
- 라이선스 ID: a565a220-f028-4b42-8ab2-cee2c95a5db8
- 트랜잭션 서명: public-chain-transaction
- 다운로드 기한: 2026년 8월 25일
"""

    delivery = _delivery_payload(message)

    assert delivery is not None
    assert delivery["amount_usdc"] == "2.500000"
    assert delivery["network_fee_usdc"] == "0"
    assert delivery["download_url"] == "https://seller.test/files/persisted-token"


def test_seller_receipt_on_one_line_is_normalized_to_markdown():
    message = (
        "작품 ID: bc065056-df26-456f-aefc-8d12f743d3e3 작품명: Character "
        "결제 금액: 2.500000 USDC 네트워크 수수료: 0 USDC · VeriProof 부담 "
        "라이선스 ID: a565a220-f028-4b42-8ab2-cee2c95a5db8 "
        "트랜잭션 서명: public-chain-transaction 다운로드 기한: 2026년 8월 25일"
    )

    rendered = _seller_message_text(message)

    assert rendered.startswith("## 구매 완료\n\n- **작품 ID**:")
    assert "\n- **트랜잭션 서명**: `public-chain-transaction`" in rendered


def test_delivery_payload_extracts_a_complete_one_line_seller_receipt():
    message = (
        "작품 ID: bc065056-df26-456f-aefc-8d12f743d3e3 작품명: Character "
        "결제 금액: 2.500000 USDC 네트워크 수수료: 0 USDC · VeriProof 부담 "
        "라이선스 ID: a565a220-f028-4b42-8ab2-cee2c95a5db8 "
        "트랜잭션 서명: public-chain-transaction 다운로드 기한: 2026년 8월 25일 "
        "[원본 다운로드](https://seller.test/files/persisted-token)"
    )

    delivery = _delivery_payload(message)

    assert delivery is not None
    assert delivery["transaction_signature"] == "public-chain-transaction"
    assert delivery["download_url"] == "https://seller.test/files/persisted-token"
