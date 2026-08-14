"""Shared models for the VeriProof AI project (app_label: ``common``).

Holds:
- ``UUIDPrimaryKey`` abstract base: UUID PK shared by all UUID-keyed models.
- ``AgentEvent``: cross-cutting audit/timeline entity (architecture 5.1).

AgentEvent references models that live in other apps via string FK references
(``ip.IpAsset``, ``negotiation.NegotiationSession``); Django resolves these at
migration time so no import cycle is created.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class UUIDPrimaryKey(models.Model):
    """Abstract base providing a UUID PK exposed as the model ``id``.

    For IpAsset this same value is the public ``asset_id``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class AgentEvent(models.Model):
    """Cross-cutting audit/timeline event (architecture 5.1).

    Fan-out target of ``EventRecorder.record()``: PostgreSQL (this model) plus
    Firestore (real-time) plus BigQuery (audit ledger).

    ``asset`` and ``session`` are nullable + SET_NULL so the audit trail
    survives deletion of the referenced entity.
    """

    # Nullability mirrors the SSOT: both FKs are optional.
    asset = models.ForeignKey(
        "ip.IpAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    session = models.ForeignKey(
        "negotiation.NegotiationSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    account_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_events",
    )
    correlation_id = models.UUIDField(null=True, blank=True, db_index=True)
    # e.g. HTTP_402 / OFFER / COUNTER / ACCEPT / PAYMENT_VERIFIED /
    # CERT_ISSUED / ANCHORED / ROYALTY_SPLIT (architecture 5.1).
    type = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["type"]),
            models.Index(fields=["asset", "created_at"]),
            models.Index(
                fields=["account_owner", "created_at"],
                name="common_age_account_672e11_idx",
            ),
            models.Index(
                fields=["correlation_id", "created_at"],
                name="common_age_correla_14dbbc_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"AgentEvent({self.type}@{self.created_at})"
