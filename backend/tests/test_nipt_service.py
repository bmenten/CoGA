from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.schemas import FamilyMemberOut, FamilyOut, FamilyRegionOfInterestOut
from backend.app.services import nipt_service
from backend.app.services.clickhouse_variant_records import Region, SmallVariantCall, SmallVariantRecord
from backend.app.services.genotypes import GENOTYPE_CLASSES, classify_genotype, genotype_vocabulary
from backend.app.services.nipt_analysis import NiptQualityThresholds, run_nipt_analysis
from backend.app.services.nipt_service import (
    build_nipt_observations,
    derive_father_state,
    get_family_nipt_coverage,
    get_family_nipt_variants,
    run_family_nipt_analysis,
)


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
        # call at all, not a confident hom-ref (#511).
        ("./1", "het"),
        ("./0", "missing"),
        ("0/.", "missing"),
    ],
)
def test_derive_father_state(gt: str, expected: str) -> None:
    assert derive_father_state(_call("father-1", gt)) == expected


def test_derive_father_state_missing_when_no_call() -> None:
    assert derive_father_state(None) == "missing"


def test_derive_father_state_follows_the_shared_genotype_classes() -> None:
    # One classification of a VCF genotype everywhere (#511): the father's state is the
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
    # "1" used to read as het and was skipped, leaving the fetal sex indeterminate.
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
    family = FamilyOut(
        id="family-uuid",
        family_id="NIPT001",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id="father-1", role="father", affected=False),
            FamilyMemberOut(
                sample_id="cfdna-1",
                role="mother",
                affected=False,
                sample_metadata={"assay": "nipt_cfdna"},
            ),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
    )

    records = [
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
    records.append(
        _record(
            "1-9000-A-G",
            start=9000,
            calls=[
                _call("father-1", "0/0", dp=50, ad=[50, 0]),
                _call("cfdna-1", "0/1", dp=600, af=[0.5], ad=[300, 300]),
            ],
        )
    )

    async def fake_get_family_record(_session, _family_id, _user):
        return family

    async def fake_build_context(_session, *, family_identifier, user, project_id=None):
        return SimpleNamespace(assembly_id="assembly-uuid", assembly_name="GRCh38")

    async def fake_fetch(_context, _filters, *, limit=None, **_kwargs):
        return records

    async def fake_load_artifacts(_session, *, assembly_id, assay_key):
        return set()

    monkeypatch.setattr(nipt_service, "get_family_record", fake_get_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_build_context)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fake_fetch)
    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", fake_load_artifacts)

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
    family = FamilyOut(
        id="family-uuid",
        family_id="NIPT001",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id="father-1", role="father", affected=False),
            FamilyMemberOut(
                sample_id="cfdna-1",
                role="mother",
                affected=False,
                sample_metadata={"assay": "nipt_cfdna"},
            ),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
    )
    records = _cohort_cat7_records()

    async def fake_get_family_record(_session, _family_id, _user):
        return family

    async def fake_build_context(_session, *, family_identifier, user, project_id=None):
        return SimpleNamespace(assembly_id="assembly-uuid", assembly_name="GRCh38")

    async def fake_fetch(_context, _filters, *, limit=None, **_kwargs):
        return records

    async def fake_load_artifacts(_session, *, assembly_id, assay_key):
        return {"1-5-A-G"}

    monkeypatch.setattr(nipt_service, "get_family_record", fake_get_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_build_context)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fake_fetch)
    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", fake_load_artifacts)

    result = await run_family_nipt_analysis(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
    )

    assert result.filter_counts["failed_artifact"] == 1
    # The artifact site is dropped before FF estimation, so 39 category-7 sites remain.
    assert result.fetal_fraction.n_sites == 39


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

    with pytest.raises(Exception) as excinfo:
        await run_family_nipt_analysis(
            session=None,  # type: ignore[arg-type]
            family_id="FAM001",
            user=None,  # type: ignore[arg-type]
        )
    assert "monogenic NIPT" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# get_family_nipt_variants (I/O mocked)
# --------------------------------------------------------------------------- #

def _nipt_family() -> FamilyOut:
    return FamilyOut(
        id="family-uuid",
        family_id="NIPT001",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id="father-1", role="father", affected=False),
            FamilyMemberOut(
                sample_id="cfdna-1",
                role="mother",
                affected=False,
                sample_metadata={"assay": "nipt_cfdna"},
            ),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
    )


def _wire_variants_mocks(
    monkeypatch: pytest.MonkeyPatch, *, cohort, filtered, artifacts: set[str] | None = None
) -> None:
    async def fake_get_family_record(_session, _family_id, _user):
        return _nipt_family()

    async def fake_build_context(_session, *, family_identifier, user, project_id=None):
        return SimpleNamespace(assembly_id="assembly-uuid", assembly_name="GRCh38")

    async def fake_fetch(_context, filters, *, limit=None, **_kwargs):
        # The cohort (FF) load carries no gene filter; the variant load does. A limit is
        # the SQL LIMIT: the first rows, in genomic order.
        if filters.gene is None:
            return cohort
        return filtered if limit is None else filtered[:limit]

    async def fake_load_artifacts(_session, *, assembly_id, assay_key):
        return set(artifacts or set())

    # Review/internal-cohort hydration is a DB concern exercised elsewhere; no-op
    # it here so the variant serialization can run against the mocked context.
    async def fake_hydrate(_session, *, context, variants):
        return None

    monkeypatch.setattr(nipt_service, "get_family_record", fake_get_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_build_context)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fake_fetch)
    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", fake_load_artifacts)
    monkeypatch.setattr(nipt_service, "_hydrate_small_variant_outs", fake_hydrate)


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


@pytest.mark.asyncio
async def test_get_family_nipt_variants_rejects_unsupported_preset() -> None:
    with pytest.raises(Exception) as excinfo:
        await get_family_nipt_variants(
            session=None,  # type: ignore[arg-type]
            family_id="NIPT001",
            user=None,  # type: ignore[arg-type]
            inheritance="not_a_preset",
        )
    assert "inheritance preset" in str(excinfo.value)


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
    # #604: the NIPT page sends the interval list, the excluded intervals and the excluded
    # genes, and chips them; the variant load used to receive none of them.
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
    # Only the first two are classified, as before: the extra row only says there are more.
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


# --------------------------------------------------------------------------- #
# get_family_nipt_coverage (I/O mocked)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_family_nipt_coverage_over_family_roi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = FamilyOut(
        id="family-uuid",
        family_id="NIPT001",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id="father-1", role="father", affected=False),
            FamilyMemberOut(
                sample_id="cfdna-1",
                role="mother",
                affected=False,
                sample_metadata={"assay": "nipt_cfdna"},
            ),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
        roi=FamilyRegionOfInterestOut(
            query="1:100-200", label="ROI", source="region", chr="1", start=100, end=200
        ),
    )

    async def fake_get_family_record(_session, _family_id, _user):
        return family

    async def fake_build_context(_session, *, family_identifier, user, project_id=None):
        return SimpleNamespace(
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
            sample_name_to_uuid={"cfdna-1": "cfdna-uuid"},
        )

    async def fake_fetch_coverage(
        _assembly_name, *, sample_uuid=None, track_type=None, chromosomes=None, **_kwargs
    ):
        assert track_type == "coverage"
        assert sample_uuid == "cfdna-uuid"
        return [{"chr": "1", "start": 100, "end": 200, "value": 30.0}]

    monkeypatch.setattr(nipt_service, "get_family_record", fake_get_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_build_context)
    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_fetch_coverage)

    summary = await get_family_nipt_coverage(
        session=None,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
    )

    assert summary.target_region_count == 1
    assert summary.overall_median_on_target == 30.0
    assert summary.per_region[0].label == "ROI"
    assert summary.per_region[0].median_coverage == 30.0


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
    family = FamilyOut(
        id="family-uuid",
        family_id="NIPT001",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id="father-1", role="father", affected=False),
            FamilyMemberOut(
                sample_id="cfdna-1",
                role="mother",
                affected=False,
                sample_metadata={"assay": "nipt_cfdna"},
            ),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
    )

    async def fake_get_family_record(_session, _family_id, _user):
        return family

    async def fake_build_context(_session, *, family_identifier, user, project_id=None):
        return SimpleNamespace(
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
            sample_name_to_uuid={"cfdna-1": "cfdna-uuid"},
        )

    async def fake_fetch_coverage(
        _assembly_name, *, sample_uuid=None, track_type=None, chromosomes=None, **_kwargs
    ):
        return [{"chr": "17", "start": 43044295, "end": 43125483, "value": 80.0}]

    monkeypatch.setattr(nipt_service, "get_family_record", fake_get_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_build_context)
    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_fetch_coverage)

    session = _FakeGeneSession(
        [{"hgnc_symbol": "BRCA1", "chr": "17", "start": 43044295, "end": 43125483}]
    )
    summary = await get_family_nipt_coverage(
        session,  # type: ignore[arg-type]
        family_id="NIPT001",
        user=None,  # type: ignore[arg-type]
        gene="BRCA1",
    )

    assert summary.target_region_count == 1
    assert summary.per_region[0].label == "BRCA1"
    assert summary.overall_median_on_target == 80.0
