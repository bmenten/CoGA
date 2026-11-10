"""The package file ``coga-package.json`` (format ``coga-package/1``, docs/coga-package.md): the one
file a pipeline writes that describes its package, in place of a PED, a manifest and a ready marker.

The examples under docs/schemas/examples are what a pipeline owner copies, and the JSON Schema
under docs/schemas is what a pipeline checks its file against: both are held to the model CoGA
reads the file with.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any

import pytest

from backend.app.services.coga_package import (
    IDENTIFIER_PATTERN,
    PACKAGE_FILE_NAME,
    PACKAGE_MAX_BYTES,
    PACKAGE_PATH_PATTERN,
    AnalysisType,
    DatasetName,
    InheritanceModel,
    Role,
    _identifier,
    _package_path,
    coga_package_json_schema,
    coga_package_json_schema_text,
    literal_values,
    read_coga_package,
)
from backend.app.services.family_package_common import SUPPORTED_DATASETS
from backend.app.services.family_package_manifest import _INHERITANCE_MODELS, _PED_ROLE_VALUES
from backend.app.services.import_ready_marker import ANALYSIS_TYPES

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = ROOT / "docs" / "schemas" / "coga-package-1.schema.json"
EXAMPLES = sorted((ROOT / "docs" / "schemas" / "examples").glob("*.json"))
SHA = "0" * 64


def _complete(**changes: Any) -> dict[str, Any]:
    package: dict[str, Any] = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T15:00:00+01:00",
        "pipeline": {"name": "pipeline", "version": "1.0", "run_id": "RUN1"},
        "project": "PGT",
        "assembly": "GRCh38",
        "analysis_type": "pgt_m",
        "family": {"family_id": "FAM001"},
        "members": [
            {"sample_id": "FATHER1", "sex": "male", "role": "father"},
            {"sample_id": "MOTHER", "sex": "female", "role": "mother"},
            {"sample_id": "EMB1", "sex": "female", "father": "FATHER1", "mother": "MOTHER", "role": "embryo"},
        ],
        "files": [{"path": "cram/EMB1.cram", "size": 10, "sha256": SHA}],
    }
    package.update(changes)
    return package


def _read(payload: dict[str, Any]) -> Any:
    return read_coga_package(json.dumps(payload).encode())


def _codes(payload: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(issue.code, issue.location) for issue in _read(payload).issues]


# ---------------------------------------------------------------------------
# The examples and the JSON Schema a pipeline reads
# ---------------------------------------------------------------------------


def test_there_is_an_example_of_each_kind_of_package() -> None:
    assert [path.name for path in EXAMPLES] == [
        "long-read-couple.json",
        "minimum.json",
        "monogenic-nipt.json",
        "pgt-m.json",
        "running.json",
    ]


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_example_is_a_package_without_issues(example: Path) -> None:
    read = read_coga_package(example.read_bytes())
    assert read.package is not None
    assert read.issues == []


def _named_paths(value: Any) -> set[str]:
    """The paths a datasets block names: every string in it but a setting's value."""
    if isinstance(value, dict):
        return set().union(
            *(_named_paths(item) for key, item in value.items() if key not in {"source_format", "exclude_filters"})
        )
    return {value} if isinstance(value, str) else set()


def test_every_file_a_complete_examples_datasets_name_is_listed_with_its_checksum() -> None:
    for example in EXAMPLES:
        payload = json.loads(example.read_text())
        if payload["status"] != "complete" or "datasets" not in payload:
            continue
        listed = {entry["path"] for entry in payload["files"]}
        named = _named_paths(payload["datasets"])
        assert named and named <= listed, (example.name, sorted(named - listed))


def test_the_committed_json_schema_is_the_models() -> None:
    assert SCHEMA_FILE.read_text(encoding="utf-8") == coga_package_json_schema_text(), (
        "Run python scripts/generate-coga-package-schema.py"
    )


def test_the_json_schema_asks_a_complete_file_where_it_belongs_and_a_checksum_per_file() -> None:
    schema = coga_package_json_schema()
    assert schema["required"] == ["schema", "status", "written_at", "pipeline"]
    assert schema["additionalProperties"] is False
    (complete,) = schema["allOf"]
    assert complete["if"]["properties"]["status"] == {"const": "complete"}
    assert complete["then"]["required"] == ["project", "assembly", "analysis_type", "family", "files"]
    assert complete["then"]["properties"]["files"] == {"type": "array", "minItems": 1}
    file_schema = schema["$defs"]["PackageFile"]
    assert [option["required"] for option in file_schema["anyOf"]] == [["sha256"], ["md5"]]
    assert all(definition["additionalProperties"] is False for definition in schema["$defs"].values())


def test_the_formats_value_lists_are_the_ones_the_import_reads() -> None:
    assert literal_values(DatasetName) == SUPPORTED_DATASETS
    assert set(literal_values(AnalysisType)) == ANALYSIS_TYPES
    assert set(literal_values(Role)) == _PED_ROLE_VALUES
    assert set(literal_values(InheritanceModel)) == _INHERITANCE_MODELS


def _accepts(check: Any, value: str) -> bool:
    try:
        check(value)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    "value",
    ["PROBAND", "FAM_001", "EMB1.2", "a-b", "x~y", "", ".", "..", "a b", "a/b", "a\\b", "a\tb", "a\x00b", "a\x1bb", "a\x85b"],
)
def test_the_schemas_id_pattern_says_what_the_id_rule_says(value: str) -> None:
    assert (re.fullmatch(IDENTIFIER_PATTERN, value) is not None) == _accepts(_identifier, value)


@pytest.mark.parametrize(
    "value",
    [
        "snv/FAM001.vcf.gz",
        "a.b/c",
        "x..y/z",
        "a/~b",
        ".hidden/file",
        "",
        "/abs/file",
        "~/file",
        "~user/file",
        "a//b",
        "a/./b",
        "./a",
        "../a",
        "a/..",
        "a/",
        "a\\b",
        "a\nb",
        "a\x7fb",
    ],
)
def test_the_schemas_path_pattern_says_what_the_path_rule_says(value: str) -> None:
    assert (re.fullmatch(PACKAGE_PATH_PATTERN, value) is not None) == _accepts(_package_path, value)


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


def test_a_running_file_needs_only_its_status_its_time_and_its_run() -> None:
    read = _read(
        {
            "schema": "coga-package/1",
            "status": "running",
            "written_at": "2026-11-10T09:00:00Z",
            "pipeline": {"name": "pipeline", "version": "1.0", "run_id": "RUN1"},
        }
    )
    assert read.package is not None and read.package.status == "running"
    assert read.issues == []
    assert len(read.sha256 or "") == 64


def test_a_complete_file_must_say_where_the_package_belongs_and_list_its_files() -> None:
    payload = _complete(files=[])
    for name in ("project", "assembly", "analysis_type", "family"):
        del payload[name]
    payload.pop("members")
    assert _codes(payload) == [
        ("package_field_missing", "project"),
        ("package_field_missing", "assembly"),
        ("package_field_missing", "analysis_type"),
        ("package_field_missing", "family"),
        ("package_field_missing", "files"),
    ]
    payload["status"] = "failed"
    assert _codes(payload) == []


def test_every_file_needs_its_status_time_and_run() -> None:
    assert _codes({"schema": "coga-package/1"}) == [
        ("package_field_missing", "status"),
        ("package_field_missing", "written_at"),
        ("package_field_missing", "pipeline"),
    ]


def test_a_field_the_format_does_not_know_is_refused_not_left_unread() -> None:
    payload = _complete()
    payload["members"][1]["clinical_staus"] = "affected"
    read = _read(payload)
    assert read.package is None
    assert [(issue.code, issue.location) for issue in read.issues] == [
        ("package_field_unknown", "members[1].clinical_staus")
    ]
    assert "extra" in read.issues[0].message
    assert _codes(_complete(extra={"lims": {"anything": [1, 2]}})) == []


def test_a_value_out_of_the_formats_list_is_refused_without_quoting_it() -> None:
    payload = _complete()
    payload["members"][0]["sex"] = "M-SECRET"
    read = _read(payload)
    assert [(issue.code, issue.location) for issue in read.issues] == [("package_field_invalid", "members[0].sex")]
    assert "M-SECRET" not in read.issues[0].message


def test_a_dataset_the_import_does_not_read_is_refused() -> None:
    assert _codes(_complete(datasets={"snvs": {}})) == [("package_field_invalid", "datasets.snvs")]


def test_the_time_it_was_written_carries_its_time_zone() -> None:
    assert _codes(_complete(written_at="2026-11-10T15:00:00")) == [("package_field_invalid", "written_at")]


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b'{"schema": "coga-package/1", "status": "running", "status": "complete"}', "package_duplicate_key"),
        (b'{"schema": "coga-package/1", "extra": {"a": 1, "a": 2}}', "package_duplicate_key"),
        (b'{"schema": "coga-package/1", "extra": {"value": NaN}}', "package_not_json"),
        (b'{"schema": "coga-package/1", "extra": {"value": 1e999}}', "package_number_invalid"),
        (b'{"schema": "coga-package/1", "notes": "a\\u0000b"}', "package_unstorable_text"),
        (b'{"schema": "coga-package/1", "notes": "\\ud800"}', "package_unstorable_text"),
        (b'{"schema": "coga-ready/1"}', "package_schema_unknown"),
        (b"[]", "package_not_json"),
        (b"{", "package_not_json"),
        (b"\xff\xfe", "package_not_json"),
    ],
)
def test_a_file_that_is_no_package_of_the_format_is_not_read(raw: bytes, code: str) -> None:
    read = read_coga_package(raw)
    assert read.package is None
    assert [issue.code for issue in read.issues] == [code]


def test_a_file_too_large_is_not_parsed() -> None:
    read = read_coga_package(b" " * (PACKAGE_MAX_BYTES + 1))
    assert read.package is None
    assert [issue.code for issue in read.issues] == ["package_too_large"]


def test_ids_and_paths_follow_the_rules() -> None:
    payload = _complete(files=[{"path": "../x.vcf.gz", "size": 1, "sha256": SHA}])
    payload["members"][0]["sample_id"] = "FATHER 1"
    payload["family"]["family_id"] = ".."
    assert _codes(payload) == [
        ("package_field_invalid", "family.family_id"),
        ("package_field_invalid", "members[0].sample_id"),
        ("package_field_invalid", "files[0].path"),
    ]


# ---------------------------------------------------------------------------
# What the schema cannot say
# ---------------------------------------------------------------------------


def test_a_file_needs_a_checksum_is_listed_once_and_never_lists_the_package_file() -> None:
    assert _codes(_complete(files=[{"path": "a.vcf.gz", "size": 1}])) == [("package_field_invalid", "files[0]")]
    assert _codes(_complete(files=[{"path": "a.vcf.gz", "size": 1, "md5": "0" * 32}])) == []
    twice = [{"path": "a.vcf.gz", "size": 1, "sha256": SHA}] * 2
    itself = [{"path": PACKAGE_FILE_NAME, "size": 1, "sha256": SHA}]
    assert _codes(_complete(files=twice)) == [("package_file_listed_twice", "files")]
    assert _codes(_complete(files=itself)) == [("package_lists_itself", "files")]


def test_every_member_named_is_a_member_of_the_package() -> None:
    payload = _complete(
        couples=[{"partners": ["FATHER1", "MOTHER2"]}, {"partners": ["MOTHER", "MOTHER"]}],
        datasets={"apcad": {"per_sample": {"EMB9": {"file": "apcad/EMB9.csv"}}}, "snv": {"family_vcf": "snv/f.vcf.gz"}},
    )
    payload["family"]["pgt"] = {"affected_parents": ["MOTHER"], "indexes": ["INDEX1"]}
    payload["members"][2]["father"] = "FATHER2"
    payload["members"][2]["related_to"] = ["EMB1", "GRANDMA"]
    payload["members"].append({"sample_id": "MOTHER", "sex": "female"})
    assert _codes(payload) == [
        ("package_member_twice", "members"),
        ("package_member_unknown", "members[2].father"),
        ("package_member_own_relative", "members[2].related_to"),
        ("package_member_unknown", "members[2].related_to"),
        ("package_member_unknown", "couples[0].partners"),
        ("package_couple_invalid", "couples[1]"),
        ("package_member_unknown", "family.pgt.indexes"),
        ("package_member_unknown", "datasets.apcad.per_sample"),
    ]


def test_a_parent_is_another_member_of_the_parents_sex() -> None:
    payload = _complete()
    payload["members"][2]["mother"] = "FATHER1"
    payload["members"][2]["father"] = "EMB1"
    assert _codes(payload) == [
        ("package_member_own_parent", "members[2].father"),
        ("package_parent_sex", "members[2].mother"),
    ]
    payload = _complete()
    payload["members"][0]["sex"] = "unknown"
    assert _codes(payload) == []
    payload["members"][1]["sex"] = "male"
    assert _codes(payload) == [("package_parent_sex", "members[2].mother")]


def test_a_couple_needs_its_members_named() -> None:
    payload = _complete(couples=[{"partners": ["FATHER1", "MOTHER"]}])
    del payload["members"]
    assert _codes(payload) == [("package_members_missing", "couples")]


def test_the_datasets_the_pipeline_computed_with_the_pedigree_are_datasets_of_the_package() -> None:
    payload = _complete(datasets={"apcad": {}}, pipeline_used_pedigree=["apcad", "haplotypes"])
    assert _codes(payload) == [("package_dataset_unknown", "pipeline_used_pedigree")]
    # Without datasets, CoGA proposes them; the list is checked against those.
    del payload["datasets"]
    assert _codes(payload) == []


def test_the_minimum_example_leaves_the_members_and_datasets_to_coga() -> None:
    payload = json.loads((ROOT / "docs/schemas/examples/minimum.json").read_text())
    read = _read(payload)
    assert read.package is not None
    assert read.package.members is None and read.package.datasets is None
    full = json.loads((ROOT / "docs/schemas/examples/pgt-m.json").read_text())
    assert full["members"] and full["datasets"] and full["pipeline_used_pedigree"]
    assert _read(copy.deepcopy(full)).issues == []
