from django.apps import AppConfig


class SandboxConfig(AppConfig):
    """Sandbox app: live negotiation showcase + event stream (SPEC-006).

    app_label = ``sandbox``. No models of its own in this scaffold.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sandbox"
    label = "sandbox"
