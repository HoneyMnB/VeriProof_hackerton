"""SPEC-005 integration tests — library dashboard web + data APIs.

Covers the SPEC §5 backend integration list through Django's test client with
real SQLite DB + factories:

- ``/library`` web view renders ONLY the owner's assets (R5, AC-4).
- ``GET /api/v1/assets?creator=`` filters by creator wallet (R11).
- ``GET /api/v1/ip/{asset_id}/transactions`` merges Licenses + AgentEvents,
  time-ascending, excludes original bytes/urls (R9, AC-8).
- ``GET /api/v1/events?asset_id=&since=`` incremental polling fallback (R10,
  AC-9; shared with SPEC-006).
- ``test_dragdrop_register_response_renders_card`` — frontend contract: the
  register response shape feeds ``analysis_card_fields`` (R1/R2, AC-1/AC-2).

Every payload the frontend consumes MUST exclude original bytes/urls.
"""
from __future__ import annotations

import decimal
import uuid

import pytest
from freezegun import freeze_time

_OWNER_WALLET = "OwnerWalletxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_OTHER_WALLET = "OtherWalletxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_BUYER_WALLET = "BuyerWalletxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _login_creator(client, wallet: str) -> None:
    """자산 API는 세션 계정과 저장된 창작자 지갑이 일치해야 한다."""
    from django.contrib.auth.models import User

    from apps.accounts.models import UserPreference

    user = User.objects.create_user(
        username=f"{wallet[:12]}@test.com", password="test-password-123"
    )
    preference, _ = UserPreference.objects.get_or_create(user=user)
    preference.creator_wallet = wallet
    preference.save(update_fields=["creator_wallet", "updated_at"])
    client.force_login(user)


# --- R5 / AC-4: /library renders only owner assets --------------------------


@pytest.mark.django_db
def test_library_view_renders_only_owner_assets(client):
    """``/library?creator=<wallet>`` renders the owner's assets and no other."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    other = CreatorFactory(wallet_address=_OTHER_WALLET)
    IpAssetFactory(creator=owner, title="Owner Asset Alpha")
    IpAssetFactory(creator=owner, title="Owner Asset Beta")
    IpAssetFactory(creator=other, title="Other Creator Asset")  # must NOT appear

    response = client.get("/library", {"creator": _OWNER_WALLET})
    assert response.status_code == 200
    content = response.content.decode()
    assert "Owner Asset Alpha" in content
    assert "Owner Asset Beta" in content
    assert "Other Creator Asset" not in content


@pytest.mark.django_db
def test_library_view_empty_state_when_no_assets(client):
    """Edge (6): a creator with zero assets sees an empty-state message."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=_OWNER_WALLET)
    response = client.get("/library", {"creator": _OWNER_WALLET})
    assert response.status_code == 200
    # Empty-state hint rendered (template includes the marker).
    assert "no-assets" in response.content.decode()


@pytest.mark.django_db
def test_library_view_accepts_wallet_alias_param(client):
    """The ``wallet`` query param is an accepted alias for ``creator``."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    IpAssetFactory(creator=owner, title="Alias Param Asset")
    response = client.get("/library", {"wallet": _OWNER_WALLET})
    assert response.status_code == 200
    assert "Alias Param Asset" in response.content.decode()


@pytest.mark.django_db
def test_workspace_view_renders(client):
    """``/workspace``는 주석·첨부 메뉴를 숨긴 상태로 렌더링한다."""
    response = client.get("/workspace")
    assert response.status_code == 200
    content = response.content.decode()
    # The drop zone + register endpoint hook exist for JS wiring.
    assert "dropzone" in content
    assert "/api/v1/ip/register" in content
    assert "data-i18n carries" not in content
    assert 'id="composer-add-menu" class="vp-composer-menu" hidden' in content


def test_start_registration_opens_canvas_before_wallet_connection():
    """등록 시작은 지갑 연결 전에도 캔버스를 열고, 확정 때만 지갑을 요구한다."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "workspace.js"
    ).read_text()
    open_canvas = source.split("function openCanvas()", 1)[1].split(
        "function closeCanvas()", 1
    )[0]
    assert "if (!wallet())" not in open_canvas
    assert "canvas.hidden = false" in open_canvas
    assert "!menu.contains(event.target)" in source


# --- R11: GET /api/v1/assets?creator= ---------------------------------------


@pytest.mark.django_db
def test_assets_api_filters_by_creator(client):
    """``GET /api/v1/assets?creator=<wallet>`` lists only that creator's assets."""
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    other = CreatorFactory(wallet_address=_OTHER_WALLET)
    IpAssetFactory(creator=owner, title="A1")
    IpAssetFactory(creator=owner, title="A2")
    IpAssetFactory(creator=other, title="A3")
    _login_creator(client, _OWNER_WALLET)

    response = client.get("/api/v1/assets", {"creator": _OWNER_WALLET})
    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    assert len(items) == 2
    titles = {it["title"] for it in items}
    assert titles == {"A1", "A2"}
    # R8 edge: no original bytes/url leaks into the listing payload.
    for item in items:
        assert "original_url" not in item
        assert "original_bytes" not in item
    # Proof / listing fields present.
    assert {"asset_id", "title", "status", "creator_wallet"}.issubset(items[0].keys())


@pytest.mark.django_db
def test_assets_api_unknown_creator_returns_empty(client):
    """An unknown creator wallet returns 200 with an empty item list."""
    _login_creator(client, _OWNER_WALLET)
    response = client.get("/api/v1/assets", {"creator": _OWNER_WALLET})
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.django_db
def test_assets_api_rejects_anonymous_and_other_creator(client):
    """개인 라이브러리 API는 지갑 query만으로 조회할 수 없다."""
    from tests.factories import CreatorFactory

    CreatorFactory(wallet_address=_OWNER_WALLET)
    assert client.get("/api/v1/assets", {"creator": _OWNER_WALLET}).status_code == 401
    _login_creator(client, _OTHER_WALLET)
    assert client.get("/api/v1/assets", {"creator": _OWNER_WALLET}).status_code == 403


# --- R9 / AC-8: GET /api/v1/ip/{asset_id}/transactions ----------------------


@pytest.mark.django_db
def test_transactions_api_returns_licenses_and_events(client):
    """Transactions timeline merges Licenses + AgentEvents, time-ascending."""
    from tests.factories import (
        AgentEventFactory,
        CreatorFactory,
        IpAssetFactory,
        LicenseFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    # Interleave events and a license at distinct timestamps.
    with freeze_time("2026-01-01T10:00:00Z"):
        AgentEventFactory(asset=asset, type="HTTP_402", payload={"buyer_hint": "b1"})
    with freeze_time("2026-01-01T10:00:05Z"):
        LicenseFactory(
            asset=asset,
            buyer_wallet=_BUYER_WALLET,
            price_usdc=decimal.Decimal("1.50"),
            usage_type="commercial",
        )
    with freeze_time("2026-01-01T10:00:10Z"):
        AgentEventFactory(asset=asset, type="CERT_ISSUED", payload={"ok": True})

    response = client.get(f"/api/v1/ip/{asset.id}/transactions")
    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    assert len(items) == 3
    # Time-ascending ordering (R9).
    timestamps = [it["timestamp"] for it in items]
    assert timestamps == sorted(timestamps)
    # Both kinds present.
    kinds = {it["kind"] for it in items}
    assert kinds == {"license", "event"}
    # License entry carries the deal fields.
    lic = next(it for it in items if it["kind"] == "license")
    assert lic["buyer_wallet"] == _BUYER_WALLET
    assert lic["price_usdc"] == "1.500000"
    assert lic["usage_type"] == "commercial"
    # Event entry carries type + payload.
    ev = next(it for it in items if it["kind"] == "event" and it["type"] == "HTTP_402")
    assert ev["payload"] == {"buyer_hint": "b1"}
    # R8 edge: no original bytes/url leaks into the timeline payload.
    import json

    blob = json.dumps(body)
    assert "original_url" not in blob
    assert "original_bytes" not in blob


@pytest.mark.django_db
def test_transactions_api_empty_when_only_asset_exists(client):
    """An asset with no License/AgentEvent yields an empty timeline (200)."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    response = client.get(f"/api/v1/ip/{asset.id}/transactions")
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.django_db
def test_transactions_api_unknown_asset_404(client):
    """Unknown asset_id -> 404 (architecture 6.1: 200 | 404)."""
    unknown = uuid.uuid4()
    response = client.get(f"/api/v1/ip/{unknown}/transactions")
    assert response.status_code == 404


# --- R10 / AC-9: GET /api/v1/events?asset_id=&since= ------------------------


@pytest.mark.django_db
def test_events_polling_endpoint_returns_since(client):
    """``since`` boundary: only AgentEvents created strictly after ``since``."""
    from tests.factories import (
        AgentEventFactory,
        CreatorFactory,
        IpAssetFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    with freeze_time("2026-01-01T10:00:00Z"):
        AgentEventFactory(asset=asset, type="HTTP_402")
    with freeze_time("2026-01-01T10:00:10Z"):
        AgentEventFactory(asset=asset, type="ACCEPT")

    # Boundary: since=10:00:05 -> only the 10:00:10 event qualifies (strict >).
    response = client.get(
        "/api/v1/events",
        {"asset_id": str(asset.id), "since": "2026-01-01T10:00:05+00:00"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "ACCEPT"


@pytest.mark.django_db
def test_events_polling_without_since_returns_all_for_asset(client):
    """Omitting ``since`` returns every AgentEvent for the asset."""
    from tests.factories import (
        AgentEventFactory,
        CreatorFactory,
        IpAssetFactory,
    )

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=_OWNER_WALLET))
    with freeze_time("2026-01-01T10:00:00Z"):
        AgentEventFactory(asset=asset, type="HTTP_402")
    with freeze_time("2026-01-01T10:00:10Z"):
        AgentEventFactory(asset=asset, type="ACCEPT")

    response = client.get("/api/v1/events", {"asset_id": str(asset.id)})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    # Time-ascending (oldest first) for incremental rendering.
    assert items[0]["type"] == "HTTP_402"
    assert items[1]["type"] == "ACCEPT"


# --- R1 / R2 / AC-1 / AC-2: register response feeds analysis card -----------

# This is the offline contract test for the SPEC §5 frontend test
# ``test_dragdrop_triggers_register_fetch`` / ``test_render_analysis_card_from_response``:
# it exercises the real register endpoint with fakes and feeds the response into
# the Python mirror of the JS card renderer, proving the drag->register->render
# contract end-to-end without a browser.


@pytest.mark.django_db
def test_dragdrop_register_response_renders_card(client, png_bytes, monkeypatch):
    """A register response's JSON shape renders a full analysis card (offline)."""
    from apps.ip.dashboard import analysis_card_fields
    from tests.fakes import FakeGeminiService, FakeSolanaService, FakeStorageService
    from tests.integration.test_register import _patch_services, _upload, _post

    _patch_services(
        monkeypatch,
        gemini=FakeGeminiService(),
        solana=FakeSolanaService(),
        storage=FakeStorageService(),
    )
    response = _post(client, _upload(png_bytes))
    assert response.status_code == 201
    body = response.json()

    # The frontend pure mirror consumes the response contract directly.
    card = analysis_card_fields(body)
    assert card["asset_id"] == body["asset_id"]
    assert card["anchor_tx"] == body["anchor_tx"]
    assert card["x402_endpoint"] == f"/api/v1/ip/{body['asset_id']}"
    assert card["originality_score"] == 80  # FakeGeminiService default
    assert card["degraded"] is False
    assert isinstance(card["tags"], list)


# --- Certificate payload served via assets context (R8 / AC-7) --------------


@pytest.mark.django_db
def test_library_view_includes_certificate_payload_for_anchored_asset(client):
    """The library view injects per-asset certificate/Explorer data into context."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    owner = CreatorFactory(wallet_address=_OWNER_WALLET)
    IpAssetFactory(
        creator=owner,
        title="Anchored Asset",
        status=IpAsset.ANCHORED,
        anchor_tx_sig="anchor_sig_lib_001",
    )
    response = client.get("/library", {"creator": _OWNER_WALLET})
    assert response.status_code == 200
    content = response.content.decode()
    # Explorer URL for the anchored asset is rendered (R7 / AC-6 wired in page).
    assert "explorer.solana.com/tx/anchor_sig_lib_001" in content
