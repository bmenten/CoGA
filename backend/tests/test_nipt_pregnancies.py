"""Several pregnancies of one woman in one family (the owner's decisions of 2026-11-21): each
pregnancy's NIPT is an analysis of its own, keyed by its type and the pregnancy its package names
(``pregnancy_id``), whose data, readings, reviews and sign-out are its own. A monogenic and a
conventional NIPT whose packages name the same pregnancy are one pregnancy. All values are
synthetic."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import family_package_registration as registration
from backend.app.services.analysis_scope import analysis_scope
from backend.app.services.coga_package import CogaPackage, package_issues
from backend.app.services.coverage_read_depth import coverage_track_content, coverage_track_label, read_depth_source
from backend.app.services.family_analysis_types import (
    analysis_key,
    analysis_type_of,
    family_analyses,
    family_analyses_of_type,
    family_analysis_types,
    pregnancy_id_problem,
    pregnancy_of,
)
from backend.app.services.family_package_common import PackageManifest
from backend.app.services.family_package_records import (
    analysis_data_words,
    analysis_label,
    package_record_key,
    package_sources,
    pregnancy_source,
    source_base,
    source_collisions,
)
from backend.app.services.family_package_validation import manifest_pregnancy_issues
from backend.app.services.signout_scope import NIPT_SECTION_ANALYSES, signout_scope
from backend.app.services.small_variant_callsets import callset_preference, is_direct_callset, is_nipt_callset

MNIPT1, MNIPT2, CNIPT1 = "monogenic_nipt:ZW1", "monogenic_nipt:ZW2", "conventional_nipt:ZW1"


def test_an_analysis_of_a_pregnancy_is_keyed_by_its_type_and_its_pregnancy() -> None:
    assert analysis_key("monogenic_nipt", "ZW1") == MNIPT1
    # Another analysis is its type alone, whatever it is given.
    assert analysis_key("short_read_wgs", "ZW1") == "short_read_wgs"
    assert (analysis_type_of(MNIPT1), pregnancy_of(MNIPT1)) == ("monogenic_nipt", "ZW1")
    assert (analysis_type_of("pgt"), pregnancy_of("pgt")) == ("pgt", None)
    # A pregnancy ID may hold the separator: the key splits at the first.
    assert pregnancy_of("conventional_nipt:ZW:2") == "ZW:2"
    assert pregnancy_of("short_read_wgs:ZW1") is None
    assert package_record_key("monogenic_nipt", "ZW1") == MNIPT1
    assert package_record_key(None, "ZW1") == "unspecified"
    metadata = {"analyses": [CNIPT1, MNIPT2, MNIPT1, "short_read_wgs"]}
    assert family_analyses(metadata) == {CNIPT1, MNIPT1, MNIPT2, "short_read_wgs"}
    assert family_analysis_types(metadata) == {"conventional_nipt", "monogenic_nipt", "short_read_wgs"}
    assert family_analyses_of_type(metadata, "monogenic_nipt") == [MNIPT1, MNIPT2]


def test_messages_name_the_pregnancy() -> None:
    assert analysis_label(MNIPT2) == "monogenic NIPT of pregnancy ZW2"
    assert analysis_label("short_read_wgs") == "short-read WGS"
    assert analysis_label("unspecified") is None
    assert analysis_data_words(CNIPT1) == "the data of this family's conventional NIPT of pregnancy ZW1"
    assert analysis_data_words("short_read_wgs") == "this family's short-read WGS data"


@pytest.mark.parametrize(
    ("value", "words"),
    [("ZW 1", "space"), ("ZW/1", "/"), ("..", "."), ("Z" * 65, "longer than 64")],
)
def test_a_pregnancy_id_follows_the_id_rule(value: str, words: str) -> None:
    problem = pregnancy_id_problem(value)
    assert problem is not None and words in problem
    assert pregnancy_id_problem("ZW-2026.01") is None


def test_each_pregnancy_stores_its_nipt_data_under_sources_of_its_own() -> None:
    digest = hashlib.sha256(b"ZW1").hexdigest()[:8]
    assert pregnancy_source("nipt", "ZW1") == f"nipt_p{digest}"
    assert pregnancy_source("nipt", None) == "nipt"
    assert pregnancy_source("nipt", "ZW1") != pregnancy_source("nipt", "ZW2")
    assert source_base(pregnancy_source("coverage_table", "ZW2")) == "coverage_table"
    assert source_base("deepvariant") == "deepvariant"
    # The datasets a pregnancy keeps as its own: its calls (the father's among them), its coverage
    # and its ccnv output; another dataset keeps its source, which the collision check guards.
    first = package_sources(["snv", "coverage", "ccnv", "mito"], pregnancy_source("nipt", "ZW1"), "ZW1")
    second = package_sources(["snv", "coverage"], pregnancy_source("nipt", "ZW2"), "ZW2")
    assert first["small_variants"] == sorted([pregnancy_source("nipt", "ZW1"), "mito"])
    assert first["tracks"] == sorted(pregnancy_source(name, "ZW1") for name in ("web", "coverage_table", "ccnv"))
    assert first["structural_variants"] == sorted([pregnancy_source("ccnv", "ZW1"), "mito_sv"])
    # Two pregnancies share no source: the second's import replaces none of the first's data.
    records = {MNIPT1: {"sources": package_sources(["snv", "coverage"], pregnancy_source("nipt", "ZW1"), "ZW1")}}
    assert source_collisions(records, MNIPT2, second) == []
    # The monogenic and the conventional NIPT of one pregnancy share none either.
    assert source_collisions(records, CNIPT1, package_sources(["ccnv"], None, "ZW1")) == []


def test_a_pregnancys_callset_is_read_as_the_nipt_callset() -> None:
    callset = pregnancy_source("nipt", "ZW2")
    assert is_nipt_callset(callset) and is_nipt_callset("nipt")
    assert not is_direct_callset(callset)
    assert callset_preference(callset)[0] == 1
    assert not is_nipt_callset(pregnancy_source("mutect2", "ZW2"))


def test_a_pregnancys_coverage_bed_is_read_as_a_coverage_bed() -> None:
    bed = pregnancy_source("web", "ZW1")
    assert coverage_track_content(bed, contents=["read_depth"]) == "read_depth"
    assert coverage_track_content(pregnancy_source("ccnv", "ZW1")) == "log2_ratio"
    assert coverage_track_label(bed) == "coverage BED"
    assert read_depth_source([bed, "hificnv"], {"hificnv": ["depth_stored_as_read"]}, {bed: ["read_depth"]}) == bed


def _package(**fields: Any) -> CogaPackage:
    return CogaPackage.model_validate(
        {
            "schema": "coga-package/1",
            "status": "complete",
            "written_at": "2026-11-21T09:00:00Z",
            "pipeline": {"name": "nipt", "version": "1", "run_id": "RUN1"},
            "project": "NIPT",
            "assembly": "GRCh38",
            "family": {"family_id": "FAM1"},
            "files": [{"path": "a.vcf.gz", "size": 1}],
            **fields,
        }
    )


def test_a_complete_nipt_package_names_its_pregnancy_and_no_other_package_does() -> None:
    def codes(package: CogaPackage) -> list[str]:
        return [issue.code for issue in package_issues(package)]

    assert codes(_package(analysis_type="monogenic_nipt")) == ["package_pregnancy_missing"]
    assert codes(_package(analysis_type="conventional_nipt", pregnancy_id="ZW1")) == []
    assert codes(_package(analysis_type="short_read_wgs", pregnancy_id="ZW1")) == ["package_pregnancy_not_nipt"]
    # A running file may name it later.
    assert codes(_package(analysis_type="monogenic_nipt", status="running")) == []
    with pytest.raises(ValueError, match="no pregnancy ID"):
        _package(analysis_type="monogenic_nipt", pregnancy_id="ZW 1")


def test_the_import_refuses_a_nipt_manifest_without_its_pregnancy() -> None:
    def codes(**fields: Any) -> list[str]:
        return [issue.code for issue in manifest_pregnancy_issues(PackageManifest(**fields))]

    assert codes(analysis_type="monogenic_nipt") == ["pregnancy_missing"]
    assert codes(analysis_type="conventional_nipt", pregnancy_id="ZW1") == []
    assert codes(analysis_type="pgt", pregnancy_id="ZW1") == ["pregnancy_not_nipt"]
    assert codes(pregnancy_id="ZW1") == ["pregnancy_without_analysis"]
    assert codes(analysis_type="monogenic_nipt", pregnancy_id="ZW\t1") == ["pregnancy_id_invalid"]
    assert codes() == []
    manifest = PackageManifest(analysis_type="monogenic_nipt", pregnancy_id="ZW2")
    assert (manifest.analysis_key, manifest.pregnancy) == (MNIPT2, "ZW2")


def _record(sources: dict[str, list[str]], members: list[str], at: str) -> dict[str, Any]:
    return {"sources": sources, "members": members, "registered_at": at}


# Two pregnancies of one woman, the second with a new partner, and the first's conventional NIPT
# of the same plasma.
_PREGNANCIES = {
    "analyses": [CNIPT1, MNIPT1, MNIPT2],
    "package_imports": {
        MNIPT1: _record(
            package_sources(["snv", "coverage"], pregnancy_source("nipt", "ZW1"), "ZW1"),
            ["CFDNA1", "FATHER1", "FETUS1"],
            "2026-11-01T10:00:00",
        ),
        CNIPT1: _record(package_sources(["ccnv"], None, "ZW1"), ["CFDNA1", "FETUS1"], "2026-11-02T10:00:00"),
        MNIPT2: _record(
            package_sources(["snv", "coverage"], pregnancy_source("nipt", "ZW2"), "ZW2"),
            ["CFDNA2", "FATHER2", "FETUS2"],
            "2026-11-03T10:00:00",
        ),
    },
}


def test_each_pregnancy_is_read_on_its_own() -> None:
    first = analysis_scope(_PREGNANCIES, MNIPT1)
    assert first.left_out_sources("small_variants") == {pregnancy_source("nipt", "ZW2")}
    assert first.left_out_members == {"CFDNA2", "FATHER2", "FETUS2"}
    second = analysis_scope(_PREGNANCIES, MNIPT2)
    assert second.left_out_sources("small_variants") == {pregnancy_source("nipt", "ZW1")}
    # The first pregnancy's plasma and fetus are its conventional NIPT's too: still left out.
    assert second.left_out_members == {"CFDNA1", "FATHER1", "FETUS1"}
    # Two pregnancies with small variants: a reading names one.
    assert analysis_scope(_PREGNANCIES).unnamed_among == (MNIPT1, MNIPT2)


def test_each_pregnancys_sign_out_holds_its_own_section_and_data() -> None:
    first = signout_scope(_PREGNANCIES, MNIPT1)
    assert first.holds_application(NIPT_SECTION_ANALYSES)
    assert not signout_scope(_PREGNANCIES, CNIPT1).holds_application(NIPT_SECTION_ANALYSES)
    assert first.holds("small_variants", [pregnancy_source("nipt", "ZW1")])
    assert not first.holds("small_variants", [pregnancy_source("nipt", "ZW2")])
    assert not first.holds("tracks", [pregnancy_source("coverage_table", "ZW2")])
    assert first.holds_record(MNIPT1) and not first.holds_record(MNIPT2)


def _bundle(pregnancy: str, analysis_type: str, members: list[tuple[str, str, str | None]]) -> Any:
    """A NIPT package: ``members`` as (sample ID, role, assay)."""
    ped = "".join(f"FAM1\t{sample_id}\t0\t0\t0\t-9\trole={role}\n" for sample_id, role, _assay in members)
    manifest = PackageManifest(
        family_id="FAM1",
        analysis_type=analysis_type,
        pregnancy_id=pregnancy,
        samples={sample_id: {"assay": assay} for sample_id, _role, assay in members if assay},
    )
    return SimpleNamespace(manifest=manifest, ped=registration._parse_ped_text_strict(ped)[0])


def test_a_plasma_or_a_fetus_is_one_pregnancys() -> None:
    family = {"package_imports": {MNIPT1: {"members": ["CFDNA1", "FATHER1", "FETUS1"]}}}
    # A later pregnancy with its own plasma and fetus: the father may be the same person.
    later = _bundle("ZW2", "monogenic_nipt", [("CFDNA2", "mother", "nipt_cfdna"), ("FATHER1", "father", None), ("FETUS2", "fetus", None)])
    assert registration._shared_pregnancy_sample_message(family, later) is None
    # The conventional NIPT of the same pregnancy may name its plasma and fetus.
    same = _bundle("ZW1", "conventional_nipt", [("CFDNA1", "mother", "cnipt_cfdna"), ("FETUS1", "fetus", None)])
    assert registration._shared_pregnancy_sample_message(family, same) is None
    # Another pregnancy's plasma or fetus: refused, naming both.
    reused = _bundle("ZW2", "monogenic_nipt", [("CFDNA1", "mother", "nipt_cfdna"), ("FETUS1", "fetus", None)])
    refused = registration._shared_pregnancy_sample_message(family, reused)
    assert refused is not None
    assert "maternal plasma CFDNA1, which the monogenic NIPT of pregnancy ZW1 lists" in refused
    assert "fetus FETUS1" in refused and "one pregnancy's" in refused
