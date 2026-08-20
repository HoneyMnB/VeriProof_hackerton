"""구매자 에이전트 B가 원격 A2A 상대를 구성할 때 사용하는 도우미."""

import os
import uuid
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.a2a import _compat
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_response_header,
)

from .payments import (
    AutonomousPaymentError,
    AutonomousSolBuyer,
    AutonomousSponsoredUsdcBuyer,
    AutonomousX402Buyer,
)
from .payment_approval import payment_approval_gate
from .live_events import publish

_ACCEPTED_SESSION_STATE_KEY = "buyer:accepted_x402_sessions"
# This is a wire-contract key, deliberately local to the Buyer deployment.
# The Seller service is independently deployable and must not be imported here.
SELLER_TOOL_TRACE_METADATA_KEY = "veriproof.seller_tool_trace"
SELLER_TOOL_TRACE_STATE_KEY = "temp:veriproof.seller_tool_trace"
_DEMO_STREAM_STATE_KEY = "temp:veriproof.demo_stream_id"
_SELLER_CALL_STATE_KEY = "temp:veriproof.seller_call_id"
_SELLER_TRACE_DISPATCH: ContextVar[dict[str, object] | None] = ContextVar(
    "seller_trace_dispatch", default=None
)


def _metadata_trace(metadata: object) -> list[dict] | None:
    try:
        trace = _compat.metadata_get(metadata, SELLER_TOOL_TRACE_METADATA_KEY)
    except (NotImplementedError, TypeError, ValueError):
        try:
            from google.protobuf.json_format import MessageToDict

            trace = MessageToDict(metadata).get(SELLER_TOOL_TRACE_METADATA_KEY)
        except (TypeError, ValueError):
            return None
    if not isinstance(trace, list) and trace is not None:
        try:
            from google.protobuf.json_format import MessageToDict

            trace = MessageToDict(trace)
        except (TypeError, ValueError):
            return None
    if not isinstance(trace, list) or not all(
        isinstance(item, dict) for item in trace
    ):
        return None
    return trace


def _response_trace(response: object) -> list[dict] | None:
    """Read a trace from legacy and current A2A client response wrappers."""
    candidates: list[object] = []
    visited: set[int] = set()

    def collect(value: object) -> None:
        if value is None or id(value) in visited:
            return
        visited.add(id(value))
        if isinstance(value, tuple):
            for item in value:
                collect(item)
            return

        candidates.append(getattr(value, "metadata", None))
        for artifact in getattr(value, "artifacts", []) or []:
            collect(artifact)
        collect(getattr(value, "artifact", None))
        status = getattr(value, "status", None)
        collect(status)
        collect(getattr(status, "message", None))

        # A2A SDK versions wrap normalized updates differently. Inspect the
        # wrapper's public task/update/result objects rather than assuming the
        # legacy ``(task, update)`` tuple shape.
        for attribute in ("task", "update", "result", "event"):
            collect(getattr(value, attribute, None))

    collect(response)
    for metadata in candidates:
        trace = _metadata_trace(metadata)
        if trace is not None:
            return trace
    return None


class TracePreservingRemoteA2aAgent(RemoteA2aAgent):
    """Expose only the Seller's namespaced public trace to its AgentTool."""

    async def _handle_a2a_response(self, a2a_response, ctx):
        event = await super()._handle_a2a_response(a2a_response, ctx)
        return self._with_seller_trace(event, a2a_response, ctx)

    async def _handle_a2a_response_v2(self, a2a_response, ctx):
        event = await super()._handle_a2a_response_v2(a2a_response, ctx)
        return self._with_seller_trace(event, a2a_response, ctx)

    @staticmethod
    def _with_seller_trace(event, response, ctx):
        trace = _response_trace(response)
        dispatch = _SELLER_TRACE_DISPATCH.get()
        if trace is not None:
            should_publish = True
            if dispatch is not None:
                should_publish = dispatch.get("published_trace") != trace
                dispatch["trace"] = trace
                dispatch["published_trace"] = trace
            stream_id = (
                dispatch.get("stream_id")
                if dispatch is not None
                else ctx.session.state.get(_DEMO_STREAM_STATE_KEY)
            )
            call_id = (
                dispatch.get("call_id")
                if dispatch is not None
                else ctx.session.state.get(_SELLER_CALL_STATE_KEY)
            )
            # Some valid A2A streaming updates do not become ADK events. The
            # trace still belongs to the active demo stream and must be shown
            # immediately instead of waiting for a final response event.
            if should_publish:
                publish(
                    stream_id,
                    {
                        "type": "seller_execution",
                        "call_id": call_id,
                        "execution": trace,
                    },
                )
        if event is not None and trace is not None:
            event.actions.state_delta[SELLER_TOOL_TRACE_STATE_KEY] = trace
        return event


class SellerAgentTool(AgentTool):
    """원격 카탈로그 장애가 빈 검색 결과처럼 보이지 않게 한다."""

    def _get_declaration(self) -> types.FunctionDeclaration:
        """Expose the public rationale without adding it to the A2A request."""
        declaration = super()._get_declaration()
        schema = declaration.parameters_json_schema
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if isinstance(properties, dict):
                properties["execution_reason"] = {
                    "type": "string",
                    "description": (
                        "Concise public display text in the format "
                        "'<current target> seller agent에게 요청'."
                    ),
                }
                properties["catalog_operation"] = {
                    "type": "string",
                    "description": (
                        "Use one of discovery, listing_verification, or fulfillment "
                        "to identify the public catalog operation for this A2A call."
                    ),
                }
        return declaration

    async def run_async(
        self,
        *,
        args: dict,
        tool_context: ToolContext,
    ):
        args = dict(args)
        args.pop("execution_reason", None)
        catalog_operation = args.pop("catalog_operation", None)
        if catalog_operation not in {
            "discovery",
            "listing_verification",
            "fulfillment",
        }:
            return (
                "catalog_operation_required: use discovery, "
                "listing_verification, or fulfillment for every catalog request."
            )
        if tool_context is not None:
            tool_context.state[SELLER_TOOL_TRACE_STATE_KEY] = []
            tool_context.state[_SELLER_CALL_STATE_KEY] = tool_context.function_call_id
        dispatch = {
            "stream_id": tool_context.state.get(_DEMO_STREAM_STATE_KEY)
            if tool_context is not None
            else None,
            "call_id": tool_context.function_call_id if tool_context is not None else None,
            "trace": [],
            "published_trace": None,
        }
        token = _SELLER_TRACE_DISPATCH.set(dispatch)
        try:
            result = await super().run_async(args=args, tool_context=tool_context)
        finally:
            _SELLER_TRACE_DISPATCH.reset(token)
        trace = dispatch["trace"]
        if isinstance(trace, list) and trace:
            return {"response": result, "seller_tool_trace": trace}
        if result:
            return result
        return (
            "catalog_agent_unavailable: the remote catalog returned no usable "
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
    execution_reason: str = "",
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


async def get_sol_payment_terms(
    asset_id: str,
    execution_reason: str = "",
) -> dict:
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
    execution_reason: str = "",
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


async def negotiate_usdc_license(
    asset_id: str,
    buyer_agent_id: str,
    offer_usdc: float,
    usage_type: str = "commercial",
    tool_context: ToolContext | None = None,
) -> dict:
    """Submit a USDC license offer for sponsor-paid USDC checkout.

    Args:
        asset_id: 협상할 VeriProof 자산 UUID.
        buyer_agent_id: 구매자 에이전트의 안정적인 식별자.
        offer_usdc: 구매자가 제시하는 USDC 금액.
        usage_type: commercial, non-commercial, editorial 중 사용 목적.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _asset_url(asset_id, "/negotiate"),
            headers={"Accept": "application/json", "X-Agent-Protocol": "x402"},
            json={
                "buyer_agent_id": buyer_agent_id,
                "offer_usdc": offer_usdc,
                "usage_type": usage_type,
            },
        )
    body = _response_body(response)
    if isinstance(body, dict) and body.get("status") == "ACCEPT":
        _remember_accepted_session(tool_context, asset_id, body)
    elif isinstance(body, dict) and body.get("status") in {"COUNTER_OFFER", "REJECT"}:
        _clear_accepted_session(tool_context, asset_id)
    return {"http_status": response.status_code, "body": body}


async def negotiate_usdc_with_list_price_fallback(
    asset_id: str,
    buyer_agent_id: str,
    opening_offer_usdc: float,
    usage_type: str = "commercial",
    execution_reason: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """할인 협상 후 거절되면 공개 원가를 한 번만 최종 제안한다.

    공개 원가는 Seller의 x402 결제 조건에서 읽으므로, 에이전트가 문장이나
    카탈로그를 보고 가격을 추정하지 않는다. 최종 원가 제안도 거절되면 실제
    Seller 응답을 그대로 반환하며 추가 반복하지 않는다.
    """
    attempts: list[dict] = []

    async def submit(offer_usdc: float) -> dict:
        result = await negotiate_usdc_license(
            asset_id,
            buyer_agent_id,
            offer_usdc,
            usage_type,
            tool_context,
        )
        attempts.append({"offer_usdc": str(offer_usdc), "result": result})
        return result

    result = await submit(opening_offer_usdc)
    body = result.get("body") if isinstance(result, dict) else None
    if isinstance(body, dict) and body.get("status") == "COUNTER_OFFER":
        counter = body.get("price_usdc")
        try:
            result = await submit(float(Decimal(str(counter))))
        except (InvalidOperation, TypeError, ValueError):
            return {
                "http_status": result.get("http_status", 502),
                "body": body,
                "attempts": attempts,
                "fallback_error": "seller_counter_price_invalid",
            }
        body = result.get("body") if isinstance(result, dict) else None

    if isinstance(body, dict) and body.get("status") == "REJECT":
        list_price = await _published_usdc_list_price(asset_id)
        if list_price is None:
            return {
                "http_status": result.get("http_status", 502),
                "body": body,
                "attempts": attempts,
                "fallback_error": "published_usdc_list_price_unavailable",
            }
        result = await submit(float(list_price))

    return {**result, "attempts": attempts}


async def _published_usdc_list_price(asset_id: str) -> Decimal | None:
    """Read the exact non-negotiated USDC amount from the Seller x402 contract."""
    terms = await get_x402_payment_terms(asset_id, tool_context=None)
    if terms.get("status") != "payment_required":
        return None
    payment_required = terms.get("payment_required")
    if not isinstance(payment_required, dict):
        return None
    accepts = payment_required.get("accepts")
    if not isinstance(accepts, list):
        return None
    expected_mint = os.environ.get("USDC_MINT_ADDRESS", "").strip()
    for requirement in accepts:
        if not isinstance(requirement, dict) or requirement.get("asset") != expected_mint:
            continue
        try:
            atomic = Decimal(str(requirement["amount"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        amount = atomic / Decimal("1000000")
        if amount.is_finite() and amount > 0 and amount.as_tuple().exponent >= -6:
            return amount
    return None


async def purchase_x402_asset(
    asset_id: str,
    session_id: str = "",
    execution_reason: str = "",
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
        approval_result = payment_approval_gate(
            tool_context,
            asset_id=asset_id,
            payment_method="USDC_X402",
        )
        if approval_result is not None:
            return approval_result
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
    execution_reason: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """판매자 설정 Devnet SOL 가격으로 자산을 자율 결제한다.

    비밀키는 환경에서만 읽는다. 서버가 거래의 수취인·lamports·memo를
    Devnet에서 확인하고 라이선스를 발급한 경우에만 ``purchased``를 반환한다.
    """
    try:
        approval_result = payment_approval_gate(
            tool_context,
            asset_id=asset_id,
            payment_method="SOL_NATIVE",
        )
        if approval_result is not None:
            return approval_result
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


async def purchase_sponsored_usdc_asset(
    asset_id: str,
    session_id: str = "",
    execution_reason: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """Buyer KMS 지갑으로 sponsor-paid USDC 즉시 구매를 완료한다.

    Phantom 또는 대화 속 개인키를 사용하지 않는다. 서버가 고정한 canonical
    transaction이 Buyer KMS 지갑·USDC mint·거래당 한도와 일치할 때만 서명한다.

    Args:
        asset_id: 구매할 공개 자산 UUID.
    """
    try:
        return await AutonomousSponsoredUsdcBuyer().purchase(
            _asset_url(asset_id, "/agent-sponsored-usdc"),
            session_id=_resolve_session_id(asset_id, session_id, tool_context),
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
    """마켓플레이스 카탈로그 에이전트를 호출할 ADK 원격 A2A 프록시를 생성한다."""
    return TracePreservingRemoteA2aAgent(
        name="veriproof_seller_agent",
        description=(
            "Remote VeriProof marketplace catalog that discovers registered "
            "works, verifies published listing terms, and fulfills settled licenses."
        ),
        agent_card=get_seller_agent_card_url(),
    )
