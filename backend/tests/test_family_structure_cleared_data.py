"""A structure update records whether it kept the family's imported data.

`PUT /families/{id}/structure` marks what an edit made stale in
`families.metadata.derived_data_status.family_metadata`. That record said
`raw_datasets_preserved: true` on every update, also when `clear_existing_genomic_data`
had just deleted the family's variants, tracks and reviews. It now says whether the data
was kept, and a clear is recorded, with a warning, even when nothing else changed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.schemas import FamilyOut, FamilyStructureUpdate
from app.services import family_structure_service as service
from app.services.access_control import CurrentUser

DATA = {"small_variants": 120, "structural_variants": 4, "coverage": 0}
NO_DATA = {"small_variants": 0, "structural_variants": 0, "coverage": 0}


def _user() -> CurrentUser:
    return CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _member(sample_id: str, *, role: str, sex: str) -> dict[str, Any]:
    return {
        "family_uuid": "fam-uuid",
        "sample_uuid": f"uuid-{sample_id.lower()}",
        "sample_id": sample_id,
        "sex": sex,
        "role": role,
        "clinical_status": "unaffected",
        "carrier_status": "unknown",
        "carrier_type": None,
        "carrier_evidence": {},
        "active": True,
    }


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return None

    async def commit(self) -> None:
        return None

    def family_metadata_update(self) -> dict[str, Any]:
        [params] = [params for sql, params in self.calls if "UPDATE families" in sql]
        return dict(params)


class _Recorded:
    def __init__(self) -> None:
        self.cleared = False
        self.version_metadata: dict[str, Any] = {}


def _patch(monkeypatch: pytest.MonkeyPatch, *, data_counts: dict[str, int]) -> _Recorded:
    recorded = _Recorded()
    members = {
        service._sample_key("FATHER"): _member("FATHER", role="father", sex="male"),
        service._sample_key("CHILD"): _member("CHILD", role="proband", sex="female"),
    }

    async def fake_mapping(*_args, **_kwargs):
        return {"id": "fam-uuid", "family_id": "FAM1"}

    async def fake_version(*_args, **_kwargs):
        return 1

    async def fake_members(*_args, **_kwargs):
        return {key: dict(row) for key, row in members.items()}

    async def fake_relationships(*_args, **_kwargs):
        return []

    async def fake_counts(*_args, **_kwargs):
        return dict(data_counts)

    async def fake_clear(*_args, **_kwargs):
        recorded.cleared = True
        return dict(data_counts)

    async def fake_record_version(_session, *, family_uuid, created_by=None, metadata=None):
        recorded.version_metadata = dict(metadata or {})

    async def fake_noop(*_args, **_kwargs):
        return None

    async def fake_family_record(*_args, **_kwargs):
        return FamilyOut(
            _id="fam-uuid",
            family_id="FAM1",
            created_at=datetime.now(timezone.utc),
            members=[],
            relationships=[],
            projects=[],
            metadata={},
        )

    monkeypatch.setattr(service, "get_accessible_family_mapping", fake_mapping)
    monkeypatch.setattr(service, "_fetch_current_structure_version", fake_version)
    monkeypatch.setattr(service, "_fetch_family_member_rows", fake_members)
    monkeypatch.setattr(service, "_fetch_current_relationship_rows", fake_relationships)
    monkeypatch.setattr(service, "_family_genomic_data_counts", fake_counts)
    monkeypatch.setattr(service, "_clear_family_genomic_data", fake_clear)
    monkeypatch.setattr(service, "_update_member_row", fake_noop)
    monkeypatch.setattr(service, "_insert_new_member", fake_noop)
    monkeypatch.setattr(service, "_replace_relationship_rows", fake_noop)
    monkeypatch.setattr(service, "_record_family_structure_version", fake_record_version)
    monkeypatch.setattr(service, "_pedigree_text_from_target", lambda **_kwargs: "")
    monkeypatch.setattr(service, "get_family_record", fake_family_record)
    return recorded


async def _update(session: _RecordingSession, **fields: Any):
    return await service.update_family_structure_for_admin(
        session,  # type: ignore[arg-type]
        family_id="FAM1",
        update=FamilyStructureUpdate(**fields),
        user=_user(),
    )


@pytest.mark.asyncio
async def test_an_update_that_clears_the_data_records_it_as_not_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=DATA)
    session = _RecordingSession()

    await _update(session, remove_members=["FATHER"], clear_existing_genomic_data=True)

    assert recorded.cleared is True
    assert session.family_metadata_update().get("raw_datasets_preserved") is False
    assert recorded.version_metadata["cleared_data_counts"] == DATA


@pytest.mark.asyncio
async def test_an_update_that_keeps_the_data_records_it_as_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=DATA)
    session = _RecordingSession()

    await _update(session, remove_members=["FATHER"])

    assert recorded.cleared is False
    assert session.family_metadata_update().get("raw_datasets_preserved") is True
    assert recorded.version_metadata["cleared_data_counts"] == {}


@pytest.mark.asyncio
async def test_asking_to_clear_a_family_without_data_keeps_the_record_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=NO_DATA)
    session = _RecordingSession()

    await _update(session, remove_members=["FATHER"], clear_existing_genomic_data=True)

    # Nothing was there to delete, so nothing was deleted.
    assert recorded.cleared is False
    assert session.family_metadata_update().get("raw_datasets_preserved") is True


@pytest.mark.asyncio
async def test_a_clear_without_any_other_change_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=DATA)
    session = _RecordingSession()

    result = await _update(session, clear_existing_genomic_data=True)

    assert recorded.cleared is True
    update = session.family_metadata_update()
    assert update.get("raw_datasets_preserved") is False
    # The family's sample data has to be imported again; the record and the response say so.
    assert "sample-data" in update["stale_scopes"]
    assert "sample-data" in result.stale_analysis_scopes
    assert any("cleared" in warning for warning in result.warnings)
    assert result.cleared_data_counts == DATA
