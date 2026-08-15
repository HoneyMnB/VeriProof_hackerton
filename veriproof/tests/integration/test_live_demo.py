from __future__ import annotations

import asyncio

import pytest


class _Mirror:
    enabled = True

    def __init__(self, documents):
        self.documents = documents

    def is_available(self):
        return True

    def recent(self, collection, *, limit):
        assert collection == "events"
        return self.documents


class _Watch:
    def __init__(self):
        self.unsubscribed = False

    def unsubscribe(self):
        self.unsubscribed = True


class _StreamingMirror(_Mirror):
    def __init__(self, documents, update):
        super().__init__(documents)
        self.update = update
        self.watch = _Watch()

    def watch_recent(self, collection, callback, *, limit):
        assert collection == "events"
        callback([self.update])
        return self.watch


def test_live_demo_requires_login(client):
    response = client.get("/live-demo")
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


@pytest.mark.django_db
def test_live_demo_renders_for_authenticated_account(client, django_user_model):
    user = django_user_model.objects.create_user("live@example.com", password="safe-password-123")
    client.force_login(user)
    response = client.get("/live-demo")
    assert response.status_code == 200
    assert b"Agent commerce" in response.content
    assert b"js/live_demo" in response.content
    assert response.content.count(b'role="tab"') == 2
    assert response.content.count(b'role="tabpanel"') == 2
    assert b'aria-controls="live-panel-registration"' in response.content
    assert b'aria-controls="live-panel-commerce"' in response.content


@pytest.mark.django_db
def test_live_feed_only_returns_owned_asset_events(client, monkeypatch, django_user_model):
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = django_user_model.objects.create_user("owner@example.com", password="safe-password-123")
    other = django_user_model.objects.create_user("other@example.com", password="safe-password-123")
    owned = IpAssetFactory(account_owner=owner, creator=CreatorFactory(), title="Owned work")
    foreign = IpAssetFactory(account_owner=other, creator=CreatorFactory(), title="Foreign work")
    mirror = _Mirror([
        {"event_id": "1", "type": "OFFER", "asset_id": str(owned.id), "created_at": "2026-08-13T01:00:00+00:00", "payload": {"offer_sol": "0.1", "buyer_wallet": "secret"}},
        {"event_id": "2", "type": "PAYMENT_VERIFIED", "asset_id": str(foreign.id), "created_at": "2026-08-13T01:01:00+00:00", "payload": {"payment_tx_sig": "hidden"}},
    ])
    monkeypatch.setattr("apps.common.views_live_demo.get_firestore_mirror", lambda: mirror)
    client.force_login(owner)
    response = client.get("/api/v1/live-demo/events")
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert [item["asset_title"] for item in body["items"]] == ["Owned work"]
    assert body["items"][0]["payload"] == {"offer_sol": "0.1"}
    assert "secret" not in response.content.decode()


@pytest.mark.django_db
def test_live_feed_reports_disabled_without_fake_data(client, settings, django_user_model):
    user = django_user_model.objects.create_user("offline@example.com", password="safe-password-123")
    settings.FIRESTORE_ENABLED = False
    client.force_login(user)
    response = client.get("/api/v1/live-demo/events")
    assert response.status_code == 200
    assert response.json() == {"connected": False, "reason": "disabled", "items": [], "metrics": {}}


def test_async_event_stream_yields_snapshot_flow_and_unsubscribes():
    from apps.common.views_live_demo import _event_stream

    asset_id = "5df593fd-a419-42e6-8471-06fa84b7e516"
    initial = {
        "event_id": "initial",
        "type": "ANCHORED",
        "asset_id": asset_id,
        "created_at": "2026-08-14T01:00:00+00:00",
        "payload": {},
    }
    update = {
        "event_id": "update",
        "type": "OFFER",
        "asset_id": asset_id,
        "created_at": "2026-08-14T01:00:01+00:00",
        "payload": {"offer_sol": "0.1"},
    }
    mirror = _StreamingMirror([initial], update)

    async def exercise():
        stream = _event_stream(mirror, {asset_id: "Owned work"}, "7", 0.01)
        snapshot = await anext(stream)
        flow = await anext(stream)
        await stream.aclose()
        return snapshot, flow

    snapshot, flow = asyncio.run(exercise())

    assert "event: snapshot" in snapshot
    assert '"event_id": "initial"' in snapshot
    assert "event: flow" in flow
    assert '"event_id": "update"' in flow
    assert mirror.watch.unsubscribed is True
