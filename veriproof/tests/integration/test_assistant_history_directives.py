"""창작자 대화 이력과 행동 지침의 분리 저장 계약 테스트."""
from __future__ import annotations

import uuid

import pytest

from services.creator_assistant_service import (
    AssistantUnavailable,
    CreatorAssistantService,
)
from tests.conftest import VALID_WALLET


@pytest.mark.django_db
def test_history_and_directives_are_creator_scoped(client):
    from apps.ip.models import AssistantMessage
    from tests.factories import CreatorFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    other = CreatorFactory()
    AssistantMessage.objects.create(
        creator=creator, role=AssistantMessage.USER, content="Register my work"
    )
    AssistantMessage.objects.create(
        creator=other, role=AssistantMessage.USER, content="Other creator message"
    )

    created = client.post(
        "/api/v1/assistant/directives",
        data={
            "creator_wallet": VALID_WALLET,
            "title": "Review before sharing",
            "instruction": "Explain visibility and ask for confirmation before public sharing.",
        },
        content_type="application/json",
    )
    assert created.status_code == 201

    history = client.get(f"/api/v1/assistant/history?creator={VALID_WALLET}")
    directives = client.get(f"/api/v1/assistant/directives?creator={VALID_WALLET}")
    assert [item["content"] for item in history.json()["items"]] == ["Register my work"]
    assert directives.json()["items"][0]["title"] == "Review before sharing"


@pytest.mark.django_db
def test_history_returns_title_only_conversation_summaries_and_can_resume(client):
    """사이드바는 사용자 발화 제목만 받고, 선택한 세션만 다시 불러올 수 있다."""
    from apps.ip.models import AssistantMessage
    from tests.factories import CreatorFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    first = uuid.uuid4()
    second = uuid.uuid4()
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=first,
        role=AssistantMessage.USER,
        content="Register the landscape series",
    )
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=first,
        role=AssistantMessage.ASSISTANT,
        content="Assistant response that must not become a history title",
    )
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=second,
        role=AssistantMessage.USER,
        content="Set a license price",
    )

    response = client.get(f"/api/v1/assistant/history?creator={VALID_WALLET}")
    assert response.status_code == 200
    conversations = response.json()["conversations"]
    assert {item["title"] for item in conversations} == {
        "Register the landscape series",
        "Set a license price",
    }
    assert all(set(item) == {"conversation_id", "title"} for item in conversations)

    resumed = client.get(
        f"/api/v1/assistant/history?creator={VALID_WALLET}&conversation={first}"
    )
    assert resumed.status_code == 200
    assert [item["content"] for item in resumed.json()["items"]] == [
        "Register the landscape series",
        "Assistant response that must not become a history title",
    ]


@pytest.mark.django_db
def test_conversation_can_be_renamed_and_deleted_only_by_its_owner(client):
    """사이드바의 이름 변경·삭제는 원문이 아닌 소유자 대화 단위에 적용한다."""
    from django.contrib.auth.models import User

    from apps.accounts.models import UserPreference
    from apps.ip.models import AssistantMessage
    from tests.factories import CreatorFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    conversation_id = uuid.uuid4()
    retained_id = uuid.uuid4()
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=conversation_id,
        role=AssistantMessage.USER,
        content="Keep this message unchanged",
    )
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=conversation_id,
        role=AssistantMessage.ASSISTANT,
        content="Response to remove with this conversation",
    )
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=retained_id,
        role=AssistantMessage.USER,
        content="Conversation that must remain",
    )
    user = User.objects.create_user(username="owner@example.com", password="test-password-123")
    UserPreference.objects.update_or_create(user=user, defaults={"creator_wallet": VALID_WALLET})
    client.force_login(user)

    renamed = client.patch(
        f"/api/v1/assistant/conversations/{conversation_id}",
        data={"title": "Renamed conversation"},
        content_type="application/json",
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed conversation"
    original = AssistantMessage.objects.get(conversation_id=conversation_id, role=AssistantMessage.USER)
    assert original.content == "Keep this message unchanged"
    assert original.conversation_title == "Renamed conversation"

    deleted = client.delete(f"/api/v1/assistant/conversations/{conversation_id}")
    assert deleted.status_code == 204
    assert not AssistantMessage.objects.filter(conversation_id=conversation_id).exists()
    assert AssistantMessage.objects.filter(conversation_id=retained_id).exists()


@pytest.mark.django_db
def test_conversation_search_queries_the_owner_history_database(client):
    """검색은 브라우저에 이미 로드된 최근 목록이 아니라 소유자 DB 전체를 조회한다."""
    from django.contrib.auth.models import User

    from apps.accounts.models import UserPreference
    from apps.ip.models import AssistantMessage
    from tests.factories import CreatorFactory

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    other = CreatorFactory()
    matching_id = uuid.uuid4()
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=matching_id,
        role=AssistantMessage.USER,
        content="Archive the landscape licensing discussion",
    )
    AssistantMessage.objects.create(
        creator=creator,
        conversation_id=uuid.uuid4(),
        role=AssistantMessage.USER,
        content="Set the next release price",
    )
    AssistantMessage.objects.create(
        creator=other,
        conversation_id=uuid.uuid4(),
        role=AssistantMessage.USER,
        content="Archive another creator's private conversation",
    )
    user = User.objects.create_user(username="owner@example.com", password="test-password-123")
    UserPreference.objects.update_or_create(user=user, defaults={"creator_wallet": VALID_WALLET})
    client.force_login(user)

    response = client.get("/api/v1/assistant/conversations/search?q=archive")

    assert response.status_code == 200
    assert response.json()["conversations"] == [{
        "conversation_id": str(matching_id),
        "title": "Archive the landscape licensing discussion",
    }]


@pytest.mark.django_db
def test_user_message_is_saved_when_gemini_is_unavailable():
    from apps.ip.models import AssistantMessage
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=VALID_WALLET)
    service = CreatorAssistantService(gemini=object())
    with pytest.raises(AssistantUnavailable):
        service.ask(VALID_WALLET, "Help me set up licensing")
    assert AssistantMessage.objects.filter(
        creator__wallet_address=VALID_WALLET,
        role=AssistantMessage.USER,
        content="Help me set up licensing",
    ).exists()


@pytest.mark.django_db
def test_active_directives_are_passed_to_the_assistant_context():
    from apps.ip.models import AgentDirective
    from tests.factories import CreatorFactory

    class RecordingGemini:
        def __init__(self):
            self.context = None

        def assist_creator(self, context, message):
            self.context = context
            return "Recorded response"

    creator = CreatorFactory(wallet_address=VALID_WALLET)
    AgentDirective.objects.create(
        creator=creator, title="Confirm sharing", instruction="Ask before public sharing."
    )
    AgentDirective.objects.create(
        creator=creator,
        title="Inactive rule",
        instruction="Do not send this.",
        is_active=False,
    )
    gemini = RecordingGemini()
    CreatorAssistantService(gemini=gemini).ask(VALID_WALLET, "Share my work")
    assert gemini.context["behavior_instructions"] == [
        {"title": "Confirm sharing", "instruction": "Ask before public sharing."}
    ]
