"""창작물 등록 파이프라인의 애플리케이션 서비스.

HTTP 파싱은 뷰에 남기고, 검증된 입력을 받아 분석·저장·온체인 앵커·영속화를
하나의 명시적인 유스케이스로 실행한다. 외부 어댑터는 생성자 주입으로만 교체한다.
"""
from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.ip.models import Creator, IpAsset
from services._types import AnalysisResult
from services.gemini_service import LLM_ANALYZABLE_MIMES

logger = logging.getLogger(__name__)


class RegistrationError(ValueError):
    """등록 유스케이스의 안전한 사용자 입력 또는 의존성 오류다."""

    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class RegistrationMetadata:
    """HTTP와 독립된 창작물 등록 메타데이터다."""

    creator_wallet: str
    asset_type: str
    visibility: str
    # Registration-canvas prices are stored verbatim as USDC amounts.
    min_price: decimal.Decimal
    target_price: decimal.Decimal
    title: str | None = None
    description: str | None = None
    parent_asset_id: str | None = None
    royalty_share_bps: int | None = None
    tags: tuple[str, ...] = ()
    category: str | None = None


@dataclass(frozen=True)
class RegistrationOutcome:
    """등록 완료 후 API가 표현할 안전한 결과다."""

    asset: IpAsset
    analysis: AnalysisResult


class RegistrationService:
    """실제 분석·저장·앵커가 모두 성공할 때만 등록을 완료한다."""

    def __init__(
        self,
        *,
        image_processor: Any,
        gemini: Any,
        solana: Any,
        storage: Any,
        event_recorder: Any,
        subscription: Any = None,
        fingerprint: Any = None,
    ) -> None:
        self.image_processor = image_processor
        self.gemini = gemini
        self.solana = solana
        self.storage = storage
        self.event_recorder = event_recorder
        self.subscription = subscription
        self.fingerprint = fingerprint or _default_fingerprint()

    def register(
        self,
        upload: Any,
        metadata: RegistrationMetadata,
        supporting_uploads: tuple[Any, ...] = (),
        gallery_uploads: tuple[Any, ...] = (),
        account_owner: Any | None = None,
        signer_secret_key: list[int] | None = None,
    ) -> RegistrationOutcome:
        """검증된 업로드를 실제 파이프라인에 등록하고 주요 단계를 기록한다."""
        if gallery_uploads and metadata.asset_type != IpAsset.IMAGE:
            raise RegistrationError("invalid_gallery", "gallery images are only supported for image works", 422)
        content = upload.read()
        if self.subscription is not None:
            self.subscription.authorize_registration(metadata.creator_wallet)
        asset_id = uuid.uuid4()
        event_context = {
            "account_owner": account_owner,
            "asset_id": asset_id,
            "correlation_id": asset_id,
        }
        self.event_recorder.record(
            "REGISTRATION_STARTED",
            {"title": metadata.title, "status": "processing"},
            **event_context,
        )
        gallery_contents = [(item, item.read()) for item in gallery_uploads]
        content_hash = self._work_manifest_hash(content, gallery_contents)
        self.event_recorder.record(
            "CONTENT_HASHED",
            {"title": metadata.title, "content_sha256": content_hash},
            **event_context,
        )
        if IpAsset.objects.filter(image_sha256=content_hash).exists():
            self.event_recorder.record(
                "REGISTRATION_FAILED",
                {"title": metadata.title, "reason": "duplicate content"},
                **event_context,
            )
            raise RegistrationError("duplicate", "identical content is already registered", 409)

        parent_asset = self._resolve_parent(metadata)
        analysis, thumbnail, watermark = self._prepare_artifacts(
            content, metadata, upload.content_type
        )
        perceptual_hash = self._perceptual_hash(content, metadata)
        self.event_recorder.record(
            "AI_ANALYZED",
            {
                "title": metadata.title,
                "category": metadata.category or analysis.category,
                "tag_count": len(analysis.tags),
                "originality_score": analysis.originality_score,
            },
            **event_context,
        )

        # 앵커와 등록 인증서는 공개 라이선스 게시의 필수 증명이다.
        self.event_recorder.record(
            "ANCHORING_STARTED",
            {"title": metadata.title, "network": settings.X402_NETWORK},
            **event_context,
        )
        try:
            anchor_tx_sig = self.solana.anchor_hash(content_hash, metadata.creator_wallet, signer_secret_key)
        except Exception as exc:  # noqa: BLE001 - adapter exceptions are external
            logger.error(
                "registration anchor failed creator_wallet=%s error=%s",
                metadata.creator_wallet,
                exc,
            )
            self.event_recorder.record(
                "REGISTRATION_FAILED",
                {"title": metadata.title, "reason": "on-chain anchoring failed"},
                **event_context,
            )
            raise RegistrationError(
                "anchor_unavailable", "on-chain anchoring could not be completed", 503
            ) from exc
        self.event_recorder.record(
            "ANCHORED",
            {"title": metadata.title, "content_sha256": content_hash, "anchor_tx_sig": anchor_tx_sig},
            **event_context,
        )
        try:
            registration_certificate_tx_sig = self.solana.issue_registration_certificate(
                asset_id,
                metadata.creator_wallet,
                content_hash,
                signer_secret_key,
            )
        except Exception as exc:  # noqa: BLE001 - adapter exceptions are external
            logger.error(
                "registration certificate failed creator_wallet=%s asset_id=%s error=%s",
                metadata.creator_wallet,
                asset_id,
                exc,
            )
            self.event_recorder.record(
                "REGISTRATION_FAILED",
                {"title": metadata.title, "reason": "registration certificate failed"},
                **event_context,
            )
            raise RegistrationError(
                "registration_certificate_unavailable",
                "registration certificate could not be issued",
                503,
            ) from exc
        self.event_recorder.record(
            "REGISTRATION_CERTIFICATE_ISSUED",
            {
                "title": metadata.title,
                "registration_certificate_tx_sig": registration_certificate_tx_sig,
            },
            **event_context,
        )

        try:
            thumbnail_url = self._save_preview("thumbnail", asset_id, thumbnail)
            watermark_url = self._save_preview("watermark", asset_id, watermark)
            retention = datetime.timedelta(days=int(settings.ORIGINAL_RETENTION_DAYS))
            original_url = self.storage.save_temporary(
                asset_id,
                content,
                retention,
                upload.content_type,
            )
            gallery_artifacts = self._prepare_gallery_artifacts(gallery_contents, asset_id, retention)
        except Exception as exc:  # noqa: BLE001 - storage adapter exceptions are external
            logger.error(
                "registration storage failed creator_wallet=%s asset_id=%s error=%s",
                metadata.creator_wallet,
                asset_id,
                exc,
            )
            self.event_recorder.record(
                "REGISTRATION_FAILED",
                {"title": metadata.title, "reason": "content storage failed"},
                **event_context,
            )
            raise RegistrationError(
                "storage_unavailable", "content storage could not be completed", 503
            ) from exc

        with transaction.atomic():
            creator, _ = Creator.objects.get_or_create(wallet_address=metadata.creator_wallet)
            asset = IpAsset.objects.create(
                id=asset_id,
                creator=creator,
                account_owner=account_owner,
                title=metadata.title,
                description=metadata.description,
                ai_description=analysis.description,
                asset_type=metadata.asset_type,
                visibility=metadata.visibility,
                content_mime_type=upload.content_type,
                tags=list(metadata.tags),
                ai_tags=list(analysis.tags),
                category=metadata.category or analysis.category,
                originality_score=analysis.originality_score,
                min_price_usdc=None,
                target_price_usdc=None,
                min_amount=metadata.min_price,
                target_amount=metadata.target_price,
                currency="USDC",
                image_sha256=content_hash,
                perceptual_hash=perceptual_hash,
                thumbnail_url=thumbnail_url,
                watermark_url=watermark_url,
                original_url=original_url,
                original_expires_at=timezone.now() + retention,
                anchor_tx_sig=anchor_tx_sig,
                registration_certificate_tx_sig=registration_certificate_tx_sig,
                parent_asset=parent_asset,
                royalty_share_bps=metadata.royalty_share_bps,
                status=IpAsset.ANCHORED,
            )
            # 보조 파일은 원본 공개 경로가 아니라 내부 매니페스트로만 보관한다.
            from apps.ip.models import AssetComponent, AssetImage
            for index, component in enumerate(supporting_uploads):
                component_bytes = component.read()
                component_hash = self.fingerprint.sha256(component_bytes)
                component_url = self.storage.save_permanent(
                    "supporting", f"{asset_id}-{index}", component_bytes
                )
                AssetComponent.objects.create(
                    asset=asset,
                    file_name=component.name,
                    content_mime_type=component.content_type or "application/octet-stream",
                    content_sha256=component_hash,
                    storage_url=component_url,
                )
            for position, artifact in enumerate(gallery_artifacts, start=1):
                AssetImage.objects.create(asset=asset, position=position, **artifact)
            if self.subscription is not None:
                self.subscription.consume_registration(creator, asset)

        # Early registration events precede the IpAsset row; attach their durable
        # PostgreSQL audit records once the asset commits successfully.
        from apps.common.models import AgentEvent

        AgentEvent.objects.filter(
            correlation_id=asset_id,
            asset__isnull=True,
        ).update(asset=asset)
        self.event_recorder.record(
            "ASSET_REGISTERED",
            {"title": asset.title, "status": asset.status},
            asset=asset,
            correlation_id=asset_id,
        )

        logger.info(
            "registration completed creator_wallet=%s asset_id=%s type=%s visibility=%s",
            metadata.creator_wallet,
            asset.id,
            asset.asset_type,
            asset.visibility,
        )
        return RegistrationOutcome(asset=asset, analysis=analysis)

    def _prepare_gallery_artifacts(self, gallery_contents, asset_id, retention):
        """추가 이미지의 보호 미리보기·임시 원본을 모두 저장할 준비를 한다."""
        artifacts = []
        for position, (upload, content) in enumerate(gallery_contents, start=1):
            try:
                watermark = self.image_processor.make_watermark(content, "VeriProof")
            except Exception as exc:  # noqa: BLE001 - Pillow decode errors are user input
                raise RegistrationError("invalid_image", "gallery image could not be decoded") from exc
            image_id = uuid.uuid4()
            try:
                watermark_url = self.storage.save_permanent("watermark", image_id, watermark)
                original_url = self.storage.save_temporary(
                    image_id,
                    content,
                    retention,
                    upload.content_type,
                )
            except Exception as exc:  # noqa: BLE001 - storage adapter exceptions are external
                raise RegistrationError("storage_unavailable", "gallery image storage could not be completed", 503) from exc
            artifacts.append(
                {
                    "file_name": upload.name,
                    "content_mime_type": upload.content_type or "image/png",
                    "content_sha256": self.fingerprint.sha256(content),
                    "watermark_url": watermark_url,
                    "original_url": original_url,
                    "id": image_id,
                }
            )
        return artifacts

    def _work_manifest_hash(self, primary_content: bytes, gallery_contents) -> str:
        """작품을 구성하는 순서 있는 모든 이미지의 단일 증명 해시를 만든다."""
        contents = [primary_content]
        contents.extend(content for _upload, content in gallery_contents)
        return self.fingerprint.content_manifest_sha256(contents)

    def _prepare_artifacts(
        self, content: bytes, metadata: RegistrationMetadata, mime: str
    ) -> tuple[AnalysisResult, bytes | None, bytes | None]:
        """LLM 분석 가능한 형식은 멀티모달 Gemini로 분석하고, 이미지엔 보호 미리보기도
        만든다. 분석 불가 형식(zip/tar 등)은 AI 필드를 비운 채 사용자 메타데이터만 보존한다."""
        if mime not in LLM_ANALYZABLE_MIMES:
            return (
                AnalysisResult(
                    tags=[],
                    category=None,
                    originality_score=None,
                    recommended_min_price_usdc=metadata.min_price,
                    degraded=False,
                    description=None,
                ),
                None,
                None,
            )
        thumbnail = watermark = None
        if metadata.asset_type == IpAsset.IMAGE:
            try:
                thumbnail = self.image_processor.make_thumbnail(content, (512, 512))
                watermark = self.image_processor.make_watermark(content, "VeriProof")
            except Exception as exc:  # noqa: BLE001 - Pillow adapter exceptions are external
                raise RegistrationError("invalid_image", "image could not be decoded") from exc
        try:
            analysis = self.gemini.analyze_asset(content, mime)
        except Exception as exc:  # noqa: BLE001 - Gemini errors are mapped at boundary
            logger.error(
                "registration analysis failed creator_wallet=%s error=%s",
                metadata.creator_wallet,
                exc,
            )
            raise RegistrationError(
                "analysis_unavailable", "AI analysis could not be completed", 503
            ) from exc
        return analysis, thumbnail, watermark

    def _perceptual_hash(self, content: bytes, metadata: RegistrationMetadata) -> str | None:
        """이미지에만 유사 검색용 지문을 남기고 비이미지에는 만들지 않는다."""
        if metadata.asset_type != IpAsset.IMAGE:
            return None
        try:
            return self.image_processor.perceptual_hash(content)
        except Exception as exc:  # noqa: BLE001 - Pillow decode errors are external input
            logger.error(
                "registration perceptual hash failed creator_wallet=%s error=%s",
                metadata.creator_wallet,
                exc,
            )
            raise RegistrationError("invalid_image", "image could not be decoded") from exc

    def _save_preview(self, kind: str, asset_id: uuid.UUID, data: bytes | None) -> str | None:
        """보호 미리보기가 없는 비이미지 자산에는 URL을 만들지 않는다."""
        return None if data is None else self.storage.save_permanent(kind, asset_id, data)

    @staticmethod
    def _resolve_parent(metadata: RegistrationMetadata) -> IpAsset | None:
        """2차 창작물의 원본과 로열티 조건을 함께 검증한다."""
        if not metadata.parent_asset_id:
            return None
        try:
            parent = IpAsset.objects.get(id=metadata.parent_asset_id)
        except (IpAsset.DoesNotExist, ValueError, TypeError) as exc:
            raise RegistrationError("parent_not_found", "parent_asset_id not found", 404) from exc
        if metadata.royalty_share_bps is None or not 1 <= metadata.royalty_share_bps <= 10000:
            raise RegistrationError(
                "invalid_royalty_share",
                "royalty_share_bps must be in 1..10000 when parent_asset_id is set",
            )
        return parent


def get_registration_service() -> RegistrationService:
    """설정 기반 실제 어댑터로 등록 유스케이스를 조립한다."""
    from services.event_recorder import get_event_recorder
    from services.gemini_service import get_gemini_service
    from services.image_processor import get_image_processor
    from services.solana_adapter_factory import get_solana_service
    from services.storage_service import get_storage_service

    return RegistrationService(
        image_processor=get_image_processor(),
        gemini=get_gemini_service(),
        solana=get_solana_service(),
        storage=get_storage_service(),
        event_recorder=get_event_recorder(),
        fingerprint=_default_fingerprint(),
    )


def _default_fingerprint():
    from services.image_fingerprint import get_fingerprint_service

    return get_fingerprint_service()
