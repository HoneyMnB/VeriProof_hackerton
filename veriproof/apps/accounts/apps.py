from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Django 사용자 계정과 VeriProof 환경설정을 초기화한다."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        from . import signals  # noqa: F401
