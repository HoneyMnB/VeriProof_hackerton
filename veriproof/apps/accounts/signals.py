"""새 사용자에 대한 필수 환경설정을 생성한다."""
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserPreference


@receiver(post_save, sender=get_user_model())
def create_user_preferences(sender, instance, created: bool, **kwargs) -> None:
    """가입 직후 계정 설정 레코드를 생성해 화면의 예외 경로를 없앤다."""
    if created:
        UserPreference.objects.create(user=instance, display_name=instance.get_username())
