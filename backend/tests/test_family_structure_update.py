"""A family-structure update (``update_family_structure_for_admin``, ``PUT /families/{id}/structure``).

What it rewrites: only the members an update actually changes are written again, not every
member (Batch-3 dirty-tracking cleanup).

What it records: the update marks what it made stale in
``families.metadata.derived_data_status.family_metadata``. That record said
``raw_datasets_preserved: true`` on every update, also when ``clear_existing_genomic_data``
had just deleted the family's variants, tracks and reviews. It now says whether the data was
kept, and a clear is recorded, with a warning, even when nothing else changed. The record's
reason is bound as text (``CAST(:reason AS text)``), which asyncpg needs to type a parameter
inside ``jsonb_build_object`` (SAFE-31).

The structure's reads and writes are faked at the service's helpers; the ``UPDATE families``
statement runs against a session that records it. All IDs are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.schemas import FamilyOut, FamilyStructureMemberUpdate, FamilyStructureUpdate
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


def _member(sample_id: str, *, role: str, sex: str, clinical_status: str = "unaffected") -> dict[str, Any]:
    return {
        "family_uuid": "fam-uuid",
        "sample_uuid": f"uuid-{sample_id.lower()}",
        "sample_id": sample_id,
        "sex": sex,
        "role": role,
        "clinical_status": clinical_status,
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

    def family_update(self) -> tuple[str, dict[str, Any]]:
        [(sql, params)] = [(sql, params) for sql, params in self.calls if "UPDATE families" in sql]
        return sql, dict(params)

    def family_metadata_update(self) -> dict[str, Any]:
        return self.family_update()[1]


class _Recorded:
    def __init__(self) -> None:
        self.cleared = False
        self.version_metadata: dict[str, Any] = {}
        self.updated_members: list[str] = []


def _patch(
    monkeypatch: pytest.MonkeyPatch, *, data_counts: dict[str, int], members: list[dict[str, Any]]
) -> _Recorded:
    recorded = _Recorded()
    current = {service._sample_key(row["sample_id"]): row for row in members}

    async def fake_mapping(*_args, **_kwargs):
        return {"id": "fam-uuid", "family_id": "FAM1"}

    async def fake_version(*_args, **_kwargs):
        return 1

    async def fake_members(*_args, **_kwargs):
        return {key: dict(row) for key, row in current.items()}

    async def fake_relationships(*_args, **_kwargs):
        return []

    async def fake_counts(*_args, **_kwargs):
        return dict(data_counts)

    async def fake_clear(*_args, **_kwargs):
        recorded.cleared = True
        # What the family held, and the assemblies whose ClickHouse data goes last: none here.
        return dict(data_counts), []

    async def fake_update_member_row(_session, *, member):
        recorded.updated_members.append(str(member["sample_id"]))

    async def fake_record_version(_session, *, family_uuid, metadata=None, **_kwargs):
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
    monkeypatch.setattr(service, "_update_member_row", fake_update_member_row)
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


# --------------------------------------------------------------------------- #
# Which members are written again
# --------------------------------------------------------------------------- #

TRIO = [
    _member("FATHER", role="father", sex="female"),
    _member("MOTHER", role="mother", sex="female"),
    _member("CHILD", role="proband", sex="female", clinical_status="unknown"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clinical_status", "written"),
    [
        # Only CHILD changed -> only CHILD is re-written (not FATHER/MOTHER).
        ("affected", ["CHILD"]),
        # clinical_status is already "unknown" for CHILD -> no actual change.
        ("unknown", []),
    ],
    ids=["a-changed-member", "a-no-op-update"],
)
async def test_only_the_members_an_update_changes_are_written_again(
    monkeypatch: pytest.MonkeyPatch, clinical_status: str, written: list[str]
) -> None:
    recorded = _patch(monkeypatch, data_counts=NO_DATA, members=TRIO)

    await _update(
        _RecordingSession(),
        members=[FamilyStructureMemberUpdate(sample_id="CHILD", clinical_status=clinical_status)],
    )

    assert recorded.updated_members == written


# --------------------------------------------------------------------------- #
# Whether the update kept the family's imported data
# --------------------------------------------------------------------------- #

FATHER_AND_CHILD = [
    _member("FATHER", role="father", sex="male"),
    _member("CHILD", role="proband", sex="female"),
]


@pytest.mark.asyncio
async def test_an_update_that_clears_the_data_records_it_as_not_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=DATA, members=FATHER_AND_CHILD)
    session = _RecordingSession()

    await _update(session, remove_members=["FATHER"], clear_existing_genomic_data=True)

    assert recorded.cleared is True
    assert session.family_metadata_update().get("raw_datasets_preserved") is False
    assert recorded.version_metadata["cleared_data_counts"] == DATA


@pytest.mark.asyncio
async def test_an_update_that_keeps_the_data_records_it_as_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=DATA, members=FATHER_AND_CHILD)
    session = _RecordingSession()

    await _update(session, remove_members=["FATHER"])

    assert recorded.cleared is False
    assert session.family_metadata_update().get("raw_datasets_preserved") is True
    assert recorded.version_metadata["cleared_data_counts"] == {}


@pytest.mark.asyncio
async def test_asking_to_clear_a_family_without_data_keeps_the_record_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=NO_DATA, members=FATHER_AND_CHILD)
    session = _RecordingSession()

    await _update(session, remove_members=["FATHER"], clear_existing_genomic_data=True)

    # Nothing was there to delete, so nothing was deleted.
    assert recorded.cleared is False
    assert session.family_metadata_update().get("raw_datasets_preserved") is True


@pytest.mark.asyncio
async def test_a_clear_without_any_other_change_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _patch(monkeypatch, data_counts=DATA, members=FATHER_AND_CHILD)
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


@pytest.mark.asyncio
async def test_family_structure_update_query_reason_parameter_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    # The change itself, as update_family_structure_for_admin runs it (SAFE-31): the reason
    # goes into jsonb_build_object, where asyncpg cannot type an untyped parameter.
    _patch(monkeypatch, data_counts=DATA, members=FATHER_AND_CHILD)
    session = _RecordingSession()

    await _update(session, remove_members=["FATHER"], change_reason="family_detail_page")

    sql, params = session.family_update()
    assert "'reason', CAST(:reason AS text)" in sql
    assert params["reason"] == "family_detail_page"
