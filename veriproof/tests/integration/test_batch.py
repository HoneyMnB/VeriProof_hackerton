"""SPEC-007 integration — POST /api/v1/ip/batch/negotiate + /batch/settle.

Drives the full views through Django's test client. Both views delegate to the
``BatchService`` SSOT (apps.settlement.batch_services); tests inject a
BatchService wired with fakes via the ``apps.ip.views_api.get_batch_service``
DI seam (same pattern as SPEC-004 ``get_settlement_service``).

TDD list (7):
- test_batch_negotiate_returns_quote (AC-1)
- test_batch_negotiate_empty_items_422 (AC-3)
- test_batch_negotiate_invalid_asset_422 (AC-4)
- test_batch_negotiate_exceeds_max_422 (AC-5)
- test_batch_settle_success_grants_licenses (AC-6)
- test_batch_settle_idempotent (AC-10)
- test_batch_settle_logs_each_item (R9)
"""
from __future__ import annotations

import decimal

import pytest

_NEGOTIATE = "/api/v1/ip/batch/negotiate"
_SETTLE = "/api/v1/ip/batch/settle"
_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDNCDU"
_AGENT_HEADERS = {"X-Agent-Protocol": "x402", "Accept": "application/json"}


# --- DI seam helpers --------------------------------------------------------


class _RecordingRecorder:
    """EventRecorder stand-in that ALSO persists AgentEvent rows (so R9 can
    query them) and records every call."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def record(self, type, payload, asset=None, session=None):
        from apps.common.models import AgentEvent

        self.calls.append((type, payload or {}))
        return AgentEvent.objects.create(
            type=type, payload=payload or {}, asset=asset, session=session
        )


def _batch_service(
    *,
    fail_quote: bool = False,
    valid: bool = True,
    fail_amount: decimal.Decimal | None = None,
    fail_license_assets: set | None = None,
):
    """Build a BatchService wired with fakes; controls quote/verify/grant paths."""
    from apps.settlement.batch_services import BatchService
    from services._types import PaymentVerification
    from services.license_service import LicenseService
    from tests.fakes import (
        FakeBigQuery,
        FakeGeminiService,
        FakeLicenseService,
        FakeSolanaService,
    )

    gemini = FakeGeminiService(fail_quote=fail_quote)
    solana = FakeSolanaService()
    if not valid:
        solana.verification = PaymentVerification(
            is_valid=False,
            amount=fail_amount or decimal.Decimal("0.10"),
            sender="x",
            slot=1,
            commitment="confirmed",
        )
    if fail_license_assets:
        license_service = FakeLicenseService(fail_on_asset_ids=fail_license_assets)
    else:
        license_service = LicenseService(event_recorder=_RecordingRecorder())
    recorder = _RecordingRecorder()
    return BatchService(
        gemini=gemini,
        solana=solana,
        license_service=license_service,
        event_recorder=recorder,
        bigquery=FakeBigQuery(),
        usdc_mint=_USDC_MINT,
    )


def _patch_batch(monkeypatch, svc):
    monkeypatch.setattr("apps.settlement.views_api.get_batch_service", lambda: svc)


# === AC-1: negotiate returns a quote ========================================


@pytest.mark.django_db
def test_batch_negotiate_returns_quote(client, monkeypatch):
    """AC-1: 3-item negotiate -> 200 quote with per-item unit_price + total."""
    from tests.factories import CreatorFactory, IpAssetFactory

    assets = [
        IpAssetFactory(
            creator=CreatorFactory(),
            min_price_usdc=decimal.Decimal("0.05"),
        )
        for _ in range(3)
    ]
    svc = _batch_service()
    _patch_batch(monkeypatch, svc)

    response = client.post(
        _NEGOTIATE,
        data={
            "buyer_agent_id": "buyer-agent-1",
            "items": [str(a.id) for a in assets],
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "quoted"
    assert body["order_id"]
    # total_usdc serializes as a 6-dp USDC string (codebase convention).
    assert decimal.Decimal(body["total_usdc"]) == decimal.Decimal("0.15")
    assert len(body["items"]) == 3
    assert all(decimal.Decimal(it["unit_price_usdc"]) == decimal.Decimal("0.05") for it in body["items"])


# === AC-3: empty items -> 422 ===============================================


@pytest.mark.django_db
def test_batch_negotiate_empty_items_422(client, monkeypatch):
    """AC-3: empty items list -> 422."""
    svc = _batch_service()
    _patch_batch(monkeypatch, svc)

    response = client.post(
        _NEGOTIATE,
        data={
            "buyer_agent_id": "buyer-agent-1",
            "items": [],
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_items"


# === AC-4: unknown asset -> 422 + invalid list ==============================


@pytest.mark.django_db
def test_batch_negotiate_invalid_asset_422(client, monkeypatch):
    """AC-4: an unknown asset_id in items -> 422 with the invalid id list."""
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    import uuid

    unknown = str(uuid.uuid4())
    svc = _batch_service()
    _patch_batch(monkeypatch, svc)

    response = client.post(
        _NEGOTIATE,
        data={
            "buyer_agent_id": "buyer-agent-1",
            "items": [str(asset.id), unknown],
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_items"
    assert body["invalid_ids"] == [unknown]


# === AC-5: exceeds BATCH_MAX_ITEMS -> 422 ===================================


@pytest.mark.django_db
def test_batch_negotiate_exceeds_max_422(client, monkeypatch, settings):
    """AC-5: more items than BATCH_MAX_ITEMS -> 422."""
    from tests.factories import CreatorFactory, IpAssetFactory

    # Lower the cap so we don't have to create 201 rows.
    settings.BATCH_MAX_ITEMS = 2
    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(3)]
    svc = _batch_service()
    _patch_batch(monkeypatch, svc)

    response = client.post(
        _NEGOTIATE,
        data={
            "buyer_agent_id": "buyer-agent-1",
            "items": [str(a.id) for a in assets],
            "usage_type": "commercial",
        },
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["error"] == "too_many_items"


# === AC-6: settle success grants licenses ===================================


@pytest.mark.django_db
def test_batch_settle_success_grants_licenses(client, monkeypatch):
    """AC-6: total-matching payment -> 200, each item gets a license + token."""
    from apps.settlement.models import BatchOrder, License
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(3)]
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.15"))
    for a in assets:
        BatchItemFactory(order=order, asset=a, unit_price_usdc=decimal.Decimal("0.05"))

    svc = _batch_service()
    _patch_batch(monkeypatch, svc)

    response = client.post(
        _SETTLE,
        data={"order_id": str(order.id), "tx_signature": "tx_batch_ok"},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "settled"
    assert len(body["items"]) == 3
    assert all(it["download_token"] for it in body["items"])
    order.refresh_from_db()
    assert order.status == BatchOrder.SETTLED
    assert License.objects.filter(payment_tx_sig__startswith="batch:tx_batch_ok").count() == 3


# === AC-10: settle idempotent on (order_id, tx_signature) ===================


@pytest.mark.django_db
def test_batch_settle_idempotent(client, monkeypatch):
    """AC-10/R10: replaying the same (order_id, tx_signature) -> no duplicate."""
    from apps.settlement.models import License
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(2)]
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.10"))
    for a in assets:
        BatchItemFactory(order=order, asset=a, unit_price_usdc=decimal.Decimal("0.05"))

    svc = _batch_service()
    _patch_batch(monkeypatch, svc)

    r1 = client.post(
        _SETTLE,
        data={"order_id": str(order.id), "tx_signature": "tx_idem"},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert r1.status_code == 200

    r2 = client.post(
        _SETTLE,
        data={"order_id": str(order.id), "tx_signature": "tx_idem"},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert r2.status_code == 200
    # Exactly 2 licenses (one per item) — the replay created NO duplicates.
    assert License.objects.filter(payment_tx_sig__startswith="batch:tx_idem").count() == 2


# === R9: per-item event + BigQuery logging ==================================


@pytest.mark.django_db
def test_batch_settle_logs_each_item(client, monkeypatch):
    """R9: each settled item records an event + a BigQuery transactions row."""
    from apps.common.models import AgentEvent
    from tests.factories import (
        CreatorFactory,
        IpAssetFactory,
        BatchItemFactory,
        BatchOrderFactory,
    )

    assets = [IpAssetFactory(creator=CreatorFactory()) for _ in range(3)]
    order = BatchOrderFactory(total_usdc=decimal.Decimal("0.15"))
    for a in assets:
        BatchItemFactory(order=order, asset=a, unit_price_usdc=decimal.Decimal("0.05"))

    svc = _batch_service()
    _patch_batch(monkeypatch, svc)

    response = client.post(
        _SETTLE,
        data={"order_id": str(order.id), "tx_signature": "tx_log"},
        content_type="application/json",
        headers=_AGENT_HEADERS,
    )
    assert response.status_code == 200

    # Per-item PAYMENT_VERIFIED events (fanned out by LicenseService.grant) +
    # one order-level BATCH_SETTLED event.
    pay_verified = AgentEvent.objects.filter(type="PAYMENT_VERIFIED").count()
    assert pay_verified == 3
    assert AgentEvent.objects.filter(type="BATCH_SETTLED").exists()

    # BigQuery received one transactions row per item.
    bq = svc.bigquery
    tx_inserts = [
        c for c in bq.calls
        if c[0] == "insert" and c[1]["table"] == "transactions"
    ]
    assert len(tx_inserts) == 3
    assert all(
        c[1]["row"]["payment_tx_sig"].startswith("batch:tx_log") for c in tx_inserts
    )
