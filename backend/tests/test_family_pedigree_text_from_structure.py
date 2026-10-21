"""The PED text the family page draws is its stored structure's (SAFE-8).

The family response carries its active members (sex, clinical status) and its relationships,
and a PED text (``pedigree``) that the family page, the NIPT page and the Sample QC page draw
the pedigree from. That text was the one stored on the family by its last import or Family
Builder save, and could disagree with the structure:

* a package import of an existing family replaced the stored text but not the stored
  relationships or sexes (#893): the drawing showed the package's pedigree;
* a Family Builder request whose parent ID was spaces alone stored a row of five fields
  (the relationship was not made), which shifted the drawing's columns.

Now the text is written from the response's own members and parent-child relationships, by the
rule the PED download uses (``pedigree_text``), whatever the stored text says. Only the stored
text's order of the members is kept, which the drawing places siblings and couples by; a member
it does not list follows, by role and sample ID.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.schemas import FamilyMemberOut, FamilyRelationshipOut
from backend.app.services import metadata_service
from backend.app.services.pedigree_text import PedMember, pedigree_text

FAMILY = "FAM_SAFE8"


def _member(sample_id: str, role: str, sex: str, status: str = "unknown", *, active: bool = True) -> FamilyMemberOut:
    return FamilyMemberOut(
        sample_id=sample_id,
        role=role,  # type: ignore[arg-type]
        affected=status == "affected",
        sex=sex,  # type: ignore[arg-type]
        clinical_status=status,  # type: ignore[arg-type]
        active=active,
    )


def _parent(parent: str, role: str, child: str) -> FamilyRelationshipOut:
    return FamilyRelationshipOut(
        id=f"rel-{parent}-{child}",
        relationship_type="parent_child",
        sample_id_a=parent,
        sample_id_b=child,
        role_a=role,
        role_b="child",
    )


def _served(stored: str | None, members: list[FamilyMemberOut], relationships: list[FamilyRelationshipOut]) -> str | None:
    mapping = {"id": "family-uuid", "family_id": FAMILY, "created_at": datetime(2026, 10, 21, tzinfo=timezone.utc), "pedigree": stored}
    return metadata_service._family_out_from_mapping(mapping, members, [], relationships).pedigree


TRIO = [
    _member("CHILD", "proband", "female", "affected"),
    _member("DAD", "father", "male", "unaffected"),
    _member("MUM", "mother", "female", "unaffected"),
]
PARENTS = [_parent("DAD", "father", "CHILD"), _parent("MUM", "mother", "CHILD")]


def test_the_family_page_draws_the_stored_structure_not_the_stored_text() -> None:
    # A re-import's PED said the child's parents were unknown and the father a woman; the
    # stored structure kept the trio.
    stale = f"{FAMILY} CHILD 0 0 2 2\n{FAMILY} DAD 0 0 2 1\n{FAMILY} MUM 0 0 2 1\n"
    # Its rows in the stored text's order, which the drawing places siblings and couples by.
    assert _served(stale, TRIO, PARENTS) == (
        f"{FAMILY} CHILD DAD MUM 2 2\n{FAMILY} DAD 0 0 1 1\n{FAMILY} MUM 0 0 2 1\n"
    )


def test_a_parent_id_of_spaces_alone_never_makes_a_row_of_five_fields() -> None:
    # The Family Builder stored "FAM CHILD    MUM 2 2" for a father ID of spaces: no relationship
    # was made, and the drawing read MUM as the father.
    stored = f"{FAMILY} CHILD    MUM 2 2\n{FAMILY} MUM 0 0 2 1\n"
    served = _served(stored, [_member("CHILD", "proband", "female", "affected"), _member("MUM", "mother", "female", "unaffected")], [_parent("MUM", "mother", "CHILD")])
    assert served == f"{FAMILY} CHILD 0 MUM 2 2\n{FAMILY} MUM 0 0 2 1\n"
    assert all(len(row.split()) == 6 for row in served.splitlines())


def test_the_rows_keep_the_stored_texts_order_and_a_member_it_lacks_follows() -> None:
    # Embryos E1, E2 and E10 stay in the stored order (not E1, E10, E2); a sibling added since
    # follows them.
    embryos = [_member(sample, "embryo", "und") for sample in ("E1", "E2", "E10")]
    stored = "".join(f"{FAMILY} {sample} 0 0 0 0\n" for sample in ("E1", "E2", "E10"))
    served = _served(stored, [_member("SIB", "sibling", "male"), *embryos], [])
    assert [row.split()[1] for row in (served or "").splitlines()] == ["E1", "E2", "E10", "SIB"]


def test_an_inactive_member_and_a_family_without_members_have_no_row() -> None:
    gone = [*TRIO, _member("AUNT", "relative", "female", active=False)]
    assert "AUNT" not in (_served(None, gone, PARENTS) or "")
    assert _served(f"{FAMILY} OLD 0 0 1 1\n", [], []) is None


def test_the_family_page_and_the_ped_download_write_one_text() -> None:
    # The PED download (ped_service.build_pedigree_text) writes its text by the same rule: the
    # parents, the proband, the siblings, then the others by sample ID; sex and status coded.
    members = [
        PedMember("SIB", "female", "unknown", "sibling"),
        PedMember("aunt", "female", "affected", "relative"),
        PedMember("Zed", "male", "unaffected", "relative"),
        PedMember("P", "und", "affected", "proband"),
        PedMember("M", "female", "unaffected", "mother"),
    ]
    assert pedigree_text(FAMILY, members, fathers={}, mothers={"P": "M", "SIB": "M"}) == (
        f"{FAMILY} M 0 0 2 1\n{FAMILY} P 0 M 0 2\n{FAMILY} SIB 0 M 2 0\n{FAMILY} aunt 0 0 2 2\n{FAMILY} Zed 0 0 1 1\n"
    )
    assert pedigree_text(FAMILY, [], fathers={}, mothers={}) == ""
