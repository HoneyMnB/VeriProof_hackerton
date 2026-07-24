"""Sandbox app web (template) view: live negotiation showcase (``/sandbox``)."""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse


@user_passes_test(lambda user: settings.DEBUG and user.is_staff)
def sandbox(request: HttpRequest) -> HttpResponse:
    """Negotiation sandbox page (architecture 6.5). SPEC-006 wires the stream."""
    return TemplateResponse(request, "sandbox.html", {"active_nav": "sandbox"})
