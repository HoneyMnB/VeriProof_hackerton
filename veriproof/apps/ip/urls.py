"""IP app M2M API URL routes (mounted under ``/api/v1/``).

Path ordering note: ``ip/<uuid:asset_id>`` uses the UUID converter, so the
literal ``ip/batch/...`` paths (where ``batch`` is not a UUID) do not collide.
"""
from django.urls import path
from apps.common import views_live_demo

from . import views_api, views_assistant

app_name = "ip"

urlpatterns = [
    # Registration + access (x402 interceptor)
    path("ip/register", views_api.register, name="api-register"),
    path("ip/<uuid:asset_id>", views_api.get_asset, name="api-get-asset"),
    path("ip/<uuid:asset_id>/solpay/verify", views_api.verify_solpay, name="api-solpay-verify"),
    path("ip/<uuid:asset_id>/agent-sol-payment", views_api.get_agent_sol_payment_terms, name="api-agent-sol-payment"),
    path("ip/<uuid:asset_id>/agent-sol-payment/settle", views_api.settle_agent_sol_payment, name="api-agent-sol-payment-settle"),
    path("ip/<uuid:asset_id>/terms", views_api.update_asset_terms, name="api-update-asset-terms"),
    path("ip/<uuid:asset_id>/delete", views_api.delete_asset, name="api-delete-asset"),
    # Certificate + transaction history
    path(
        "ip/<uuid:asset_id>/certificate/<str:cert_id>",
        views_api.get_certificate,
        name="api-certificate",
    ),
    path(
        "ip/<uuid:asset_id>/transactions",
        views_api.transactions,
        name="api-transactions",
    ),
    # Listing + event polling (Firestore fallback)
    path("assets", views_api.asset_list, name="api-asset-list"),
    path("catalog", views_api.catalog, name="api-catalog"),
    path("assistant/overview", views_assistant.overview, name="api-assistant-overview"),
    path("assistant/history", views_assistant.history, name="api-assistant-history"),
    path("assistant/conversations/search", views_assistant.conversation_search, name="api-assistant-conversation-search"),
    path("assistant/conversations/<uuid:conversation_id>", views_assistant.conversation, name="api-assistant-conversation"),
    path("assistant/sales", views_assistant.sales, name="api-assistant-sales"),
    path("assistant/actions", views_assistant.actions, name="api-assistant-actions"),
    path("subscriptions/activate", views_assistant.activate_subscription, name="api-subscription-activate"),
    path("subscriptions/plans", views_assistant.subscription_plans, name="api-subscription-plans"),
    path("assistant/directives", views_assistant.directives, name="api-assistant-directives"),
    path("assistant/status", views_assistant.status, name="api-assistant-status"),
    path("assistant/chat", views_assistant.chat, name="api-assistant-chat"),
    path("assistant/attachments", views_assistant.conversation_attachment, name="api-assistant-attachment"),
    path("assistant/attachments/<uuid:attachment_id>/file", views_assistant.conversation_attachment_file, name="api-assistant-attachment-file"),
    path("assistant/registration-drafts", views_assistant.registration_drafts, name="api-registration-drafts"),
    path("assistant/registration-drafts/<uuid:draft_id>/confirm", views_assistant.registration_drafts, name="api-registration-draft-confirm"),
    path("assistant/expenses", views_assistant.record_expense, name="api-assistant-expense"),
    path("events", views_api.events, name="api-events"),
    path("live-demo/events", views_live_demo.feed, name="api-live-demo-events"),
    path("live-demo/stream", views_live_demo.stream, name="api-live-demo-stream"),
    path("openapi.json", views_api.openapi, name="api-openapi"),
]
