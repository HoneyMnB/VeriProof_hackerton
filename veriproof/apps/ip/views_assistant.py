"""HTTP adapter for the creator assistant module."""
from __future__ import annotations

import decimal
import json
import uuid
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.accounts.services import WalletSigningError, active_wallet_address
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
    """창작자 지갑 기준 비서 대시보드 요약 데이터를 반환한다."""
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
        return JsonResponse({"items": [], "conversations": []})
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
        start_date = _sales_date(request.GET.get("start"), "start")
        end_date = _sales_date(request.GET.get("end"), "end")
        if start_date and end_date and start_date > end_date:
            raise ValueError("start date must not be after end date")
        return JsonResponse(get_creator_assistant_service().sales(
            wallet,
            search=(request.GET.get("q") or "").strip()[:120],
            asset_id=(request.GET.get("asset") or "").strip(),
            usage_type=(request.GET.get("usage") or "").strip()[:30],
            start_date=start_date,
            end_date=end_date,
            page=max(1, int(request.GET.get("page") or 1)),
            page_size=min(50, max(1, int(request.GET.get("page_size") or 20))),
            work_page=max(1, int(request.GET.get("work_page") or 1)),
            work_page_size=min(25, max(1, int(request.GET.get("work_page_size") or 10))),
        ))
    except (TypeError, ValueError):
        return JsonResponse({"error": "invalid_sales_filter"}, status=422)
    except LookupError:
        return JsonResponse({"error": "creator_not_found"}, status=404)


def _sales_date(raw: str | None, name: str) -> date | None:
    """ISO 날짜 문자열을 date로 변환한다. 실패 시 invalid <name> date ValueError를 발생시킨다."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"invalid {name} date") from exc


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
    """Reject subscription activation until a real payment path is wired."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    return JsonResponse(
        {
            "error": "subscription_payment_unavailable",
            "detail": "subscription activation requires a real payment integration",
        },
        status=503,
    )


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
    """사용자 메시지를 비서에 전달하고 응답·수행 액션·대화 식별자를 반환한다."""
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
    if request.method == "GET":
        wallet = (
            request.GET.get("creator")
            or request.GET.get("wallet")
            or _request_creator_wallet(request)
        ).strip()
        return JsonResponse({"items": _registration_draft_items(wallet)})
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    payload, uploads, parse_error = _registration_draft_payload(request)
    if parse_error is not None:
        return parse_error
    try:
        wallet = active_wallet_address(request.user)
    except WalletSigningError as exc:
        return JsonResponse({"error": "active_wallet_required", "detail": str(exc)}, status=422)
    service = get_registration_draft_service()
    try:
        if draft_id:
            result = service.confirm(wallet, str(draft_id))
        else:
            result = service.save(wallet, payload, uploads=uploads)
    except LookupError:
        return JsonResponse({"error": "draft_not_found"}, status=404)
    except DraftValidationError as exc:
        return JsonResponse({"error": "invalid_draft", "detail": str(exc)}, status=422)
    return JsonResponse(result, status=200 if draft_id else 201)


def _request_creator_wallet(request: HttpRequest) -> str:
    """로그인 사용자의 설정 지갑을 반환한다. 없으면 빈 문자열."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return ""
    preference = getattr(request.user, "veriproof_preferences", None)
    return (getattr(preference, "creator_wallet", "") or "").strip()


def _registration_draft_items(wallet: str) -> list[dict[str, object]]:
    """지갑의 등록 초안 목록을 반환한다. 알 수 없는 지갑이면 빈 목록."""
    if not wallet:
        return []
    from apps.ip.models import Creator, RegistrationDraft

    creator = Creator.objects.filter(wallet_address=wallet).first()
    if creator is None:
        return []
    return [
        get_registration_draft_service().serialize(draft)
        for draft in RegistrationDraft.objects.filter(creator=creator)[:50]
    ]


def _registration_draft_payload(request: HttpRequest):
    """Parse JSON or multipart draft updates without accepting client hashes."""
    if request.content_type and request.content_type.startswith("multipart/"):
        fields_raw = request.POST.get("fields") or "{}"
        try:
            fields = json.loads(fields_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, None, JsonResponse({"error": "invalid_json"}, status=422)
        if not isinstance(fields, dict):
            return None, None, JsonResponse({"error": "invalid_json"}, status=422)
        payload = {
            "creator_wallet": request.POST.get("creator_wallet"),
            "draft_id": request.POST.get("draft_id"),
            "file_name": request.POST.get("file_name"),
            "fields": fields,
        }
        uploads = tuple(request.FILES.getlist("files"))
        if not uploads and request.FILES.get("file") is not None:
            uploads = (request.FILES["file"],)
        return payload, uploads or None, None
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return None, None, JsonResponse({"error": "invalid_json"}, status=422)
    return payload, None, None


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
