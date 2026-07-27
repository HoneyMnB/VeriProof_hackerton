"""Admin registration for the settlement app."""
from django.contrib import admin

from .models import BatchItem, BatchOrder, License, RoyaltyDistribution


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "asset",
        "buyer_user",
        "buyer_wallet",
        "price_usdc",
        "usage_type",
        "payment_tx_sig",
        "granted_at",
    )
    search_fields = ("buyer_user__email", "buyer_wallet", "payment_tx_sig")
    readonly_fields = ("granted_at",)


@admin.register(RoyaltyDistribution)
class RoyaltyDistributionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "license",
        "recipient_wallet",
        "role",
        "amount_usdc",
        "status",
    )
    list_filter = ("role", "status")


class BatchItemInline(admin.TabularInline):
    model = BatchItem
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(BatchOrder)
class BatchOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "buyer_agent_id", "total_usdc", "status", "created_at")
    list_filter = ("status",)
    inlines = [BatchItemInline]
    readonly_fields = ("created_at",)
