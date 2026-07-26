"""판매자 에이전트 도구가 외부에 노출하는 입력 스키마."""

from enum import StrEnum

from apps.ip.models import IpAsset


class AssetType(StrEnum):
    """DB 자산 유형과 동일한 표준 검색 값."""

    IMAGE = IpAsset.IMAGE
    DOCUMENT = IpAsset.DOCUMENT
    AUDIO = IpAsset.AUDIO
    VIDEO = IpAsset.VIDEO
    SOFTWARE = IpAsset.SOFTWARE
    PRODUCT = IpAsset.PRODUCT
    OTHER = IpAsset.OTHER
