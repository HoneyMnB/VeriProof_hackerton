"""IP app web routes: creator assistant, public discovery, and library.

The ``/files/<token>`` download route is implemented in ``apps.settlement``
(the License model that authorises a download lives there), exposed here at the
architecture §6.5 root path (no ``/api/v1/`` prefix).
"""
from django.urls import path

from apps.settlement.views_api import download as settlement_download

from . import views_web

app_name = "ip-web"

urlpatterns = [
    path("", views_web.workspace, name="home"),
    path("discover", views_web.discover, name="discover"),
    path("discover/<uuid:asset_id>", views_web.asset_detail, name="asset-detail"),
    path("previews/<uuid:asset_id>/<str:variant>", views_web.preview, name="preview"),
    path("workspace", views_web.workspace, name="workspace"),
    path("library", views_web.library, name="library"),
    path("files/<str:token>", settlement_download, name="download"),
]
