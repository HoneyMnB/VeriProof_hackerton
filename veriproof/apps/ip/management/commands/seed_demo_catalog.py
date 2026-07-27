"""실제 등록 파이프라인으로 로컬 Discover 데모 자산을 준비한다."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from solders.pubkey import Pubkey

from apps.accounts.models import UserPreference
from apps.accounts.services import ensure_developer_account
from apps.ip.models import Creator, IpAsset, SubscriptionPlan
from services.registration_service import RegistrationMetadata, get_registration_service
from services.solana_adapter_factory import get_solana_service
from services.subscription_service import get_subscription_service

DEMO_PLAN_CODE = "local-demo-catalog"


@dataclass(frozen=True)
class DemoWork:
    """데모에만 쓰는 명시적 콘텐츠 편집 정보다. 런타임 분류 규칙이 아니다."""

    filename: str
    title: str
    description: str
    minimum: str
    target: str


DEMO_WORKS = (
    DemoWork("ceramic-incense-holder.png", "Ceramic incense holder", "Hand-crafted ceramic object photographed for a product license.", "4.00", "9.00"),
    DemoWork("ceramic-cup.png", "Stoneware morning cup", "A studio photograph of a hand-thrown stoneware cup.", "4.00", "8.00"),
    DemoWork("brass-mobile.png", "Brass balance mobile", "A small hand-built brass mobile documented as a product work.", "5.00", "11.00"),
    DemoWork("pop-flowers.png", "Electric flowers", "A bold contemporary pop-art flower composition.", "6.00", "14.00"),
    DemoWork("pop-portrait.png", "Chromatic profile", "A contemporary pop-art portrait with graphic color fields.", "7.00", "16.00"),
    DemoWork("pop-still-life.png", "Sunday objects", "A graphic pop-art still life with everyday objects.", "6.00", "13.00"),
    DemoWork("coast-dawn.png", "Coast at dawn", "A landscape photograph of a quiet coast at sunrise.", "5.00", "12.00"),
    DemoWork("forest-mist.png", "Forest mist", "A fine-art landscape photograph of a misty forest.", "5.00", "12.00"),
    DemoWork("mountain-lake.png", "Mountain lake", "A landscape photograph of a high mountain lake.", "5.00", "12.00"),
    DemoWork("oil-portrait-blue.png", "Blue study", "An oil-painted portrait study in a restrained palette.", "8.00", "18.00"),
    DemoWork("oil-portrait-gold.png", "Gold collar", "An oil-painted portrait with warm gold accents.", "8.00", "18.00"),
    DemoWork("oil-portrait-window.png", "Window sitter", "An oil portrait painted in soft afternoon light.", "8.00", "18.00"),
    DemoWork("street-photo-red.png", "Red crossing", "An editorial street photograph with a red architectural detail.", "5.00", "11.00"),
    DemoWork("street-photo-market.png", "Market interval", "An editorial photograph of a local market scene.", "5.00", "11.00"),
    DemoWork("street-photo-shadow.png", "Long shadow", "An architectural street photograph with long shadows.", "5.00", "11.00"),
)


class Command(BaseCommand):
    """데모 카탈로그 자산을 실제 등록 파이프라인으로 시딩하는 관리 명령이다."""

    help = "Register the generated local demo works through the normal pipeline."

    def handle(self, *args, **options):
        """이미지·Gemini·저장소·Solana Memo signer가 준비될 때만 데모를 만든다."""
        if not settings.DEBUG:
            raise CommandError("seed_demo_catalog is available only when DEBUG=true")
        if getattr(settings, "SOLANA_ADAPTER", "mock") != "mock":
            raise CommandError("local demo catalog requires SOLANA_ADAPTER=mock")
        wallet = str(getattr(settings, "DEMO_CREATOR_WALLET", "")).strip()
        if not wallet:
            raise CommandError(
                "DEMO_CREATOR_WALLET must be a controlled Solana Devnet wallet"
            )
        try:
            Pubkey.from_string(wallet)
        except ValueError as exc:
            raise CommandError(
                "DEMO_CREATOR_WALLET is not a valid Solana public key"
            ) from exc
        directory = Path(settings.BASE_DIR) / "demo_assets"
        missing = [work.filename for work in DEMO_WORKS if not (directory / work.filename).is_file()]
        if missing:
            raise CommandError("generated demo images are missing: " + ", ".join(missing))

        user = ensure_developer_account()
        preference, _ = UserPreference.objects.get_or_create(user=user)
        preference.creator_wallet = wallet
        preference.save(update_fields=["creator_wallet", "updated_at"])
        creator, _ = Creator.objects.get_or_create(wallet_address=wallet)
        plan, _ = SubscriptionPlan.objects.update_or_create(
            code=DEMO_PLAN_CODE,
            defaults={"name": "Local demo catalog", "monthly_fee_usdc": "0", "included_registrations": len(DEMO_WORKS), "is_active": True},
        )
        pending = []
        demo_hashes = []
        for work in DEMO_WORKS:
            path = directory / work.filename
            content = path.read_bytes()
            content_hash = hashlib.sha256(content).hexdigest()
            demo_hashes.append(content_hash)
            if IpAsset.objects.filter(image_sha256=content_hash).exists():
                self.stdout.write(f"skip existing: {work.filename}")
                continue
            pending.append((work, content))
        # 기존 데모 데이터도 현재 제어 가능한 판매자 지갑으로만 수취하게 한다.
        # 지갑이 바뀐 자산은 목업 등록 인증서도 새 공개키 기준으로 다시 발급한다.
        reassigned = IpAsset.objects.filter(
            image_sha256__in=demo_hashes,
        ).exclude(creator=creator).update(
            creator=creator,
            account_owner=user,
            registration_certificate_tx_sig=None,
        )
        IpAsset.objects.filter(
            image_sha256__in=demo_hashes,
            creator=creator,
        ).update(account_owner=user)
        if not pending:
            repaired = self._backfill_registration_certificates(
                wallet,
                demo_hashes,
            )
            self.stdout.write(self.style.SUCCESS(
                "Demo catalog already ready; "
                f"created=0 reassigned={reassigned} certificates_repaired={repaired}"
            ))
            return
        self._ensure_capacity(wallet, plan.code, len(pending))
        service = get_registration_service()
        created = 0
        for work, content in pending:
            upload = SimpleUploadedFile(work.filename, content, content_type="image/png")
            outcome = service.register(
                upload,
                RegistrationMetadata(
                    creator_wallet=wallet,
                    asset_type=IpAsset.IMAGE,
                    visibility=IpAsset.PUBLIC,
                    min_price=Decimal(work.minimum),
                    target_price=Decimal(work.target),
                    title=work.title,
                    description=work.description,
                ),
                account_owner=user,
            )
            created += 1
            self.stdout.write(f"registered: {outcome.asset.id} {work.filename}")
        self.stdout.write(self.style.SUCCESS(f"Demo catalog ready; created={created}"))

    @staticmethod
    def _backfill_registration_certificates(
        wallet: str,
        demo_hashes: list[str],
    ) -> int:
        """기존 로컬 데모도 인증서가 없으면 공개 목록에서 제외되지 않게 보완한다."""
        solana = get_solana_service()
        repaired = 0
        assets = IpAsset.objects.filter(
            creator__wallet_address=wallet,
            image_sha256__in=demo_hashes,
            registration_certificate_tx_sig__isnull=True,
        )
        for asset in assets.iterator():
            certificate = solana.issue_registration_certificate(
                asset.id,
                wallet,
                asset.image_sha256,
            )
            asset.registration_certificate_tx_sig = certificate
            asset.save(update_fields=["registration_certificate_tx_sig"])
            repaired += 1
        return repaired

    @staticmethod
    def _ensure_capacity(wallet: str, plan_code: str, needed: int) -> None:
        """데모 목업 구독은 명시적 mock 거래 식별자로만 활성화한다."""
        from apps.ip.models import CreatorSubscription

        active = CreatorSubscription.objects.filter(
            creator__wallet_address=wallet,
            status=CreatorSubscription.ACTIVE,
            plan__code=plan_code,
        ).select_related("plan").first()
        if active and active.registrations_used + needed <= active.plan.included_registrations:
            return

        get_subscription_service().activate_mock_subscription(
            wallet, plan_code, f"mock:demo-catalog-subscription:{timezone.now().timestamp()}"
        )
