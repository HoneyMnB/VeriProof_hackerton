from django.apps import AppConfig


class SettlementConfig(AppConfig):
    """Settlement app: License, RoyaltyDistribution, BatchOrder, BatchItem.

    app_label = ``settlement``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.settlement"
    label = "settlement"
