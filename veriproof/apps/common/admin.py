"""Admin registration for the common app (AgentEvent)."""
from django.contrib import admin

from .models import AgentEvent


@admin.register(AgentEvent)
class AgentEventAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "asset", "session", "created_at")
    list_filter = ("type", "created_at")
    search_fields = ("type",)
    readonly_fields = ("created_at",)
