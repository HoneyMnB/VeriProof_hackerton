"""factory_boy factories for every model (test-plan 4).

Usage: ``CreatorFactory()``, ``IpAssetFactory()``, etc. UUID-PK models rely on
Django to assign the uuid4 PK on save (factories do not set ``id``).
"""
from __future__ import annotations

import secrets

import factory
from django.utils import timezone
from factory.fuzzy import FuzzyDecimal, FuzzyInteger

from apps.common.models import AgentEvent
from apps.ip.models import Creator, IpAsset
from apps.negotiation.models import NegotiationSession
from apps.settlement.models import BatchItem, BatchOrder, License


def _unique_sha256() -> str:
    """64-char hex string; unique per call (used for image_sha256)."""
    return secrets.token_hex(32)


def _unique_wallet(n: int) -> str:
    """Deterministic unique 44-char wallet string for tests."""
    return f"wallet{n}".ljust(44, "x")[:44]


class CreatorFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Creator

    wallet_address = factory.Sequence(_unique_wallet)
    display_name = factory.Faker("name")


class IpAssetFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = IpAsset

    creator = factory.SubFactory(CreatorFactory)
    title = factory.Faker("catch_phrase")
    tags = factory.List(["sample", "test"])
    category = "photography"
    originality_score = FuzzyInteger(50, 99)
    # FuzzyDecimal takes float low/high (returns a Decimal).
    min_price_usdc = FuzzyDecimal(0.50, 2.00)
    target_price_usdc = FuzzyDecimal(2.00, 10.00)
    min_price_sol = FuzzyDecimal(0.05, 0.20)
    target_price_sol = FuzzyDecimal(0.20, 1.00)
    min_amount = factory.SelfAttribute("min_price_sol")
    target_amount = factory.SelfAttribute("target_price_sol")
    currency = "USDC"
    image_sha256 = factory.LazyFunction(_unique_sha256)
    thumbnail_url = factory.Sequence(lambda n: f"https://cdn.test/thumb-{n}.png")
    watermark_url = factory.Sequence(lambda n: f"https://cdn.test/wm-{n}.png")
    registration_certificate_tx_sig = factory.Sequence(lambda n: f"registration_cert_{n}")
    status = IpAsset.LISTED


class NegotiationSessionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = NegotiationSession

    asset = factory.SubFactory(IpAssetFactory)
    buyer_agent_id = factory.Sequence(lambda n: f"buyer-agent-{n}")
    usage_type = "commercial"
    initial_offer_usdc = FuzzyDecimal(0.50, 2.00)
    initial_offer_sol = FuzzyDecimal(0.05, 0.20)
    status = NegotiationSession.NEGOTIATING
    rounds = factory.List([])


class LicenseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = License

    asset = factory.SubFactory(IpAssetFactory)
    session = factory.SubFactory(
        NegotiationSessionFactory, asset=factory.SelfAttribute("..asset")
    )
    buyer_wallet = factory.Sequence(_unique_wallet)
    price_usdc = FuzzyDecimal(0.50, 5.00)
    usage_type = "commercial"
    # Unique idempotency key per license.
    payment_tx_sig = factory.Sequence(lambda n: f"pay_sig_{n}_{secrets.token_hex(8)}")
    download_token = factory.Sequence(lambda n: f"download-token-{n}")
    download_expires_at = factory.LazyFunction(
        lambda: timezone.now() + timezone.timedelta(days=7)
    )


class BatchOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BatchOrder

    buyer_agent_id = factory.Sequence(lambda n: f"batch-buyer-{n}")
    total_usdc = FuzzyDecimal(0.50, 50.00)
    status = BatchOrder.QUOTED


class BatchItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BatchItem

    order = factory.SubFactory(BatchOrderFactory)
    asset = factory.SubFactory(IpAssetFactory)
    unit_price_usdc = FuzzyDecimal(0.05, 1.00)


class AgentEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AgentEvent

    asset = factory.SubFactory(IpAssetFactory)
    session = None
    type = "HTTP_402"
    payload = factory.Dict({"detail": "sample event"})
