"""A PED upload or a manual family with overwrite deletes the replaced family's variants
last, just before its commit (SAFE-25).

Replacing a family deletes it, in the request's transaction, and its small variants and
structural variants, in ClickHouse, which has no transactions. The variants went first,
before the checks that can still refuse the upload: a sample ID another family has (409), a
phenotype the PED does not allow (400; a header line such as ``#FID IID PAT MAT SEX
PHENOTYPE`` is read as a family and refused there), carriers not in the PED, the region of
interest, a database constraint. The refusal rolled the family back, without its variants.
Now they are deleted once everything else of the replacement is written.

``ped_service`` runs against a fake of the request's transaction and of the stores; the
family's variants are deleted by ``delete_family_small_variants`` and
``delete_family_structural_variants``.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.schemas import ManualPedFamilyCreate, ManualPedMemberCreate
from backend.app.services import ped_service

_STORED = {"family_uuid": "uuid-old", "family_id": "FAM1", "project_id": "project-1", "assembly_name": "GRCh38"}


class _Transaction:
    """The request's transaction: each statement, and the commit, in ``events``; the sample IDs
    stored in other families answer the sample-ID check."""

    def __init__(self, events: list[tuple[Any, ...]], *, taken_sample_ids: tuple[str, ...] = ()) -> None:
        self.events = events
        self.taken_sample_ids = taken_sample_ids

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("DELETE FROM families"):
            self.events.append(("delete family", (params or {})["family_uuid"]))
            return SimpleNamespace(rowcount=1)
        if "FROM samples" in sql:
            taken = [(sample,) for sample in self.taken_sample_ids if sample in (params or {})["sample_ids"]]
            return SimpleNamespace(all=lambda: taken)
        raise AssertionError(f"unexpected statement: {sql}")

    async def commit(self) -> None:
        self.events.append(("commit",))


@pytest.fixture
def events(monkeypatch) -> list[tuple[Any, ...]]:
    """What the replacement did, in order: the family's locks, its delete, the new family,
    its variants' deletes and the commit."""
    recorded: list[tuple[Any, ...]] = []

    async def project(_session, _user, project_id):
        return project_id

    async def stored(_session, family_ids):
        return [dict(_STORED)] if "FAM1" in family_ids else []

    async def lock(_session, family_uuid, variant_types, **_kwargs) -> None:
        recorded.append(("lock", family_uuid))

    async def create(_session, **kwargs):
        recorded.append(("create family", kwargs["family_id"]))
        return {"family_id": kwargs["family_id"], "samples": []}

    def delete(kind: str):
        async def record(assembly_name: str, family_uuid: str) -> None:
            recorded.append((f"delete {kind}", assembly_name, family_uuid))

        return record

    async def family_deleted(_session, *, family_uuid, source, **_kwargs) -> None:
        # The replaced family's last clinical audit event (SEC-7).
        recorded.append(("audit: family deleted", family_uuid, source))

    monkeypatch.setattr(ped_service, "_resolve_accessible_project_id", project)
    monkeypatch.setattr(ped_service, "_existing_family_rows", stored)
    monkeypatch.setattr(ped_service, "lock_family_variant_writes", lock)
    monkeypatch.setattr(ped_service, "_create_family", create)
    monkeypatch.setattr(ped_service, "delete_family_small_variants", delete("small variants"))
    monkeypatch.setattr(ped_service, "delete_family_structural_variants", delete("structural variants"))
    monkeypatch.setattr(ped_service, "record_family_deleted", family_deleted)

    async def record_phenotypes(_session, *, family_uuid, change, **_kwargs) -> int:
        # The phenotypes the replaced family goes with, before its deletion's event.
        recorded.append(("audit: phenotypes removed", family_uuid, change))
        return 0

    monkeypatch.setattr(ped_service, "record_phenotypes_deleted_with_family", record_phenotypes)
    return recorded


_ADMIN = SimpleNamespace(id="admin", username="admin", role="admin", project_ids=[])


def _ped(text: str) -> UploadFile:
    return UploadFile(file=BytesIO(text.encode()), filename="family.ped")


_TRIO = "FAM1 CHILD1 FATHER1 MOTHER1 1 2\nFAM1 FATHER1 0 0 1 1\nFAM1 MOTHER1 0 0 2 1\n"


@pytest.fixture(autouse=True)
def _admin_may_replace(monkeypatch) -> None:
    monkeypatch.setattr(ped_service, "_ensure_user_can_replace_existing_families", lambda _user: None)


# PED uploads refused after the replacement began: the file, the sample IDs other families
# have, and the answer.
_REFUSED = {
    # A header line of six fields is read as a family, and its phenotype refused.
    "a header line": ("#FID IID PAT MAT SEX PHENOTYPE\n" + _TRIO, (), 400),
    "a phenotype the PED does not allow": (_TRIO.replace("1 2\n", "1 3\n", 1), (), 400),
    "a sample ID another family has": (_TRIO, ("MOTHER1",), 409),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_REFUSED))
async def test_a_ped_upload_refused_after_the_replacement_leaves_the_familys_variants(events, case) -> None:
    ped, taken, status = _REFUSED[case]
    with pytest.raises(HTTPException) as refused:
        await ped_service.upload_ped_data(
            _Transaction(events, taken_sample_ids=taken),  # type: ignore[arg-type]
            _ped(ped),
            True,
            _ADMIN,  # type: ignore[arg-type]
            "project-1",
        )

    assert refused.value.status_code == status, case
    # The family's delete is the transaction's, which the refusal rolls back; its variants
    # are not deleted (before: deleted at once, so the family came back without them).
    assert events == [
        ("lock", "uuid-old"),
        ("audit: phenotypes removed", "uuid-old", "replaced"),
        ("audit: family deleted", "uuid-old", "pedigree_replaced"),
        ("delete family", "uuid-old"),
    ]


@pytest.mark.asyncio
async def test_a_ped_upload_that_replaces_a_family_deletes_its_variants_last(events) -> None:
    result = await ped_service.upload_ped_data(
        _Transaction(events),  # type: ignore[arg-type]
        _ped(_TRIO),
        True,
        _ADMIN,  # type: ignore[arg-type]
        "project-1",
    )

    assert [family.family_id for family in result.families] == ["FAM1"]
    # Once the new family is written, before the commit (before: before the checks).
    assert events == [
        ("lock", "uuid-old"),
        ("audit: phenotypes removed", "uuid-old", "replaced"),
        ("audit: family deleted", "uuid-old", "pedigree_replaced"),
        ("delete family", "uuid-old"),
        ("create family", "FAM1"),
        ("delete small variants", "GRCh38", "uuid-old"),
        ("delete structural variants", "GRCh38", "uuid-old"),
        ("commit",),
    ]


def _manual_trio() -> ManualPedFamilyCreate:
    return ManualPedFamilyCreate(
        family_id="FAM1",
        project_id="project-1",
        members=[
            ManualPedMemberCreate(sample_id="FATHER1", sex="male"),
            ManualPedMemberCreate(sample_id="MOTHER1", sex="female"),
            ManualPedMemberCreate(sample_id="CHILD1", father_id="FATHER1", mother_id="MOTHER1", is_proband=True),
        ],
    )


@pytest.mark.asyncio
async def test_a_manual_family_refused_after_the_replacement_leaves_the_familys_variants(events) -> None:
    with pytest.raises(HTTPException) as refused:
        await ped_service.create_manual_family_data(
            _Transaction(events, taken_sample_ids=("FATHER1",)),  # type: ignore[arg-type]
            _manual_trio(),
            True,
            _ADMIN,  # type: ignore[arg-type]
        )

    assert refused.value.status_code == 409
    assert events == [
        ("lock", "uuid-old"),
        ("audit: phenotypes removed", "uuid-old", "replaced"),
        ("audit: family deleted", "uuid-old", "pedigree_replaced"),
        ("delete family", "uuid-old"),
    ]


@pytest.mark.asyncio
async def test_a_manual_family_that_replaces_a_family_deletes_its_variants_last(events) -> None:
    await ped_service.create_manual_family_data(
        _Transaction(events),  # type: ignore[arg-type]
        _manual_trio(),
        True,
        _ADMIN,  # type: ignore[arg-type]
    )

    assert events[-3:] == [
        ("delete small variants", "GRCh38", "uuid-old"),
        ("delete structural variants", "GRCh38", "uuid-old"),
        ("commit",),
    ]
    assert events.index(("create family", "FAM1")) < events.index(("delete small variants", "GRCh38", "uuid-old"))
