"""Advertise the machine-only agent manifest without adding visible UI."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

_MANIFEST_PATH = "/.well-known/ai-plugin.json"
_MANIFEST_LINK = f'<{_MANIFEST_PATH}>; rel="service-desc"; type="application/json"'


class AgentDiscoveryMiddleware:
    """Expose the standard manifest location in every non-manifest response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.path != _MANIFEST_PATH:
            response.headers["Link"] = _MANIFEST_LINK
        return response
