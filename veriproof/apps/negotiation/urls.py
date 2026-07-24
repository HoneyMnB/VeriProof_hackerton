"""Negotiation app M2M API routes (mounted under ``/api/v1/``)."""
from django.urls import path

from . import views_api

app_name = "negotiation"

urlpatterns = [
    path(
        "ip/<uuid:asset_id>/negotiate",
        views_api.negotiate,
        name="api-negotiate",
    ),
]
