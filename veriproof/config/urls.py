"""Root URL configuration for the VeriProof AI project.

Routing map (architecture SSOT 6.1 M2M API + 6.5 web routes):

- ``/api/v1/`` prefix hosts the M2M REST surface, split across apps:
    * apps.ip          -> register, get(402), certificate, transactions,
                          asset list, events, batch negotiate/settle
    * apps.negotiation -> negotiate
    * apps.settlement  -> settle, paysh webhook
    * apps.sandbox     -> run
- Web pages: creator assistant ``/``, public marketplace ``/discover``,
  private library ``/library``, sandbox ``/sandbox``, and ``/files/<token>``.
- ``/.well-known/ai-plugin.json`` is the agent discovery manifest.

Views delegate to their application services; external integrations are lazy
and configured through settings.
"""
from django.contrib import admin
from django.urls import include, path

from apps.ip.views_api import ai_plugin

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),

    # --- M2M REST API surface (mounted under /api/v1/) ----------------------
    path("api/v1/", include("apps.ip.urls")),
    path("api/v1/", include("apps.negotiation.urls")),
    path("api/v1/", include("apps.settlement.urls")),
    path("api/v1/", include("apps.sandbox.urls")),

    # --- Agent discovery manifest -------------------------------------------
    path(".well-known/ai-plugin.json", ai_plugin, name="ai-plugin"),

    # --- Web pages ----------------------------------------------------------
    path("", include("apps.ip.urls_web")),
    path("", include("apps.sandbox.urls_web")),
]
