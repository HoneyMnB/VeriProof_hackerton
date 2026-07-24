"""Settlement app M2M API routes (mounted under ``/api/v1/``)."""
from django.urls import path

from . import views_api

app_name = "settlement"

urlpatterns = [
    path("ip/<uuid:asset_id>/settle", views_api.settle, name="api-settle"),
    path("ip/batch/negotiate", views_api.batch_negotiate, name="api-batch-negotiate"),
    path("ip/batch/settle", views_api.batch_settle, name="api-batch-settle"),
    path("paysh/webhook", views_api.paysh_webhook, name="api-paysh-webhook"),
]
