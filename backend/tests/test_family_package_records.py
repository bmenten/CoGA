"""A family's package records, one per analysis it was imported for (``family_package_records``),
and what an import of one analysis keeps of another's: its members, in the update check
(``family_member_differences``) and in an overwrite's pedigree (``take_package_pedigree``). A
family holds the monogenic and the conventional NIPT of one pregnancy, each from a package of its
own (the owner's decision of 2026-11-19). All values are synthetic."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import family_package_registration as registration
from backend.app.services import family_structure_service as structure
from backend.app.services.family_package_records import (
    analysis_data_words,
    family_package_folders,
    family_package_records,
    package_folder_of,
    package_record_key,
)

_RECORDS = {
    "monogenic_nipt": {"folder_path": "/imports/MNIPT/F1", "members": ["CFDNA1", "FATHER1", "FETUS1"]},
    "conventional_nipt": {"folder_path": "/imports/CNIPT/F1", "members": ["CFDNA1"]},
    "unspecified": "not a record",
}


def test_a_record_is_keyed_by_its_packages_analysis() -> None:
    assert package_record_key("conventional_nipt") == "conventional_nipt"
    assert package_record_key(" monogenic_nipt ") == "monogenic_nipt"
    for none in (None, "", "  "):
        assert package_record_key(none) == "unspecified"


def test_a_family_reads_each_of_its_records_and_their_folders() -> None:
    metadata = {"package_imports": _RECORDS}
    assert sorted(family_package_records(metadata)) == ["conventional_nipt", "monogenic_nipt"]
    assert family_package_folders(metadata) == ["/imports/CNIPT/F1", "/imports/MNIPT/F1"]
    for none in (None, {}, {"package_imports": "x"}):
        assert family_package_records(none) == {} and family_package_folders(none) == []


def test_a_samples_file_is_read_in_the_folder_of_the_analysis_that_recorded_it() -> None:
    assert package_folder_of(_RECORDS, {"path": "a.cram", "package": "monogenic_nipt"}) == "/imports/MNIPT/F1"
    assert package_folder_of(_RECORDS, {"path": "a.cram", "package": "conventional_nipt"}) == "/imports/CNIPT/F1"
    # A file that names no analysis, or one the family does not hold, is read in no package folder.
    for record in ({"path": "a.cram"}, {"package": "pgt"}, {"package": "unspecified"}, None):
        assert package_folder_of(_RECORDS, record) is None
    assert package_folder_of(None, {"package": "monogenic_nipt"}) is None


def test_a_message_names_the_analysiss_data() -> None:
    assert analysis_data_words("conventional_nipt") == "this family's conventional NIPT data"
    assert analysis_data_words("unspecified") == "this family's data"
    assert analysis_data_words(None) == "this family's data"


class _Records:
    """Answers the read of the family's package records."""

    def __init__(self, records: Any) -> None:
        self.records = records

    async def execute(self, _statement: Any, _params: Any = None) -> Any:
        row = {"package_imports": self.records}
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))


def test_an_import_keeps_the_members_of_the_familys_other_analyses() -> None:
    kept = asyncio.run(
        registration.members_of_other_analyses(_Records(_RECORDS), family_id="F1", analysis_type="conventional_nipt")  # type: ignore[arg-type]
    )
    assert kept == {"CFDNA1", "FATHER1", "FETUS1"}
    # Its own analysis's members are not "another's".
    kept = asyncio.run(
        registration.members_of_other_analyses(_Records(_RECORDS), family_id="F1", analysis_type="monogenic_nipt")  # type: ignore[arg-type]
    )
    assert kept == {"CFDNA1"}
    assert asyncio.run(registration.members_of_other_analyses(_Records(None), family_id="F1", analysis_type=None)) == set()  # type: ignore[arg-type]


def test_a_package_of_another_analysis_may_leave_out_the_familys_other_members(monkeypatch: pytest.MonkeyPatch) -> None:
    async def existing(*_args: Any, **_kwargs: Any) -> Any:
        return {"family_uuid": "u1"}

    async def stored(*_args: Any, **_kwargs: Any) -> str:
        return "F1\tFATHER1\t0\t0\t1\t1\nF1\tCFDNA1\t0\t0\t2\t1\nF1\tFETUS1\tFATHER1\tCFDNA1\t0\t2\n"

    monkeypatch.setattr(registration, "_fetch_existing_family", existing)
    monkeypatch.setattr(registration.ped_service, "build_pedigree_text", stored)
    ped = registration._parse_ped_text_strict("F1\tCFDNA1\t0\t0\t2\t-9\n")[0]

    differences = asyncio.run(
        registration.family_member_differences(
            _Records(_RECORDS), family_id="F1", ped=ped, analysis_type="conventional_nipt"  # type: ignore[arg-type]
        )
    )
    # The father and the fetus are the monogenic NIPT's: not missing, so an update keeps them.
    assert differences.missing == () and differences.kept_by_an_update

    # A family holding no other analysis: they are missing, as before.
    differences = asyncio.run(
        registration.family_member_differences(
            _Records(None), family_id="F1", ped=ped, analysis_type="conventional_nipt"  # type: ignore[arg-type]
        )
    )
    assert differences.missing == ("FATHER1", "FETUS1") and not differences.kept_by_an_update


def test_an_overwrite_keeps_the_other_analyses_members_active_with_their_links(monkeypatch: pytest.MonkeyPatch) -> None:
    held = {
        sample: {"sample_id": sample, "active": True}
        for sample in ("CFDNA1", "FATHER1", "FETUS1", "OLD1")
    }
    current_links = [
        {"relationship_type": "parent_child", "sample_id_a": "FATHER1", "sample_id_b": "FETUS1", "role_a": "father"},
        {"relationship_type": "parent_child", "sample_id_a": "CFDNA1", "sample_id_b": "FETUS1", "role_a": "mother"},
        {"relationship_type": "relative", "sample_id_a": "OLD1", "sample_id_b": "CFDNA1"},
    ]
    updates: list[Any] = []

    async def family(*_args: Any, **_kwargs: Any) -> Any:
        return {"id": "u1"}

    async def version(*_args: Any, **_kwargs: Any) -> int:
        return 3

    async def members(*_args: Any, **_kwargs: Any) -> Any:
        return {structure._sample_key(sample): row for sample, row in held.items()}

    async def links(*_args: Any, **_kwargs: Any) -> Any:
        return current_links

    async def update(_session: Any, **kwargs: Any) -> Any:
        updates.append(kwargs["update"])
        return None

    monkeypatch.setattr(structure, "get_accessible_family_mapping", family)
    monkeypatch.setattr(structure, "_fetch_current_structure_version", version)
    monkeypatch.setattr(structure, "_fetch_family_member_rows", members)
    monkeypatch.setattr(structure, "_fetch_current_relationship_rows", links)
    monkeypatch.setattr(structure, "update_family_structure_for_admin", update)

    asyncio.run(
        structure.take_package_pedigree(
            None,  # type: ignore[arg-type]
            family_id="F1",
            members=[{"sample_id": "CFDNA1", "sex": "female", "role": "mother"}],
            relationships=[],
            user=SimpleNamespace(id="u", username="admin"),  # type: ignore[arg-type]
            replace=True,
            kept={"CFDNA1", "FATHER1", "FETUS1"},
        )
    )

    (taken,) = updates
    # Only the member neither package lists is made inactive.
    assert taken.remove_members == ["OLD1"]
    # The links between the kept members stay; one to a removed member does not.
    assert sorted((link.parent, link.child) for link in taken.relationships.parent_child) == [
        ("CFDNA1", "FETUS1"),
        ("FATHER1", "FETUS1"),
    ]
    assert taken.relationships.relatives == []
