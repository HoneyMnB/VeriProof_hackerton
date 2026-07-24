"""SPEC-008 unit tests — 2nd-creation registration validation (R1/R2/R3, AC-1..AC-3).

SPEC-001 already persists ``parent_asset`` / ``royalty_share_bps`` with the
model-level S3 invariant guard and the register view already returns 404 for an
unknown ``parent_asset_id`` (test_register.py::test_register_rejects_unknown_parent)
and 400 for an out-of-range share (test_register.py::test_register_rejects_invalid_royalty_share).
This file adds the focused model/helper-level unit coverage from the SPEC §5 list:

- test_secondary_registration_sets_parent_and_bps (AC-1)
- test_reject_out_of_range_bps (AC-2, model guard at 0 and 10001)
- test_reject_missing_parent (AC-3, _resolve_parent helper -> None -> view 404)
"""
from __future__ import annotations

import uuid

import pytest


@pytest.mark.django_db
def test_secondary_registration_sets_parent_and_bps():
    """AC-1: registering with parent_asset + royalty_share_bps=3000 persists both."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    creator = CreatorFactory()
    parent = IpAssetFactory(creator=creator, status=IpAsset.LISTED)
    child = IpAssetFactory(
        creator=creator,
        parent_asset=parent,
        royalty_share_bps=3000,
    )

    child.refresh_from_db()
    assert child.parent_asset_id == parent.id
    assert child.royalty_share_bps == 3000


@pytest.mark.parametrize("bad_bps", [0, 10001, -1])
@pytest.mark.django_db
def test_reject_out_of_range_bps(bad_bps):
    """AC-2 / R2: royalty_share_bps outside 1..10000 -> ValidationError on save."""
    from django.core.exceptions import ValidationError

    from tests.factories import CreatorFactory, IpAssetFactory

    creator = CreatorFactory()
    parent = IpAssetFactory(creator=creator)
    child = IpAssetFactory.build(creator=creator, parent_asset=parent)
    child.royalty_share_bps = bad_bps
    with pytest.raises(ValidationError):
        child.save()


@pytest.mark.django_db
def test_reject_missing_parent():
    """등록 유스케이스가 존재하지 않는 원본을 명시적으로 거부한다."""
    import decimal
    from services.registration_service import RegistrationError, RegistrationMetadata, RegistrationService

    bogus_id = str(uuid.uuid4())
    metadata = RegistrationMetadata("wallet", "image", "private", decimal.Decimal("1"), decimal.Decimal("1"), parent_asset_id=bogus_id, royalty_share_bps=100)
    with pytest.raises(RegistrationError) as exc_info:
        RegistrationService._resolve_parent(metadata)
    assert exc_info.value.code == "parent_not_found"


@pytest.mark.django_db
def test_resolve_parent_returns_asset_for_known_id():
    """Complement to AC-3: a known parent_asset_id resolves to the IpAsset."""
    import decimal
    from tests.factories import CreatorFactory, IpAssetFactory
    from services.registration_service import RegistrationMetadata, RegistrationService

    parent = IpAssetFactory(creator=CreatorFactory())
    metadata = RegistrationMetadata("wallet", "image", "private", decimal.Decimal("1"), decimal.Decimal("1"), parent_asset_id=str(parent.id), royalty_share_bps=100)
    assert RegistrationService._resolve_parent(metadata).id == parent.id
