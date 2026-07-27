"""Settlement models (app_label: ``settlement``).

Holds ``License``, ``RoyaltyDistribution``, ``BatchOrder``, ``BatchItem`` per
architecture SSOT 5.1.

Idempotency: ``License.payment_tx_sig`` is UNIQUE so duplicate settlement of
the same on-chain tx returns the existing license (architecture 8).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import UUIDPrimaryKey


class License(UUIDPrimaryKey):
    """A granted license for one asset to one buyer wallet.

    Architecture 5.1: UUID PK, FK(asset) PROTECT (legal record preservation),
    optional buyer account for browser purchases, FK(session) SET_NULL
    (batch-issued licenses have no session), buyer wallet indexed, price/usage,
    ``payment_tx_sig`` UNIQUE (idempotency key), certificate tx, expiry-bound
    download token, granted_at.
    """

    asset = models.ForeignKey(
        "ip.IpAsset",
        on_delete=models.PROTECT,
        related_name="licenses",
    )
    buyer_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchased_licenses",
    )
    session = models.ForeignKey(
        "negotiation.NegotiationSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="licenses",
    )
    buyer_wallet = models.CharField(max_length=44, db_index=True)
    price_usdc = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True
    )
    price_sol = models.DecimalField(
        max_digits=16, decimal_places=9, null=True, blank=True
    )
    payment_currency = models.CharField(max_length=8, default="USDC")
    usage_type = models.CharField(max_length=30)
    # Idempotency key: verified on-chain payment signature.
    payment_tx_sig = models.CharField(max_length=90, unique=True)
    certificate_tx_sig = models.CharField(max_length=90, null=True, blank=True)
    download_token = models.CharField(max_length=64, null=True, blank=True)
    download_expires_at = models.DateTimeField(null=True, blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-granted_at"]
        indexes = [
            models.Index(fields=["buyer_wallet"]),
            models.Index(fields=["certificate_tx_sig"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"License({self.id})"


class RoyaltyDistribution(UUIDPrimaryKey):
    """One leg of an escrow royalty split (architecture 5.1, S3).

    Architecture 5.1: UUID PK, FK(license), recipient_wallet, role
    (original/secondary), amount, transfer_tx_sig (null until settled),
    status pending/settled/failed.
    """

    ORIGINAL = "original"
    SECONDARY = "secondary"
    ROLE_CHOICES = [
        (ORIGINAL, "original"),
        (SECONDARY, "secondary"),
    ]

    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, "pending"),
        (SETTLED, "settled"),
        (FAILED, "failed"),
    ]

    license = models.ForeignKey(
        License,
        on_delete=models.CASCADE,
        related_name="royalty_distributions",
    )
    recipient_wallet = models.CharField(max_length=44)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    amount_usdc = models.DecimalField(max_digits=12, decimal_places=6)
    transfer_tx_sig = models.CharField(max_length=90, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"RoyaltyDistribution({self.role}, {self.status})"


class BatchOrder(UUIDPrimaryKey):
    """A batch purchase order (architecture 5.1, S2).

    Architecture 5.1: UUID PK, buyer_agent_id, total_usdc, status
    quoted/paid/settled/partial/failed, optional payment_tx_sig.
    """

    QUOTED = "quoted"
    PAID = "paid"
    SETTLED = "settled"
    PARTIAL = "partial"
    FAILED = "failed"
    STATUS_CHOICES = [
        (QUOTED, "quoted"),
        (PAID, "paid"),
        (SETTLED, "settled"),
        (PARTIAL, "partial"),
        (FAILED, "failed"),
    ]

    buyer_agent_id = models.CharField(max_length=80)
    total_usdc = models.DecimalField(max_digits=12, decimal_places=6)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=QUOTED)
    payment_tx_sig = models.CharField(max_length=90, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"BatchOrder({self.id}, {self.status})"


class BatchItem(UUIDPrimaryKey):
    """One line in a BatchOrder (architecture 5.1, S2).

    Architecture 5.1: UUID PK, FK(order) related_name=items, FK(asset)
    PROTECT, unit_price_usdc, FK(license) SET_NULL (linked at settlement).
    """

    order = models.ForeignKey(
        BatchOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    asset = models.ForeignKey(
        "ip.IpAsset",
        on_delete=models.PROTECT,
        related_name="batch_items",
    )
    unit_price_usdc = models.DecimalField(max_digits=12, decimal_places=6)
    license = models.ForeignKey(
        License,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batch_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"BatchItem({self.id})"
