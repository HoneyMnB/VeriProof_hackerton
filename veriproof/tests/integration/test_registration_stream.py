"""Live registration progress: owner-scoped SSE over AgentEvent rows."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.conftest import VALID_WALLET
from tests.fakes import FakeGeminiService, FakeSolanaService, FakeStorageService

STREAM_URL = "/api/v1/ip/register/stream"


def test_stream_requires_login(client):
    response = client.get(STREAM_URL)
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


@pytest.mark.django_db
def test_stream_route_returns_event_stream_for_owner(client, django_user_model):
    user = django_user_model.objects.create_user("owner@example.com", password="safe-password-123")
    client.force_login(user)
    response = client.get(STREAM_URL)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/event-stream")
    assert response["Cache-Control"] == "no-cache, no-transform"
    assert response.streaming is True


@pytest.mark.django_db
def test_events_after_returns_only_owner_registration_stages_after_cursor(django_user_model):
    from apps.common.models import AgentEvent
    from apps.ip.views_registration_stream import _events_after, _latest_event_id

    owner = django_user_model.objects.create_user("owner@example.com", password="safe-password-123")
    other = django_user_model.objects.create_user("other@example.com", password="safe-password-123")
    flow = uuid.uuid4()
    AgentEvent.objects.create(type="CONTENT_HASHED", payload={}, account_owner=owner, correlation_id=flow)
    cursor = _latest_event_id()
    AgentEvent.objects.create(type="OFFER", payload={}, account_owner=owner, correlation_id=flow)
    AgentEvent.objects.create(type="ANCHORED", payload={"anchor_tx_sig": "sig-other"}, account_owner=other, correlation_id=uuid.uuid4())
    anchored = AgentEvent.objects.create(
        type="ANCHORED",
        payload={"anchor_tx_sig": "sig-1", "content_sha256": "ab" * 32, "secret_key": "hidden"},
        account_owner=owner,
        correlation_id=flow,
    )

    items = _events_after(owner.pk, cursor)

    assert [item["type"] for item in items] == ["ANCHORED"]
    assert items[0]["cursor"] == anchored.id
    assert items[0]["correlation_id"] == str(flow)
    assert items[0]["payload"] == {"anchor_tx_sig": "sig-1", "content_sha256": "ab" * 32}
    assert _events_after(owner.pk, anchored.id) == []


def test_registration_stream_yields_ready_then_stage_frames_and_advances_cursor():
    from apps.ip.views_registration_stream import registration_stream

    calls = []

    def fetch_after(after):
        calls.append(after)
        if after == 10:
            return [
                {"cursor": 11, "type": "REGISTRATION_STARTED", "correlation_id": "c", "asset_id": None, "timestamp": "t", "payload": {}},
                {"cursor": 12, "type": "CONTENT_HASHED", "correlation_id": "c", "asset_id": None, "timestamp": "t", "payload": {}},
            ]
        return []

    async def exercise():
        stream = registration_stream(fetch_after, 10, poll_seconds=0.001, keepalive_seconds=0, max_seconds=60)
        frames = [await anext(stream), await anext(stream), await anext(stream), await anext(stream)]
        await stream.aclose()
        return frames

    ready, first, second, keepalive = asyncio.run(exercise())

    assert ready == 'event: ready\ndata: {"cursor": 10}\n\n'
    assert first.startswith("id: 11\nevent: stage\n") and '"type": "REGISTRATION_STARTED"' in first
    assert second.startswith("id: 12\nevent: stage\n") and '"type": "CONTENT_HASHED"' in second
    assert keepalive == ": keep-alive\n\n"
    assert calls[:2] == [10, 12]


def test_registration_stream_closes_after_max_lifetime():
    from apps.ip.views_registration_stream import registration_stream

    async def exercise():
        frames = []
        async for frame in registration_stream(lambda after: [], 0, poll_seconds=0.001, keepalive_seconds=60, max_seconds=0):
            frames.append(frame)
        return frames

    frames = asyncio.run(exercise())
    assert frames == ['event: ready\ndata: {"cursor": 0}\n\n', 'event: closed\ndata: {"reason": "timeout"}\n\n']


@pytest.mark.django_db
def test_register_records_every_visualised_stage_in_order(client, png_bytes, monkeypatch, django_user_model):
    """The overlay maps these types 1:1; the pipeline must emit all of them for the owner."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.common.models import AgentEvent
    from services.event_recorder import get_event_recorder
    from services.image_processor import get_image_processor
    from services.registration_service import RegistrationService

    service = RegistrationService(
        image_processor=get_image_processor(),
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
        event_recorder=get_event_recorder(),
    )
    monkeypatch.setattr("apps.ip.views_api.get_registration_service", lambda: service)
    monkeypatch.setattr("apps.ip.views_api.active_wallet_signer", lambda user: (VALID_WALLET, [7] * 64))
    user = django_user_model.objects.create_user("registrant@example.com", password="safe-password-123")
    client.force_login(user)

    response = client.post(
        "/api/v1/ip/register",
        {"image": SimpleUploadedFile("work.png", png_bytes, content_type="image/png"), "creator_wallet": VALID_WALLET, "min_price": "1.5", "target_price": "2.25"},
        format="multipart",
    )
    assert response.status_code == 201, response.content

    types = list(
        AgentEvent.objects.filter(account_owner=user, correlation_id=response.json()["asset_id"]).order_by("id").values_list("type", flat=True)
    )
    assert types == [
        "REGISTRATION_STARTED", "CONTENT_HASHED", "AI_ANALYZED", "ANCHORING_STARTED", "ANCHORED",
        "REGISTRATION_CERTIFICATE_ISSUED", "CONTENT_STORED", "ASSET_REGISTERED",
    ]
