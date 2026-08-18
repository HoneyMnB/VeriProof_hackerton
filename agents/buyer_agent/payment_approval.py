"""Session-scoped payment approval gate for Buyer Agent tools."""

import uuid

from google.adk.tools.tool_context import ToolContext

PAYMENT_MODE_STATE_KEY = "buyer:payment_mode"
PAYMENT_MODE_INSTRUCTION_STATE_KEY = "buyer_payment_mode"
PAYMENT_APPROVAL_STATE_KEY = "buyer:payment_approval"
PAYMENT_MODE_AUTONOMOUS = "autonomous"
PAYMENT_MODE_APPROVAL = "approval"
PAYMENT_MODES = {PAYMENT_MODE_AUTONOMOUS, PAYMENT_MODE_APPROVAL}


def payment_approval_gate(
    tool_context: ToolContext | None,
    *,
    asset_id: str,
    payment_method: str,
) -> dict | None:
    """Allow a purchase or return a non-paying approval state response."""
    if tool_context is None:
        return None
    if tool_context.state.get(PAYMENT_MODE_STATE_KEY) != PAYMENT_MODE_APPROVAL:
        return None

    normalized_asset_id = str(uuid.UUID(asset_id))
    approval = tool_context.state.get(PAYMENT_APPROVAL_STATE_KEY, {})
    if not isinstance(approval, dict):
        approval = {}
    matches = (
        approval.get("asset_id") == normalized_asset_id
        and approval.get("payment_method") == payment_method
    )
    decision = approval.get("decision") if matches else None

    if decision == "approved":
        tool_context.state[PAYMENT_APPROVAL_STATE_KEY] = {
            "asset_id": normalized_asset_id,
            "payment_method": payment_method,
            "decision": "consumed",
        }
        return None

    if decision == "declined":
        tool_context.state[PAYMENT_APPROVAL_STATE_KEY] = {
            "asset_id": normalized_asset_id,
            "payment_method": payment_method,
            "decision": "consumed",
        }
        return {
            "status": "payment_declined",
            "asset_id": normalized_asset_id,
            "payment_method": payment_method,
        }

    pending = {
        "asset_id": normalized_asset_id,
        "payment_method": payment_method,
        "decision": "pending",
    }
    tool_context.state[PAYMENT_APPROVAL_STATE_KEY] = pending
    return {"status": "approval_required", **pending}
