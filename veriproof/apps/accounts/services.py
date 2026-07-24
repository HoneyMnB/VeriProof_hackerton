"""계정 시드처럼 HTTP와 분리해야 하는 로컬 개발 기능."""
from __future__ import annotations

from django.contrib.auth.models import User

DEVELOPER_EMAIL = "admin@test.com"
DEVELOPER_PASSWORD = "a123456789?"


def ensure_developer_account() -> User:
    """반복 실행해도 같은 로컬 개발자 계정을 반환한다."""
    user, created = User.objects.get_or_create(
        username=DEVELOPER_EMAIL,
        defaults={"email": DEVELOPER_EMAIL, "is_staff": True, "is_superuser": True},
    )
    changed = created or not user.check_password(DEVELOPER_PASSWORD) or not user.is_staff
    if changed:
        user.email = DEVELOPER_EMAIL
        user.is_staff = True
        user.is_superuser = True
        user.set_password(DEVELOPER_PASSWORD)
        user.save()
    return user
