from django.apps import AppConfig


class IpConfig(AppConfig):
    """IP asset app: Creator + IpAsset (registration, library, anchoring).

    app_label = ``ip``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ip"
    label = "ip"
