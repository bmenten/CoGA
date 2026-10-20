"""A family-structure change that clears the family's genomic data deletes it in ClickHouse
last, just before its commit (SAFE-28).

``PUT /families/{id}/structure`` with ``clear_existing_genomic_data`` deletes the family's
variants and tracks with its reviews and Postgres-held data. ClickHouse has no transactions:
its small variants, structural variants and interval tracks went first, before the change's
own writes (the members, the relationships, the pedigree, the structure version), any of
which can still fail. The failure rolled the family back -- its members, its reviews, its
repeat calls -- without its variants and tracks, and nothing marked it, as the PED upload's
replacement did before #859. Now they go once everything else is written, under the locks
the clear took.

``family_structure_service`` runs against a fake of the request's transaction and of the
stores; ClickHouse is ``delete_family_small_variants``, ``delete_family_structural_variants``
and ``delete_interval_tracks``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import FamilyOut, FamilyStructureUpdate
from backend.app.services import family_structure_service as service
from backend.app.services.access_control import CurrentUser
from backend.tests._deletion_mark_fakes import UnmarkedDeletion

DATA = {"small_variants": 120, "structural_variants": 4, "coverage": 2}


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


class _Transaction:
    """The request's transaction: each statement it runs, and its commit, in ``events``."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def execute(self, statement, _params=None):
        sql = " ".join(str(statement).split())
        self.events.append(sql.split(" WHERE ")[0] if sql.startswith("DELETE") else sql.split(" ")[0])
        return None

    async def commit(self) -> None:
        self.events.append("commit")


@pytest.fixture
def events(monkeypatch) -> list[str]:
    """What the structure change did, in order."""
    recorded: list[str] = []
    members = {
        service._sample_key("FATHER"): _member("FATHER", role="father", sex="male"),
        service._sample_key("CHILD"): _member("CHILD", role="proband", sex="female"),
    }

    async def mapping(*_args, **_kwargs):
        return {"id": "fam-uuid", "family_id": "FAM1"}

    async def version(*_args, **_kwargs):
        return 1

    async def current_members(*_args, **_kwargs):
        return {key: dict(row) for key, row in members.items()}

    async def no_relationships(*_args, **_kwargs):
        return []

    async def counts(*_args, **_kwargs):
        return dict(DATA)

    async def assemblies(*_args, **_kwargs):
        return {"GRCh38": ["project-1"]}

    def record(name: str):
        async def done(*_args, **_kwargs) -> None:
            recorded.append(name)

        return done

    async def family_record(*_args, **_kwargs):
        return FamilyOut(
            _id="fam-uuid",
            family_id="FAM1",
            created_at=datetime.now(timezone.utc),
            members=[],
            relationships=[],
            projects=[],
            metadata={},
        )

    for name, value in {
        "get_accessible_family_mapping": mapping,
        "_fetch_current_structure_version": version,
        "_fetch_family_member_rows": current_members,
        "_fetch_current_relationship_rows": no_relationships,
        "_family_genomic_data_counts": counts,
        "_family_assembly_groups": assemblies,
        "lock_family_variant_writes": record("lock"),
        "delete_interval_track_sources": record("delete interval track sources"),
        "delete_family_small_variants": record("ClickHouse: delete small variants"),
        "delete_family_structural_variants": record("ClickHouse: delete structural variants"),
        "delete_interval_tracks": record("ClickHouse: delete interval tracks"),
        "_update_member_row": record("update member"),
        "_record_family_structure_version": record("record structure version"),
        "_pedigree_text_from_target": lambda **_kwargs: "",
        "get_family_record": family_record,
        # Its mark is test_deletions_mark_the_family.py's (SAFE-31).
        "FamilyDeletion": UnmarkedDeletion,
    }.items():
        monkeypatch.setattr(service, name, value)
    return recorded


async def _clear(events: list[str]):
    return await service.update_family_structure_for_admin(
        _Transaction(events),  # type: ignore[arg-type]
        family_id="FAM1",
        update=FamilyStructureUpdate(remove_members=["FATHER"], clear_existing_genomic_data=True),
        user=_user(),
    )


@pytest.mark.asyncio
async def test_a_structure_change_that_fails_after_the_clear_leaves_the_familys_clickhouse_data(
    events, monkeypatch
) -> None:
    # A write of the change after the clear fails (a database constraint, say): the
    # transaction rolls back, bringing the family back with its reviews and repeat calls.
    async def fails(*_args, **_kwargs) -> None:
        raise RuntimeError("constraint violated")

    monkeypatch.setattr(service, "_record_family_structure_version", fails)

    with pytest.raises(RuntimeError, match="constraint violated"):
        await _clear(events)

    # Before: the family's variants and tracks were deleted first, and stayed deleted.
    assert not [event for event in events if event.startswith("ClickHouse")]
    assert "commit" not in events


@pytest.mark.asyncio
async def test_a_structure_change_deletes_the_familys_clickhouse_data_last_before_its_commit(events) -> None:
    result = await _clear(events)

    assert result.cleared_data_counts == DATA
    # The Postgres-held data and every write of the change first, under the locks the clear
    # took; then the variants and tracks in ClickHouse; then the commit. The change takes the
    # variant-write locks and then the family's pedigree lock (the SELECT) before it reads the
    # pedigree (SEC-7); the clear takes the variant-write locks again, granted at once.
    assert events == [
        "lock",
        "SELECT",
        "lock",
        "delete interval track sources",
        "DELETE FROM repeat_expansions",
        "DELETE FROM sample_paraphase_results",
        "DELETE FROM small_variant_reviews",
        "DELETE FROM structural_variant_reviews",
        "update member",
        "UPDATE",
        "record structure version",
        "ClickHouse: delete small variants",
        "ClickHouse: delete structural variants",
        "ClickHouse: delete interval tracks",
        "commit",
    ]
