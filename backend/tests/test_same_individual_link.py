"""Two samples of one person (a same-individual link) as a package gives them: the member field
``same_individual_as`` of ``coga-package.json``, what the import's manifest makes of it
(``family.relationships.same_individual``), the relationship rows, and the checks that need no
family. The checks against the family (a sample neither in the package nor in the family) are in
``test_e2e_same_individual_link.py``; those of the structure edit in
``test_family_structure_validation.py``."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.app.services import import_delivery_revisions as revisions
from backend.app.services.coga_package import read_coga_package
from backend.app.services.coga_package_manifest import package_manifest
from backend.app.services.family_package_common import PackageManifest
from backend.app.services.family_package_manifest import (
    _manifest_relationship_issues,
    _manifest_relationships,
    _parse_ped_text_strict,
)


def _package(*members: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-20T09:00:00Z",
        "pipeline": {"name": "ccnv", "version": "1", "run_id": "RUN1"},
        "project": "NIPT",
        "assembly": "GRCh38",
        "analysis_type": "conventional_nipt",
        "family": {"family_id": "FAM001"},
        "members": list(members),
        "files": [{"path": "ccnv/PLASMA2_segments.bed.gz", "size": 1, "sha256": "0" * 64}],
    }


def _codes(payload: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    return [
        (issue.code, issue.location, issue.message)
        for issue in read_coga_package(json.dumps(payload).encode()).issues
    ]


def test_a_member_may_name_a_sample_of_another_analysis_as_the_same_person() -> None:
    # The monogenic NIPT's plasma is in that analysis's package, not this one: the import checks
    # it against the family.
    payload = _package({"sample_id": "PLASMA2", "sex": "female", "role": "mother", "same_individual_as": ["PLASMA1"]})

    read = read_coga_package(json.dumps(payload).encode())

    assert read.issues == []
    assert read.package is not None
    assert package_manifest(read.package, None)["family"]["relationships"] == {
        "same_individual": [{"samples": ["PLASMA1", "PLASMA2"]}]
    }


def test_two_members_naming_each_other_are_one_link() -> None:
    payload = _package(
        {"sample_id": "PLASMA1", "sex": "female", "same_individual_as": ["PLASMA2"]},
        {"sample_id": "PLASMA2", "sex": "female", "same_individual_as": ["PLASMA1"]},
    )

    read = read_coga_package(json.dumps(payload).encode())

    assert read.package is not None
    assert package_manifest(read.package, None)["family"]["relationships"]["same_individual"] == [
        {"samples": ["PLASMA1", "PLASMA2"]}
    ]


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ([{"sample_id": "PLASMA2", "sex": "female", "same_individual_as": ["PLASMA2"]}], "a sample is not linked to itself"),
        ([{"sample_id": "PLASMA2", "sex": "female", "same_individual_as": ["PLASMA1", "PLASMA1"]}], "PLASMA1 is named twice"),
        (
            [
                {"sample_id": "PLASMA1", "sex": "female"},
                {"sample_id": "PLASMA2", "sex": "male", "same_individual_as": ["PLASMA1"]},
            ],
            "PLASMA1 is female, PLASMA2 male",
        ),
        (
            [
                {"sample_id": "PLASMA1", "sex": "female"},
                {"sample_id": "FETUS", "sex": "unknown", "mother": "PLASMA1", "same_individual_as": ["PLASMA1"]},
            ],
            "PLASMA1 is also recorded as a parent or child of FETUS",
        ),
        (
            [
                {"sample_id": "PLASMA1", "sex": "female"},
                {"sample_id": "PLASMA2", "sex": "female", "related_to": ["PLASMA1"], "same_individual_as": ["PLASMA1"]},
            ],
            "PLASMA1 is also recorded as a relative of PLASMA2",
        ),
    ],
)
def test_a_same_individual_link_the_package_alone_shows_wrong_is_refused(
    members: list[dict[str, Any]], message: str
) -> None:
    codes = _codes(_package(*members))

    assert len(codes) == 1
    code, location, text = codes[0]
    assert code in {"package_same_individual_invalid", "package_same_individual_twice"}
    assert location == f"members[{len(members) - 1}].same_individual_as"
    assert message in text


def _ped() -> Any:
    ped, errors = _parse_ped_text_strict(
        "FAM001 FATHER 0 0 1 -9\nFAM001 PLASMA1 0 0 2 -9\nFAM001 FETUS FATHER PLASMA1 0 -9\nFAM001 PLASMA2 0 0 2 -9\n"
    )
    assert ped is not None and errors == []
    return ped


def _issues(same_individual: list[Any], **relationships: Any) -> list[tuple[str, str | None]]:
    manifest = PackageManifest.model_validate(
        {"family": {"relationships": {"same_individual": same_individual, **relationships}}}
    )
    ped = _ped()
    return [(issue.code, issue.sample_id) for issue in _manifest_relationship_issues(manifest, set(ped.sample_ids), ped)]


def test_the_manifest_makes_a_relationship_row_of_each_link() -> None:
    manifest = PackageManifest.model_validate(
        {"family": {"relationships": {"same_individual": [{"samples": ["PLASMA2", "PLASMA1"]}, {"samples": ["X"]}]}}}
    )

    assert _manifest_relationships(manifest) == [
        {
            "relationship_type": "same_individual",
            "sample_id_a": "PLASMA2",
            "sample_id_b": "PLASMA1",
            "role_a": None,
            "role_b": None,
            "source": "manifest",
            "metadata": {},
        }
    ]


@pytest.mark.parametrize(
    ("same_individual", "relationships", "expected"),
    [
        # A sample outside the package is the import's to check against the family.
        ([{"samples": ["PLASMA2", "ELSEWHERE"]}], {}, []),
        ([{"samples": ["PLASMA2", "PLASMA2"]}], {}, [("manifest_same_individual_link_invalid", "PLASMA2")]),
        (
            [{"samples": ["PLASMA2", "PLASMA1"]}, {"samples": ["PLASMA1", "PLASMA2"]}],
            {},
            [("manifest_same_individual_link_invalid", "PLASMA1")],
        ),
        # The mother of the fetus in the PED.
        ([{"samples": ["FETUS", "PLASMA1"]}], {}, [("manifest_same_individual_link_invalid", "FETUS")]),
        (
            [{"samples": ["PLASMA2", "PLASMA1"]}],
            {"relatives": [{"member": "PLASMA2", "related_to": ["PLASMA1"]}]},
            [("manifest_same_individual_link_invalid", "PLASMA2")],
        ),
    ],
)
def test_a_manifest_link_must_add_to_what_the_family_records(
    same_individual: list[Any], relationships: dict[str, Any], expected: list[tuple[str, str]]
) -> None:
    assert _issues(same_individual, **relationships) == expected


def test_the_field_is_the_laboratorys_to_correct_on_a_delivery() -> None:
    assert "same_individual_as" in revisions.LABORATORY_MEMBER_FIELDS
    assert "same_individual_as" not in revisions.PIPELINE_MEMBER_FIELDS
