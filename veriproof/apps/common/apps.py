from django.apps import AppConfig


class CommonConfig(AppConfig):
    """Shared app: AgentEvent audit timeline + reusable abstract bases.

    app_label = ``common``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"
