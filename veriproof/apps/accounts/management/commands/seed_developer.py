"""로컬 개발자 계정을 명시적으로 준비하는 명령."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.services import DEVELOPER_EMAIL, ensure_developer_account


class Command(BaseCommand):
    help = "Create or update the DEBUG-only VeriProof developer account."

    def handle(self, *args, **options):
        """운영에서 고정 개발자 자격증명이 생기지 않도록 DEBUG를 강제한다."""
        if not settings.DEBUG:
            raise CommandError("seed_developer is available only when DEBUG=true")
        user = ensure_developer_account()
        self.stdout.write(self.style.SUCCESS(f"Developer account ready: {DEVELOPER_EMAIL} (id={user.id})"))
