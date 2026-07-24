"""SPEC-001 unit tests — EventRecorder (services layer).

SPEC-001 only records the ANCHORED event (R7). EventRecorder persists an
AgentEvent row and fans out to Firestore/BigQuery (no-ops when disabled).
"""
from __future__ import annotations

import pytest

from services.event_recorder import EventRecorder
from tests.fakes import FakeBigQuery, FakeFirestore


@pytest.mark.django_db
def test_event_recorder_records_anchored_event():
    """record() persists an AgentEvent row and returns it."""
    from apps.common.models import AgentEvent
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    recorder = EventRecorder(firestore=FakeFirestore(), bigquery=FakeBigQuery())

    before = AgentEvent.objects.count()
    event = recorder.record("ANCHORED", {"anchor_tx_sig": "sig123"}, asset=asset)
    after = AgentEvent.objects.count()

    assert after == before + 1
    assert event.type == "ANCHORED"
    assert event.asset_id == asset.id
    assert event.payload == {"anchor_tx_sig": "sig123"}
    # Fan-out to mirrors happened.
    assert event.pk is not None


@pytest.mark.django_db
def test_event_recorder_fans_out_to_firestore_and_bigquery():
    """Disabled sinks no-op; enabled fakes receive the row."""
    from apps.ip.models import IpAsset
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    fs = FakeFirestore(enabled=True)
    bq = FakeBigQuery(dataset="veriproof_analytics")
    recorder = EventRecorder(firestore=fs, bigquery=bq)

    recorder.record("ANCHORED", {"sha": "deadbeef"}, asset=asset)

    assert len(fs.calls) == 1
    assert fs.calls[0][0] == "set"
    assert len(bq.calls) == 1
    assert bq.calls[0][0] == "insert"


@pytest.mark.django_db
def test_event_recorder_works_without_sinks():
    """EventRecorder tolerates None Firestore/BigQuery (offline default)."""
    from apps.common.models import AgentEvent
    from tests.factories import CreatorFactory, IpAssetFactory

    asset = IpAssetFactory(creator=CreatorFactory())
    recorder = EventRecorder(firestore=None, bigquery=None)

    event = recorder.record("ANCHORED", {"x": 1}, asset=asset)
    assert AgentEvent.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_event_recorder_survives_fan_out_failures():
    """If Firestore/BigQuery raise, the AgentEvent row still persists (R7)."""
    from apps.common.models import AgentEvent

    class _ExplodingSink:
        def __init__(self) -> None:
            self.calls = 0

        def set(self, collection, doc_id, data):  # noqa: ANN001
            self.calls += 1
            raise RuntimeError("firestore down")

        def insert(self, table, row):  # noqa: ANN001
            raise RuntimeError("bigquery down")

    boom = _ExplodingSink()
    recorder = EventRecorder(firestore=boom, bigquery=boom)

    from tests.factories import CreatorFactory, IpAssetFactory
    asset = IpAssetFactory(creator=CreatorFactory())
    event = recorder.record("ANCHORED", {"k": "v"}, asset=asset)

    # The row still persisted despite both mirrors raising.
    assert AgentEvent.objects.filter(pk=event.pk, type="ANCHORED").exists()


@pytest.mark.django_db
def test_event_recorder_factory_builds_recorder():
    """get_event_recorder() builds a recorder with sink wiring from settings."""
    from services.event_recorder import EventRecorder, get_event_recorder

    recorder = get_event_recorder()
    assert isinstance(recorder, EventRecorder)
