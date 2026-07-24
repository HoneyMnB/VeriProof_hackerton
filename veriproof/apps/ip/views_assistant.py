"""HTTP adapter for the creator assistant module."""
from __future__ import annotations

import decimal
import json
import uuid

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods

from services.cashflow_service import CashflowValidationError, get_cashflow_service
from services.conversation_attachment_service import (
    ConversationAttachmentError,
    get_conversation_attachment_service,
)
from services.creator_assistant_service import (
    AssistantUnavailable,
    get_creator_assistant_service,
)
from services.gemini_service import get_gemini_service
from services.registration_draft_service import (
    DraftValidationError,
    get_registration_draft_service,
)
from services.subscription_service import (
    SubscriptionRequiredError,
    get_subscription_service,
)


def status(request: HttpRequest) -> JsonResponse:
    """자격증명 값은 노출하지 않고 Gemini 연결 가능 여부만 제공한다."""
    service = get_gemini_service()
    connection = service.connection_status()
    return JsonResponse(
        {
            "provider": "gemini",
            "model": service.assistant_model,
            **connection,
        }
    )


def overview(request: HttpRequest) -> JsonResponse:
    wallet = (request.GET.get("creator") or request.GET.get("wallet") or "").strip()
    if not wallet:
        return JsonResponse({"error": "creator_required"}, status=422)
    data = get_creator_assistant_service().overview(wallet)
    if data is None:
        return JsonResponse({"error": "creator_not_found"}, status=404)
    return JsonResponse(data)


def history(request: HttpRequest) -> JsonResponse:
    """저장된 대화 이력을 창작자 지갑 기준으로 조회한다."""
    wallet = (request.GET.get("creator") or request.GET.get("wallet") or "").strip()
    if not wallet:
        return JsonResponse({"error": "creator_required"}, status=422)
    raw_conversation_id = (request.GET.get("conversation") or "").strip()
    try:
        conversation_id = uuid.UUID(raw_conversation_id) if raw_conversation_id else None
    except ValueError:
        return JsonResponse({"error": "invalid_conversation"}, status=422)
    try:
        service = get_creator_assistant_service()
        items = service.history(wallet, conversation_id=conversation_id)
        conversations = service.conversation_summaries(wallet)
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)
    return JsonResponse({"items": items, "conversations": conversations})


@login_required
@require_http_methods(["GET"])
def conversation_search(request: HttpRequest) -> JsonResponse:
    """현재 로그인한 창작자의 전체 대화 제목을 DB에서 검색한다."""
    preference = getattr(request.user, "veriproof_preferences", None)
    wallet = (getattr(preference, "creator_wallet", "") or "").strip()
    if not wallet:
        return JsonResponse({"error": "creator_required"}, status=422)
    try:
        conversations = get_creator_assistant_service().conversation_summaries(
            wallet,
            limit=None,
            search=request.GET.get("q", ""),
        )
    except ValueError as exc:
        return JsonResponse({"error": "invalid_search", "detail": str(exc)}, status=422)
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)
    return JsonResponse({"conversations": conversations})


@login_required
@require_http_methods(["PATCH", "DELETE"])
def conversation(request: HttpRequest, conversation_id: uuid.UUID) -> JsonResponse:
    """로그인 계정의 지갑에 속한 대화만 이름 변경하거나 삭제한다."""
    preference = getattr(request.user, "veriproof_preferences", None)
    wallet = (getattr(preference, "creator_wallet", "") or "").strip()
    if not wallet:
        return JsonResponse({"error": "creator_required"}, status=422)
    service = get_creator_assistant_service()
    try:
        if request.method == "DELETE":
            service.delete_conversation(wallet, conversation_id)
            return JsonResponse({}, status=204)
        payload = json.loads(request.body or "{}")
        title = str(payload.get("title") or "")
        return JsonResponse(service.rename_conversation(wallet, conversation_id, title))
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json"}, status=400)
    except ValueError as exc:
        return JsonResponse({"error": "invalid_title", "detail": str(exc)}, status=422)
    except LookupError:
        return JsonResponse({"error": "conversation_not_found"}, status=404)


def sales(request: HttpRequest) -> JsonResponse:
    """판매자인 창작자가 실제 라이선스 판매 결과를 확인한다."""
    wallet = (request.GET.get("creator") or request.GET.get("wallet") or "").strip()
    if not wallet:
        return JsonResponse({"error": "creator_required"}, status=422)
    try:
        return JsonResponse(get_creator_assistant_service().sales(wallet))
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)


def actions(request: HttpRequest) -> JsonResponse:
    """대화가 유발한 변경의 검증 상태를 창작자에게 공개한다."""
    wallet = (request.GET.get("creator") or request.GET.get("wallet") or "").strip()
    if not wallet:
        return JsonResponse({"error": "creator_required"}, status=422)
    try:
        return JsonResponse({"items": get_creator_assistant_service().actions(wallet)})
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)


@csrf_exempt
def activate_subscription(request: HttpRequest) -> JsonResponse:
    """로컬 데모 구독 결제를 활성화한다."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    try:
        data = json.loads(request.body or b"{}")
        subscription = get_subscription_service().activate_mock_subscription(
            str(data.get("creator_wallet") or "").strip(),
            str(data.get("plan_code") or "").strip(),
            str(data.get("payment_tx_sig") or "").strip(),
        )
    except (ValueError, json.JSONDecodeError, SubscriptionRequiredError) as exc:
        return JsonResponse({"error": "invalid_subscription", "detail": str(exc)}, status=422)
    except LookupError:
        return JsonResponse({"error": "subscription_plan_not_found"}, status=404)
    return JsonResponse({"subscription_id": subscription.id, "status": subscription.status}, status=201)


def subscription_plans(request: HttpRequest) -> JsonResponse:
    """등록 권한을 포함한 활성 플랜만 반환한다."""
    from apps.ip.models import SubscriptionPlan

    return JsonResponse({"items": [
        {"code": plan.code, "name": plan.name, "monthly_fee_usdc": str(plan.monthly_fee_usdc), "included_registrations": plan.included_registrations}
        for plan in SubscriptionPlan.objects.filter(is_active=True).order_by("monthly_fee_usdc", "id")
    ]})


@csrf_exempt
def directives(request: HttpRequest) -> JsonResponse:
    """행동 지침을 조회하거나 창작자의 명시적 지침을 추가한다."""
    if request.method == "GET":
        wallet = (request.GET.get("creator") or request.GET.get("wallet") or "").strip()
        if not wallet:
            return JsonResponse({"error": "creator_required"}, status=422)
        try:
            return JsonResponse({"items": get_creator_assistant_service().directives(wallet)})
        except LookupError:
            return JsonResponse({"error": "creator_not_found"}, status=404)
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=422)
    try:
        directive = get_creator_assistant_service().add_directive(
            str(data.get("creator_wallet") or ""),
            str(data.get("title") or ""),
            str(data.get("instruction") or ""),
        )
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)
    except ValueError as exc:
        return JsonResponse({"error": "invalid_directive", "detail": str(exc)}, status=422)
    return JsonResponse(directive, status=201)


@csrf_exempt
def chat(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=422)
    wallet = str(data.get("creator_wallet") or "").strip()
    message = str(data.get("message") or "").strip()
    attachment_ids = data.get("attachment_ids") or []
    raw_conversation_id = str(data.get("conversation_id") or "").strip()
    if not isinstance(attachment_ids, list) or not all(isinstance(item, str) for item in attachment_ids):
        return JsonResponse({"error": "invalid_attachments"}, status=422)
    if not wallet or not message or len(message) > 2000:
        return JsonResponse({"error": "invalid_message"}, status=422)
    try:
        conversation_id = uuid.UUID(raw_conversation_id) if raw_conversation_id else None
    except ValueError:
        return JsonResponse({"error": "invalid_conversation"}, status=422)
    try:
        outcome = get_creator_assistant_service().ask(
            wallet, message, attachment_ids, conversation_id=conversation_id
        )
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)
    except ValueError as exc:
        return JsonResponse({"error": "invalid_attachments", "detail": str(exc)}, status=422)
    except AssistantUnavailable:
        return JsonResponse(
            {"error": "assistant_unavailable", "detail": "Gemini credentials are unavailable or the request failed."},
            status=503,
        )
    return JsonResponse(
        {
            "answer": outcome.answer,
            "action": outcome.action,
            "conversation_id": outcome.conversation_id,
        },
        status=200,
    )


@csrf_exempt
def conversation_attachment(request: HttpRequest) -> JsonResponse:
    """대화 첨부를 실제 저장·분석하고 분석 결과만 브라우저로 돌려준다."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    wallet = str(request.POST.get("creator_wallet") or "").strip()
    upload = request.FILES.get("file")
    if not wallet or upload is None:
        return JsonResponse({"error": "missing_attachment"}, status=422)
    try:
        attachment = get_conversation_attachment_service().attach(wallet, upload)
    except ConversationAttachmentError as exc:
        return JsonResponse({"error": exc.code, "detail": exc.detail}, status=exc.status)
    return JsonResponse(
        {
            "attachment_id": str(attachment.id),
            "file_name": attachment.file_name,
            "content_mime_type": attachment.content_mime_type,
            "analysis": attachment.analysis,
        },
        status=201,
    )


@csrf_exempt
def registration_drafts(request: HttpRequest, draft_id=None) -> JsonResponse:
    """대화형 등록 캔버스의 초안을 저장·확정한다. 실제 등록은 하지 않는다."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=422)
    wallet = str(payload.get("creator_wallet") or "").strip()
    service = get_registration_draft_service()
    try:
        if draft_id:
            result = service.confirm(wallet, str(draft_id))
        else:
            result = service.save(wallet, payload)
    except LookupError:
        return JsonResponse({"error": "draft_not_found"}, status=404)
    except DraftValidationError as exc:
        return JsonResponse({"error": "invalid_draft", "detail": str(exc)}, status=422)
    return JsonResponse(result, status=200 if draft_id else 201)


@csrf_exempt
def record_expense(request: HttpRequest) -> JsonResponse:
    """창작자가 입력한 실제 지출을 기록하고 조작된 요약값을 만들지 않는다."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    try:
        data = json.loads(request.body or b"{}")
        amount = decimal.Decimal(str(data.get("amount_usdc")))
    except (ValueError, json.JSONDecodeError, decimal.InvalidOperation, TypeError):
        return JsonResponse({"error": "invalid_amount"}, status=422)
    try:
        expense = get_cashflow_service().record_expense(
            wallet=str(data.get("creator_wallet") or "").strip(),
            amount_usdc=amount,
            memo=str(data.get("memo") or ""),
        )
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)
    except CashflowValidationError as exc:
        return JsonResponse({"error": "invalid_expense", "detail": str(exc)}, status=422)
    return JsonResponse(
        {"expense_id": expense.id, "amount_usdc": str(expense.amount_usdc), "memo": expense.memo},
        status=201,
    )
