"""Sandbox app M2M API routes (mounted under ``/api/v1/``)."""
from django.urls import path

from . import views_api

app_name = "sandbox"

urlpatterns = [
    path("sandbox/run", views_api.run, name="api-sandbox-run"),
]
