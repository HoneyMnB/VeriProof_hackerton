"""Creator workspace intelligence, isolated from HTTP and Gemini transport."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class AssistantUnavailable(Exception):
    """The configured AI provider could not generate a grounded answer."""


@dataclass(frozen=True)
class CreatorChatOutcome:
    """대화 응답과 별도 검증된 도구 실행 결과를 함께 전달한다."""

    answer: str
    action: dict[str, Any] | None
    conversation_id: str


class CreatorAssistantService:
    """검증된 작업 공간 데이터만 근거로 삼아 창작자 비서 대화·도구 실행을 조율한다.

    Gemini 응답과 첨부 분석, 도구 실행을 한 곳에서 묶되 스스로 실행 권한은
    가지지 않으며, 실제 변경은 검증 가능한 하위 서비스들에 위임한다.
    """

    def __init__(
        self,
        gemini: Any,
        action_service: Any | None = None,
        attachment_service: Any | None = None,
    ) -> None:
        self.gemini = gemini
        self.action_service = action_service
        self.attachment_service = attachment_service

    def overview(self, wallet: str) -> dict[str, Any] | None:
        """창작자 자산·수익·판매·파이프라인 요약을 DB에서 조립해 반환한다.

        외부 AI 호출 없이 검증된 데이터만 사용하며, 알 수 없는 지갑이면 None을
        반환한다.
        """
        from apps.ip.models import AgentDirective, Creator, IpAsset
        from services.cashflow_service import get_cashflow_service
        from services.sales_service import get_sales_service

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            logger.info("creator assistant overview rejected: unknown wallet")
            return None
        assets = IpAsset.objects.filter(creator=creator)
        cashflow = get_cashflow_service().summary(creator)
        sales = get_sales_service().summary(creator)
        overview = {
            "creator_wallet": creator.wallet_address,
            "asset_count": assets.count(),
            "public_asset_count": assets.filter(visibility=IpAsset.PUBLIC).count(),
            "anchored_asset_count": assets.filter(status=IpAsset.ANCHORED).count(),
            "license_revenue_usdc": str(cashflow.income_usdc),
            "expense_usdc": str(cashflow.expense_usdc),
            "net_usdc": str(cashflow.income_usdc - cashflow.expense_usdc),
            "sale_count": sales.sale_count,
            "gross_sales_sol": str(sales.gross_sol),
            "platform_fee_bps": sales.platform_fee_bps,
            "platform_fee_sol": str(sales.platform_fee_sol),
            "creator_proceeds_sol": str(sales.creator_proceeds_sol),
            "pipeline": [
                "register", "x402_access", "agent_negotiation", "onchain_settlement"
            ],
            "active_directive_count": AgentDirective.objects.filter(
                creator=creator, is_active=True
            ).count(),
        }
        logger.info("creator assistant overview generated assets=%d", overview["asset_count"])
        return overview

    def ask(
        self,
        wallet: str,
        message: str,
        attachment_ids: list[str] | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> CreatorChatOutcome:
        """대화와 명시적으로 선택한 실제 첨부 분석만 Gemini 컨텍스트에 전달한다."""
        from apps.ip.models import AssistantMessage, ConversationAttachment, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        context = self.overview(wallet)
        if creator is None or context is None:
            logger.info("creator assistant chat rejected: unknown wallet")
            raise LookupError("creator_not_found")
        context["behavior_instructions"] = [
            {
                "title": directive["title"],
                "instruction": directive["instruction"],
            }
            for directive in self.directives(wallet)
            if directive["is_active"]
        ]
        context["creator_library"] = self._library_context(creator)
        context["public_catalog"] = self._public_catalog_context()
        attachments = self._attachments_for_message(creator, attachment_ids or [])
        # 채팅 첨부는 업로드/전송 시점에 분석하지 않는다. 파일은 사용자가 분석·
        # 의견을 요청할 때만(analyze_attachment) 멀티모달 LLM으로 전송된다. 여기서는
        # 플래너가 첨부 존재를 인지하도록 메타데이터(파일명·형식)만 컨텍스트에 넣는다.
        context["conversation_attachments"] = [
            {
                "file_name": attachment.file_name,
                "content_mime_type": attachment.content_mime_type,
            }
            for attachment in attachments
        ]
        # 사용자의 지시는 외부 AI가 실패해도 감사 이력에 먼저 남긴다.
        conversation_id = conversation_id or uuid.uuid4()
        source_message = AssistantMessage.objects.create(
            creator=creator,
            conversation_id=conversation_id,
            role=AssistantMessage.USER,
            content=message,
        )
        if attachments:
            ConversationAttachment.objects.filter(
                id__in=[attachment.id for attachment in attachments]
            ).update(source_message=source_message)
        try:
            if hasattr(self.gemini, "plan_creator_action"):
                plan = self.gemini.plan_creator_action(context, message)
                answer = plan.reply
                planned_action = plan.action
                # 첨부 분석 요청은 도구 실행이 아니라 멀티모달 LLM 분기로 처리한다.
                # 첨부가 실제로 있을 때만 분기한다(플래너 오분류 방어).
                if (
                    attachments
                    and planned_action is not None
                    and planned_action.get("name") == "analyze_attachment"
                ):
                    answer = self._answer_with_attachments(context, message, attachments)
                    planned_action = None
                elif (
                    planned_action is not None
                    and planned_action.get("name") == "analyze_attachment"
                ):
                    # 첨부 없이 analyze_attachment가 오면 도구 실행 대상이 아니므로 무시.
                    planned_action = None
            else:
                # 기존 테스트 대역은 텍스트 응답만 제공한다. 실제 서비스에는
                # Gemini의 구조화 계획 인터페이스만 사용한다.
                answer = self.gemini.assist_creator(context, message)
                planned_action = None
        except Exception as exc:  # Gemini adapter errors are translated at this boundary.
            logger.error(
                "creator assistant unavailable creator_wallet=%s error=%s",
                creator.wallet_address,
                exc,
            )
            raise AssistantUnavailable("gemini_unavailable") from exc
        action_result = None
        if planned_action is not None:
            from services.creator_action_service import get_creator_action_service

            executor = self.action_service or get_creator_action_service()
            execution = executor.execute(
                creator=creator, source_message=source_message, action=planned_action
            )
            action_result = {
                "action_id": execution.action_id,
                "action_name": execution.action_name,
                "status": execution.status,
                "verification_passed": execution.verification_passed,
                "result": execution.result,
            }
            logger.info(
                "creator assistant action recorded creator_wallet=%s action=%s status=%s verified=%s",
                creator.wallet_address,
                execution.action_name,
                execution.status,
                execution.verification_passed,
            )
        AssistantMessage.objects.create(
            creator=creator,
            conversation_id=conversation_id,
            role=AssistantMessage.ASSISTANT,
            content=answer,
        )
        logger.info("creator assistant response recorded")
        return CreatorChatOutcome(
            answer=answer,
            action=action_result,
            conversation_id=str(conversation_id),
        )

    def _answer_with_attachments(
        self, context: dict[str, Any], message: str, attachments: list[Any]
    ) -> str:
        """분석 가능한 첨부는 원본 바이트를 읽어 멀티모달 Gemini로 분석하고,
        분석 불가한 형식(zip/tar 등)은 사용자에게 그 사실을 안내한다."""
        from services.gemini_service import LLM_ANALYZABLE_MIMES

        analyzable, unsupported = [], []
        for attachment in attachments:
            mime = str(attachment.content_mime_type or "")
            (analyzable if mime in LLM_ANALYZABLE_MIMES else unsupported).append(attachment)
        if not analyzable:
            names = ", ".join(a.file_name for a in unsupported) or "the attachment"
            return (
                f"{names} cannot be analyzed by the assistant. Supported formats are "
                "images, PDF, plain text, audio, and video."
            )
        storage = self._attachment_storage()
        files = []
        for attachment in analyzable:
            content = storage.read_temporary(attachment.id)
            if content:
                files.append((content, str(attachment.content_mime_type or "")))
        if not files:
            return (
                "The attached file is no longer available to analyze. Please re-upload "
                "it and try again."
            )
        answer = self.gemini.assist_with_attachments(context, message, files)
        if unsupported:
            names = ", ".join(a.file_name for a in unsupported)
            answer = f"{answer}\n\nNote: {names} could not be analyzed (unsupported format)."
        return answer

    def _attachment_storage(self) -> Any:
        """첨부 원본을 다시 읽기 위한 스토리지 심(seam)을 얻는다."""
        service = self.attachment_service
        if service is None:
            from services.conversation_attachment_service import (
                get_conversation_attachment_service,
            )

            service = get_conversation_attachment_service()
        return service.storage

    @staticmethod
    def _attachments_for_message(creator: Any, attachment_ids: list[str]) -> list[Any]:
        """다른 사용자의 첨부나 이미 연결된 파일은 대화 컨텍스트에 넣지 않는다."""
        from apps.ip.models import ConversationAttachment

        if len(attachment_ids) > 4:
            raise ValueError("at most four attachments can be sent with one message")
        attachments = list(
            ConversationAttachment.objects.filter(
                id__in=attachment_ids, creator=creator, source_message__isnull=True
            )
        )
        if len(attachments) != len(set(attachment_ids)):
            raise ValueError("an attachment is unavailable for this conversation")
        return attachments

    @staticmethod
    def _library_context(creator: Any) -> list[dict[str, Any]]:
        """비서가 창작자 본인의 최근 자산을 근거로 답하도록 최소 메타데이터만 제공한다."""
        from apps.ip.models import IpAsset

        rows = IpAsset.objects.filter(creator=creator).order_by("-created_at")[:20]
        return [
            {"id": str(item.id), "title": item.title, "asset_type": item.asset_type,
             "visibility": item.visibility, "tags": list(item.tags or []),
             "min_price_usdc": str(item.min_price_usdc), "target_price_usdc": str(item.target_price_usdc)}
            for item in rows
        ]

    @staticmethod
    def _public_catalog_context() -> list[dict[str, Any]]:
        """공개·앵커 완료 자산의 안전한 검색 메타데이터만 Gemini에 제공한다."""
        from apps.ip.models import IpAsset

        rows = IpAsset.objects.filter(visibility=IpAsset.PUBLIC, status=IpAsset.ANCHORED).order_by("-created_at")[:30]
        return [
            {"id": str(item.id), "title": item.title, "asset_type": item.asset_type,
             "tags": list(item.tags or []), "ai_tags": list(item.ai_tags or []),
             "ai_description": item.ai_description,
             "min_price_usdc": str(item.min_price_usdc),
             "target_price_usdc": str(item.target_price_usdc)}
            for item in rows
        ]

    def history(
        self, wallet: str, limit: int = 100, conversation_id: uuid.UUID | None = None
    ) -> list[dict[str, str]]:
        """창작자 본인의 저장된 대화만 시간순으로 반환한다."""
        from apps.ip.models import AssistantMessage, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        query = AssistantMessage.objects.filter(creator=creator)
        if conversation_id is not None:
            query = query.filter(conversation_id=conversation_id)
        messages = list(query.order_by("-created_at", "-id")[:limit])
        return [
            {
                "message_id": str(item.id),
                "conversation_id": str(item.conversation_id) if item.conversation_id else None,
                "role": item.role,
                "content": item.content,
                "created_at": item.created_at.isoformat(),
            }
            for item in reversed(messages)
        ]

    def conversation_summaries(
        self, wallet: str, limit: int | None = 30, search: str = ""
    ) -> list[dict[str, str]]:
        """창작자 대화 제목을 반환하고, 검색어가 있으면 DB에서 먼저 범위를 좁힌다."""
        from django.db.models import Q

        from apps.ip.models import AssistantMessage, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        normalized_search = " ".join(search.split())
        if len(normalized_search) > 120:
            raise ValueError("search query must be 120 characters or fewer")
        rows = AssistantMessage.objects.filter(
            creator=creator,
            role=AssistantMessage.USER,
            conversation_id__isnull=False,
        ).order_by("-created_at", "-id")
        if normalized_search:
            matching_conversations = AssistantMessage.objects.filter(
                creator=creator,
                conversation_id__isnull=False,
            ).filter(
                Q(conversation_title__icontains=normalized_search)
                | Q(role=AssistantMessage.USER, content__icontains=normalized_search)
            ).values_list("conversation_id", flat=True).distinct()
            rows = rows.filter(conversation_id__in=matching_conversations)
        custom_titles = {
            item.conversation_id: item.conversation_title
            for item in AssistantMessage.objects.filter(
                creator=creator,
                conversation_id__isnull=False,
                conversation_title__isnull=False,
            ).exclude(conversation_title="")
        }
        summaries: list[dict[str, str]] = []
        seen: set[uuid.UUID] = set()
        for item in rows:
            if item.conversation_id in seen:
                continue
            seen.add(item.conversation_id)
            summaries.append(
                {
                    "conversation_id": str(item.conversation_id),
                    "title": custom_titles.get(item.conversation_id)
                    or item.content.strip().splitlines()[0],
                }
            )
            if limit is not None and len(summaries) == limit:
                break
        return summaries

    def rename_conversation(
        self, wallet: str, conversation_id: uuid.UUID, title: str
    ) -> dict[str, str]:
        """원문을 유지한 채 대화 목록에 표시할 제목만 변경한다."""
        from apps.ip.models import AssistantMessage, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        normalized_title = " ".join(title.split())
        if not normalized_title or len(normalized_title) > 120:
            raise ValueError("conversation title must be between 1 and 120 characters")
        updated = AssistantMessage.objects.filter(
            creator=creator,
            conversation_id=conversation_id,
            role=AssistantMessage.USER,
        ).order_by("created_at", "id").first()
        if updated is None:
            raise LookupError("conversation_not_found")
        updated.conversation_title = normalized_title
        updated.save(update_fields=["conversation_title"])
        return {"conversation_id": str(conversation_id), "title": normalized_title}

    def delete_conversation(self, wallet: str, conversation_id: uuid.UUID) -> None:
        """창작자 본인의 지정 대화 메시지 전체를 삭제한다."""
        from apps.ip.models import AssistantMessage, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        deleted, _ = AssistantMessage.objects.filter(
            creator=creator, conversation_id=conversation_id
        ).delete()
        if not deleted:
            raise LookupError("conversation_not_found")

    def directives(self, wallet: str) -> list[dict[str, Any]]:
        """사용자가 확인하는 행동 지침 목록을 반환한다."""
        from apps.ip.models import AgentDirective, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        return [
            {
                "directive_id": item.id,
                "title": item.title,
                "instruction": item.instruction,
                "is_active": item.is_active,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in AgentDirective.objects.filter(creator=creator)
        ]

    def add_directive(self, wallet: str, title: str, instruction: str) -> dict[str, Any]:
        """창작자가 명시적으로 제공한 행동 지침만 저장한다."""
        from apps.ip.models import AgentDirective, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        normalized_title = title.strip()
        normalized_instruction = instruction.strip()
        if not normalized_title or not normalized_instruction:
            raise ValueError("directive title and instruction are required")
        directive = AgentDirective.objects.create(
            creator=creator, title=normalized_title, instruction=normalized_instruction
        )
        logger.info("creator directive recorded creator_wallet=%s", creator.wallet_address)
        return {
            "directive_id": directive.id,
            "title": directive.title,
            "instruction": directive.instruction,
            "is_active": directive.is_active,
            "updated_at": directive.updated_at.isoformat(),
        }

    def sales(self, wallet: str, **filters: Any) -> dict[str, Any]:
        """창작자가 확인하는 판매 결과와 수수료 정책을 반환한다."""
        from apps.ip.models import Creator
        from services.sales_service import get_sales_service

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        service = get_sales_service()
        return service.report(creator, **filters)

    def actions(self, wallet: str, limit: int = 100) -> list[dict[str, Any]]:
        """사용자가 확인할 수 있는 실행·검증 감사 기록을 반환한다."""
        from apps.ip.models import AssistantAction, Creator

        creator = Creator.objects.filter(wallet_address=wallet).first()
        if creator is None:
            raise LookupError("creator_not_found")
        return [
            {
                "action_id": item.id,
                "action_name": item.action_name,
                "status": item.status,
                "verification_passed": item.verification_passed,
                "result": item.result_payload,
                "created_at": item.created_at.isoformat(),
            }
            for item in AssistantAction.objects.filter(creator=creator)[:limit]
        ]


def get_creator_assistant_service() -> CreatorAssistantService:
    """실제 Gemini 어댑터를 연결한 창작자 비서 서비스 팩토리."""
    from .gemini_service import get_gemini_service

    return CreatorAssistantService(gemini=get_gemini_service())
