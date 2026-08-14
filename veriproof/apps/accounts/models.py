"""계정에 종속되는 최소 사용자 설정 모델."""
from __future__ import annotations

from django.conf import settings
from django.db import models


class UserPreference(models.Model):
    """로그인 계정의 표시·복구 연락처·창작자 지갑 설정을 한 곳에 보관한다."""

    KOREAN = "ko"
    ENGLISH = "en"
    LANGUAGE_CHOICES = [(KOREAN, "한국어"), (ENGLISH, "English")]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="veriproof_preferences",
    )
    display_name = models.CharField(max_length=80, blank=True)
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default=KOREAN)
    recovery_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    creator_wallet = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Preferences for {self.user.get_username()}"


class PasskeyCredential(models.Model):
    """Public WebAuthn credential bound to one Django account."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="passkey_credentials")
    user_handle = models.BinaryField(max_length=64, db_index=True)
    credential_id = models.BinaryField(unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    device_name = models.CharField(max_length=80, default="Passkey")
    device_type = models.CharField(max_length=32, blank=True)
    backed_up = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_used_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.device_name} for {self.user.get_username()}"


class WalletConfiguration(models.Model):
    """A creator's public wallet settings and encrypted signing secret."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet_configurations")
    label = models.CharField(max_length=40)
    address = models.CharField(max_length=64)
    private_address = models.CharField(max_length=512, blank=True)
    accepts_deposits = models.BooleanField(default=True)
    receives_payouts = models.BooleanField(default=False)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "address"], name="accounts_wallet_per_user_address")]

    def __str__(self) -> str:
        return f"{self.label} ({self.address[:8]}… )"
