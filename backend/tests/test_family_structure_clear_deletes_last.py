"""A family-structure change that clears the family's genomic data deletes it in ClickHouse
last, just before its commit.

``PUT /families/{id}/structure`` with ``clear_existing_genomic_data`` deletes the family's
variants and tracks with its reviews and Postgres-held data. ClickHouse has no transactions:
deleted before the change's own writes (the members, the relationships, the pedigree, the
structure version), any of which can fail, its small variants, structural variants and
interval tracks would stay deleted when the failure rolled the family back -- its members,
its reviews, its repeat calls. So they go once everything else is written, under the locks
the clear took.

``family_structure_service`` runs against a fake of the request's transaction and of the
stores; ClickHouse is ``delete_family_small_variants``, ``delete_family_structural_variants``
and ``delete_interval_tracks``.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.schemas import FamilyStructureUpdate
from backend.app.services import family_structure_service as service
from backend.tests._deletion_mark_fakes import UnmarkedDeletion
from backend.tests._family_structure_fakes import admin_user, fake_structure_reads, member_row

DATA = {"small_variants": 120, "structural_variants": 4, "coverage": 2}


def _member(sample_id: str, *, role: str, sex: str) -> dict[str, Any]:
    return member_row(
        family_uuid="fam-uuid", sample_uuid=f"uuid-{sample_id.lower()}", sample_id=sample_id, role=role, sex=sex
    )


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
    fake_structure_reads(
        monkeypatch,
        family_uuid="fam-uuid",
        family_id="FAM1",
        members=[_member("FATHER", role="father", sex="male"), _member("CHILD", role="proband", sex="female")],
        data_counts=DATA,
    )

    def record(name: str):
        async def done(*_args, **_kwargs) -> None:
            recorded.append(name)

        return done

    for name, value in {
        "lock_family_variant_writes": record("lock"),
        "delete_interval_track_sources": record("delete interval track sources"),
        "delete_family_small_variants": record("ClickHouse: delete small variants"),
        "delete_family_structural_variants": record("ClickHouse: delete structural variants"),
        "delete_interval_tracks": record("ClickHouse: delete interval tracks"),
        "_update_member_row": record("update member"),
        "_record_family_structure_version": record("record structure version"),
        # Its mark is test_deletions_mark_the_family.py's.
        "FamilyDeletion": UnmarkedDeletion,
    }.items():
        monkeypatch.setattr(service, name, value)
    return recorded


async def _clear(events: list[str]):
    return await service.update_family_structure_for_admin(
        _Transaction(events),  # type: ignore[arg-type]
        family_id="FAM1",
        update=FamilyStructureUpdate(remove_members=["FATHER"], clear_existing_genomic_data=True),
        user=admin_user(),
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

    # Nothing was deleted in ClickHouse: the deletes come after the writes that failed.
    assert not [event for event in events if event.startswith("ClickHouse")]
    assert "commit" not in events


@pytest.mark.asyncio
async def test_a_structure_change_deletes_the_familys_clickhouse_data_last_before_its_commit(events) -> None:
    result = await _clear(events)

    assert result.cleared_data_counts == DATA
    # The Postgres-held data and every write of the change first, under the locks the clear
    # took; then the variants and tracks in ClickHouse; then the commit. The change takes the
    # variant-write locks and then the family's pedigree lock (the SELECT) before it reads the
    # pedigree; the clear takes the variant-write locks again, granted at once.
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
