"""Buyer Agent demo UI and A2A route integration tests."""

import json
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from agents.buyer_agent.app import application
from agents.buyer_agent.demo import (
    _agent_message_text,
    _event_payloads,
    _usdc_negotiation_attempts,
    _usdc_negotiation_result,
    _delivery_payload,
    _linkify_bare_urls,
    _seller_message_text,
    _seller_tool_trace,
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


def test_demo_negotiation_events_do_not_expose_internal_usage_type():
    event = SimpleNamespace(
        author="tool",
        get_function_calls=lambda: [
            SimpleNamespace(
                name="negotiate_usdc_license",
                id="offer-1",
                args={"offer_usdc": 2.5, "usage_type": "commercial"},
            )
        ],
        get_function_responses=lambda: [],
        is_final_response=lambda: False,
    )

    payloads = _event_payloads(event)

    assert payloads[-1] == {
        "type": "negotiation_offer",
        "call_id": "offer-1",
        "offer_usdc": "2.5",
    }


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
    assert 'class="execution-label">Execution</span>' in demo.text
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


def test_seller_message_uses_only_the_seller_internal_tool_trace():
    event = SimpleNamespace(
        author="tool",
        content=None,
        get_function_calls=lambda: [],
        get_function_responses=lambda: [
            SimpleNamespace(
                name="veriproof_seller_agent",
                id="seller-call",
                response={
                    "response": "검증된 작품을 찾았습니다.",
                    "seller_tool_trace": [
                        {
                            "type": "tool_call",
                            "tool": "search_licensable_assets",
                            "call_id": "seller-search",
                            "reason": "요청 조건의 공개 작품을 조회합니다.",
                            "input": {"query": "바다"},
                            "status": "called",
                        },
                        {
                            "type": "tool_result",
                            "tool": "search_licensable_assets",
                            "call_id": "seller-search",
                            "output": {"count": 1},
                            "status": "completed",
                        },
                    ],
                },
            )
        ],
        is_final_response=lambda: False,
    )

    tool_result, seller_message = _event_payloads(event)

    assert tool_result["output"] == {"response": "검증된 작품을 찾았습니다."}
    assert seller_message["text"] == "검증된 작품을 찾았습니다."
    assert seller_message["execution"][0]["tool"] == "search_licensable_assets"
    assert seller_message["execution"][0]["tool"] != "veriproof_seller_agent"


def test_seller_trace_ignores_untrusted_or_malformed_values():
    assert _seller_tool_trace({"seller_tool_trace": [{"tool": "search"}, "bad"]}) == []


def test_tool_trace_uses_only_the_llm_public_reason_and_hides_it_from_input():
    event = SimpleNamespace(
        author="tool",
        content=None,
        get_function_calls=lambda: [
            SimpleNamespace(
                name="get_x402_payment_terms",
                id="call-1",
                args={
                    "asset_id": "asset-1",
                    "execution_reason": "검증된 자산의 실제 x402 결제 조건을 확인해야 합니다.",
                },
            )
        ],
        get_function_responses=lambda: [],
        is_final_response=lambda: False,
    )

    payloads = _event_payloads(event)

    assert payloads == [
        {
            "type": "tool_call",
            "tool": "get_x402_payment_terms",
            "call_id": "call-1",
            "input": {"asset_id": "asset-1"},
            "reason": "검증된 자산의 실제 x402 결제 조건을 확인해야 합니다.",
        }
    ]


def test_seller_a2a_message_carries_the_same_llm_public_reason():
    event = SimpleNamespace(
        author="tool",
        content=None,
        get_function_calls=lambda: [
            SimpleNamespace(
                name="veriproof_seller_agent",
                id="call-a2a",
                args={
                    "request": "Find a verified sea image",
                    "execution_reason": "요청 조건에 맞는 검증된 자산을 판매자에게 조회해야 합니다.",
                },
            )
        ],
        get_function_responses=lambda: [],
        is_final_response=lambda: False,
    )

    message, tool_call = _event_payloads(event)

    assert message["type"] == "agent_message"
    assert message["tool"] == "veriproof_seller_agent"
    assert message["reason"] == tool_call["reason"]


def test_seller_a2a_message_carries_the_catalog_operation_for_demo_rendering():
    event = SimpleNamespace(
        author="tool",
        content=None,
        get_function_calls=lambda: [
            SimpleNamespace(
                name="veriproof_seller_agent",
                id="call-a2a-operation",
                args={
                    "request": "Verify the published terms for asset_id 123",
                    "catalog_operation": "listing_verification",
                },
            )
        ],
        get_function_responses=lambda: [],
        is_final_response=lambda: False,
    )

    message, tool_call = _event_payloads(event)

    assert message["catalog_operation"] == "listing_verification"
    assert tool_call["catalog_operation"] == "listing_verification"
    assert "catalog_operation" not in tool_call["input"]


def test_tool_trace_does_not_invent_a_reason_when_the_model_omits_it():
    event = SimpleNamespace(
        author="tool",
        content=None,
        get_function_calls=lambda: [
            SimpleNamespace(
                name="get_sol_payment_terms",
                id="call-2",
                args={"asset_id": "asset-2"},
            )
        ],
        get_function_responses=lambda: [],
        is_final_response=lambda: False,
    )

    assert _event_payloads(event)[0]["reason"] is None


def test_tool_trace_bounds_the_public_reason_to_a_short_ui_line():
    event = SimpleNamespace(
        author="tool",
        content=None,
        get_function_calls=lambda: [
            SimpleNamespace(
                name="get_sol_payment_terms",
                id="call-short-reason",
                args={"execution_reason": "가" * 120},
            )
        ],
        get_function_responses=lambda: [],
        is_final_response=lambda: False,
    )

    assert _event_payloads(event)[0]["reason"] == "가" * 40


def test_demo_script_keeps_buyer_and_seller_messages_with_buyer_tool_trace():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()

    seller_tool_handler = script.split(
        'if (event.tool === "veriproof_seller_agent")', 1
    )[1].split("addExecutionItem(turn", 1)[0]
    assert "addSellerActivity" not in script
    assert "addExecutionItem(buyerExchange" in seller_tool_handler
    assert "executionRoot(exchangeElement).hidden = true;" in script
    assert "sellerWaitingByCall" in script
    assert "function addSellerWaiting" in script
    assert 'title: "veriproof_seller_agent",' in script
    assert 'title: "veriproof_seller_agent 결과",' in script
    assert 'typing.setAttribute("aria-label", "Seller Agent is responding")' in script
    assert "function addSellerExecution" in script
    assert "function renderSellerExecution" in script
    assert 'executionRoot(container).hidden = true;' in script
    assert 'Seller Agent에 도구 호출을 완료했습니다.' not in script
    assert 'Trace unavailable' not in script
    assert 'if (list.children.length > 0)' in script
    assert 'case "seller_execution"' in script


def test_demo_script_labels_each_seller_operation():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()

    assert 'listing_verification: "선택 에셋 판매 조건 재검증"' in script
    assert 'fulfillment: "정산 완료 라이선스 전달 조회"' in script
    assert 'const SELLER_AGENT = "SELLER AGENT"' in script


def test_demo_script_keeps_execution_visible_on_the_buyer_request_message():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()
    request_preparation = script.split("if (!fromSeller)", 1)[1].split(
        "return exchangeElement", 1
    )[0]

    assert "executionRoot(exchangeElement).hidden = true;" not in request_preparation


def test_demo_script_hides_generic_lifecycle_events_from_execution():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()

    assert 'title: "Preparing request"' not in script
    assert 'title: "Request accepted"' not in script
    assert 'title: "Response completed"' not in script


def test_demo_script_places_the_actual_negotiation_tool_in_the_price_card():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()

    assert "const NEGOTIATION_TOOLS" in script
    assert "state.negotiationToolCallsByCall.set(event.call_id, event)" in script
    assert "state.negotiationToolResultsByCall.set(event.call_id, event)" in script
    assert "title: toolCall.tool," in script
    assert "function flushUnattachedNegotiationToolEvents" in script


def test_demo_script_preserves_the_parent_tool_trace_across_seller_calls():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()
    seller_tool_handler = script.split(
        'if (event.tool === "veriproof_seller_agent")', 1
    )[1].split("addExecutionItem(turn", 1)[0]

    assert "resetExecution" not in script
    assert "replaceChildren()" not in seller_tool_handler
    assert "setExecutionLabel(turn, event.tool)" not in script
    assert "setExecutionLabel(exchangeElement, event.tool)" not in script
    assert "setExecutionLabel(buyerExchange, event.tool)" not in script


def test_demo_script_does_not_force_the_viewport_to_scroll():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()

    assert "scrollTo" not in script
    assert "scrollIntoView" not in script


def test_demo_message_links_use_the_button_link_class():
    script = Path("agents/buyer_agent/ui/assets/buyer-demo.js").read_text()
    stylesheet = Path("agents/buyer_agent/ui/assets/buyer-demo.css").read_text()

    assert 'link.className = "message-link"' in script
    assert ".assistant-message a.message-link" in stylesheet
    assert ".exchange-message a.message-link" in stylesheet
    assert "a.message-link:focus-visible" in stylesheet


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
