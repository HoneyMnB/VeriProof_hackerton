"""구매자 에이전트 B가 원격 A2A 상대를 구성할 때 사용하는 도우미."""

import os
import uuid
from urllib.parse import urlparse

import httpx
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_response_header,
)

from .payments import AutonomousPaymentError, AutonomousX402Buyer


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


async def get_x402_payment_terms(
    asset_id: str,
    session_id: str = "",
) -> dict:
    """공식 x402 V2 결제 조건을 조회한다.

    Args:
        asset_id: 구매할 VeriProof 자산 UUID.
        session_id: 수락된 협상 가격을 사용할 때의 선택적 세션 UUID.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            _asset_url(asset_id),
            params=_session_query(session_id),
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


async def negotiate_license(
    asset_id: str,
    buyer_agent_id: str,
    offer_usdc: float,
    usage_type: str = "commercial",
) -> dict:
    """판매자 Agent A의 라이선스 가격 협상 API를 호출한다.

    Args:
        asset_id: 협상할 VeriProof 자산 UUID.
        buyer_agent_id: 구매자 에이전트의 안정적인 식별자.
        offer_usdc: 구매자가 제시하는 USDC 금액.
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
                "offer_usdc": offer_usdc,
                "usage_type": usage_type,
            },
        )
    return {
        "http_status": response.status_code,
        "body": _response_body(response),
    }


async def purchase_x402_asset(
    asset_id: str,
    session_id: str = "",
) -> dict:
    """위임된 거래당 한도 안에서 자산을 자율 결제한다.

    개인키는 환경 변수에서 결제 서비스가 직접 읽으며 모델이나 도구 인자로
    전달되지 않는다. 판매자의 공식 PAYMENT-RESPONSE가 성공인 경우에만
    ``purchased`` 상태를 반환한다.

    Args:
        asset_id: 구매할 VeriProof 자산 UUID.
        session_id: 협상 가격을 적용할 수락 완료 세션 UUID.
    """
    try:
        buyer = AutonomousX402Buyer()
        return await buyer.purchase(
            _asset_url(asset_id),
            params=_session_query(session_id),
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
            "returns public USDC licensing terms."
        ),
        agent_card=get_seller_agent_card_url(),
    )
