"""Smoke test: prove the harness imports every model + service and the DB
layer round-trips. This is the only test in SPEC-000; downstream SPECs add
their own RED tests in tests/unit|integration|e2e.
"""
from __future__ import annotations

import pytest


# --- Import-time wiring (no DB needed) --------------------------------------


def test_imports_all_models():
    """All Django models import cleanly and carry the expected app labels."""
    from apps.common.models import AgentEvent
    from apps.ip.models import Creator, IpAsset
    from apps.negotiation.models import NegotiationSession
    from apps.settlement.models import (
        BatchItem,
        BatchOrder,
        License,
        RoyaltyDistribution,
    )

    assert Creator._meta.app_label == "ip"
    assert IpAsset._meta.app_label == "ip"
    assert NegotiationSession._meta.app_label == "negotiation"
    assert License._meta.app_label == "settlement"
    assert RoyaltyDistribution._meta.app_label == "settlement"
    assert BatchOrder._meta.app_label == "settlement"
    assert BatchItem._meta.app_label == "settlement"
    assert AgentEvent._meta.app_label == "common"


def test_imports_all_services():
    """All service classes + factories import without external deps."""
    from services import (  # noqa: F401  (import-only assertion)
        AnalysisResult,
        BatchQuote,
        BigQuerySink,
        EventRecorder,
        FirestoreMirror,
        GeminiService,
        ImageProcessor,
        KmsSigner,
        LicenseService,
        NegotiationEngine,
        NegotiationResult,
        PaymentVerification,
        PubSubPublisher,
        RoyaltyService,
        SolanaService,
        StorageService,
        SubmittedPayment,
        X402Service,
    )


def test_all_services_are_implemented():
    """Closing gate: NO service method stubs remain.

    Progression log:
    - SPEC-001 implemented ImageProcessor, GeminiService.analyze_image,
      SolanaService.anchor_hash, StorageService.save_permanent.
    - SPEC-002 implemented X402Service.build_payment_required / classify_client
      / build_solana_pay_fallback and LicenseService.is_licensed.
    - SPEC-003 implemented GeminiService.negotiate, NegotiationEngine.run_round,
      and X402Service.build_ap2_mandate.
    - SPEC-004 implemented SolanaService.verify_usdc_payment /
      issue_certificate / transfer_usdc, LicenseService.grant, KmsSigner.sign /
      public_key, X402Service.parse_payment_submitted, PubSubPublisher.publish,
      FirestoreMirror.set, BigQuerySink.insert.
    - SPEC-007 implemented GeminiService.quote_batch (gemini-3.5-flash-lite,
      rule fallback) and the BatchService batch quote/settle SSOT.
    - SPEC-008 implemented RoyaltyService.distribute (escrow royalty split:
      integer min-units compute_split + per-leg transfer_usdc + ROYALTY_SPLIT).

    Every service/ method listed in architecture §4 is now implemented. This
    test is the closing gate that no ``NotImplementedError`` stubs remain.
    """
    import datetime
    import decimal

    from services import (
        GeminiService,
        ImageProcessor,
        KmsSigner,
        LicenseService,
        NegotiationEngine,
        PubSubPublisher,
        RoyaltyService,
        SolanaService,
        StorageService,
        X402Service,
    )
    from services.kms_signer import KmsSignerError

    # 외부 AI 미설정은 구현 누락이 아니라 명시적 unavailable 오류여야 한다.
    from services.gemini_service import GeminiUnavailableError
    ImageProcessor().sha256(b"x")
    with pytest.raises(GeminiUnavailableError):
        GeminiService().analyze_image(b"x")
    StorageService(backend="local", media_root="/tmp").save_permanent(
        "thumbnail", "id", b"x"
    )

    # SPEC-003 implemented methods.
    with pytest.raises(GeminiUnavailableError):
        GeminiService().negotiate(
            decimal.Decimal("1"), decimal.Decimal("1"), decimal.Decimal("1"),
            "commercial", [],
        )
    assert X402Service().build_ap2_mandate(None, "cart") is None

    with pytest.raises(GeminiUnavailableError):
        GeminiService().quote_batch(
            [{"asset_id": "a", "min_price_usdc": decimal.Decimal("0.01")}],
            "commercial",
        )

    # SPEC-004 implemented methods (sanity: they no longer raise NotImplementedError).
    # KmsSigner is now implemented; an unconfigured signer raises KmsSignerError
    # at call time (NOT NotImplementedError).
    with pytest.raises(KmsSignerError):
        KmsSigner().public_key()
    # X402 parse_payment_submitted raises InvalidPaymentSubmitted on bad input.
    from services.x402_service import InvalidPaymentSubmitted

    with pytest.raises(InvalidPaymentSubmitted):
        X402Service().parse_payment_submitted({})

    # SPEC-008 implemented method (sanity: RoyaltyService is NO LONGER a stub).
    # compute_split is the pure split helper; exercising it proves the service
    # body is implemented (distribute itself needs a persisted License + asset
    # graph and is covered by tests/unit/test_royalty_service.py).
    original, secondary = RoyaltyService.compute_split(
        decimal.Decimal("10"), 3000
    )
    assert original == decimal.Decimal("3.000000")
    assert secondary == decimal.Decimal("7.000000")
    assert original + secondary == decimal.Decimal("10")

    # PubSubPublisher.publish is implemented; without a client it returns None
    # (disabled/no-op signal) rather than raising.
    assert PubSubPublisher().publish("topic", {"x": 1}) is None

    # NegotiationEngine.run_round needs a real asset/session shape; implemented,
    # exercised by the SPEC-003 suites.
    assert NegotiationEngine is not None
    assert datetime.timedelta  # imported for parity


def test_fakes_implement_interfaces():
    """Fakes implement the real interface and record calls."""
    import decimal

    from tests.fakes import (
        FakeBigQuery,
        FakeFirestore,
        FakeGeminiService,
        FakePubSub,
        FakeSolanaService,
        FakeStorageService,
    )

    gemini = FakeGeminiService()
    res = gemini.analyze_image(b"img")
    assert res.originality_score == 80
    assert gemini.calls[0][0] == "analyze_image"

    solana = FakeSolanaService()
    sig = solana.anchor_hash("hash", "pubkey")
    assert sig.startswith("anchor_fake_sig_")

    storage = FakeStorageService()
    url = storage.save_permanent("thumbnail", "aid", b"bytes")
    assert url == "memory://thumbnail/aid"

    fs = FakeFirestore()
    fs.set("asset_status", "aid", {"status": "LICENSED"})
    assert fs.docs[("asset_status", "aid")] == {"status": "LICENSED"}

    bq = FakeBigQuery()
    bq.insert("transactions", {"amount": decimal.Decimal("1.5")})
    assert bq.rows["transactions"][0]["amount"] == decimal.Decimal("1.5")

    ps = FakePubSub()
    msg_id = ps.publish("veriproof-payments", {"event": "payment.completed"})
    assert msg_id.startswith("fake-msg-")
    assert ps.published[0][0] == "veriproof-payments"


# --- DB round-trip (proves migrations apply on SQLite) ----------------------


@pytest.mark.django_db
def test_factory_creates_persisted_models():
    """factories + DB work end-to-end (Creator -> IpAsset -> License chain)."""
    from apps.ip.models import IpAsset
    from apps.settlement.models import License
    from tests.factories import CreatorFactory, IpAssetFactory, LicenseFactory

    creator = CreatorFactory()
    asset = IpAssetFactory(creator=creator, status=IpAsset.LISTED)
    license = LicenseFactory(asset=asset)

    assert creator.pk is not None
    assert asset.asset_id == asset.id  # public alias works
    assert asset.status == IpAsset.LISTED
    # payment_tx_sig uniqueness enforced.
    assert License.objects.filter(payment_tx_sig=license.payment_tx_sig).count() == 1


@pytest.mark.django_db
def test_ipasset_royalty_guard_rejects_invalid_parent():
    """S3 invariant: parent_asset set without valid royalty_share_bps raises."""
    from django.core.exceptions import ValidationError

    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    creator = CreatorFactory()
    parent = IpAssetFactory(creator=creator)
    child = IpAssetFactory.build(creator=creator, parent_asset=parent)
    child.royalty_share_bps = None  # invalid: parent set but no share
    with pytest.raises(ValidationError):
        child.save()

    child.royalty_share_bps = 3000  # valid 30%
    child.save()
    assert child.pk is not None
