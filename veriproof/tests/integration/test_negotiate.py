"""SPEC-003 integration tests ??POST /api/v1/ip/{asset_id}/negotiate.

Drives the full view through Django's test client with the engine + event
recorder + x402 service swapped via the ``get_*()`` factory seam in
``apps.negotiation.views_api`` (monkeypatched per-test). Mirrors the SPEC-002
``test_get_asset.py`` DI pattern.

Covers the SPEC-003 짠5 integration TDD list (10 tests):
- AC-1  accept returns creator pay_address
- AC-1b accept routes secondary-creation to escrow
- AC-2  counter-offer price range, pay_address null
- AC-3  ACCEPT finalises the session
- AC-9  round appended + AgentEvent recorded
- AC-7  negative offer -> 422
- AC-8  bad usage_type -> 422
-       unknown asset -> 404
- AC-4  Gemini failure -> rule fallback
- AC-10 AP2_ENABLED + ACCEPT -> cart mandate stored
"""
from __future__ import annotations

import decimal
import json
import uuid

import pytest
from django.test.client import RequestFactory

from tests.conftest import VALID_WALLET

NEGOTIATE_TEMPLATE = "/api/v1/ip/{asset_id}/negotiate"

_ESCROW_PUBKEY = "EscrowWallet11111111111111111111111111111111"

_AGENT_HEADERS = {"X-Agent-Protocol": "x402", "Accept": "application/json"}


# --- DI seam helpers --------------------------------------------------------


def _patch_view_services(
    monkeypatch,
    *,
    engine=None,
    recorder=None,
    x402=None,
):
    """Swap the negotiate view's service factories for fakes/reals (DI seam)."""
    if engine is not None:
        monkeypatch.setattr(
            "apps.negotiation.views_api.get_negotiation_engine", lambda: engine
        )
    if recorder is not None:
        monkeypatch.setattr(
            "apps.negotiation.views_api.get_event_recorder", lambda: recorder
        )
    if x402 is not None:
        monkeypatch.setattr(
            "apps.negotiation.views_api.get_x402_service", lambda: x402
        )


def _rule_engine():
    """?뚯뒪???꾩슜 Gemini ?붾툝濡??붿쭊??寃쎄퀎쨌遺덈??앹쓣 寃利앺븳??"""
    import decimal as _decimal

    from services.negotiation_engine import NegotiationEngine
    from services._types import NegotiationResult
    from tests.fakes import FakeGeminiService

    class _PricingDouble(FakeGeminiService):
        def negotiate(self, min_price, target_price, offer_sol, usage_type, history, *, currency="SOL"):
            if offer_sol >= min_price:
                return NegotiationResult("ACCEPT", offer_sol, "test acceptance")
            return NegotiationResult(
                "COUNTER_OFFER",
                (min_price + max(min_price, target_price)) / _decimal.Decimal("2"),
                "test counter",
            )

    return NegotiationEngine(gemini=_PricingDouble(), max_rounds=5)


def _failing_gemini_engine():
    """Engine whose Gemini always raises -> exercises the rule fallback (AC-4)."""
    from services.negotiation_engine import NegotiationEngine
    from tests.fakes import FakeGeminiService

    return NegotiationEngine(
        gemini=FakeGeminiService(fail_negotiate=True), max_rounds=5
    )


def _ap2_x402(settings):
    """A real X402Service with AP2 enabled, for AC-10."""
    from services.x402_service import X402Service

    return X402Service(
        ap2_enabled=True,
        usdc_mint=settings.USDC_MINT_ADDRESS,
        escrow_pubkey=_ESCROW_PUBKEY,
        network="devnet",
    )


# === AC-1: accept returns creator pay_address ===============================


@pytest.mark.django_db
def test_negotiate_accept_returns_pay_address(client, monkeypatch):
    """AC-1: offer >= min on a standalone asset -> ACCEPT, pay_address=creator."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["status"] == "ACCEPT"
    assert decimal.Decimal(body["price_sol"]) == decimal.Decimal("2.0")
    assert body["pay_address"] == VALID_WALLET
    assert body["session_id"]


@pytest.mark.django_db
def test_negotiate_usdc_stores_an_accepted_usdc_price(monkeypatch):
    from apps.negotiation.views_api import negotiate
    from apps.ip.models import IpAsset
    from apps.negotiation.models import NegotiationSession
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        currency="USDC",
        min_amount=decimal.Decimal("0.50"),
        target_amount=decimal.Decimal("1.00"),
        status=IpAsset.LISTED,
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    request = RequestFactory().post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-usdc",
            "offer_usdc": "0.75",
            "usage_type": "commercial",
        },
        content_type="application/json",
        HTTP_X_AGENT_PROTOCOL="x402",
        HTTP_ACCEPT="application/json",
    )
    response = negotiate(request, asset.id)

    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["currency"] == "USDC"
    assert decimal.Decimal(body["price_usdc"]) == decimal.Decimal("0.750000")
    session = NegotiationSession.objects.get(id=body["session_id"])
    assert session.currency == "USDC"
    assert session.final_price_usdc == decimal.Decimal("0.750000")


# === AC-1b: accept routes secondary creation to escrow ======================


@pytest.mark.django_db
def test_negotiate_accept_routes_secondary_to_escrow(client, monkeypatch, settings):
    """AC-1b: 2nd-creation asset -> ACCEPT pay_address == PLATFORM_ESCROW_PUBKEY."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW_PUBKEY
    creator = CreatorFactory(wallet_address=VALID_WALLET)
    parent = IpAssetFactory(creator=creator, status=IpAsset.LISTED)
    child = IpAssetFactory(
        creator=creator,
        parent_asset=parent,
        royalty_share_bps=3000,
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(child.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ACCEPT"
    assert body["pay_address"] == _ESCROW_PUBKEY


# === AC-2: counter-offer range =============================================


@pytest.mark.django_db
def test_negotiate_counter_offer_range(client, monkeypatch):
    """AC-2: offer < min -> COUNTER_OFFER, price in [min,target], pay_address null."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "1.0",
            "usage_type": "editorial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COUNTER_OFFER"
    assert body["pay_address"] is None
    price = decimal.Decimal(body["price_sol"])
    assert decimal.Decimal("1.5") <= price <= decimal.Decimal("3.0")


# === AC-3: ACCEPT finalises the session =====================================


@pytest.mark.django_db
def test_negotiate_creates_and_updates_session(client, monkeypatch):
    """AC-3: ACCEPT -> session.status=accepted, final_price set, rounds>=1."""
    from apps.negotiation.models import NegotiationSession
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 200

    session = NegotiationSession.objects.get(asset_id=asset.id, buyer_agent_id="buyer-1")
    assert session.status == NegotiationSession.ACCEPTED
    assert session.final_price_sol == decimal.Decimal("2.0")
    assert len(session.rounds) >= 1
    assert session.pay_address == VALID_WALLET


@pytest.mark.django_db
def test_negotiate_resumes_existing_session_by_buyer(client, monkeypatch):
    """Separate sessions per (asset, buyer); a second buyer gets a new session."""
    from apps.negotiation.models import NegotiationSession
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    for buyer in ("buyer-A", "buyer-B"):
        client.post(
            NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
            data={
                "buyer_agent_id": buyer,
                "offer_sol": "2.0",
                "usage_type": "commercial",
            },
            content_type="application/json",
            headers=_AGENT_HEADERS,
        )

    assert NegotiationSession.objects.filter(asset_id=asset.id).count() == 2


# === AC-9: rounds + events recorded =========================================


@pytest.mark.django_db
def test_negotiate_records_rounds_and_events(client, monkeypatch):
    """AC-9: a round is appended and an AgentEvent is recorded for the outcome."""
    from apps.common.models import AgentEvent
    from apps.negotiation.models import NegotiationSession
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "1.0",  # below min -> COUNTER_OFFER
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 200

    session = NegotiationSession.objects.get(asset_id=asset.id)
    assert len(session.rounds) == 1
    round_entry = session.rounds[0]
    assert round_entry["status"] == "COUNTER_OFFER"
    assert round_entry["offer_sol"] is not None
    assert round_entry["ts"] is not None

    # R6 / AC-9: an event of the round's type was recorded.
    events = AgentEvent.objects.filter(asset_id=asset.id, session_id=session.id)
    assert events.count() >= 1
    assert events.first().type in {"OFFER", "COUNTER", "ACCEPT"}


# === AC-7: negative/non-numeric offer -> 422 ================================


@pytest.mark.django_db
def test_negotiate_rejects_negative_offer_422(client, monkeypatch):
    """AC-7: offer_sol <= 0 -> 422."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET))
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "-1",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_offer"


@pytest.mark.django_db
def test_negotiate_rejects_non_numeric_offer_422(client, monkeypatch):
    """AC-7: non-numeric offer_sol -> 422."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET))
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "not-a-number",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 422


# === AC-8: bad usage_type -> 422 ===========================================


@pytest.mark.django_db
def test_negotiate_rejects_bad_usage_type_422(client, monkeypatch):
    """AC-8: usage_type outside the allowlist -> 422."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory(wallet_address=VALID_WALLET))
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "unknown",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_usage_type"


# === unknown asset -> 404 ===================================================


@pytest.mark.django_db
def test_negotiate_unknown_asset_404(client, monkeypatch):
    """R13: asset_id that does not exist -> 404."""
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(uuid.uuid4())),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


# === Gemini failure -> explicit unavailable ==================================


@pytest.mark.django_db
def test_negotiate_gemini_failure_returns_unavailable(client, monkeypatch):
    """Gemini ?ㅽ뙣 ???꾩쓽??怨꾩빟 議곌굔??留뚮뱾吏 ?딅뒗??"""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_failing_gemini_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 503
    assert response.json()["error"] == "negotiation_unavailable"


# === AC-10: AP2 Cart Mandate on ACCEPT ======================================


@pytest.mark.django_db
def test_sol_negotiate_does_not_create_usdc_ap2_mandate(client, monkeypatch, settings):
    """Native SOL negotiation must not create a USDC AP2 mandate."""
    from apps.negotiation.models import NegotiationSession
    from tests.factories import CreatorFactory, IpAssetFactory

    settings.AP2_ENABLED = True
    settings.PLATFORM_ESCROW_PUBKEY = _ESCROW_PUBKEY
    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPT"

    session = NegotiationSession.objects.get(asset_id=asset.id)
    assert session.ap2_cart_mandate is None


@pytest.mark.django_db
def test_negotiate_ap2_disabled_leaves_mandate_null(client, monkeypatch):
    """AP2 disabled (default) -> no mandate stored even on ACCEPT."""
    from apps.negotiation.models import NegotiationSession
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(
        creator=CreatorFactory(wallet_address=VALID_WALLET),
        min_price_sol=decimal.Decimal("1.5"),
        target_price_sol=decimal.Decimal("3.0"),
    )
    _patch_view_services(monkeypatch, engine=_rule_engine())

    response = client.post(
        NEGOTIATE_TEMPLATE.format(asset_id=str(asset.id)),
        data={
            "buyer_agent_id": "buyer-1",
            "offer_sol": "2.0",
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 200

    session = NegotiationSession.objects.get(asset_id=asset.id)
    assert session.ap2_cart_mandate is None
