"""Negotiation models (app_label: ``negotiation``).

Holds ``NegotiationSession`` per architecture SSOT 5.1.
"""
from __future__ import annotations

from django.db import models

from apps.common.models import UUIDPrimaryKey


class NegotiationSession(UUIDPrimaryKey):
    """A single buyer-agent negotiation thread for one IpAsset.

    Architecture 5.1: UUID PK, FK(asset), buyer_agent_id, usage_type,
    currency-specific initial/final price, status, rounds JSON log,
    pay_address (set on ACCEPT),
    optional AP2 Cart Mandate (VDC), timestamps.
    """

    NEGOTIATING = "negotiating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    STATUS_CHOICES = [
        (NEGOTIATING, "negotiating"),
        (ACCEPTED, "accepted"),
        (REJECTED, "rejected"),
        (EXPIRED, "expired"),
    ]

    asset = models.ForeignKey(
        "ip.IpAsset",
        on_delete=models.CASCADE,
        related_name="negotiation_sessions",
    )
    buyer_agent_id = models.CharField(max_length=80)
    # commercial / non-commercial / editorial.
    usage_type = models.CharField(max_length=30)
    initial_offer_usdc = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True
    )
    final_price_usdc = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True
    )
    initial_offer_sol = models.DecimalField(
        max_digits=16, decimal_places=9, null=True, blank=True
    )
    final_price_sol = models.DecimalField(
        max_digits=16, decimal_places=9, null=True, blank=True
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=NEGOTIATING
    )
    # [{offer, counter, status, reason, ts}] round log (JSONB on PG).
    rounds = models.JSONField(default=list)
    # Recipient address set on ACCEPT (= creator wallet or escrow, see 8).
    pay_address = models.CharField(max_length=44, null=True, blank=True)
    # AP2 Cart Mandate Verifiable Digital Credential (when AP2_ENABLED).
    ap2_cart_mandate = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["buyer_agent_id"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"NegotiationSession({self.id}, {self.status})"
