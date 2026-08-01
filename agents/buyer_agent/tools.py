"""구매자 에이전트 B가 원격 A2A 상대를 구성할 때 사용하는 도우미."""

import os
import uuid
from urllib.parse import urlparse

import httpx
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_response_header,
)

from .payments import AutonomousPaymentError, AutonomousSolBuyer, AutonomousX402Buyer

_ACCEPTED_SESSION_STATE_KEY = "buyer:accepted_x402_sessions"


class SellerAgentTool(AgentTool):
    """원격 판매자 장애가 빈 검색 결과처럼 보이지 않게 한다."""

    async def run_async(
        self,
        *,
        args: dict,
        tool_context: ToolContext,
    ):
        result = await super().run_async(args=args, tool_context=tool_context)
        if result:
            return result
        return (
            "seller_agent_unavailable: the remote seller returned no usable "
            "A2A response. Verify SELLER_AGENT_CARD_URL and that the seller "
            "service is running."
        )


def get_seller_agent_card_url() -> str:
    """설정된 판매자 에이전트 A의 탐색 URL을 반환한다."""
    value = os.environ.get(
        "SELLER_AGENT_CARD_URL",
        "http://localhost:8000/.well-known/agent-card.json",
    ).strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("SELLER_AGENT_CARD_URL must be an absolute HTTP(S) URL")
    return value


def get_seller_api_base_url() -> str:
    """판매자 REST/x402 API의 공개 기준 URL을 반환한다."""
    explicit = os.environ.get("SELLER_API_BASE_URL", "").strip()
    if explicit:
        value = explicit.rstrip("/")
    else:
        card_url = get_seller_agent_card_url()
        suffix = "/.well-known/agent-card.json"
        value = card_url.removesuffix(suffix)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("SELLER_API_BASE_URL must be an absolute HTTP(S) URL")
    return value


def _asset_url(asset_id: str, suffix: str = "") -> str:
    """검증한 자산 UUID로 판매자 API URL을 안전하게 조립한다."""
    parsed_id = uuid.UUID(asset_id)
    return f"{get_seller_api_base_url()}/api/v1/ip/{parsed_id}{suffix}"


def _session_query(session_id: str) -> dict[str, str]:
    """선택적 협상 세션 UUID를 쿼리 파라미터로 변환한다."""
    if not session_id:
        return {}
    return {"session_id": str(uuid.UUID(session_id))}


def _remember_accepted_session(
    tool_context: ToolContext | None,
    asset_id: str,
    response_body: object,
) -> None:
    """Store an accepted negotiation only in the current ADK session."""
    if tool_context is None or not isinstance(response_body, dict):
        return
    if response_body.get("status") != "ACCEPT":
        return

    session_id = response_body.get("session_id")
    if not isinstance(session_id, str):
        return
    try:
        normalized_asset_id = str(uuid.UUID(asset_id))
        normalized_session_id = str(uuid.UUID(session_id))
    except ValueError:
        return

    stored_sessions = tool_context.state.get(_ACCEPTED_SESSION_STATE_KEY, {})
    sessions = dict(stored_sessions) if isinstance(stored_sessions, dict) else {}
    sessions[normalized_asset_id] = normalized_session_id
    tool_context.state[_ACCEPTED_SESSION_STATE_KEY] = sessions


def _clear_accepted_session(
    tool_context: ToolContext | None,
    asset_id: str,
) -> None:
    """Discard a prior agreement when a later round does not accept it."""
    if tool_context is None:
        return
    try:
        normalized_asset_id = str(uuid.UUID(asset_id))
    except ValueError:
        return
    stored_sessions = tool_context.state.get(_ACCEPTED_SESSION_STATE_KEY, {})
    if not isinstance(stored_sessions, dict):
        return
    sessions = dict(stored_sessions)
    if normalized_asset_id not in sessions:
        return
    del sessions[normalized_asset_id]
    tool_context.state[_ACCEPTED_SESSION_STATE_KEY] = sessions


def _resolve_session_id(
    asset_id: str,
    session_id: str,
    tool_context: ToolContext | None,
) -> str:
    """Prefer an explicit session; otherwise reuse this asset's accepted session."""
    if session_id:
        return str(uuid.UUID(session_id))
    if tool_context is None:
        return ""

    sessions = tool_context.state.get(_ACCEPTED_SESSION_STATE_KEY, {})
    if not isinstance(sessions, dict):
        return ""
    stored_session_id = sessions.get(str(uuid.UUID(asset_id)), "")
    if not isinstance(stored_session_id, str):
        return ""
    try:
        return str(uuid.UUID(stored_session_id))
    except ValueError:
        return ""


async def get_x402_payment_terms(
    asset_id: str,
    session_id: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """공식 x402 V2 결제 조건을 조회한다.

    Args:
        asset_id: 구매할 VeriProof 자산 UUID.
        session_id: 수락된 협상 가격을 사용할 때의 선택적 세션 UUID.
    """
    resolved_session_id = _resolve_session_id(asset_id, session_id, tool_context)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            _asset_url(asset_id),
            params=_session_query(resolved_session_id),
            headers={
                "Accept": "application/json",
                "X-Agent-Protocol": "x402",
            },
        )

    required_header = response.headers.get("PAYMENT-REQUIRED")
    if response.status_code != 402 or not required_header:
        return {
            "status": "unexpected_response",
            "http_status": response.status_code,
            "body": _response_body(response),
        }

    payment_required = decode_payment_required_header(required_header)
    return {
        "status": "payment_required",
        "http_status": 402,
        "payment_required": payment_required.model_dump(
            by_alias=True,
            exclude_none=True,
        ),
        "payment_required_header": required_header,
        "negotiation_endpoint": response.headers.get(
            "X-402-Negotiation-Endpoint"
        ),
    }


async def get_sol_payment_terms(asset_id: str) -> dict:
    """판매자가 직접 설정한 Devnet 네이티브 SOL 결제 조건을 조회한다.

    기존 USDC x402 조건을 환산하지 않는다. SOL 가격을 설정하지 않은 자산은
    결제 가능으로 표현하지 않는다.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            _asset_url(asset_id, "/agent-sol-payment"),
            headers={"Accept": "application/json"},
        )
    return {
        "http_status": response.status_code,
        "body": _response_body(response),
    }


async def negotiate_license(
    asset_id: str,
    buyer_agent_id: str,
    offer_sol: float,
    usage_type: str = "commercial",
    tool_context: ToolContext | None = None,
) -> dict:
    """판매자 Agent A의 라이선스 가격 협상 API를 호출한다.

    Args:
        asset_id: 협상할 VeriProof 자산 UUID.
        buyer_agent_id: 구매자 에이전트의 안정적인 식별자.
        offer_sol: 구매자가 제시하는 native SOL 금액.
        usage_type: commercial, non-commercial, editorial 중 사용 목적.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _asset_url(asset_id, "/negotiate"),
            headers={
                "Accept": "application/json",
                "X-Agent-Protocol": "x402",
            },
            json={
                "buyer_agent_id": buyer_agent_id,
                "offer_sol": offer_sol,
                "usage_type": usage_type,
            },
        )
    body = _response_body(response)
    if isinstance(body, dict) and body.get("status") == "ACCEPT":
        _remember_accepted_session(tool_context, asset_id, body)
    elif isinstance(body, dict) and body.get("status") in {
        "COUNTER_OFFER",
        "REJECT",
    }:
        _clear_accepted_session(tool_context, asset_id)
    return {
        "http_status": response.status_code,
        "body": body,
    }


async def purchase_x402_asset(
    asset_id: str,
    session_id: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """위임된 거래당 한도 안에서 자산을 자율 결제한다.

    개인키는 환경 변수에서 결제 서비스가 직접 읽으며 모델이나 도구 인자로
    전달되지 않는다. 판매자의 공식 PAYMENT-RESPONSE가 성공인 경우에만
    ``purchased`` 상태를 반환한다.

    Args:
        asset_id: 구매할 VeriProof 자산 UUID.
        session_id: 협상 가격을 적용할 수락 완료 세션 UUID. 생략하면 현재
            대화에서 이 자산에 대해 수락된 협상 세션을 사용한다.
    """
    try:
        buyer = AutonomousX402Buyer()
        resolved_session_id = _resolve_session_id(
            asset_id, session_id, tool_context
        )
        return await buyer.purchase(
            _asset_url(asset_id),
            params=_session_query(resolved_session_id),
        )
    except AutonomousPaymentError as exc:
        return {
            "status": "payment_rejected",
            "error": exc.code,
            "detail": str(exc),
        }


async def purchase_sol_asset(
    asset_id: str,
    session_id: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """판매자 설정 Devnet SOL 가격으로 자산을 자율 결제한다.

    비밀키는 환경에서만 읽는다. 서버가 거래의 수취인·lamports·memo를
    Devnet에서 확인하고 라이선스를 발급한 경우에만 ``purchased``를 반환한다.
    """
    try:
        resolved_session_id = _resolve_session_id(asset_id, session_id, tool_context)
        return await AutonomousSolBuyer().purchase(
            _asset_url(asset_id),
            params=_session_query(resolved_session_id),
        )
    except AutonomousPaymentError as exc:
        return {
            "status": "payment_rejected",
            "error": exc.code,
            "detail": str(exc),
        }


async def submit_x402_payment(
    asset_id: str,
    payment_signature: str,
    session_id: str = "",
) -> dict:
    """외부 지갑이 만든 PAYMENT-SIGNATURE로 동일 자산 GET을 재호출한다.

    이 도구는 개인키를 받거나 보관하지 않는다. 구매자가 결제를 명시적으로
    승인하고 외부 지갑이 공식 x402 페이로드를 서명한 뒤에만 호출해야 한다.

    Args:
        asset_id: 구매할 VeriProof 자산 UUID.
        payment_signature: 공식 x402 클라이언트가 생성한 Base64 PAYMENT-SIGNATURE.
        session_id: 협상 가격을 사용한 경우의 수락 완료 세션 UUID.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            _asset_url(asset_id),
            params=_session_query(session_id),
            headers={
                "Accept": "application/json",
                "X-Agent-Protocol": "x402",
                "PAYMENT-SIGNATURE": payment_signature,
            },
        )

    result = {
        "http_status": response.status_code,
        "body": _response_body(response),
    }
    payment_response = response.headers.get("PAYMENT-RESPONSE")
    if payment_response:
        decoded = decode_payment_response_header(payment_response)
        result["payment_response"] = decoded.model_dump(
            by_alias=True,
            exclude_none=True,
        )
        result["payment_response_header"] = payment_response
    elif response.status_code == 402:
        required = response.headers.get("PAYMENT-REQUIRED")
        if required:
            result["payment_required"] = decode_payment_required_header(
                required
            ).model_dump(by_alias=True, exclude_none=True)
    return result


def _response_body(response: httpx.Response):
    """JSON 응답은 객체로, 그 외 응답은 제한된 문자열로 반환한다."""
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def build_seller_agent() -> RemoteA2aAgent:
    """판매자 에이전트 A를 호출할 ADK 공식 원격 A2A 프록시를 생성한다."""
    return RemoteA2aAgent(
        name="veriproof_seller_agent",
        description=(
            "Remote VeriProof seller that discovers registered works and "
            "returns public native SOL licensing terms."
        ),
        agent_card=get_seller_agent_card_url(),
    )
