from datetime import datetime, timezone

import pytest

from backend.app.schemas import (
    FamilyMemberImpactOut,
    FamilyMemberOut,
    FamilyOut,
    FamilyRelationshipOut,
)
from backend.app.services import family_member_management_service as service
from backend.app.services.access_control import CurrentUser


def _user() -> CurrentUser:
    return CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _family_record() -> FamilyOut:
    now = datetime.now(timezone.utc)
    return FamilyOut(
        _id="fam-uuid-xyz",
        family_id="FAM1",
        created_at=now,
        members=[
            FamilyMemberOut(
                sample_id="CHILD",
                role="proband",
                affected=True,
                sex="female",
                clinical_status="affected",
                carrier_status="unknown",
            ),
        ],
        relationships=[
            FamilyRelationshipOut(
                id="rel-father",
                relationship_type="parent_child",
                sample_id_a="FATHER",
                sample_id_b="CHILD",
                role_a="father",
                role_b="child",
                source="ped_import",
            ),
        ],
        projects=[],
        metadata={},
    )


@pytest.mark.asyncio
async def test_detail_reuses_resolved_uuid_and_skips_redundant_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The detail endpoint resolves the family once (get_family_record) and threads
    that UUID into the HPO lookup and the impact call — never re-running the
    family-mapping aggregation directly."""
    family = _family_record()
    mapping_calls = 0
    captured: dict = {}

    async def fake_family_record(*args, **kwargs):
        return family

    async def fake_accessible_family_mapping(*args, **kwargs):
        nonlocal mapping_calls
        mapping_calls += 1
        return {"id": "SHOULD-NOT-BE-USED", "family_id": "FAM1"}

    async def fake_list_hpo(session, *, family_uuid, sample_id):
        captured["hpo_family_uuid"] = family_uuid
        return []

    async def fake_impact(session, *, family_id, sample_id, user, family_uuid=None):
        captured["impact_family_uuid"] = family_uuid
        return FamilyMemberImpactOut(sample_id=sample_id)

    monkeypatch.setattr(service, "get_family_record", fake_family_record)
    monkeypatch.setattr(service, "get_accessible_family_mapping", fake_accessible_family_mapping)
    monkeypatch.setattr(service, "list_family_hpo_annotations", fake_list_hpo)
    monkeypatch.setattr(service, "get_family_member_impact_for_user", fake_impact)

    result = await service.get_family_member_detail_for_user(
        object(),
        family_id="FAM1",
        sample_id="CHILD",
        user=_user(),
    )

    # No direct family-mapping aggregation in the detail flow (only get_family_record's).
    assert mapping_calls == 0
    # The resolved FamilyOut.id is threaded into both downstream calls.
    assert captured["hpo_family_uuid"] == "fam-uuid-xyz"
    assert captured["impact_family_uuid"] == "fam-uuid-xyz"
    assert result.member.sample_id == "CHILD"
    assert result.father_id == "FATHER"


@pytest.mark.asyncio
async def test_impact_skips_mapping_when_family_uuid_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_calls = 0
    seen_family_uuid: list[str] = []

    async def fake_accessible_family_mapping(*args, **kwargs):
        nonlocal mapping_calls
        mapping_calls += 1
        return {"id": "mapped-uuid"}

    async def fake_sample_uuid(session, *, family_uuid, sample_id):
        seen_family_uuid.append(family_uuid)
        return ("sample-uuid", "RESOLVED")

    async def fake_postgres_counts(*args, **kwargs):
        return ({}, {})

    async def fake_review_counts(*args, **kwargs):
        return {}

    async def fake_clickhouse_counts(*args, **kwargs):
        return {}

    def fake_scopes(*args, **kwargs):
        return ([], [], [])

    monkeypatch.setattr(service, "get_accessible_family_mapping", fake_accessible_family_mapping)
    monkeypatch.setattr(service, "_sample_uuid_for_member", fake_sample_uuid)
    monkeypatch.setattr(service, "_sample_postgres_counts", fake_postgres_counts)
    monkeypatch.setattr(service, "_family_review_counts", fake_review_counts)
    monkeypatch.setattr(service, "_sample_clickhouse_counts", fake_clickhouse_counts)
    monkeypatch.setattr(service, "_impact_scopes", fake_scopes)

    # Supplied UUID -> the aggregation is skipped and the value is used as-is.
    await service.get_family_member_impact_for_user(
        object(),
        family_id="FAM1",
        sample_id="CHILD",
        user=_user(),
        family_uuid="threaded-uuid",
    )
    assert mapping_calls == 0
    assert seen_family_uuid[-1] == "threaded-uuid"

    # Omitted (the default for every other call site) -> aggregation still runs.
    await service.get_family_member_impact_for_user(
        object(),
        family_id="FAM1",
        sample_id="CHILD",
        user=_user(),
    )
    assert mapping_calls == 1
    assert seen_family_uuid[-1] == "mapped-uuid"


def test_rename_block_reason_fails_closed_on_unverified_clickhouse() -> None:
    # No genomic data and ClickHouse counts verified -> safe to rename.
    assert service._rename_block_reason({}) is None
    # Imported genomic data -> blocked (raw datasets still use the source sample id).
    assert service._rename_block_reason({"small_variants": 5}) is not None
    # #333: when the ClickHouse counts could not be verified, fail CLOSED even with no
    # other genomic data — a rename must not silently orphan CH variant rows keyed by the
    # old sample id. (Previously the marker was excluded from the count, so it summed to
    # 0 and the rename proceeded.)
    reason = service._rename_block_reason({"clickhouse_counts_unavailable": 1})
    assert reason is not None
    assert "could not be verified" in reason
