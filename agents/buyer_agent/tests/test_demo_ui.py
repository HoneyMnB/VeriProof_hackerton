"""Buyer Agent demo UI and A2A route integration tests."""

from starlette.testclient import TestClient

from agents.buyer_agent.app import application
from agents.buyer_agent.demo import _agent_message_text, safe_tool_value


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
