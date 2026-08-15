"""IP asset models (app_label: ``ip``).

Holds ``Creator`` and ``IpAsset`` per architecture SSOT 5.1. Field names,
types, and constraints match the SSOT exactly so downstream SPECs can rely on
this contract.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.models import UUIDPrimaryKey


class Creator(models.Model):
    """Self-custodial Solana creator identified by a wallet address.

    Architecture 5.1: id BigAuto PK, wallet_address varchar(44) unique+indexed,
    display_name nullable, created_at auto.
    """

    id = models.BigAutoField(primary_key=True)
    wallet_address = models.CharField(
        max_length=44, unique=True, db_index=True
    )
    display_name = models.CharField(max_length=80, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"Creator({self.wallet_address[:8]}...)"


class IpAsset(UUIDPrimaryKey):
    """An IP asset registered by a creator (architecture 5.1).

    The UUID PK (``id``) is the public ``asset_id`` in the API surface.
    """

    DRAFT = "draft"
    ANCHORED = "anchored"
    LISTED = "listed"
    RETIRED = "retired"
    STATUS_CHOICES = [
        (DRAFT, "draft"),
        (ANCHORED, "anchored"),
        (LISTED, "listed"),
        (RETIRED, "retired"),
    ]

    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    SOFTWARE = "software"
    PRODUCT = "product"
    OTHER = "other"
    ASSET_TYPE_CHOICES = [
        (IMAGE, "image"),
        (DOCUMENT, "document"),
        (AUDIO, "audio"),
        (VIDEO, "video"),
        (SOFTWARE, "software"),
        (PRODUCT, "product"),
        (OTHER, "other"),
    ]

    PRIVATE = "private"
    PUBLIC = "public"
    VISIBILITY_CHOICES = [(PRIVATE, "private"), (PUBLIC, "public")]

    creator = models.ForeignKey(
        Creator,
        on_delete=models.PROTECT,
        related_name="assets",
    )
    # 작품의 계정 소유자와 창작자 지갑을 분리한다. 한 계정은 여러 지갑의
    # 작품을 한 라이브러리에서 관리하고, 지갑은 작품별 증명·정산 정보로 남긴다.
    account_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="library_assets",
    )
    title = models.CharField(max_length=120, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    # 등록 시점 AI가 자산을 분석해 생성한 설명. 사용자 description과 별개로 보존해
    # 에이전트(외부/내부) 검색·발견에 활용한다.
    ai_description = models.TextField(null=True, blank=True)
    asset_type = models.CharField(
        max_length=20, choices=ASSET_TYPE_CHOICES, default=IMAGE, db_index=True
    )
    visibility = models.CharField(
        max_length=10, choices=VISIBILITY_CHOICES, default=PRIVATE, db_index=True
    )
    content_mime_type = models.CharField(max_length=100, null=True, blank=True)
    # JSONField -> JSONB on PG, TEXT on SQLite (no JSONB-only features).
    tags = models.JSONField(default=list)
    # 등록 시점 AI가 붙인 태그. 사용자 tags와 분리 저장해 A2A 검색성을 높인다.
    ai_tags = models.JSONField(default=list)
    category = models.CharField(max_length=60, null=True, blank=True)
    # Gemini originality score 0..100.
    originality_score = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    min_price_usdc = models.DecimalField(
        max_digits=12, decimal_places=6,
        null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    target_price_usdc = models.DecimalField(
        max_digits=12, decimal_places=6,
        null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    # Native Devnet SOL price for autonomous buyer agents. It is separate from
    # legacy USDC terms: no currency conversion or inferred fallback is used.
    target_price_sol = models.DecimalField(
        max_digits=16,
        decimal_places=9,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    min_price_sol = models.DecimalField(
        max_digits=16,
        decimal_places=9,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    # Canonical amount fields. Existing SOL-price values are copied without
    # conversion by migration 0020; currency is recorded as requested.
    target_amount = models.DecimalField(
        max_digits=16,
        decimal_places=9,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    min_amount = models.DecimalField(
        max_digits=16,
        decimal_places=9,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=8, default="USDC")
    # Permanent original-content hash (64 hex chars).
    image_sha256 = models.CharField(max_length=64, unique=True, db_index=True)
    # 이미지 검색 후보를 빠르게 좁히는 64-bit 지각 해시. 원본 SHA-256과 달리
    # 리사이즈·가벼운 인코딩 변경에도 유사 이미지를 찾기 위한 보조 지문이다.
    perceptual_hash = models.CharField(max_length=16, null=True, blank=True, db_index=True)
    # Permanent preview artifacts.
    thumbnail_url = models.CharField(max_length=500, null=True, blank=True)
    watermark_url = models.CharField(max_length=500, null=True, blank=True)
    # Temporary original (purged after ORIGINAL_RETENTION_DAYS).
    original_url = models.CharField(max_length=500, null=True, blank=True)
    original_expires_at = models.DateTimeField(null=True, blank=True)
    original_purged = models.BooleanField(default=False)
    # On-chain anchoring signature (indexed for lookup).
    anchor_tx_sig = models.CharField(
        max_length=90, null=True, blank=True, db_index=True
    )
    # 등록 완료를 증명하는 별도 Solana Memo 인증서. 공개 카탈로그 노출의
    # 필수 조건이며, 구매 후 발급되는 License 인증서와 역할이 다르다.
    registration_certificate_tx_sig = models.CharField(
        max_length=90, null=True, blank=True, db_index=True
    )
    # Secondary-creation lineage (self-FK). PROTECT preserves royalty lineage.
    parent_asset = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="derivatives",
    )
    # Basis points of the parent's share (3000 = 30%). Null for originals.
    royalty_share_bps = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["creator"]),
            models.Index(
                fields=["visibility", "status", "asset_type"],
                name="ip_ipasset_visibil_df7361_idx",
            ),
        ]

    # --- Validation invariant (architecture 5.1 S3 constraint) --------------
    # If a parent_asset is set, royalty_share_bps MUST be in 1..10000.
    def clean(self):
        """파생 작품의 로열티 분담률(1..10000 bps) 불변식을 검증한다."""
        super().clean()
        if self.parent_asset_id is not None:
            if (
                self.royalty_share_bps is None
                or not (1 <= self.royalty_share_bps <= 10000)
            ):
                raise ValidationError(
                    {
                        "royalty_share_bps": (
                            "royalty_share_bps must be between 1 and 10000 "
                            "(basis points) when parent_asset is set."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        """저장 직전 로열티 불변식을 강제 적용하고 부모 저장에 위임한다."""
        # Enforce the S3 invariant on every save (not just full_clean) so the
        # contract holds even when callers bypass form validation.
        if self.parent_asset_id is not None:
            if (
                self.royalty_share_bps is None
                or not (1 <= self.royalty_share_bps <= 10000)
            ):
                raise ValidationError(
                    {
                        "royalty_share_bps": (
                            "royalty_share_bps must be between 1 and 10000 "
                            "(basis points) when parent_asset is set."
                        )
                    }
                )
        super().save(*args, **kwargs)

    @property
    def asset_id(self) -> uuid.UUID:
        """Public alias for the UUID PK (architecture exposes ``asset_id``)."""
        return self.id

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"IpAsset({self.id})"


class AssetComponent(UUIDPrimaryKey):
    """한 저작물 인증서에 포함된 보조 소스 파일의 검증 가능한 매니페스트 항목."""

    asset = models.ForeignKey(IpAsset, on_delete=models.CASCADE, related_name="components")
    file_name = models.CharField(max_length=255)
    content_mime_type = models.CharField(max_length=100)
    content_sha256 = models.CharField(max_length=64, db_index=True)
    storage_url = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class AssetImage(UUIDPrimaryKey):
    """한 작품에 속한 추가 이미지와 공개용 보호 미리보기 정보.

    첫 번째 이미지는 기존 ``IpAsset``의 미리보기/원본 필드를 계속 사용해 기존
    카탈로그와 라이브러리 계약을 보존한다. 이 모델은 같은 작품에 추가된 이미지에만
    사용되며, 독립적인 인증서·라이선스 대상이 아니다.
    """

    asset = models.ForeignKey(IpAsset, on_delete=models.CASCADE, related_name="gallery_images")
    position = models.PositiveIntegerField()
    file_name = models.CharField(max_length=255)
    content_mime_type = models.CharField(max_length=100)
    content_sha256 = models.CharField(max_length=64, db_index=True)
    watermark_url = models.CharField(max_length=500)
    original_url = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["asset", "position"], name="ip_assetimage_unique_position"),
        ]


class AssistantMessage(models.Model):
    """Auditable creator-assistant conversation, isolated from licensing flows."""

    USER = "user"
    ASSISTANT = "assistant"
    ROLE_CHOICES = [(USER, "user"), (ASSISTANT, "assistant")]

    id = models.BigAutoField(primary_key=True)
    creator = models.ForeignKey(
        Creator, on_delete=models.CASCADE, related_name="assistant_messages"
    )
    # 한 대화 세션에 속한 메시지를 묶는다. 제목형 히스토리와 정확한 재개 경로의 기준이다.
    conversation_id = models.UUIDField(null=True, blank=True, db_index=True)
    # 첫 사용자 메시지에만 저장하는 사용자가 지정한 대화 제목이다. 원문을 변경하지 않고
    # 사이드바 제목만 바꿀 수 있도록 대화 단위의 표시명을 분리한다.
    conversation_title = models.CharField(max_length=120, null=True, blank=True)
    role = models.CharField(max_length=12, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["creator", "created_at"],
                name="ip_assistan_creator_042b78_idx",
            )
        ]


class ConversationAttachment(UUIDPrimaryKey):
    """대화 분석용 임시 첨부 파일과 실제 AI 분석 결과다."""

    creator = models.ForeignKey(
        Creator, on_delete=models.CASCADE, related_name="conversation_attachments"
    )
    source_message = models.ForeignKey(
        AssistantMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
    )
    file_name = models.CharField(max_length=255)
    content_mime_type = models.CharField(max_length=100)
    content_sha256 = models.CharField(max_length=64, db_index=True)
    perceptual_hash = models.CharField(max_length=16, null=True, blank=True, db_index=True)
    temporary_url = models.CharField(max_length=500)
    expires_at = models.DateTimeField()
    analysis = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["creator", "created_at"], name="ip_attach_creator_idx")
        ]


class AgentDirective(models.Model):
    """창작자가 확인·수정하는 비서 행동 지침이다.

    대화 원문과 분리해 저장하며, 활성 지침만 Gemini 컨텍스트에 전달한다.
    """

    id = models.BigAutoField(primary_key=True)
    creator = models.ForeignKey(
        Creator, on_delete=models.CASCADE, related_name="agent_directives"
    )
    title = models.CharField(max_length=120)
    instruction = models.TextField(max_length=2000)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(
                fields=["creator", "is_active", "updated_at"],
                name="ip_directive_creator_idx",
            )
        ]


class AssistantAction(models.Model):
    """대화에서 실행된 비서 도구 호출과 검증 결과의 감사 기록이다."""

    COMPLETED = "completed"
    AWAITING_INPUT = "awaiting_input"
    REJECTED = "rejected"
    FAILED = "failed"
    STATUS_CHOICES = [
        (COMPLETED, "completed"),
        (AWAITING_INPUT, "awaiting_input"),
        (REJECTED, "rejected"),
        (FAILED, "failed"),
    ]

    id = models.BigAutoField(primary_key=True)
    creator = models.ForeignKey(
        Creator, on_delete=models.CASCADE, related_name="assistant_actions"
    )
    source_message = models.ForeignKey(
        AssistantMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actions",
    )
    action_name = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    request_payload = models.JSONField(default=dict)
    result_payload = models.JSONField(default=dict)
    verification_passed = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["creator", "created_at"], name="ip_action_creator_idx"
            )
        ]


class SubscriptionPlan(models.Model):
    """등록·인증서 발급 비용을 선납하는 운영 플랜 정의다."""

    id = models.BigAutoField(primary_key=True)
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    monthly_fee_usdc = models.DecimalField(max_digits=12, decimal_places=6)
    included_registrations = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class CreatorSubscription(models.Model):
    """창작자의 활성 구독 및 기간 내 등록 권한 사용량이다."""

    ACTIVE = "active"
    EXPIRED = "expired"
    STATUS_CHOICES = [(ACTIVE, "active"), (EXPIRED, "expired")]
    id = models.BigAutoField(primary_key=True)
    creator = models.ForeignKey(Creator, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ACTIVE)
    payment_tx_sig = models.CharField(max_length=140, unique=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    registrations_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["creator", "status", "period_end"], name="ip_sub_creator_status_idx")]


class RegistrationCharge(models.Model):
    """구독이 부담한 등록·인증서 발급 권한 사용의 감사 기록이다."""

    id = models.BigAutoField(primary_key=True)
    subscription = models.ForeignKey(CreatorSubscription, on_delete=models.PROTECT, related_name="registration_charges")
    asset = models.OneToOneField(IpAsset, on_delete=models.PROTECT, related_name="registration_charge")
    created_at = models.DateTimeField(auto_now_add=True)


class RegistrationDraft(UUIDPrimaryKey):
    """대화에서 수집한 등록 초안과 사용자의 최종 확인 상태를 분리한다."""

    COLLECTING = "collecting"
    CONFIRMED = "confirmed"
    EXECUTED = "executed"
    STATUS_CHOICES = [(COLLECTING, "collecting"), (CONFIRMED, "confirmed"), (EXECUTED, "executed")]

    creator = models.ForeignKey(Creator, on_delete=models.CASCADE, related_name="registration_drafts")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=COLLECTING)
    file_name = models.CharField(max_length=255, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True)
    fields = models.JSONField(default=dict)
    confirmation_token = models.UUIDField(null=True, blank=True, unique=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    executed_asset = models.OneToOneField(IpAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="source_draft")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["creator", "status", "updated_at"], name="ip_draft_creator_status_idx")]


class CreatorExpense(models.Model):
    """창작자가 직접 기록한 운영 지출이다. 수입은 검증된 License에서 계산한다."""

    id = models.BigAutoField(primary_key=True)
    creator = models.ForeignKey(
        Creator, on_delete=models.CASCADE, related_name="expenses"
    )
    amount_usdc = models.DecimalField(max_digits=12, decimal_places=6)
    memo = models.CharField(max_length=200)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(
                fields=["creator", "occurred_at"],
                name="ip_expense_creator_9e34bc_idx",
            )
        ]


class SponsoredPaymentIntent(UUIDPrimaryKey):
    """One browser-authorized USDC transfer whose fee is paid by VeriProof."""

    CREATED = "created"
    SUBMITTED = "submitted"
    SETTLED = "settled"
    EXPIRED = "expired"
    BROWSER = "browser"
    AGENT = "agent"
    STATUS_CHOICES = [
        (CREATED, "created"),
        (SUBMITTED, "submitted"),
        (SETTLED, "settled"),
        (EXPIRED, "expired"),
    ]
    CHANNEL_CHOICES = [
        (BROWSER, "browser"),
        (AGENT, "agent"),
    ]

    asset = models.ForeignKey(IpAsset, on_delete=models.PROTECT, related_name="sponsored_payment_intents")
    buyer_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sponsored_payment_intents")
    buyer_wallet = models.CharField(max_length=64)
    recipient_wallet = models.CharField(max_length=64)
    amount_usdc = models.DecimalField(max_digits=12, decimal_places=6)
    memo = models.CharField(max_length=120, unique=True)
    channel = models.CharField(max_length=12, choices=CHANNEL_CHOICES, default=BROWSER)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=CREATED, db_index=True)
    transaction_signature = models.CharField(max_length=140, unique=True, null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["buyer_user", "asset", "status"], name="ip_sponsor_buyer_asset_idx"),
        ]
