from django.apps import AppConfig


class NegotiationConfig(AppConfig):
    """Negotiation app: NegotiationSession + autonomous-negotiation API.

    app_label = ``negotiation``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.negotiation"
    label = "negotiation"
