from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.schemas import FamilyMemberOut, FamilyOut, FamilyRegionOfInterestOut
from backend.app.services import clickhouse_interval_tracks, nipt_service
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.genotypes import GENOTYPE_CLASSES, classify_genotype, genotype_vocabulary
from backend.app.services.nipt_analysis import NiptQualityThresholds, run_nipt_analysis
from backend.app.services.nipt_service import (
    build_nipt_observations,
    derive_father_state,
    get_family_nipt_coverage,
    get_family_nipt_variants,
    run_family_nipt_analysis,
)
from backend.tests._nipt_service_fakes import nipt_family, wire_nipt_reads


def _call(sample: str, gt: str, *, dp=None, af=None, ad=None) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=None, dp=dp, af=af or [], ad=ad or [], ps=None)


def _record(
    variant_id: str,
    *,
    chrom: str = "1",
    calls: list[SmallVariantCall],
    qual: float | None = 30.0,
    start: int = 100,
    gene_symbols: list[str] | None = None,
) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=start,
        end=start,
        ref="A",
        alt="G",
        source=None,
        rsid=None,
        filters=[],
        gene_symbols=gene_symbols or [],
        annotations=[],
        calls=calls,
        qual=qual,
    )


# --------------------------------------------------------------------------- #
# derive_father_state
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "gt,expected",
    [
        ("0/0", "hom_ref"),
        ("0/1", "het"),
        ("1/0", "het"),
        ("0|1", "het"),
        ("1/1", "hom_alt"),
        ("1|1", "hom_alt"),
        ("./.", "missing"),
        ("", "missing"),
        # Haploid calls (a male's non-PAR chrX from callers that emit ploidy 1): the one
        # allele he has is the call, so "1" is hom-alt, not het.
        ("1", "hom_alt"),
        ("0", "hom_ref"),
        (".", "missing"),
        # A half call carries an alt but is not homozygous; a half reference call is no
        # call at all, not a confident hom-ref.
        ("./1", "het"),
        ("./0", "missing"),
        ("0/.", "missing"),
    ],
)
def test_derive_father_state(gt: str, expected: str) -> None:
    assert derive_father_state(_call("father-1", gt)) == expected


# No call at all (a joint VCF without his column) is missing:
# test_nipt_pair_service.py::test_no_father_call_is_absent_in_a_per_sample_callset.


def test_derive_father_state_follows_the_shared_genotype_classes() -> None:
    # One classification of a VCF genotype everywhere: the father's state is the
    # genotype class, with no-call read as missing.
    for gt in genotype_vocabulary(*GENOTYPE_CLASSES):
        expected = classify_genotype(gt)
        assert derive_father_state(_call("father-1", gt)) == (
            "missing" if expected == "no_call" else expected
        ), gt


def _x_records(*, father_gt: str, cf_alt: int, n: int = 10) -> list[SmallVariantRecord]:
    """Non-PAR chrX sites where the father carries the alt; the cfDNA shows it or not."""
    return [
        _record(
            f"X-{3_000_000 + i * 1000}-A-G",
            chrom="X",
            start=3_000_000 + i * 1000,
            calls=[
                _call("father-1", father_gt, dp=30, ad=[0, 30]),
                _call("cfdna-1", "0/1" if cf_alt else "0/0", dp=400, ad=[400 - cf_alt, cf_alt]),
            ],
        )
        for i in range(n)
    ]


@pytest.mark.parametrize(("cf_alt", "expected"), [(20, "female"), (0, "male")])
@pytest.mark.parametrize("father_gt", ["1", "1/1"])
def test_a_haploid_paternal_x_call_sexes_the_fetus(cf_alt: int, expected: str, father_gt: str) -> None:
    # The paternal-X alleles show at FF/2 for a daughter and are absent for a son. A haploid
    # "1" is read as hom-alt, so the fetal sex is determined.
    records = _cohort_cat7_records() + _x_records(father_gt=father_gt, cf_alt=cf_alt)
    sites = build_nipt_observations(records, father_sample_id="father-1", cfdna_sample_id="cfdna-1")

    result = run_nipt_analysis(sites, NiptQualityThresholds())

    assert result.fetal_sex.informative_sites == 10
    assert result.fetal_sex.inferred == expected


# --------------------------------------------------------------------------- #
# build_nipt_observations
# --------------------------------------------------------------------------- #

def test_build_observation_paternal_transmitted_site() -> None:
    record = _record(
        "1-100-A-G",
        calls=[
            _call("father-1", "0/1", dp=50, ad=[25, 25]),
            _call("cfdna-1", "0/1", dp=120, af=[0.05], ad=[114, 6]),
        ],
        qual=42.0,
    )
    sites = build_nipt_observations(
        [record], father_sample_id="father-1", cfdna_sample_id="cfdna-1"
    )
    assert len(sites) == 1
    site = sites[0]
    assert site.variant_id == "1-100-A-G"
    assert site.is_autosomal
    assert site.father_state == "het"
    assert site.father_dp == 50
    assert site.cf_dp == 120
    assert site.cf_alt_reads == 6
    assert site.cf_vaf == pytest.approx(0.05)
    assert site.cf_present
    assert site.cf_qual == 42.0


def test_build_observation_false_negative_site_is_absent() -> None:
    record = _record(
        "1-200-A-G",
        calls=[
            _call("father-1", "1/1", dp=50, ad=[0, 50]),
            _call("cfdna-1", "0/0", dp=100, ad=[100, 0]),
        ],
    )
    site = build_nipt_observations(
        [record], father_sample_id="father-1", cfdna_sample_id="cfdna-1"
    )[0]
    assert site.father_state == "hom_alt"
    assert site.cf_alt_reads == 0
    assert not site.cf_present


def test_build_observation_missing_father_and_sex_chromosome() -> None:
    record = _record(
        "X-100-A-G",
        chrom="X",
        calls=[_call("cfdna-1", "0/1", dp=120, af=[0.05], ad=[114, 6])],
    )
    site = build_nipt_observations(
        [record], father_sample_id="father-1", cfdna_sample_id="cfdna-1"
    )[0]
    assert site.father_state == "missing"
    assert site.father_dp is None
    assert not site.is_autosomal


def test_build_observation_reads_a_call_without_allele_depths_off_its_gt_and_af() -> None:
    # No AD: the plasma's allele is present by its GT, at the caller's AF, with no alt-read
    # count to classify on.
    record = _record("1-500-A-G", calls=[_call("father-1", "0/0", dp=50, ad=[50, 0]), _call("cfdna-1", "0/1", dp=300, af=[0.05])])
    site = build_nipt_observations([record], father_sample_id="father-1", cfdna_sample_id="cfdna-1")[0]
    assert (site.cf_present, site.cf_vaf, site.cf_alt_reads, site.cf_dp) == (True, 0.05, None, 300)
    absent = _record("1-600-A-G", calls=[_call("cfdna-1", "0/0", dp=300, af=[0.0])])
    assert not build_nipt_observations([absent], father_sample_id="father-1", cfdna_sample_id="cfdna-1")[0].cf_present


def test_build_observation_skips_sites_without_cfdna_call() -> None:
    record = _record("1-300-A-G", calls=[_call("father-1", "0/1", dp=50, ad=[25, 25])])
    assert build_nipt_observations(
        [record], father_sample_id="father-1", cfdna_sample_id="cfdna-1"
    ) == []


# --------------------------------------------------------------------------- #
# run_family_nipt_analysis (I/O mocked)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_run_family_nipt_analysis_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    cat3 = _record(
        "1-9000-A-G",
        start=9000,
        calls=[
            _call("father-1", "0/0", dp=50, ad=[50, 0]),
            _call("cfdna-1", "0/1", dp=600, af=[0.5], ad=[300, 300]),
        ],
    )
    _wire_variants_mocks(monkeypatch, cohort=[*_cohort_cat7_records(), cat3], filtered=[])

    result = await run_family_nipt_analysis(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
    )

    assert result.fetal_fraction.ff_computed == pytest.approx(0.10, abs=0.01)
    assert result.fetal_fraction.n_sites == 40
    assert result.category_counts[7] == 40
    assert result.category_counts[3] == 1


@pytest.mark.asyncio
async def test_run_family_nipt_analysis_counts_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(monkeypatch, cohort=_cohort_cat7_records(), filtered=[], artifacts={"1-5-A-G"})

    result = await run_family_nipt_analysis(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
    )

    assert result.filter_counts["failed_artifact"] == 1
    # The artifact site is dropped before FF estimation, so 39 category-7 sites remain.
    assert result.fetal_fraction.n_sites == 39


@pytest.mark.asyncio
async def test_the_variant_summary_reads_what_the_artifact_list_did_to_the_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A listed allele ClinVar may call pathogenic stays in the analysis; a listed allele the
    # family does not carry counts only toward the list's size.
    protected = dataclasses.replace(
        _record(
            "1-777-A-G",
            start=777,
            calls=[
                _call("father-1", "0/0", dp=50, ad=[50, 0]),
                _call("cfdna-1", "0/1", dp=600, af=[0.5], ad=[300, 300]),
            ],
        ),
        annotations=[{"clinvar": "Likely_pathogenic"}],
    )
    _wire_variants_mocks(
        monkeypatch,
        cohort=[*_cohort_cat7_records(), protected],
        filtered=[],
        artifacts={"1-5-A-G", "1-777-A-G", "9-999-A-G"},
    )

    summary, artifacts = await nipt_service.get_family_nipt_summary_with_artifacts(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
    )

    assert dataclasses.astuple(artifacts) == (3, 2, 1, {"clinvar": 1})
    # The summary is the NIPT page's: the removed allele is the funnel's artifact.
    assert summary.analysis.filter_counts["failed_artifact"] == 1
    assert summary.analysis.category_counts[3] == 1


@pytest.mark.asyncio
async def test_run_family_nipt_analysis_rejects_non_nipt_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = FamilyOut(
        id="family-uuid",
        family_id="FAM001",
        created_at=datetime.now(timezone.utc),
        members=[FamilyMemberOut(sample_id="father-1", role="father", affected=False)],
        metadata={},
    )

    async def fake_get_family_record(_session, _family_id, _user):
        return family

    monkeypatch.setattr(nipt_service, "get_family_record", fake_get_family_record)

    with pytest.raises(HTTPException) as excinfo:
        await run_family_nipt_analysis(
            session=None,  # type: ignore[arg-type]
            family_id="FAM001",
            user=None,  # type: ignore[arg-type]
        )
    assert (excinfo.value.status_code, excinfo.value.detail) == (
        400,
        "Family is not configured for monogenic NIPT analysis",
    )


# --------------------------------------------------------------------------- #
# get_family_nipt_variants (I/O mocked)
# --------------------------------------------------------------------------- #

def _nipt_family(roi: FamilyRegionOfInterestOut | None = None) -> FamilyOut:
    return nipt_family(family_id="NIPT001", father="father-1", plasma="cfdna-1", roi=roi)


def _wire_variants_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cohort,
    filtered,
    artifacts: set[str] | None = None,
    family: FamilyOut | None = None,
    sample_name_to_uuid: dict[str, str] | None = None,
) -> None:
    # No other cfDNA sample of the assay: no allele recurs.
    wire_nipt_reads(
        monkeypatch,
        family=family or _nipt_family(),
        cohort=cohort,
        filtered=filtered,
        artifacts=artifacts,
        sample_name_to_uuid=sample_name_to_uuid,
    )


def _cohort_cat7_records() -> list[SmallVariantRecord]:
    return [
        _record(
            f"1-{i}-A-G",
            start=i,
            calls=[
                _call("father-1", "0/1", dp=50, ad=[25, 25]),
                _call("cfdna-1", "0/1", dp=400, af=[0.05], ad=[380, 20]),
            ],
        )
        for i in range(40)
    ]


def _de_novo_and_cat3_records() -> list[SmallVariantRecord]:
    de_novo = _record(
        "1-9000-A-G",
        start=9000,
        calls=[
            _call("father-1", "0/0", dp=50, ad=[50, 0]),
            _call("cfdna-1", "0/1", dp=300, af=[0.05], ad=[285, 15]),
        ],
    )
    cat3 = _record(
        "1-9100-A-G",
        start=9100,
        calls=[
            _call("father-1", "0/0", dp=50, ad=[50, 0]),
            _call("cfdna-1", "0/1", dp=600, af=[0.5], ad=[300, 300]),
        ],
    )
    return [de_novo, cat3]


@pytest.mark.asyncio
async def test_get_family_nipt_variants_classifies_filtered_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records()
    )

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1"},
    )

    assert result.fetal_fraction.ff_computed == pytest.approx(0.10, abs=0.01)
    assert result.total == 2
    categories = {item.classification.category for item in result.variants}
    assert categories == {1, 3}


@pytest.mark.asyncio
async def test_get_family_nipt_variants_names_the_transcript_the_filters_held_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the NIPT variant list uses the family search's annotation filters, so a
    # variant kept by SIFT deleterious on its second transcript names that transcript
    # beside its primary one, which SIFT calls tolerated.
    annotations = [
        {"gene": "SYN51", "transcript_id": "ENST_SYN51_1", "impact": "MODERATE",
         "effect": "missense_variant", "canonical": True, "sift": "tolerated(0.2)"},
        {"gene": "SYN51", "transcript_id": "ENST_SYN51_2", "impact": "MODERATE",
         "effect": "missense_variant", "sift": "deleterious(0.02)"},
    ]
    filtered = [
        dataclasses.replace(
            record, annotations=annotations, variant_key=index, annotation_version="current", annotation_set_hash=7
        )
        for index, record in enumerate(_de_novo_and_cat3_records(), start=1)
    ]
    _wire_variants_mocks(monkeypatch, cohort=_cohort_cat7_records(), filtered=filtered)

    async def sift_matches(_context, filters, records):
        # The SQL's answer for SIFT deleterious: each variant's second transcript.
        from backend.app.services.clickhouse_family_variants import _annotation_set_identity
        from backend.app.services.clickhouse_variant_rows import _annotation_payload_hash

        if filters is None or not filters.sift:
            return None
        return {
            _annotation_set_identity(record): frozenset({_annotation_payload_hash(annotations[1])})
            for record in records
        }

    monkeypatch.setattr("backend.app.services.clickhouse_family_variants._filter_transcript_matches", sift_matches)

    async def variant_outs(query_filters):
        result = await get_family_nipt_variants(
            session=None,  # type: ignore[arg-type]
            family_id="NIPT001",
            user=None,  # type: ignore[arg-type]
            query_filters=query_filters,
        )
        return [item.variant_out for item in result.variants]

    kept = await variant_outs({"gene": "SYN51", "sift": "deleterious"})
    assert len(kept) == 2
    for variant in kept:
        assert variant.sift == "tolerated(0.2)"
        assert variant.filter_transcript is not None
        assert (variant.filter_transcript.transcript_id, variant.filter_transcript.sift) == (
            "ENST_SYN51_2",
            "deleterious(0.02)",
        )
    assert all(variant.filter_transcript is None for variant in await variant_outs({"gene": "SYN51"}))


@pytest.mark.asyncio
async def test_get_family_nipt_variants_category_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records()
    )

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1"},
        categories=[1],
    )

    assert result.total == 1
    assert result.variants[0].classification.category == 1


@pytest.mark.asyncio
async def test_get_family_nipt_variants_de_novo_inheritance_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records()
    )

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1"},
        inheritance="de_novo",
    )

    assert {item.classification.category for item in result.variants} == {1}
    # Each candidate carries its triage: in the fetal window of the FF sites (all at
    # 5%), a novel, very rare SNV with clean technical terms.
    [candidate] = result.variants
    assert candidate.de_novo is not None
    assert candidate.de_novo.window == "strict"
    # 8 strict + 1 novel + 1 very rare + 6 technical (a joint VCF has no caller metrics:
    # every term is met) + 1 SNV.
    assert (candidate.de_novo.score, candidate.de_novo.label) == (17, "high")


@pytest.mark.asyncio
async def test_get_family_nipt_variants_rejects_unsupported_preset() -> None:
    # Refused before any read: no family, context or variant load is stubbed here.
    with pytest.raises(HTTPException) as excinfo:
        await get_family_nipt_variants(
            session=None,  # type: ignore[arg-type]
            family_id="NIPT001",
            user=None,  # type: ignore[arg-type]
            inheritance="not_a_preset",
        )
    assert (excinfo.value.status_code, excinfo.value.detail) == (400, "Unsupported NIPT inheritance preset: not_a_preset")


_SMALL_VARIANT_SEARCH_TS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "families" / "smallVariantSearch.ts"
)


def test_the_nipt_page_sends_exactly_the_inheritance_views_the_list_takes() -> None:
    # The NIPT page sends an inheritance only when it is one of these views, and names any other
    # a saved search or a link carries as a filter the list does not apply. A view
    # added here and not there would never be sent; one added there and not here would fail
    # the whole search, as an unsupported preset.
    source = _SMALL_VARIANT_SEARCH_TS.read_text(encoding="utf-8")
    declaration = re.search(r"export const NIPT_INHERITANCE_VIEWS\b[^=]*=\s*\[([^\]]*)\]", source)
    assert declaration, "NIPT_INHERITANCE_VIEWS not found in smallVariantSearch.ts"
    page_views = re.findall(r"'(\w+)'", declaration.group(1))
    assert len(page_views) == len(set(page_views))
    assert set(page_views) == set(nipt_service._INHERITANCE_VIEWS)


def _recessive_filtered_records() -> list[SmallVariantRecord]:
    return [
        # GENEA: a paternal hit (cat 7) and a maternal hit (cat 3) -> compound het.
        _record(
            "1-1-A-G",
            start=1,
            gene_symbols=["GENEA"],
            calls=[
                _call("father-1", "0/1", dp=50, ad=[25, 25]),
                _call("cfdna-1", "0/1", dp=400, af=[0.05], ad=[380, 20]),
            ],
        ),
        _record(
            "1-2-A-G",
            start=2,
            gene_symbols=["GENEA"],
            calls=[
                _call("father-1", "0/0", dp=50, ad=[50, 0]),
                _call("cfdna-1", "0/1", dp=600, af=[0.5], ad=[300, 300]),
            ],
        ),
        # GENEB: a lone paternal hit (cat 7), no maternal hit -> not at risk.
        _record(
            "1-3-A-G",
            start=3,
            gene_symbols=["GENEB"],
            calls=[
                _call("father-1", "0/1", dp=50, ad=[25, 25]),
                _call("cfdna-1", "0/1", dp=400, af=[0.05], ad=[380, 20]),
            ],
        ),
        # GENEC: fetus homozygous-alt (cat 4) -> at risk on its own.
        _record(
            "1-4-A-G",
            start=4,
            gene_symbols=["GENEC"],
            calls=[
                _call("father-1", "0/1", dp=50, ad=[25, 25]),
                _call("cfdna-1", "1/1", dp=600, af=[0.55], ad=[270, 330]),
            ],
        ),
    ]


@pytest.mark.asyncio
async def test_get_family_nipt_variants_recessive_at_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=_recessive_filtered_records()
    )

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "GENEA GENEB GENEC"},
        inheritance="recessive_at_risk",
    )

    kept = {item.record.variant_id for item in result.variants}
    # GENEA compound pair + GENEC homozygote; GENEB's lone paternal hit is excluded.
    assert kept == {"1-1-A-G", "1-2-A-G", "1-4-A-G"}
    assert result.total == 3


@pytest.mark.asyncio
async def test_get_family_nipt_variants_excludes_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(
        monkeypatch,
        cohort=_cohort_cat7_records(),
        filtered=_de_novo_and_cat3_records(),
        artifacts={"1-9000-A-G"},  # the de novo site is a known artifact
    )

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1"},
    )

    assert result.total == 1
    assert {item.classification.category for item in result.variants} == {3}


def _artifact_ff_shaped_records() -> list[SmallVariantRecord]:
    """Listed artifacts that look like paternal FF sites (father carries, low cfDNA VAF)
    but sit at VAF 0.15, not FF/2: counted, they pull the estimate up."""
    return [
        _record(
            f"1-{5000 + i}-A-G",
            start=5000 + i,
            calls=[
                _call("father-1", "0/1", dp=50, ad=[25, 25]),
                _call("cfdna-1", "0/1", dp=400, af=[0.15], ad=[340, 60]),
            ],
        )
        for i in range(10)
    ]


@pytest.mark.asyncio
async def test_the_variant_list_reports_the_summary_fetal_fraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The summary estimated FF after the quality and artifact filters, the variant list
    # over every family site, so the two views could show different fetal fractions, and
    # the list classified against the one the summary did not report.
    artifacts = _artifact_ff_shaped_records()
    _wire_variants_mocks(
        monkeypatch,
        cohort=_cohort_cat7_records() + artifacts,
        filtered=_de_novo_and_cat3_records(),
        artifacts={record.variant_id for record in artifacts},
    )

    summary = await run_family_nipt_analysis(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
    )
    listed = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1"},
    )

    assert summary.fetal_fraction.n_sites == 40  # the listed artifacts are not FF sites
    assert summary.fetal_fraction.ff_computed == pytest.approx(0.10, abs=0.01)
    assert listed.fetal_fraction == summary.fetal_fraction
    de_novo = next(item for item in listed.variants if item.classification.category == 1)
    assert de_novo.classification.expected_vaf == pytest.approx(summary.fetal_fraction.ff / 2)


@pytest.mark.asyncio
async def test_get_family_nipt_variants_applies_the_interval_and_exclusion_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The NIPT page sends the interval list, the excluded intervals and the excluded genes,
    # and chips them; the variant load receives each of them.
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records()
    )
    variant_loads: list[dict] = []
    inner_fetch = nipt_service._fetch_small_variant_rows

    async def recording_fetch(context, filters, **kwargs):
        if filters.gene is not None:
            variant_loads.append(kwargs)
        return await inner_fetch(context, filters, **kwargs)

    async def fake_gene_regions(_session, *, gene_query, assembly_id):
        assert (gene_query, assembly_id) == ("GENE4", "assembly-uuid")
        return [Region("1", 5_000, 5_100)]

    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", recording_fetch)
    monkeypatch.setattr(nipt_service, "_fetch_gene_regions", fake_gene_regions)

    await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={
            "gene": "BRCA1",
            "intervals": "chr1:9,000-9,200",
            "exclude_intervals": "1:9050-9060",
            "exclude_gene": "GENE4",
        },
    )

    (load,) = variant_loads
    assert load["include_regions"] == [Region("1", 9_000, 9_200)]
    assert load["exclude_regions"] == [Region("1", 9_050, 9_060)]
    assert load["exclude_gene_regions"] == [Region("1", 5_000, 5_100)]
    assert load["exclude_gene_terms"] == ["GENE4"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_filters", "detail"),
    [
        ({"intervals": "chr1\t100\t200"}, "Interval 'chr1\\t100\\t200' is not chr:start-end."),
        ({"exclude_intervals": "chr1:200-100"}, "Excluded interval 'chr1:200-100' ends before it starts."),
    ],
)
async def test_get_family_nipt_variants_refuses_an_unreadable_interval(
    monkeypatch: pytest.MonkeyPatch, query_filters: dict, detail: str
) -> None:
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records()
    )
    loads: list[object] = []

    async def no_fetch(*args, **kwargs):
        loads.append(args)
        return []

    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", no_fetch)

    with pytest.raises(HTTPException) as refused:
        await get_family_nipt_variants(
            session=None,  # type: ignore[arg-type]
            family_id="NIPT001",
            user=None,  # type: ignore[arg-type]
            query_filters=query_filters,
        )

    assert (refused.value.status_code, refused.value.detail) == (422, detail)
    # Refused before the cohort-wide fetal-fraction load, not after it.
    assert loads == []


def _a_third_record_after_the_others() -> SmallVariantRecord:
    return _record(
        "1-9200-A-G",
        start=9200,
        calls=[
            _call("father-1", "0/0", dp=50, ad=[50, 0]),
            _call("cfdna-1", "0/1", dp=300, af=[0.05], ad=[285, 15]),
        ],
    )


def _record_variant_load_limits(monkeypatch: pytest.MonkeyPatch) -> list[int | None]:
    limits: list[int | None] = []
    inner_fetch = nipt_service._fetch_small_variant_rows

    async def recording_fetch(context, filters, **kwargs):
        if filters.gene is not None:
            limits.append(kwargs.get("limit"))
        return await inner_fetch(context, filters, **kwargs)

    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", recording_fetch)
    return limits


@pytest.mark.asyncio
async def test_a_variant_list_past_the_classification_limit_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The list classifies the first variants of its scope in genomic order, up to a limit.
    # Past the limit it stopped part-way through the genome with a total that looked exact,
    # so the page and the report read as complete.
    _wire_variants_mocks(
        monkeypatch,
        cohort=_cohort_cat7_records(),
        filtered=[*_de_novo_and_cat3_records(), _a_third_record_after_the_others()],
    )
    limits = _record_variant_load_limits(monkeypatch)
    monkeypatch.setattr(nipt_service, "_NIPT_VARIANT_FETCH_LIMIT", 2)

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1"},
    )

    # One row past the limit tells a cut list from a complete one.
    assert limits == [3]
    assert (result.total_is_estimated, result.count_limit) == (True, 2)
    # Only the first two are classified: the extra row only says there are more.
    assert [item.record.variant_id for item in result.variants] == ["1-9000-A-G", "1-9100-A-G"]
    assert result.total == 2


@pytest.mark.asyncio
async def test_a_variant_list_that_fits_the_classification_limit_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records()
    )
    monkeypatch.setattr(nipt_service, "_NIPT_VARIANT_FETCH_LIMIT", 2)

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1"},
    )

    assert (result.total_is_estimated, result.count_limit) == (False, None)
    assert result.total == 2


@pytest.mark.asyncio
async def test_a_minimum_confidence_lists_only_the_categorised_calls_at_least_that_confident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A paternal allele the plasma lacks at depth has no category: not transmitted.
    not_transmitted = _record(
        "1-9400-A-G",
        start=9400,
        calls=[_call("father-1", "0/1", dp=50, ad=[25, 25]), _call("cfdna-1", "0/0", dp=120, ad=[120, 0])],
    )
    _wire_variants_mocks(
        monkeypatch, cohort=_cohort_cat7_records(), filtered=[*_de_novo_and_cat3_records(), not_transmitted]
    )

    async def listed(min_confidence: float | None) -> dict:
        result = await get_family_nipt_variants(
            session=None,  # type: ignore[arg-type]
            family_id="NIPT001",
            user=None,  # type: ignore[arg-type]
            query_filters={"gene": "BRCA1"},
            min_confidence=min_confidence,
        )
        return {item.record.variant_id: item.classification for item in result.variants}

    everything = await listed(None)
    assert {variant_id: c.category for variant_id, c in everything.items()} == {
        "1-9000-A-G": 1,
        "1-9100-A-G": 3,
        "1-9400-A-G": None,
    }
    assert everything["1-9400-A-G"].fetal_inheritance == "paternal_not_transmitted"
    # The maternal allele at VAF 0.50 and FF 10% is the less certain call.
    assert everything["1-9100-A-G"].confidence < 0.9 <= everything["1-9000-A-G"].confidence
    assert set(await listed(0.0)) == {"1-9000-A-G", "1-9100-A-G"}
    assert set(await listed(0.9)) == {"1-9000-A-G"}


# --------------------------------------------------------------------------- #
# A gene panel narrows the list, on the family's audit trail first (REQ-CARR-003)
# --------------------------------------------------------------------------- #


def _wire_panel(monkeypatch: pytest.MonkeyPatch, constraints: PanelFilterConstraints) -> list[tuple]:
    """The panel's genes and regions; each step the variant list takes with it, in order: the
    panel use recorded, its constraints read, and the variant load (not the cohort's)."""
    steps: list[tuple] = []

    async def record_use(_session, *, context, panel_id, user, search):
        steps.append(("recorded", panel_id, search, context.family_uuid))

    async def panel_constraints(_session, panel_id, *, assembly_id):
        steps.append(("read", panel_id, assembly_id))
        return constraints

    inner_fetch = nipt_service._fetch_small_variant_rows

    async def recording_fetch(context, filters, **kwargs):
        if filters.gene is not None:
            steps.append(("variant load", kwargs.get("panel_constraints")))
        return await inner_fetch(context, filters, **kwargs)

    monkeypatch.setattr(nipt_service, "record_family_panel_use", record_use)
    monkeypatch.setattr(nipt_service, "_fetch_panel_constraints", panel_constraints)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", recording_fetch)
    return steps


@pytest.mark.asyncio
async def test_a_panel_is_recorded_on_the_audit_trail_before_it_narrows_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records())
    constraints = PanelFilterConstraints(genes=("GENEA",), regions=(Region("1", 9_000, 9_200),))
    steps = _wire_panel(monkeypatch, constraints)

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1", "panel_id": "panel-1"},
    )

    assert steps == [
        ("recorded", "panel-1", "nipt_variants", "family-uuid"),
        ("read", "panel-1", "assembly-uuid"),
        ("variant load", constraints),
    ]
    assert result.total == 2


@pytest.mark.asyncio
async def test_a_panel_without_genes_or_regions_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_variants_mocks(monkeypatch, cohort=_cohort_cat7_records(), filtered=_de_novo_and_cat3_records())
    steps = _wire_panel(monkeypatch, PanelFilterConstraints())

    result = await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "BRCA1", "panel_id": "panel-empty"},
    )

    # Recorded all the same, and no variant load: an empty panel selects nothing.
    assert steps == [("recorded", "panel-empty", "nipt_variants", "family-uuid"), ("read", "panel-empty", "assembly-uuid")]
    assert (result.total, result.variants, result.not_classified) == (0, [], {})
    assert result.fetal_fraction.n_sites == 40


# --------------------------------------------------------------------------- #
# The review-tag filter: the small-variant list's review_tag and
# exclude_review_tag, read from the family's reviews by the same code.
# --------------------------------------------------------------------------- #

class _ReviewRows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_ReviewRows":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)


class _ReviewSession:
    """The family's small-variant reviews, as Postgres answers the review-tag lookup
    (``list_matching_small_variant_review_ids``, which runs as it does in production)."""

    def __init__(self, reviews: dict[str, dict]) -> None:
        self.reviews = reviews
        self.lookups = 0

    async def execute(self, statement, params=None) -> _ReviewRows:
        assert "FROM small_variant_reviews" in str(statement)
        assert params == {"family_id": "family-uuid"}
        self.lookups += 1
        return _ReviewRows(
            [
                {
                    "variant_id": variant_id,
                    "classification": review.get("classification"),
                    "tags": review.get("tags", []),
                    "note": review.get("note"),
                    "compound_het_classification": None,
                    "compound_het_tags": review.get("compound_het_tags", []),
                    "compound_het_note": None,
                }
                for variant_id, review in self.reviews.items()
            ]
        )


def _family_records() -> list[SmallVariantRecord]:
    # The FF sites, the de novo candidate (1-9000), the maternal allele (1-9100) and a
    # second de novo candidate (1-9200), in genomic order.
    return [*_cohort_cat7_records(), *_de_novo_and_cat3_records(), _a_third_record_after_the_others()]


def _father_only_record() -> SmallVariantRecord:
    # A joint callset's record without a plasma call: the analysis cannot classify it.
    return _record("1-9300-A-G", start=9300, calls=[_call("father-1", "0/1", dp=50, ad=[25, 25])])


def _wire_family_rows(
    monkeypatch: pytest.MonkeyPatch, records: list[SmallVariantRecord], *, artifacts: set[str] | None = None
) -> list[dict]:
    """The variant load as ClickHouse answers it: the family's rows in genomic order,
    the review selection's ids kept or left out, then the row limit. Each load's
    selection is recorded."""
    _wire_variants_mocks(monkeypatch, cohort=records, filtered=records, artifacts=artifacts)
    loads: list[dict] = []

    async def fetch(_context, _filters, *, limit=None, include_variant_ids=None, exclude_variant_ids=(), **_kwargs):
        loads.append({"include": include_variant_ids, "exclude": list(exclude_variant_ids), "limit": limit})
        rows = [
            record
            for record in records
            if (include_variant_ids is None or record.variant_id in include_variant_ids)
            and record.variant_id not in exclude_variant_ids
        ]
        return rows if limit is None else rows[:limit]

    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fetch)
    return loads


async def _nipt_list(session, **kwargs):
    return await get_family_nipt_variants(
        session=session,
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        page_size=500,
        **kwargs,
    )


def _listed(result) -> list[str]:
    return [item.record.variant_id for item in result.variants]


@pytest.mark.asyncio
async def test_the_review_tag_filter_keeps_exactly_the_tagged_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The NIPT page had no review-tag filter: the variants a sign-out freezes, those tagged
    # report, could not be listed.
    loads = _wire_family_rows(monkeypatch, _family_records())
    session = _ReviewSession(
        {
            "1-5-A-G": {"tags": ["report"]},  # an FF site the fetus inherited, category 7
            "1-9000-A-G": {"tags": ["report", "review"]},
            "1-9100-A-G": {"tags": ["review"], "note": "maternal"},
            "1-9200-A-G": {"tags": ["excluded"]},
        }
    )

    reported = await _nipt_list(session, review_tags=["report"])

    assert _listed(reported) == ["1-5-A-G", "1-9000-A-G"]
    assert reported.total == 2
    assert {item.record.variant_id: item.classification.category for item in reported.variants} == {
        "1-5-A-G": 7,
        "1-9000-A-G": 1,
    }
    assert reported.not_classified == {}
    # The selection goes into the variant load itself, as ids, after the cohort load.
    assert loads[-1] == {"include": ["1-5-A-G", "1-9000-A-G"], "exclude": [], "limit": 5001}
    # Any of several tags, and the view and category filters still apply within it.
    assert _listed(await _nipt_list(session, review_tags=["report", "review"])) == [
        "1-5-A-G",
        "1-9000-A-G",
        "1-9100-A-G",
    ]
    assert _listed(await _nipt_list(session, review_tags=["report"], inheritance="de_novo")) == ["1-9000-A-G"]
    assert _listed(await _nipt_list(session, review_tags=["report", "review"], categories=[3])) == ["1-9100-A-G"]


@pytest.mark.asyncio
async def test_an_excluded_review_tag_leaves_its_variants_out(monkeypatch: pytest.MonkeyPatch) -> None:
    loads = _wire_family_rows(monkeypatch, _family_records())
    session = _ReviewSession({"1-9100-A-G": {"tags": ["excluded"]}, "1-9200-A-G": {"tags": ["review"]}})

    listed = await _nipt_list(session, exclude_review_tags=["excluded"])

    assert "1-9100-A-G" not in _listed(listed)
    assert {"1-9000-A-G", "1-9200-A-G"} <= set(_listed(listed))
    assert listed.total == len(_family_records()) - 1
    assert loads[-1] == {"include": None, "exclude": ["1-9100-A-G"], "limit": 5001}
    # With a review tag as well: the tagged variants without an excluded tag.
    both = await _nipt_list(session, review_tags=["review", "excluded"], exclude_review_tags=["excluded"])
    assert _listed(both) == ["1-9200-A-G"]


@pytest.mark.asyncio
@pytest.mark.parametrize("review_tags", [["report"], [" "]])
async def test_a_review_tag_no_variant_has_lists_nothing(
    monkeypatch: pytest.MonkeyPatch, review_tags: list[str]
) -> None:
    # As on the small-variant list: a tag no review holds, or a blank one, selects nothing,
    # and the variant load is not run.
    loads = _wire_family_rows(monkeypatch, _family_records())
    session = _ReviewSession({"1-9000-A-G": {"tags": ["review"]}})

    listed = await _nipt_list(session, review_tags=review_tags)

    assert (listed.total, listed.variants, listed.not_classified) == (0, [], {})
    assert [load["include"] for load in loads] == [None]  # the cohort load for the FF only


@pytest.mark.asyncio
async def test_a_tagged_variant_past_the_classification_limit_is_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The list classifies the first variants of its scope in genomic order. Read after the
    # limit, the review tags would lose a reported variant beyond it; read by id in the
    # load, they never do.
    _wire_family_rows(monkeypatch, _family_records())
    monkeypatch.setattr(nipt_service, "_NIPT_VARIANT_FETCH_LIMIT", 2)
    session = _ReviewSession({"1-9200-A-G": {"tags": ["report"]}})

    listed = await _nipt_list(session, review_tags=["report"])

    assert _listed(listed) == ["1-9200-A-G"]
    assert (listed.total_is_estimated, listed.count_limit) == (False, None)


@pytest.mark.asyncio
async def test_a_tagged_variant_the_analysis_does_not_classify_is_named_with_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No list can show it, whatever the filters: the page names it, with the reason the
    # signed record gives a reported one.
    _wire_family_rows(monkeypatch, [*_family_records(), _father_only_record()], artifacts={"1-9100-A-G"})
    session = _ReviewSession(
        {
            "1-9000-A-G": {"tags": ["report"]},
            "1-9100-A-G": {"tags": ["report"]},
            "1-9300-A-G": {"tags": ["report"]},
            "9-999-A-G": {"tags": ["report"]},
            "1-9200-A-G": {"tags": ["report", "excluded"]},
        }
    )

    listed = await _nipt_list(session, review_tags=["report"], exclude_review_tags=["excluded"])

    assert _listed(listed) == ["1-9000-A-G"]
    assert listed.not_classified == {
        "1-9100-A-G": "artifact_list",
        "1-9300-A-G": "no_plasma_call",
        "9-999-A-G": "not_in_data",
    }
    # The other filters narrow the list, not what the analysis cannot classify.
    narrowed = await _nipt_list(session, review_tags=["report"], inheritance="paternal_dominant")
    assert (_listed(narrowed), narrowed.not_classified) == ([], listed.not_classified)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("review_tags", "exclude_review_tags"),
    [
        (["report"], []),
        (["report", "review"], ["excluded"]),
        ([], ["excluded"]),
        ([" "], []),
        (["no-such-tag"], ["report"]),
    ],
)
async def test_the_nipt_list_selects_by_review_tag_as_the_small_variant_list_does(
    monkeypatch: pytest.MonkeyPatch, review_tags: list[str], exclude_review_tags: list[str]
) -> None:
    # The same names, the same rule: a variant's own tags or its compound-het pair's, any of
    # the given ones; a blank tag still selects (nothing).
    from backend.app.services import clickhouse_family_variants as family_variants
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    reviews = {
        "1-5-A-G": {"tags": ["report"]},
        "1-9000-A-G": {"compound_het_tags": ["report"]},
        "1-9100-A-G": {"tags": ["review", "excluded"]},
        "1-9200-A-G": {"tags": ["excluded"]},
    }
    loads = _wire_family_rows(monkeypatch, _family_records())

    await _nipt_list(_ReviewSession(reviews), review_tags=review_tags, exclude_review_tags=exclude_review_tags)
    scope = await family_variants._resolve_small_variant_scope(
        _ReviewSession(reviews),  # type: ignore[arg-type]
        SimpleNamespace(family_uuid="family-uuid", assembly_id="assembly-uuid"),  # type: ignore[arg-type]
        SmallVariantQueryFilters(page=1, page_size=100),
        review_classifications=None,
        review_tags=review_tags,
        exclude_review_tags=exclude_review_tags,
        has_notes=False,
    )

    nipt_load = loads[-1] if len(loads) > 1 else None
    if scope is None:
        assert nipt_load is None  # nothing matches: no variant load at all
        return
    assert nipt_load is not None
    row_filters = scope.row_filters()
    expected_include = row_filters["include_variant_ids"]
    assert nipt_load["include"] == (sorted(expected_include) if expected_include is not None else None)
    assert nipt_load["exclude"] == sorted(row_filters["exclude_variant_ids"])


# --------------------------------------------------------------------------- #
# get_family_nipt_coverage (I/O mocked)
# --------------------------------------------------------------------------- #


def _plasma_coverage_is_a_bed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plasma's coverage track is a coverage BED (source ``web``) declared as read depth:
    the track the coverage check reads as depth (coverage_read_depth)."""

    async def fake_sources(_assembly_name, *, sample_uuid_to_name, **_kwargs):
        return {name: ["web"] for name in sample_uuid_to_name.values()}

    async def fake_normalizations(_session, **_kwargs):
        return {}

    async def fake_contents(_session, *, sample_uuid_to_name, **_kwargs):
        return {name: {"web": ["read_depth"]} for name in sample_uuid_to_name.values()}

    monkeypatch.setattr(clickhouse_interval_tracks, "get_interval_track_sources_by_sample", fake_sources)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_normalizations_by_sample", fake_normalizations)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_contents_by_sample", fake_contents)


@pytest.mark.asyncio
async def test_get_family_nipt_coverage_over_family_roi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roi = FamilyRegionOfInterestOut(query="1:100-200", label="ROI", source="region", chr="1", start=100, end=200)
    _wire_variants_mocks(
        monkeypatch, cohort=[], filtered=[], family=_nipt_family(roi), sample_name_to_uuid={"cfdna-1": "cfdna-uuid"}
    )

    async def fake_fetch_coverage(
        _assembly_name, *, sample_uuid=None, track_type=None, chromosomes=None, source=None, **_kwargs
    ):
        if track_type == "target_coverage":
            return []  # no per-target table: the coverage track over the ROI
        assert track_type == "coverage"
        assert sample_uuid == "cfdna-uuid"
        assert source == "web"  # the coverage BED, the plasma's track of read depth
        return [{"chr": "1", "start": 100, "end": 200, "value": 30.0}]

    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_fetch_coverage)
    _plasma_coverage_is_a_bed(monkeypatch)

    coverage = await get_family_nipt_coverage(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
    )
    summary = coverage.regions
    assert coverage.targets is None

    assert summary.target_region_count == 1
    assert summary.overall_median_on_target == 30.0
    assert summary.per_region[0].label == "ROI"
    assert summary.per_region[0].median_coverage == 30.0


@pytest.mark.asyncio
async def test_the_coverage_over_a_panel_is_recorded_and_reads_its_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_variants_mocks(monkeypatch, cohort=[], filtered=[], sample_name_to_uuid={"cfdna-1": "cfdna-uuid"})
    steps = _wire_panel(monkeypatch, PanelFilterConstraints(regions=(Region("chr7", 1_000, 1_100),)))

    async def fake_fetch_coverage(_assembly_name, *, sample_uuid=None, track_type=None, chromosomes=None, **_kwargs):
        if track_type == "target_coverage":
            return []  # no per-target table: the coverage track over the panel's regions
        assert chromosomes == ["7"]
        # A bin without a value holds no coverage, and is not read as zero.
        return [{"chr": "7", "start": 1_000, "end": 1_100, "value": 45.0}, {"chr": "7", "start": 1_000, "end": 1_100, "value": None}]

    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_fetch_coverage)
    _plasma_coverage_is_a_bed(monkeypatch)

    coverage = await get_family_nipt_coverage(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        panel_id="panel-1",
    )

    # On the family's clinical audit trail first, as the variant list's panel is.
    assert steps == [("recorded", "panel-1", "nipt_coverage", "family-uuid"), ("read", "panel-1", "assembly-uuid")]
    [region] = coverage.regions.per_region
    # A panel's explicit region is labelled by its coordinates.
    assert (region.label, region.chrom, region.median_coverage, region.covered_bases) == ("7:1000-1100", "7", 45.0, 100)


class _FakeGeneMappings:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeGeneSession:
    """Minimal session whose execute() returns a fixed gene row (for coverage)."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _statement, _params=None):
        return _FakeGeneMappings(self._rows)


@pytest.mark.asyncio
async def test_get_family_nipt_coverage_labels_gene_regions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_variants_mocks(monkeypatch, cohort=[], filtered=[], sample_name_to_uuid={"cfdna-1": "cfdna-uuid"})

    async def fake_fetch_coverage(
        _assembly_name, *, sample_uuid=None, track_type=None, chromosomes=None, **_kwargs
    ):
        if track_type == "target_coverage":
            return []
        return [{"chr": "17", "start": 43044295, "end": 43125483, "value": 80.0}]

    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_fetch_coverage)
    _plasma_coverage_is_a_bed(monkeypatch)

    session = _FakeGeneSession(
        [{"hgnc_symbol": "BRCA1", "chr": "17", "start": 43044295, "end": 43125483}]
    )
    coverage = await get_family_nipt_coverage(
        session,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        gene="BRCA1",
    )
    summary = coverage.regions

    assert summary.target_region_count == 1
    assert summary.per_region[0].label == "BRCA1"
    assert summary.overall_median_on_target == 80.0
