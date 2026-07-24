"""Admin registration for the negotiation app (NegotiationSession)."""
from django.contrib import admin

from .models import NegotiationSession


@admin.register(NegotiationSession)
class NegotiationSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "asset",
        "buyer_agent_id",
        "usage_type",
        "status",
        "final_price_usdc",
        "created_at",
    )
    list_filter = ("status", "usage_type")
    search_fields = ("buyer_agent_id",)
    readonly_fields = ("created_at", "updated_at")
