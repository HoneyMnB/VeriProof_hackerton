"""Sandbox app web (template) routes: ``/sandbox``."""
from django.urls import path

from . import views_web

app_name = "sandbox-web"

urlpatterns = [
    path("sandbox", views_web.sandbox, name="sandbox"),
]
