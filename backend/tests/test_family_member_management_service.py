"""The member edits and the member page (``family_member_management_service``).

A batch member update passes on only what it changes: parent fields that name the parents
the member already has are no relationship change. The member page resolves the family
once and threads its UUID into the HPO lookup and the impact call, without running the
family-mapping aggregation again. The impact of a member change names what it makes stale;
when the ClickHouse genotype counts cannot be read it says so, and a rename is then blocked
(fails closed): a rename must not orphan ClickHouse rows keyed by the old sample ID.
The service's reads are faked at its helpers. All IDs are synthetic.
"""

from datetime import datetime, timezone

import pytest

from backend.app.schemas import (
    FamilyMemberBatchUpdate,
    FamilyMemberBatchUpdateItem,
    FamilyMemberImpactOut,
    FamilyMemberOut,
    FamilyOut,
    FamilyRelationshipOut,
    FamilyStructureUpdateOut,
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
    """FATHER and MOTHER with their affected child CHILD."""
    now = datetime.now(timezone.utc)
    return FamilyOut(
        _id="fam-uuid-xyz",
        family_id="FAM1",
        created_at=now,
        members=[
            FamilyMemberOut(
                sample_id="FATHER",
                role="father",
                affected=False,
                sex="male",
                clinical_status="unaffected",
                carrier_status="unknown",
            ),
            FamilyMemberOut(
                sample_id="MOTHER",
                role="mother",
                affected=False,
                sex="female",
                clinical_status="unaffected",
                carrier_status="unknown",
            ),
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
            FamilyRelationshipOut(
                id="rel-mother",
                relationship_type="parent_child",
                sample_id_a="MOTHER",
                sample_id_b="CHILD",
                role_a="mother",
                role_b="child",
                source="ped_import",
            ),
        ],
        projects=[],
        metadata={},
    )


# --------------------------------------------------------------------------- #
# A batch member update
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_batch_metadata_update_ignores_unchanged_parent_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    family = _family_record()

    async def fake_accessible_family_mapping(*args, **kwargs):
        return {"id": "fam-1", "family_id": "FAM1"}

    async def fake_family_record(*args, **kwargs):
        return family

    async def fake_structure_update(*args, **kwargs):
        captured["update"] = kwargs["update"]
        return FamilyStructureUpdateOut(family=family, warnings=[])

    async def no_pedigree_lock(*args, **kwargs):
        # The family's pedigree lock is a statement on the session; there is none here.
        return None

    monkeypatch.setattr(service, "get_accessible_family_mapping", fake_accessible_family_mapping)
    monkeypatch.setattr(service, "get_family_record", fake_family_record)
    monkeypatch.setattr(service, "update_family_structure_for_admin", fake_structure_update)
    monkeypatch.setattr(service, "lock_family_pedigree", no_pedigree_lock)

    response = await service.update_family_members_batch_for_admin(
        object(),
        family_id="FAM1",
        update=FamilyMemberBatchUpdate(
            updates=[
                FamilyMemberBatchUpdateItem(
                    sample_id="CHILD",
                    carrier_status="carrier",
                    father_id="FATHER",
                    mother_id="MOTHER",
                )
            ],
        ),
        user=_user(),
    )

    assert response.family.family_id == "FAM1"
    assert captured["update"].relationships is None
    assert captured["update"].members[0].sample_id == "CHILD"
    assert captured["update"].members[0].carrier_status == "carrier"


# --------------------------------------------------------------------------- #
# The member page and the impact of a change
# --------------------------------------------------------------------------- #


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


def _impact_reads(monkeypatch: pytest.MonkeyPatch, *, clickhouse_counts=None) -> dict:
    """The impact's reads faked; ``clickhouse_counts`` an exception the ClickHouse count
    raises, or the counts it returns. Returns what the reads saw."""
    seen: dict = {"mapping_calls": 0, "family_uuids": []}

    async def fake_accessible_family_mapping(*args, **kwargs):
        seen["mapping_calls"] += 1
        return {"id": "mapped-uuid"}

    async def fake_sample_uuid(session, *, family_uuid, sample_id):
        seen["family_uuids"].append(family_uuid)
        return ("sample-uuid", "RESOLVED")

    async def fake_postgres_counts(*args, **kwargs):
        return ({}, {})

    async def fake_review_counts(*args, **kwargs):
        return {}

    async def fake_clickhouse_counts(*args, **kwargs):
        if isinstance(clickhouse_counts, Exception):
            raise clickhouse_counts
        return dict(clickhouse_counts or {})

    monkeypatch.setattr(service, "get_accessible_family_mapping", fake_accessible_family_mapping)
    monkeypatch.setattr(service, "_sample_uuid_for_member", fake_sample_uuid)
    monkeypatch.setattr(service, "_sample_postgres_counts", fake_postgres_counts)
    monkeypatch.setattr(service, "_family_review_counts", fake_review_counts)
    monkeypatch.setattr(service, "_sample_clickhouse_counts", fake_clickhouse_counts)
    return seen


@pytest.mark.asyncio
async def test_impact_skips_mapping_when_family_uuid_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _impact_reads(monkeypatch)

    # Supplied UUID -> the aggregation is skipped and the value is used as-is.
    await service.get_family_member_impact_for_user(
        object(),
        family_id="FAM1",
        sample_id="CHILD",
        user=_user(),
        family_uuid="threaded-uuid",
    )
    assert seen["mapping_calls"] == 0
    assert seen["family_uuids"][-1] == "threaded-uuid"

    # Omitted (the default for every other call site) -> aggregation still runs.
    await service.get_family_member_impact_for_user(
        object(),
        family_id="FAM1",
        sample_id="CHILD",
        user=_user(),
    )
    assert seen["mapping_calls"] == 1
    assert seen["family_uuids"][-1] == "mapped-uuid"


@pytest.mark.asyncio
async def test_an_impact_whose_clickhouse_counts_cannot_be_read_says_so_and_blocks_a_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ClickHouse unreachable is no "no genotypes": the impact names it, counts the
    # change as destructive, and the rename guard reads the marker and refuses.
    _impact_reads(monkeypatch, clickhouse_counts=RuntimeError("ClickHouse unreachable"))

    impact = await service.get_family_member_impact_for_user(
        object(), family_id="FAM1", sample_id="CHILD", user=_user(), family_uuid="fam-uuid"
    )

    assert impact.data_counts == {"clickhouse_counts_unavailable": 1}
    assert "ClickHouse genotype counts could not be verified; treat genotype linkage as unknown." in impact.warnings
    assert impact.destructive is True
    reason = service._rename_block_reason(impact.data_counts)
    assert reason is not None and "could not be verified" in reason

    # Read, and empty: nothing to orphan, so nothing is blocked.
    _impact_reads(monkeypatch, clickhouse_counts={})
    impact = await service.get_family_member_impact_for_user(
        object(), family_id="FAM1", sample_id="CHILD", user=_user(), family_uuid="fam-uuid"
    )
    assert (impact.data_counts, impact.destructive) == ({}, False)
    assert service._rename_block_reason(impact.data_counts) is None


def test_rename_block_reason_fails_closed_on_unverified_clickhouse() -> None:
    # No genomic data and ClickHouse counts verified -> safe to rename.
    assert service._rename_block_reason({}) is None
    # Imported genomic data -> blocked (raw datasets still use the source sample id).
    assert service._rename_block_reason({"small_variants": 5}) is not None
    # When the ClickHouse counts could not be verified, fail CLOSED even with no
    # other genomic data — a rename must not silently orphan CH variant rows keyed by the
    # old sample id.
    reason = service._rename_block_reason({"clickhouse_counts_unavailable": 1})
    assert reason is not None
    assert "could not be verified" in reason


@pytest.mark.parametrize(
    ("kind", "scopes", "stale_scopes"),
    [
        ("coverage", ["coverage", "apcad"], ["coverage", "apcad"]),
        ("apcad_segregation", ["coverage", "apcad"], ["coverage", "apcad"]),
        ("repeat_expansions", ["repeat-expansions"], ["repeat-expansions"]),
        ("paraphase", ["paraphase"], ["paraphase"]),
        (
            "hpo_annotations",
            ["phenotype-annotations", "phenotype-driven-filtering"],
            ["segregation", "inheritance-filtering"],
        ),
        ("small_variant_reviews", ["saved-variant-reviews"], []),
    ],
)
def test_the_impact_names_what_a_member_change_makes_stale(
    kind: str, scopes: list[str], stale_scopes: list[str]
) -> None:
    found, stale, warnings = service._impact_scopes(pedigree_counts={}, data_counts={kind: 1})

    assert (found, stale) == (scopes, stale_scopes)
    # The data a change leaves to be reassessed is said in a warning, except repeats,
    # Paraphase and phenotypes, which only go stale.
    assert bool(warnings) is (kind in {"coverage", "apcad_segregation", "small_variant_reviews"})
