"""The manifest the import reads for a package file ``coga-package.json``.

The file's members are the pedigree: each becomes a row of ``family.add_members`` and no PED is
read; carrier fields, HPO terms and assays land where a manifest keeps them. What the file leaves
out (its members, its datasets) is taken from Discover's draft of the folder, and what it gives
always wins. A member without a role gets one from the pedigree, as a PED row does. The whole
chain is checked on a synthetic copgtm package: the import watcher's check of a full package file
reads the members from the file, never from the folder's PED. The reference genome the run used
(its FASTA) becomes the Reference assembly of the family's annotation manifest, beside a VCF
header's ``##reference``.

All samples and values here are synthetic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildRequest
from backend.app.services import annotation_manifest_service
from backend.app.services import family_package_registration as registration
from backend.app.services import import_watcher as watcher
from backend.app.services.annotation_manifest_service import _refresh_modules
from backend.app.services.coga_package import PACKAGE_FILE_NAME, CogaPackage
from backend.app.services.coga_package_manifest import package_manifest, proposed_package, proposed_sections
from backend.app.services.family_package_common import PackageManifest
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_manifest import _ped_members_for_import
from backend.app.services.family_package_validation import load_validated_family_package
from backend.tests.test_family_package_pgt import EMBRYOS, FAMILY, FATHER, INDEX, MOTHER, _write_copgtm_package

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs" / "schemas" / "examples"


def _example(name: str) -> CogaPackage:
    return CogaPackage.model_validate(json.loads((EXAMPLES / name).read_text()))


def _package(**fields: Any) -> CogaPackage:
    body: dict[str, Any] = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T09:00:00Z",
        "pipeline": {"name": "p", "version": "1", "run_id": "RUN1"},
        "project": "WGS",
        "assembly": "GRCh38",
        "analysis_type": "long_read_wgs",
        "family": {"family_id": "FAM001"},
        "files": [{"path": "snv/FAM001.vcf.gz", "size": 1, "sha256": "0" * 64}],
    }
    body.update(fields)
    return CogaPackage.model_validate(body)


# --- the translation -------------------------------------------------------------------------


def test_a_full_pgt_package_file_is_the_whole_manifest() -> None:
    package = _example("pgt-m.json")

    manifest = package_manifest(package, None)

    assert proposed_sections(package) == []
    assert "ped" not in manifest
    assert (manifest["family_id"], manifest["analysis_type"], manifest["roi"]) == ("FAM001", "pgt", "CFTR")
    assert manifest["metadata"] == {
        "pgt": {"inheritance_model": "AR", "affected_parents": ["MOTHER"], "indexes": ["INDEX1"]}
    }
    assert manifest["family"]["add_members"] == [
        {"sample_id": "FATHER1", "sex": "male", "role": "father"},
        {"sample_id": "MOTHER", "sex": "female", "role": "mother"},
        {"sample_id": "EMB1", "sex": "male", "role": "embryo", "father": "FATHER1", "mother": "MOTHER"},
        {"sample_id": "EMB2", "sex": "female", "role": "embryo", "father": "FATHER1", "mother": "MOTHER"},
        {"sample_id": "INDEX1", "sex": "female", "role": "relative"},
    ]
    assert manifest["family"]["relationships"] == {
        "couples": [{"partners": ["FATHER1", "MOTHER"], "context": "reproductive"}],
        "relatives": [{"member": "INDEX1", "related_to": ["MOTHER"]}],
    }
    assert manifest["datasets"] == json.loads((EXAMPLES / "pgt-m.json").read_text())["datasets"]


def test_a_members_assay_carrier_fields_and_hpo_terms_land_where_a_manifest_keeps_them() -> None:
    package = _package(
        members=[
            {"sample_id": "FATHER1", "sex": "male", "carrier_status": "carrier", "carrier_type": "obligate"},
            {"sample_id": "CFDNA1", "sex": "female", "assay": "nipt_cfdna", "assay_panel": "PANEL1"},
            {
                "sample_id": "PROBAND",
                "sex": "male",
                "father": "FATHER1",
                "mother": "CFDNA1",
                "clinical_status": "affected",
                "hpo": {"present": ["HP:0001250"], "absent": ["HP:0004322"]},
            },
            {"sample_id": "SIB1", "sex": "female", "hpo": {}},
        ],
        couples=[{"partners": ["FATHER1", "CFDNA1"]}],
        datasets={},
    )

    manifest = package_manifest(package, None)

    assert manifest["family"]["members"] == {"FATHER1": {"carrier_status": "carrier", "carrier_type": "obligate"}}
    assert manifest["family"]["add_members"][2] == {
        "sample_id": "PROBAND",
        "sex": "male",
        "clinical_status": "affected",
        "father": "FATHER1",
        "mother": "CFDNA1",
    }
    assert manifest["family"]["relationships"] == {"couples": [{"partners": ["FATHER1", "CFDNA1"]}]}
    assert manifest["samples"] == {"CFDNA1": {"assay": "nipt_cfdna", "assay_panel": "PANEL1"}}
    assert manifest["individuals"] == {"PROBAND": {"hpo": {"present": ["HP:0001250"], "absent": ["HP:0004322"]}}}
    assert manifest["datasets"] == {} and "roi" not in manifest and "metadata" not in manifest


def test_what_a_minimal_file_leaves_out_is_taken_from_the_draft_and_what_it_gives_wins() -> None:
    package = _package(family={"family_id": "FAM001", "pgt": {"inheritance_model": "AD"}})
    draft = {
        "schema_version": 1,
        "family_id": "FOLDER_NAME",
        "ped": "FAM001.ped",
        "roi": "chr7:1000-2000",
        "metadata": {"pgt": {"inheritance_model": "AR", "affected_parents": ["MOTHER"]}, "other": 1},
        "family": {"members": {"EMB1": {"role": "embryo"}}},
        "samples": {"PROBAND": {}},
        "individuals": None,
        "datasets": {"snv": {"family_vcf": "snv/FAM001.vcf.gz"}},
    }

    manifest = package_manifest(package, draft)

    assert proposed_sections(package) == ["members", "datasets"]
    assert manifest == {
        "schema_version": 1,
        "family_id": "FAM001",
        "analysis_type": "long_read_wgs",
        "roi": "chr7:1000-2000",
        "metadata": {"pgt": {"inheritance_model": "AD", "affected_parents": ["MOTHER"]}, "other": 1},
        "ped": "FAM001.ped",
        "family": {"members": {"EMB1": {"role": "embryo"}}},
        "samples": {"PROBAND": {}},
        "datasets": {"snv": {"family_vcf": "snv/FAM001.vcf.gz"}},
    }


def test_a_file_with_members_but_no_datasets_takes_only_the_datasets_from_the_draft() -> None:
    package = _package(members=[{"sample_id": "PROBAND", "sex": "male"}])
    draft = {"ped": "FAM001.ped", "family": {"members": {}}, "datasets": {"snv": {"family_vcf": "a.vcf.gz"}}}

    manifest = package_manifest(package, draft)

    assert proposed_sections(package) == ["datasets"]
    assert "ped" not in manifest
    assert manifest["family"] == {"add_members": [{"sample_id": "PROBAND", "sex": "male"}]}
    assert manifest["datasets"] == {"snv": {"family_vcf": "a.vcf.gz"}}
    assert package_manifest(package, None)["datasets"] == {}


def test_without_a_family_the_drafts_family_id_is_kept() -> None:
    package = _package(status="failed", family=None)
    assert package_manifest(package, {"family_id": "FOLDER"})["family_id"] == "FOLDER"
    assert "family_id" not in package_manifest(package, None)


def test_what_discover_proposes_is_written_into_the_package() -> None:
    package = _package(family={"family_id": "FAM001", "pgt": {"indexes": ["INDEX1"]}})
    draft = {
        "roi": "chr7:1000-2000",
        "metadata": {"pgt": {"inheritance_model": "AR", "indexes": ["OTHER"], "affected_parents": ["MOTHER"]}},
        "samples": {"MOTHER": {"assay": "nipt_cfdna", "assay_panel": "PANEL1"}, "FATHER1": "not an object"},
        "individuals": {"PROBAND": {"hpo": {"present": ["HP:0001250"]}}},
        "family": {
            "relationships": {
                "couples": [{"partners": ["FATHER1", "MOTHER"], "context": "reproductive"}, "not a couple"],
                "relatives": [{"member": "INDEX1", "related_to": ["MOTHER"]}, {"related_to": ["X"]}],
            }
        },
        "datasets": {"snv": {"family_vcf": "a.vcf.gz"}, "pcf": {"enabled": False}, "qc": "not an entry"},
    }
    members = [
        {"sample_id": "FATHER1", "sex": "male", "role": "father", "clinical_status": "unknown", "carrier_status": "carrier", "carrier_type": "obligate"},
        {"sample_id": "MOTHER", "sex": "female", "role": "mother", "clinical_status": "unaffected", "carrier_status": "unknown"},
        {"sample_id": "PROBAND", "sex": "und", "father_id": "FATHER1", "mother_id": "MOTHER", "role": "proband", "clinical_status": "affected"},
        {"sample_id": "INDEX1", "sex": "female", "role": "relative"},
    ]

    completed = proposed_package(package, draft, members)

    document = completed.model_dump(mode="json", exclude_none=True)
    assert document["family"] == {
        "family_id": "FAM001",
        "roi": "chr7:1000-2000",
        # What the file gives (the index) wins; what it leaves out is the draft's.
        "pgt": {"inheritance_model": "AR", "affected_parents": ["MOTHER"], "indexes": ["INDEX1"]},
    }
    assert document["members"] == [
        {"sample_id": "FATHER1", "sex": "male", "role": "father", "carrier_status": "carrier", "carrier_type": "obligate", "related_to": []},
        {"sample_id": "MOTHER", "sex": "female", "role": "mother", "clinical_status": "unaffected", "assay": "nipt_cfdna", "assay_panel": "PANEL1", "related_to": []},
        {
            "sample_id": "PROBAND",
            "sex": "unknown",
            "father": "FATHER1",
            "mother": "MOTHER",
            "role": "proband",
            "clinical_status": "affected",
            "hpo": {"present": ["HP:0001250"], "absent": []},
            "related_to": [],
        },
        {"sample_id": "INDEX1", "sex": "female", "role": "relative", "related_to": ["MOTHER"]},
    ]
    assert document["couples"] == [{"partners": ["FATHER1", "MOTHER"], "context": "reproductive"}]
    assert document["datasets"] == {"snv": {"family_vcf": "a.vcf.gz"}}


def test_an_analysis_discover_names_is_taken_where_the_package_names_none() -> None:
    package = _package(analysis_type=None, family={"family_id": "FAM001"})
    assert proposed_package(package, {"analysis_type": "monogenic_nipt"}, []).analysis_type == "monogenic_nipt"
    named = _package(analysis_type="long_read_wgs", family={"family_id": "FAM001"})
    assert proposed_package(named, {"analysis_type": "monogenic_nipt"}, []).analysis_type == "long_read_wgs"


def test_a_file_that_gives_its_members_and_datasets_keeps_them() -> None:
    package = _package(
        members=[{"sample_id": "PROBAND", "sex": "male"}], datasets={"snv": {"family_vcf": "b.vcf.gz"}}, couples=[]
    )
    completed = proposed_package(package, {"roi": None, "datasets": {"snv": {"family_vcf": "a.vcf.gz"}}}, [])
    assert completed.members == package.members and completed.datasets == package.datasets


# --- the reference genome --------------------------------------------------------------------

FAMILY_UUID = "00000000-0000-0000-0000-00000000f001"
ASSEMBLY_ID = "00000000-0000-0000-0000-0000000000a1"


class _CommitSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _recorded_reference(monkeypatch: pytest.MonkeyPatch, package: CogaPackage) -> tuple[list[dict[str, Any]], int]:
    """What the import's registration records of the package's reference: the merges into the
    family's annotation manifest, and its commits."""
    merged: list[dict[str, Any]] = []

    async def merge(_session: Any, **kwargs: Any) -> None:
        merged.append(kwargs)

    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", merge)
    session = _CommitSession()
    bundle = SimpleNamespace(manifest=PackageManifest.model_validate(package_manifest(package, None)))
    context = SimpleNamespace(family_uuid=FAMILY_UUID, assembly_id=ASSEMBLY_ID)
    asyncio.run(registration._record_package_reference(session, bundle=bundle, context=context))  # type: ignore[arg-type]
    return merged, session.commits


@pytest.mark.parametrize(
    ("given", "recorded"),
    [
        ("GRCh38_full_analysis_set_plus_decoy_hla.fa", "GRCh38_full_analysis_set_plus_decoy_hla.fa"),
        # Its FASTA's name, as a VCF's ##reference is read: without the path before it.
        ("/refs/GRCh38_no_alt_analysis_set.fasta", "GRCh38_no_alt_analysis_set.fasta"),
    ],
)
def test_the_packages_reference_is_the_reference_assembly_of_the_familys_annotation_manifest(
    monkeypatch: pytest.MonkeyPatch, given: str, recorded: str
) -> None:
    package = _package(reference=given)
    assert package_manifest(package, None)["reference"] == given

    merged, commits = _recorded_reference(monkeypatch, package)

    assert merged == [
        {
            "family_uuid": FAMILY_UUID,
            "assembly_id": ASSEMBLY_ID,
            "modules": {"assembly": {"version": recorded, "detail": f"from {PACKAGE_FILE_NAME}"}},
            "modality": "package",
            "recorded_by": f"import ({PACKAGE_FILE_NAME})",
            "source": "manifest",
        }
    ]
    assert commits == 1


def test_a_package_without_a_reference_records_none(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package()
    assert "reference" not in package_manifest(package, None)
    assert _recorded_reference(monkeypatch, package) == ([], 0)


def test_the_packages_fasta_and_a_vcfs_reference_are_both_kept() -> None:
    # The package names one FASTA, the SNV VCF's ##reference another: the manifest keeps each
    # under its modality, so the record shows they differ.
    stored = _refresh_modules(
        {}, {"assembly": {"version": "GRCh38_no_alt.fa", "detail": f"from {PACKAGE_FILE_NAME}"}}, modality="package"
    )
    stored = _refresh_modules(
        stored, {"assembly": {"version": "hg38_alt_masked.fa", "detail": "from VCF ##reference"}}, modality="snv"
    )
    assert stored["assembly"]["by_modality"] == {"package": "GRCh38_no_alt.fa", "snv": "hg38_alt_masked.fa"}


# --- read by the import ----------------------------------------------------------------------


def _validated(folder: Path, package: CogaPackage) -> Any:
    validation, bundle = load_validated_family_package(folder, manifest_text=json.dumps(package_manifest(package, None)))
    assert bundle is not None, validation.errors
    return validation, bundle


def test_a_member_without_a_role_gets_one_from_the_pedigree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    folder = tmp_path / "FAM001"
    folder.mkdir()
    package = _package(
        members=[
            {"sample_id": "FATHER1", "sex": "male"},
            {"sample_id": "MOTHER", "sex": "female"},
            {"sample_id": "PROBAND", "sex": "male", "father": "FATHER1", "mother": "MOTHER", "clinical_status": "affected"},
            {"sample_id": "SIB1", "sex": "female", "father": "FATHER1", "mother": "MOTHER"},
            {"sample_id": "FETUS1", "sex": "unknown", "father": "FATHER1", "mother": "MOTHER"},
            {"sample_id": "AUNT1", "sex": "female", "role": "relative", "related_to": ["MOTHER"]},
        ],
        datasets={},
    )

    validation, bundle = _validated(folder, package)

    assert validation.sample_ids == ["FATHER1", "MOTHER", "PROBAND", "SIB1", "FETUS1", "AUNT1"]
    roles = {member["sample_id"]: member["role"] for member in _ped_members_for_import(bundle.ped)}
    assert roles == {
        "FATHER1": "father",
        "MOTHER": "mother",
        "PROBAND": "proband",
        "SIB1": "sibling",
        "FETUS1": "embryo",
        "AUNT1": "relative",
    }


class _NoRows:
    async def __aenter__(self) -> "_NoRows":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, _statement: Any, _params: Any = None) -> Any:
        return SimpleNamespace(
            first=lambda: None, all=lambda: [], mappings=lambda: SimpleNamespace(first=lambda: None, all=lambda: [])
        )


def _files(folder: Path) -> list[dict[str, Any]]:
    return [
        {"path": path.relative_to(folder).as_posix(), "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(folder.rglob("*"))
        if path.is_file() and path.name != PACKAGE_FILE_NAME
    ]


@pytest.mark.asyncio
async def test_the_watcher_imports_a_full_copgtm_package_with_the_files_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    folder = _write_copgtm_package(tmp_path / "FAMPGT")
    draft = yaml.safe_load(
        discover_family_package_manifest(
            FamilyPackageManifestBuildRequest(folder_path=str(folder), family_id=FAMILY)
        ).manifest_yaml
    )
    datasets = {name: entry for name, entry in draft["datasets"].items() if entry.get("enabled", True)}
    assert {"apcad", "qdnaseq", "alignments", "qc"} <= set(datasets)
    members = [
        {"sample_id": FATHER, "sex": "male", "role": "father"},
        {"sample_id": MOTHER, "sex": "female", "role": "mother"},
        *({"sample_id": embryo, "sex": "male" if embryo == "EMB1" else "female", "father": FATHER, "mother": MOTHER, "role": "embryo"} for embryo in EMBRYOS),
        {"sample_id": INDEX, "sex": "female", "role": "relative", "related_to": [MOTHER]},
    ]
    body = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T15:00:00+01:00",
        "pipeline": {"name": "nf-cmgg/copgtm", "version": "1.1.0", "run_id": "RUN1"},
        "project": "PGT",
        "assembly": "GRCh38",
        "analysis_type": "pgt",
        "family": {"family_id": FAMILY, "roi": "chr7:1000-2000", "pgt": {"inheritance_model": "AR", "affected_parents": [MOTHER], "indexes": [INDEX]}},
        "members": members,
        "couples": [{"partners": [FATHER, MOTHER], "context": "reproductive"}],
        "datasets": datasets,
        "pipeline_used_pedigree": [name for name in ("apcad", "haplotypes") if name in datasets],
        "files": _files(folder),
        "qc": {"status": "pass"},
    }
    (folder / PACKAGE_FILE_NAME).write_text(json.dumps(body), encoding="utf-8")

    assessment = await watcher.assess_delivery(lambda: _NoRows(), folder)

    # The only issue: CoGA has no project PGT (the session answers no rows).
    assert [issue.code for issue in assessment.issues] == ["package_project_unknown"]
    assert not any(issue.code.endswith("_proposed") for issue in assessment.warnings)
    # The folder's PED holds the members the package file names: no difference.
    assert "package_ped_differs" not in {issue.code for issue in assessment.warnings}
    assert (assessment.run_id, assessment.family_id, assessment.marker) == ("RUN1", FAMILY, body)
    manifest = json.loads(assessment.manifest_text or "")
    assert "ped" not in manifest
    assert sorted(assessment.validation["sample_ids"]) == sorted([FATHER, MOTHER, *EMBRYOS, INDEX])

    # A PED that says another father of an embryo is told, but the file's members are read.
    ped = folder / f"{FAMILY}.ped"
    ped.write_text(ped.read_text().replace(f"EMB2\t{FATHER}", "EMB2\tOTHER1"), encoding="utf-8")
    body["files"] = _files(folder)
    (folder / PACKAGE_FILE_NAME).write_text(json.dumps(body), encoding="utf-8")

    assessment = await watcher.assess_delivery(lambda: _NoRows(), folder)

    warning = next(issue for issue in assessment.warnings if issue.code == "package_ped_differs")
    assert "of EMB2:" in warning.message
    assert [issue.code for issue in assessment.issues] == ["package_project_unknown"]


@pytest.mark.asyncio
async def test_a_minimal_file_has_its_members_and_datasets_proposed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    folder = _write_copgtm_package(tmp_path / "FAMPGT")
    body = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T15:00:00+01:00",
        "pipeline": {"name": "nf-cmgg/copgtm", "version": "1.1.0", "run_id": "RUN1"},
        "project": "PGT",
        "assembly": "GRCh38",
        "analysis_type": "pgt",
        # Discover drafts for the family the file names, not for the folder's name.
        "family": {"family_id": FAMILY},
        "files": _files(folder),
    }
    (folder / PACKAGE_FILE_NAME).write_text(json.dumps(body), encoding="utf-8")

    assessment = await watcher.assess_delivery(lambda: _NoRows(), folder)

    codes = [issue.code for issue in assessment.warnings]
    assert "package_members_proposed" in codes and "package_datasets_proposed" in codes
    assert [issue.code for issue in assessment.issues] == ["package_project_unknown"]
    # Discover's draft is written into the package: the folder's PED as members (the embryos'
    # role, the index its related_to column links to the mother), the datasets and the PGT
    # details, so they can be corrected like the pipeline's.
    package = assessment.package or {}
    roles = {member["sample_id"]: member.get("role") for member in package["members"]}
    assert roles == {FATHER: "father", MOTHER: "mother", "EMB1": "embryo", "EMB2": "embryo", INDEX: "relative"}
    index = next(member for member in package["members"] if member["sample_id"] == INDEX)
    assert index["related_to"] == [MOTHER]
    assert {"apcad", "qdnaseq", "alignments", "qc"} <= set(package["datasets"])
    assert package["family"]["pgt"]["affected_parents"] == [MOTHER]
    assert (assessment.package_check or {})["proposed"] == ["members", "datasets"]
    manifest = json.loads(assessment.manifest_text or "")
    assert "ped" not in manifest
    assert manifest["analysis_type"] == "pgt"
    assert INDEX in assessment.validation["sample_ids"]
