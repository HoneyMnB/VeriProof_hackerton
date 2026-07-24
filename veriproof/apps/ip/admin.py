"""Admin registration for the IP app (Creator, IpAsset)."""
from django.contrib import admin

from .models import Creator, IpAsset


@admin.register(Creator)
class CreatorAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet_address", "display_name", "created_at")
    search_fields = ("wallet_address", "display_name")


@admin.register(IpAsset)
class IpAssetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "creator",
        "status",
        "originality_score",
        "target_price_usdc",
        "parent_asset",
        "created_at",
    )
    list_filter = ("status", "category")
    search_fields = ("id", "image_sha256", "title")
    readonly_fields = ("created_at",)
