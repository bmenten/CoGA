"""The NIPT service on a per-sample (two-VCF) callset: the father's genotype from his allele
depths, a paternal allele the plasma did not show, the site depth of a split multi-allelic
record, and the paternal, maternal, de novo and recessive views. Synthetic data only."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.services import nipt_service
from backend.app.services.clickhouse_family_variants import CfdnaRecurrence
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.nipt_service import (
    artifact_protection,
    build_nipt_observations,
    derive_father_state,
    family_artifact_ids,
    listed_artifact_protection,
    get_family_nipt_coverage,
    get_family_nipt_diagnostic_candidates,
    get_family_nipt_gene_targets,
    get_family_nipt_summary,
    get_family_nipt_variants,
    restored_site_depths,
    triage_annotation,
)
from backend.app.services.nipt_analysis import NiptQualityThresholds
from backend.app.services.nipt_target_coverage import TargetCoverageRow, TargetDepthLookup
from backend.tests._nipt_service_fakes import nipt_family, wire_nipt_reads

FATHER = "FATHER1"
PLASMA = "CFDNA1"


def _call(sample: str, ad: list[int], *, dp: int | None = None, tlod: float | None = 300.0, gt: str = "0/1",
          filters: list[str] | None = None, metrics: dict[str, float] | None = None) -> SmallVariantCall:
    call_metrics = dict(metrics or {})
    if tlod is not None:
        call_metrics.setdefault("TLOD", tlod)
    return SmallVariantCall(
        sample=sample, gt=gt, gq=None, dp=dp if dp is not None else sum(ad), af=[], ad=ad, ps=None,
        filters=filters if filters is not None else ["PASS"], metrics=call_metrics,
    )


def _record(variant_id: str, calls: list[SmallVariantCall], *, genes: list[str] | None = None,
            annotations: list[dict] | None = None, rsid: str | None = None) -> SmallVariantRecord:
    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos), ref=ref, alt=alt,
        source="nipt", rsid=rsid, filters=[], gene_symbols=genes or [], annotations=annotations or [],
        calls=calls, qual=None,
    )


# --------------------------------------------------------------------------- #
# The father's genotype class from his allele depths (R NIPT-M paternal classes)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ad", "tlod", "expected"),
    [
        ([150, 150], 300.0, "het"),
        ([1, 299], 900.0, "hom_alt"),  # Mutect2 writes GT 0/1 whatever the fraction
        ([240, 60], 100.0, "het"),  # 20%: the het floor
        ([255, 45], 80.0, "low_vaf"),  # 15%: well supported, below het
        ([297, 3], 2.0, "low_support"),  # 3 alt reads
        ([8, 7], 30.0, "low_support"),  # 15 reads
        ([150, 150], 12.0, "low_support"),  # TLOD below 20
        ([300, 0], None, "hom_ref"),  # a reference call with depth
        ([10, 0], None, "low_support"),  # a thin reference call
    ],
)
def test_father_class_from_his_allele_depths(ad: list[int], tlod: float | None, expected: str) -> None:
    assert derive_father_state(_call(FATHER, ad, tlod=tlod)) == expected


@pytest.mark.parametrize(
    ("gt", "ad", "qual", "gq", "expected"),
    [
        ("0/1", [30, 28], 812.0, 99.0, "het"),
        ("0/1", [52, 6], 61.0, 25.0, "het"),  # 10% of the reads: GT decides, not the fraction
        ("1/1", [0, 41], 1500.0, 99.0, "hom_alt"),
        ("1", [0, 22], 900.0, 66.0, "hom_alt"),  # haploid: a man's X outside the PARs
        ("1/2", [2, 20, 18], 900.0, 99.0, "het"),  # one copy of this allele
        ("0/1", [30, 28], 49.0, 99.0, "low_support"),  # QUAL below 50
        ("0/1", [30, 28], 812.0, 19.0, "low_support"),  # GQ below 20
        ("0/1", [12, 6], 210.0, 99.0, "low_support"),  # 18 reads
        ("0/1", [40, 4], 70.0, 30.0, "low_support"),  # 4 alt reads
        ("./.", [30, 28], 812.0, 0.0, "low_support"),  # no called genotype
        ("0/0", [45, 0], 0.0, 99.0, "hom_ref"),
        ("0/0", [9, 0], 0.0, 27.0, "low_support"),
    ],
)
def test_father_class_from_his_haplotypecaller_genotype(
    gt: str, ad: list[int], qual: float, gq: float, expected: str
) -> None:
    assert derive_father_state(_hc_call(FATHER, ad, gt=gt, qual=qual, gq=gq)) == expected


def test_no_father_call_is_absent_in_a_per_sample_callset() -> None:
    assert derive_father_state(None, uncalled="absent") == "absent"
    assert derive_father_state(None) == "missing"


def test_a_split_records_site_depth_counts_every_alleles_reads() -> None:
    # bcftools norm -m- split one site into two records; each kept its own allele's reads.
    records = [
        _record("7-1000-A-G", [_call(PLASMA, [500, 400], dp=1000)]),
        _record("7-1000-A-T", [_call(PLASMA, [500, 100], dp=1000)]),
        _record("7-2000-C-T", [_call(PLASMA, [900, 100], dp=1000)]),
    ]
    assert restored_site_depths(records, PLASMA) == {"7-1000-A-G": 1000, "7-1000-A-T": 1000}
    sites = {site.variant_id: site for site in build_nipt_observations(records, father_sample_id=FATHER, cfdna_sample_id=PLASMA)}
    assert sites["7-1000-A-T"].cf_dp == 1000
    assert sites["7-1000-A-T"].cf_vaf == pytest.approx(0.1)  # not 100/600
    assert sites["7-2000-C-T"].cf_dp == 1000


def test_per_sample_observations() -> None:
    plasma_lookup = TargetDepthLookup([TargetCoverageRow(chrom="7", start=2900, end=3100, gene="GENEA", attribute="GENEA", mean=1500.0)])
    records = [
        # Both have a call: a paternal allele the fetus inherited.
        _record("7-1000-A-G", [_call(PLASMA, [1350, 150], metrics={"FS": 2.0, "MMQ": 60.0}), _call(FATHER, [150, 150])]),
        # Only the plasma: the father's call is absent (his file has none).
        _record("7-2000-C-T", [_call(PLASMA, [1350, 150])]),
        # Only the father, inside a target: the plasma's depth comes from its coverage.
        _record("7-3000-G-A", [_call(FATHER, [1, 299])]),
        # Only the father, outside every target: no plasma depth.
        _record("7-9000-G-A", [_call(FATHER, [150, 150])]),
    ]
    sites = {
        site.variant_id: site
        for site in build_nipt_observations(records, father_sample_id=FATHER, cfdna_sample_id=PLASMA, plasma_depth=plasma_lookup)
    }
    both = sites["7-1000-A-G"]
    assert (both.father_state, both.father_vaf, both.father_dp) == ("het", 0.5, 300)
    assert both.cf_fs == 2.0 and both.cf_metrics["TLOD"] == 300.0 and both.cf_filters == ["PASS"]
    assert both.cf_qual is None  # a per-sample record's QUAL is not the plasma call's
    assert sites["7-2000-C-T"].father_state == "absent"
    paternal_only = sites["7-3000-G-A"]
    assert paternal_only.cf_depth_estimated and not paternal_only.cf_present
    assert (paternal_only.cf_dp, paternal_only.cf_alt_reads, paternal_only.father_state) == (1500, 0, "hom_alt")
    assert sites["7-9000-G-A"].cf_dp is None
    # Paternity judges a site at the plasma's depth from its coverage, whether or not the
    # plasma called the allele: the call's own depth is known only where it shows.
    assert paternal_only.cf_coverage_dp == 1500
    assert both.cf_dp == 1500 and both.cf_coverage_dp is None  # outside every target
    assert sites["7-9000-G-A"].cf_coverage_dp is None
    inside = _record("7-3050-A-G", [_call(PLASMA, [1100, 100]), _call(FATHER, [150, 150])])
    called = build_nipt_observations([inside], father_sample_id=FATHER, cfdna_sample_id=PLASMA, plasma_depth=plasma_lookup)
    assert (called[0].cf_dp, called[0].cf_coverage_dp) == (1200, 1500)


# --------------------------------------------------------------------------- #
# One allele in two representations: an MNV in one VCF, its SNVs in the other
# --------------------------------------------------------------------------- #


def _sites(records, **kwargs):
    return {
        site.variant_id: site
        for site in build_nipt_observations(records, father_sample_id=FATHER, cfdna_sample_id=PLASMA, **kwargs)
    }


def test_the_fathers_mnv_carries_a_plasma_snv() -> None:
    plasma_lookup = TargetDepthLookup([TargetCoverageRow(chrom="7", start=900, end=1100, gene="GENEA", attribute="GENEA", mean=1500.0)])
    records = [
        # The fetus inherited the father's MNV; the plasma calls its two bases apart.
        _record("7-1000-A-G", [_call(PLASMA, [1380, 120])]),
        _record("7-1001-C-T", [_call(PLASMA, [1370, 130])]),
        _record("7-1000-AC-GT", [_call(FATHER, [150, 150])]),
    ]
    sites = _sites(records, plasma_depth=plasma_lookup)
    first = sites["7-1000-A-G"]
    assert first.representation == "father_call_other_representation"
    assert (first.father_state, first.father_vaf, first.father_alt_reads) == ("het", 0.5, 150)
    # The record his call was read from: the plasma's SNV's own record holds none of his.
    assert first.represented_by == "7-1000-AC-GT"
    # The father's MNV reads the plasma's calls of its bases (the weaker base's), and is
    # marked so it is no second piece of evidence.
    mnv = sites["7-1000-AC-GT"]
    assert mnv.representation == "plasma_call_other_representation"
    assert mnv.cf_present and not mnv.cf_depth_estimated
    assert (mnv.cf_alt_reads, mnv.cf_dp) == (120, 1500)
    assert mnv.represented_by == "7-1000-A-G"  # the weaker base's call


def test_a_plasma_mnv_the_father_calls_in_part() -> None:
    records = [
        # The plasma's MNV; the father calls one of its bases.
        _record("7-1000-AC-GT", [_call(PLASMA, [1380, 120])]),
        _record("7-1001-C-T", [_call(FATHER, [150, 150])]),
    ]
    sites = _sites(records)
    mnv = sites["7-1000-AC-GT"]
    assert (mnv.representation, mnv.father_state) == ("father_call_overlaps", "absent")
    # No call of his was read: none is named.
    assert mnv.represented_by is None
    snv = sites["7-1001-C-T"]
    # The plasma's MNV carries this base: the father's SNV is marked, not read off it.
    assert snv.representation == "plasma_call_other_representation"
    assert snv.cf_alt_reads == 120
    assert snv.represented_by == "7-1000-AC-GT"


def test_the_other_representation_is_looked_up_in_the_family_records() -> None:
    plasma_snv = _record("7-1000-A-G", [_call(PLASMA, [1380, 120])])
    fathers_mnv = _record("7-1000-AC-GT", [_call(FATHER, [150, 150])])
    # A filter kept the plasma's SNV, not the father's MNV.
    alone = _sites([plasma_snv])
    assert alone["7-1000-A-G"].father_state == "absent"
    looked_up = _sites([plasma_snv], counterpart_records=[plasma_snv, fathers_mnv])
    assert looked_up["7-1000-A-G"].father_state == "het"


def test_an_indel_has_no_other_representation() -> None:
    records = [
        _record("7-1000-AT-A", [_call(PLASMA, [1380, 120])]),
        _record("7-1000-A-G", [_call(FATHER, [150, 150])]),
    ]
    sites = _sites(records)
    assert sites["7-1000-AT-A"].representation is None
    assert sites["7-1000-AT-A"].father_state == "absent"


def test_triage_annotation() -> None:
    record = _record(
        "7-1000-A-G",
        [],
        annotations=[
            {"impact": "LOW", "gnomad_af": 0.0001},
            {"impact": "MODERATE", "spliceai_ds_ag": 0.3, "gnomad_popmax_af": 0.002},
        ],
    )
    annotation = triage_annotation(record)
    assert annotation.impact == "MODERATE"
    assert annotation.spliceai_max == 0.3
    assert annotation.max_population_af == 0.002
    assert annotation.is_novel
    assert not triage_annotation(_record("7-1-A-G", [], rsid="rs1")).is_novel
    assert not triage_annotation(_record("7-1-A-G", [], annotations=[{"rsid": "rs2"}])).is_novel


def test_triage_annotation_reads_the_parsers_population_frequencies() -> None:
    # The CSQ parser lifts only gnomad_af to the top level; VEP MAX_AF stays nested.
    record = _record(
        "7-1000-A-G",
        [],
        annotations=[
            {
                "impact": "MODERATE",
                "gnomad_af": 0.0001,
                "population_frequencies": {"gnomad_af": 0.0001, "gnomad_popmax_af": 0.03},
            }
        ],
    )
    assert triage_annotation(record).max_population_af == 0.03


# --------------------------------------------------------------------------- #
# The views over a per-sample callset (I/O mocked)
# --------------------------------------------------------------------------- #


def _ff_sites() -> list[SmallVariantRecord]:
    """60 paternal het alleles the fetus inherited (FF 0.20) and 60 it did not."""
    inherited = [
        _record(f"1-{1000 + i}-A-G", [_call(PLASMA, [1400 - (5 * (i % 5)), 140 + (5 * (i % 5))]), _call(FATHER, [150, 150])])
        for i in range(60)
    ]
    missed = [_record(f"1-{5000 + i}-A-G", [_call(FATHER, [150, 150])]) for i in range(60)]
    homs = [_record(f"1-{8000 + i}-A-G", [_call(PLASMA, [1350, 150]), _call(FATHER, [1, 299])]) for i in range(30)]
    return [*inherited, *missed, *homs]


def _gene_sites() -> list[SmallVariantRecord]:
    return [
        # GENEA: the mother's allele (inherited: VAF 0.49 at FF 0.20) and the father's (inherited).
        _record("7-100-A-G", [_call(PLASMA, [1020, 980])], genes=["GENEA"]),
        _record("7-200-C-T", [_call(PLASMA, [1350, 150]), _call(FATHER, [150, 150])], genes=["GENEA"]),
        # GENEB: the mother's allele, not inherited (0.39), and the father's, not inherited.
        _record("7-300-A-G", [_call(PLASMA, [1220, 780])], genes=["GENEB"]),
        _record("7-400-C-T", [_call(FATHER, [150, 150])], genes=["GENEB"]),
        # A de novo candidate: in the fetal window, no paternal call, a rare missense.
        _record(
            "7-500-G-A",
            [_call(PLASMA, [1360, 140])],
            genes=["GENEC"],
            annotations=[{"impact": "MODERATE", "gnomad_af": None}],
        ),
    ]


def _plasma_targets() -> list[TargetCoverageRow]:
    return [
        # Over every chr1 site of _ff_sites, called by the plasma or not.
        TargetCoverageRow(chrom="1", start=900, end=8100, gene="GENEZ", attribute="GENEZ", mean=1500.0, median=1500.0),
        TargetCoverageRow(chrom="7", start=350, end=450, gene="GENEB", attribute="GENEB", mean=1500.0, median=1500.0),
        # Over the de novo candidate: a call outside every target is none (off_target).
        TargetCoverageRow(chrom="7", start=480, end=520, gene="GENEC", attribute="GENEC", mean=1500.0, median=1500.0),
        TargetCoverageRow(chrom="7", start=0, end=120, gene="GENEA", attribute="GENEA;NM_1", mean=250.0, median=240.0,
                          proportion_covered=100.0),
    ]


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    filtered: list[SmallVariantRecord],
    targets=None,
    artifacts: set[str] | None = None,
    recurrence: dict[str, CfdnaRecurrence] | None = None,
    other_samples: int = 1,
) -> list[list[str]]:
    # Another family's plasma of the assay: what a variant's recurrence is counted in.
    counts = wire_nipt_reads(
        monkeypatch,
        family=nipt_family(family_id="NIPTFAM1", father=FATHER, plasma=PLASMA),
        cohort=_ff_sites() + filtered,
        filtered=filtered,
        artifacts=artifacts,
        sample_name_to_uuid={FATHER: "father-uuid", PLASMA: "cfdna-uuid"},
        carrier_samples=["OTHER1"],
        recurrence=recurrence,
        other_samples=other_samples,
    )

    async def fake_tracks(
        _assembly, *, sample_uuid=None, track_type=None, include_metadata=False, record_ids=None, **_kwargs
    ):
        assert track_type == "target_coverage"
        rows = targets if targets is not None else _plasma_targets()
        if sample_uuid != "cfdna-uuid":
            return []
        if record_ids:
            # As ClickHouse filters: the record_id (the target's gene), case-insensitive.
            wanted = {value.upper() for value in record_ids}
            rows = [row for row in rows if row.gene.upper() in wanted]
        return [
            {"chr": row.chrom, "start": row.start, "end": row.end, "record_id": row.gene, "value": row.mean,
             "metadata_json": '{"median": %s, "proportion_covered": %s}' % (
                 row.median if row.median is not None else "null",
                 row.proportion_covered if row.proportion_covered is not None else "null",
             ) if include_metadata else None}
            for row in rows
        ]

    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_tracks)
    return counts


async def _variants(**kwargs):
    return await get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "GENEA,GENEB,GENEC"},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_the_summary_reads_the_paternal_alleles_and_the_target_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=_gene_sites())
    summary = await get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]
    analysis = summary.analysis
    assert analysis.fetal_fraction.ff_computed == pytest.approx(0.20, abs=0.01)
    # 60 het alleles inherited, 60 + 1 not (GENEB's), 30 hom ones seen, each judged at the
    # plasma's depth from the targets. GENEA's paternal allele lies outside every target: the
    # plasma shows it, but had it not, there would be no depth to judge it at, so it does
    # not count either way.
    evidence = analysis.paternal_transmission
    assert (evidence.het_transmitted, evidence.het_not_transmitted) == (60, 61)
    assert (evidence.hom_alt_transmitted, evidence.hom_alt_not_transmitted) == (30, 0)
    assert evidence.without_plasma_depth == 1
    assert analysis.filter_counts["paternal_only"] == 61  # 60 missed + the GENEB paternal allele
    assert summary.target_coverage is not None and summary.target_coverage.targets == 4
    assert summary.sex_chromosomes is not None and summary.sex_chromosomes.profile == "no_chrY_targets"


@pytest.mark.asyncio
async def test_the_paternal_view_lists_what_the_fetus_inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=_gene_sites())
    inherited = await _variants(inheritance="paternal_dominant")
    assert [item.record.variant_id for item in inherited.variants] == ["7-200-C-T"]
    # The list says which samples it read as the plasma and the father.
    assert (inherited.trio.cfdna_sample_id, inherited.trio.father_sample_id) == (PLASMA, FATHER)
    everything = await _variants(inheritance="paternal_dominant", include_not_inherited=True)
    ids = [item.record.variant_id for item in everything.variants]
    assert ids == ["7-200-C-T", "7-400-C-T"]
    missed = everything.variants[1].classification
    assert missed.fetal_inheritance == "paternal_not_transmitted"
    assert missed.paternal_transmission_probability is not None and missed.paternal_transmission_probability < 1e-6


@pytest.mark.asyncio
async def test_the_maternal_view_lists_what_the_fetus_inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=_gene_sites())
    inherited = await _variants(inheritance="maternal_dominant")
    assert [item.record.variant_id for item in inherited.variants] == ["7-100-A-G"]
    everything = await _variants(inheritance="maternal_dominant", include_not_inherited=True)
    assert [item.record.variant_id for item in everything.variants] == ["7-100-A-G", "7-300-A-G"]


@pytest.mark.asyncio
async def test_the_recessive_view_gives_each_genes_fetal_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=_gene_sites())
    result = await _variants(inheritance="recessive_at_risk")
    genes = {gene.gene: gene for gene in result.recessive_genes}
    assert set(genes) == {"GENEA", "GENEB"}
    assert [gene.gene for gene in result.recessive_genes] == ["GENEA", "GENEB"]  # highest risk first
    assert genes["GENEA"].risk is not None and genes["GENEA"].risk > 0.9
    assert genes["GENEB"].risk is not None and genes["GENEB"].risk < 0.01
    assert {item.record.variant_id for item in result.variants} == {"7-100-A-G", "7-200-C-T", "7-300-A-G", "7-400-C-T"}


@pytest.mark.asyncio
async def test_the_de_novo_view_triages_the_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=_gene_sites())
    result = await _variants(inheritance="de_novo")
    [candidate] = result.variants
    assert candidate.record.variant_id == "7-500-G-A"
    assert candidate.de_novo is not None and candidate.de_novo.label == "high"
    assert "father_no_call" in candidate.classification.flags
    high_only = await _variants(inheritance="de_novo", de_novo_priority="high")
    assert len(high_only.variants) == 1
    with pytest.raises(HTTPException) as refused:
        await _variants(inheritance="de_novo", de_novo_priority="urgent")
    assert (refused.value.status_code, refused.value.detail) == (400, "Unsupported de novo priority: urgent")


async def _diagnostic(**kwargs):
    return (
        await get_family_nipt_diagnostic_candidates(
            session=None,  # type: ignore[arg-type]
            family_id="NIPTFAM1",
            user=None,  # type: ignore[arg-type]
            **kwargs,
        )
    ).candidates


def _annotated_gene_sites() -> list[SmallVariantRecord]:
    """The gene sites, the inherited maternal (7-100) and paternal (7-200) alleles rare and
    protein-altering, the paternal one the fetus did not inherit (7-400) too."""
    sites = _gene_sites()
    for index in (0, 1, 3):
        sites[index].annotations = [{"impact": "HIGH", "gnomad_af": 0.0002}]
    return sites


@pytest.mark.asyncio
async def test_the_locked_criteria_leave_the_rare_protein_altering_candidates_of_each_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # REQ-NIPT-020: de novo, paternal and maternal alleles the fetus inherited, rare (0.1%) and
    # protein-altering; the recessive genes whose parents both carry a qualifying allele.
    _wire(monkeypatch, filtered=_annotated_gene_sites())
    candidates = await _diagnostic()
    assert [c.item.record.variant_id for c in candidates.de_novo] == ["7-500-G-A"]
    assert (candidates.de_novo[0].confidence, candidates.de_novo[0].flags) == ("standard", [])
    assert [c.item.record.variant_id for c in candidates.paternal] == ["7-200-C-T"]
    assert [c.item.record.variant_id for c in candidates.maternal] == ["7-100-A-G"]
    # GENEA: both parents carry a qualifying allele. GENEB: the mother's (7-300) changes no
    # protein, so only the father carries one.
    assert [gene.gene for gene in candidates.recessive_genes] == ["GENEA"]
    assert candidates.criteria.reference.startswith("R NIPT-M v0.7.3")
    # Without an annotation the paternal and maternal alleles change no protein: left out.
    bare = await _diagnostic_without_annotation(monkeypatch)
    assert (bare.paternal, bare.maternal, bare.recessive_genes) == ([], [], [])


async def _diagnostic_without_annotation(monkeypatch: pytest.MonkeyPatch):
    _wire(monkeypatch, filtered=_gene_sites())
    return await _diagnostic()


@pytest.mark.asyncio
async def test_a_de_novo_candidate_in_another_plasma_stays_only_when_clinvar_calls_it_pathogenic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recurrent = {"7-500-G-A": CfdnaRecurrence(carriers=1)}
    _wire(monkeypatch, filtered=_annotated_gene_sites(), recurrence=recurrent)
    assert (await _diagnostic()).de_novo == []
    # A conflicting record is no strict ClinVar call (R): still left out.
    sites = _annotated_gene_sites()
    sites[4].annotations = [{"impact": "MODERATE", "clinvar": "Conflicting_classifications_of_pathogenicity"}]
    _wire(monkeypatch, filtered=sites, recurrence=recurrent)
    assert (await _diagnostic()).de_novo == []
    sites[4].annotations = [{"impact": "MODERATE", "clinvar": "Pathogenic"}]
    _wire(monkeypatch, filtered=sites, recurrence=recurrent)
    [candidate] = (await _diagnostic()).de_novo
    assert candidate.flags == ["clinvar_pathogenic"]


@pytest.mark.asyncio
async def test_a_de_novo_indel_in_a_repeat_is_a_low_confidence_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    sites = _annotated_gene_sites()
    sites[4] = _record(
        "7-500-G-GCAGCAG",
        [_call(PLASMA, [1360, 140])],
        genes=["GENEC"],
        annotations=[{"impact": "MODERATE", "gnomad_af": None}],
    )
    _wire(monkeypatch, filtered=sites)
    [candidate] = (await _diagnostic()).de_novo
    assert (candidate.confidence, candidate.flags) == ("low", ["repeat_context"])


@pytest.mark.asyncio
async def test_a_call_outside_every_capture_target_is_no_de_novo_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without the GENEC target the candidate's call lies outside every target of the plasma's
    # coverage table: it stays in the list, flagged off_target, and is no de novo candidate.
    targets = [row for row in _plasma_targets() if row.gene != "GENEC"]
    _wire(monkeypatch, filtered=_gene_sites(), targets=targets)
    assert (await _variants(inheritance="de_novo")).variants == []
    listed = {item.record.variant_id: item for item in (await _variants()).variants}
    assert "off_target" in listed["7-500-G-A"].classification.flags
    assert listed["7-500-G-A"].site is not None and listed["7-500-G-A"].site.in_target is False
    # A site over a target is not flagged (the GENEB paternal allele, at 7:400).
    assert "off_target" not in listed["7-400-C-T"].classification.flags


def test_a_record_is_in_a_capture_target_when_its_ref_span_overlaps_one() -> None:
    # The R pipeline's rule: a deletion whose padding base lies just before a target overlaps it.
    lookup = TargetDepthLookup(
        [TargetCoverageRow(chrom="7", start=100, end=200, gene="GENEA", attribute="GENEA", mean=900.0, median=900.0)]
    )
    sites = {
        site.variant_id: site
        for site in build_nipt_observations(
            [
                _record("7-100-ACG-A", [_call(PLASMA, [900, 100])]),  # REF 100-102: over the target's first base
                _record("7-100-A-G", [_call(PLASMA, [900, 100])]),  # BED start 100 is outside (start < pos)
                _record("7-150-C-T", [_call(PLASMA, [900, 100])]),
            ],
            father_sample_id=FATHER,
            cfdna_sample_id=PLASMA,
            plasma_depth=lookup,
        )
    }
    assert {variant_id: site.in_target for variant_id, site in sites.items()} == {
        "7-100-ACG-A": True,
        "7-100-A-G": False,
        "7-150-C-T": True,
    }
    # A joint VCF, or no coverage table: unknown, and read as before.
    [joint] = build_nipt_observations(
        [_record("7-150-C-T", [_call(PLASMA, [900, 100]), _call(FATHER, [50, 0])])],
        father_sample_id=FATHER,
        cfdna_sample_id=PLASMA,
    )
    assert joint.in_target is None


@pytest.mark.asyncio
async def test_a_de_novo_candidate_another_pregnancy_carries(monkeypatch: pytest.MonkeyPatch) -> None:
    # Another family's plasma of the assay has the call: a recurrent artefact, unless a
    # ClinVar record may call it pathogenic (a de novo hotspot recurs between pregnancies).
    _wire(monkeypatch, filtered=_gene_sites(), recurrence={"7-500-G-A": CfdnaRecurrence(carriers=1)})
    assert (await _variants(inheritance="de_novo")).variants == []
    sites = _gene_sites()
    sites[4].annotations = [{"impact": "MODERATE", "clinvar": "Pathogenic"}]
    _wire(monkeypatch, filtered=sites, recurrence={"7-500-G-A": CfdnaRecurrence(carriers=1)})
    [candidate] = (await _variants(inheritance="de_novo")).variants
    assert candidate.de_novo is not None and candidate.de_novo.label == "high"
    assert "ClinVar pathogenic: not excluded as recurrent" in candidate.de_novo.reasons


@pytest.mark.asyncio
async def test_each_listed_variant_carries_its_recurrence_counted_once_for_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 7-500-G-A is an unexplained low-VAF call in 3 of the 20 other cfDNA samples of the
    # assay: the mark of a recurrent artefact. The others recur in none.
    recurrent = CfdnaRecurrence(
        carriers=4, maternal=1, paternal_low_vaf=0, unexplained_low_vaf=3, unexplained_median_vaf=0.012
    )
    counts = _wire(monkeypatch, filtered=_gene_sites(), recurrence={"7-500-G-A": recurrent}, other_samples=20)
    result = await _variants(page_size=2)
    assert [item.record.variant_id for item in result.variants] == ["7-100-A-G", "7-200-C-T"]
    # Without the filter only the page is counted, in one count.
    assert counts == [["7-100-A-G", "7-200-C-T"]]
    for item in result.variants:
        assert item.recurrence is not None
        assert (item.recurrence.other_samples, item.recurrence.counts) == (20, CfdnaRecurrence())
        assert item.recurrence.protection is None
    [candidate] = (await _variants(inheritance="de_novo", de_novo_priority="low")).variants or [None]
    # The de novo view leaves out a candidate another plasma carries.
    assert candidate is None
    whole = await _variants(page=3, page_size=2)
    [last] = whole.variants
    assert last.record.variant_id == "7-500-G-A"
    assert last.recurrence is not None and last.recurrence.counts == recurrent


@pytest.mark.asyncio
async def test_the_recurrence_filter_leaves_out_unexplained_low_vaf_calls_that_recur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recurrence = {
        # An unexplained low-VAF call in 3 other plasmas: an artefact's mark.
        "7-500-G-A": CfdnaRecurrence(carriers=3, unexplained_low_vaf=3, unexplained_median_vaf=0.012),
        # The father's allele in 5 other pregnancies, and a maternal allele in 9: explained.
        "7-200-C-T": CfdnaRecurrence(carriers=5, paternal_low_vaf=5),
        "7-300-A-G": CfdnaRecurrence(carriers=9, maternal=9),
    }
    counts = _wire(monkeypatch, filtered=_gene_sites(), recurrence=recurrence, other_samples=20)

    def listed(result) -> list[str]:
        return [item.record.variant_id for item in result.variants]

    every = ["7-100-A-G", "7-200-C-T", "7-300-A-G", "7-400-C-T", "7-500-G-A"]
    assert listed(await _variants()) == every
    counts.clear()
    hidden = await _variants(max_unexplained_recurrence=2)
    assert listed(hidden) == every[:-1]
    assert hidden.total == 4
    # The filter counts every classified variant before the page is cut, in one count.
    assert counts == [every]
    assert listed(await _variants(max_unexplained_recurrence=3)) == every
    assert listed(await _variants(max_unexplained_recurrence=0)) == every[:-1]

    # A ClinVar record that may call it pathogenic keeps it listed, whatever its count: a de
    # novo hotspot recurs between pregnancies.
    sites = _gene_sites()
    sites[4].annotations = [{"impact": "MODERATE", "clinvar": "Pathogenic"}]
    _wire(monkeypatch, filtered=sites, recurrence=recurrence, other_samples=20)
    kept = await _variants(max_unexplained_recurrence=0)
    assert listed(kept) == every
    assert kept.variants[-1].recurrence is not None and kept.variants[-1].recurrence.protection == "clinvar"

    with pytest.raises(HTTPException) as refused:
        await _variants(max_unexplained_recurrence=-1)
    assert refused.value.status_code == 400


def test_what_the_recurrence_filter_leaves_out() -> None:
    record = _record("7-1-A-G", [])

    def item(unexplained: int, protection: str | None) -> nipt_service.NiptClassifiedVariant:
        return nipt_service.NiptClassifiedVariant(
            record=record,
            classification=None,  # type: ignore[arg-type]
            recurrence=nipt_service.NiptRecurrence(
                other_samples=10,
                counts=CfdnaRecurrence(carriers=unexplained + 4, maternal=4, unexplained_low_vaf=unexplained),
                protection=protection,
            ),
        )

    assert nipt_service.hidden_as_recurrent(item(3, None), 2)
    assert not nipt_service.hidden_as_recurrent(item(2, None), 2)
    assert not nipt_service.hidden_as_recurrent(item(3, "common"), 2)
    with pytest.raises(RuntimeError):
        nipt_service.hidden_as_recurrent(
            nipt_service.NiptClassifiedVariant(record=record, classification=None),  # type: ignore[arg-type]
            2,
        )


@pytest.mark.asyncio
async def test_a_listed_artefact_the_familys_annotation_protects_stays_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # The artefact table was imported before any family carrying these alleles was
    # annotated, so the list holds them unchecked. The family's own annotation protects the
    # de novo candidate (a ClinVar pathogenic record), not the maternal allele.
    sites = _gene_sites()
    sites[4].annotations = [{"impact": "MODERATE", "clinvar": "Pathogenic"}]
    _wire(monkeypatch, filtered=sites, artifacts={"7-500-G-A", "7-300-A-G"})
    result = await _variants()
    listed = {item.record.variant_id: item for item in result.variants}
    assert "7-300-A-G" not in listed
    assert "artifact_list_protected" in listed["7-500-G-A"].classification.flags
    summary = await get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]
    assert summary.analysis.filter_counts["failed_artifact"] == 1


def test_what_protects_a_listed_artefact() -> None:
    assert artifact_protection(_record("7-1-A-G", [], annotations=[{"clinvar": "Likely_pathogenic"}])) == "clinvar"
    assert artifact_protection(
        _record("7-1-A-G", [], annotations=[{"clinvar": "Conflicting_classifications_of_pathogenicity"}])
    ) == "clinvar"
    assert artifact_protection(
        _record("7-1-A-G", [], annotations=[{"population_frequencies": {"gnomad_popmax_af": 0.2}}])
    ) == "common"
    assert artifact_protection(_record("7-1-A-G", [], annotations=[{"clinvar": "Benign", "gnomad_af": 0.01}])) is None
    removed, protected = family_artifact_ids(
        [
            _record("7-1-A-G", [], annotations=[{"clinvar": "Pathogenic"}]),
            _record("7-2-A-G", []),
            _record("7-3-A-G", []),
        ],
        {"7-1-A-G": 10, "7-2-A-G": 10, "7-9-A-G": 10},
        father_sample_id=FATHER,
        cfdna_sample_id=PLASMA,
        qc=NiptQualityThresholds(),
    )
    assert (removed, protected) == ({"7-2-A-G"}, {"7-1-A-G": "clinvar"})


def _hc_call(sample: str, ad: list[int], *, gt: str = "0/1", qual: float = 812.0, gq: float = 99.0) -> SmallVariantCall:
    """A HaplotypeCaller call: a GT, a GQ and the record's QUAL, no TLOD."""
    return SmallVariantCall(
        sample=sample, gt=gt, gq=gq, dp=sum(ad), af=[], ad=ad, ps=None, filters=["PASS"], metrics={"QUAL": qual},
    )


def test_a_listed_allele_the_father_carries_stays_in_when_it_recurs_in_few_plasmas() -> None:
    # The R NIPT-M v0.7.3 rule: a call the father carries, at a fetal-like allele fraction
    # (at most 0.30) and in at most 3 cfDNA samples, is his allele (a founder allele), not
    # a recurrent artefact.
    qc = NiptQualityThresholds()
    father_het = _hc_call(FATHER, [30, 28])
    records = [
        _record("7-1-A-G", [_call(PLASMA, [1350, 150]), father_het]),  # fetal window, 3 samples
        _record("7-2-A-G", [_call(PLASMA, [1350, 150]), father_het]),  # 4 samples: an artefact
        _record("7-3-A-G", [_call(PLASMA, [980, 520]), father_het]),  # 0.35: a maternal fraction
        _record("7-4-A-G", [father_het]),  # no plasma call: his allele, not transmitted
        _record("7-5-A-G", [_call(PLASMA, [1350, 150]), _hc_call(FATHER, [30, 28], qual=31.0)]),  # QUAL < 50
        _record("7-6-A-G", [_call(PLASMA, [1350, 150]), _call(FATHER, [150, 150])]),  # Mutect2 het father
        _record("7-7-A-G", [_call(PLASMA, [1350, 150]), _call(FATHER, [255, 45])]),  # 15%: low_vaf
        _record("7-8-A-G", [_call(PLASMA, [1350, 150])]),  # no father call
    ]
    listed = {"7-1-A-G": 3, "7-2-A-G": 4, "7-3-A-G": 1, "7-4-A-G": 0, "7-5-A-G": 1, "7-6-A-G": 2,
              "7-7-A-G": 1, "7-8-A-G": 1}
    removed, protected = family_artifact_ids(
        records, listed, father_sample_id=FATHER, cfdna_sample_id=PLASMA, qc=qc
    )
    assert protected == {"7-1-A-G": "paternal_carrier", "7-4-A-G": "paternal_carrier", "7-6-A-G": "paternal_carrier"}
    assert removed == {"7-2-A-G", "7-3-A-G", "7-5-A-G", "7-7-A-G", "7-8-A-G"}
    # The family's annotation comes first: a ClinVar record names its own reason.
    clinvar = _record("7-9-A-G", [_call(PLASMA, [1350, 150]), father_het], annotations=[{"clinvar": "Pathogenic"}])
    assert listed_artifact_protection(clinvar, 1, father_sample_id=FATHER, cfdna_sample_id=PLASMA, qc=qc) == "clinvar"


@pytest.mark.asyncio
async def test_a_call_failing_the_quality_filter_is_listed_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    weak = _record("7-700-A-G", [_call(PLASMA, [980, 1020], tlod=5.0)], genes=["GENEA"])
    _wire(monkeypatch, filtered=[*_gene_sites(), weak])
    listed = {item.record.variant_id: item for item in (await _variants()).variants}
    assert listed["7-700-A-G"].classification.quality_failures == ["low_quality"]
    assert "quality:low_quality" in listed["7-700-A-G"].classification.flags
    summary = await get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]
    assert summary.analysis.filter_counts["failed_quality"] == 1
    assert summary.analysis.quality_failure_counts == {"low_quality": 1}


@pytest.mark.asyncio
async def test_the_paternal_view_lists_an_allele_the_plasma_calls_in_part(monkeypatch: pytest.MonkeyPatch) -> None:
    # The father's MNV; the plasma calls one of its two bases. Whether the fetus inherited
    # his allele is uncertain, so the view lists it, flagged, though no read shows it.
    targets = [
        *_plasma_targets(),
        TargetCoverageRow(chrom="7", start=590, end=610, gene="GENED", attribute="GENED", mean=1500.0, median=1500.0),
    ]
    fathers_mnv = _record("7-600-AC-GT", [_call(FATHER, [150, 150])], genes=["GENED"])
    plasma_snv = _record("7-601-C-T", [_call(PLASMA, [1350, 150])], genes=["GENED"])
    _wire(monkeypatch, filtered=[*_gene_sites(), fathers_mnv, plasma_snv], targets=targets)
    listed = {item.record.variant_id: item for item in (await _variants(inheritance="paternal_dominant")).variants}
    assert "plasma_call_overlaps" in listed["7-600-AC-GT"].classification.flags
    # The plasma's SNV reads the father's genotype off his MNV: a paternal allele, inherited.
    assert "father_call_other_representation" in listed["7-601-C-T"].classification.flags
    assert listed["7-601-C-T"].classification.category == 7
    # The list names the record his call was read from, with his reads, so the page can show
    # them where the SNV's own record holds no call of his; the MNV reads no plasma call.
    from backend.app.routers.families_nipt import _nipt_variant_out

    snv = _nipt_variant_out(listed["7-601-C-T"]).nipt
    assert (snv.father_call_variant_id, snv.cf_call_variant_id) == ("7-600-AC-GT", None)
    assert (snv.father_state, snv.father_alt_reads, snv.father_depth) == ("het", 150, 300)
    mnv = _nipt_variant_out(listed["7-600-AC-GT"]).nipt
    assert (mnv.father_call_variant_id, mnv.cf_call_variant_id) == (None, None)


@pytest.mark.asyncio
async def test_the_quality_checks_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.routers.families_nipt import _qc_out

    sex_targets = [
        TargetCoverageRow(chrom="X", start=0, end=100, gene="GENEX", attribute="GENEX", mean=1300.0, median=1300.0),
        TargetCoverageRow(chrom="Y", start=0, end=100, gene="GENEY", attribute="GENEY", mean=0.0, median=0.0),
    ]
    _wire(monkeypatch, filtered=_gene_sites(), targets=[*_plasma_targets(), *sex_targets])
    qc = _qc_out(await get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None))  # type: ignore[arg-type]
    assert qc.paternity.status == "pass"
    assert (qc.paternity.het_transmitted, qc.paternity.het_not_transmitted) == (60, 61)
    # No paternal-X site; no chrY signal at an FF of 20%: female, by the chrY coverage.
    assert (qc.fetal_sex.paternal_x, qc.fetal_sex.chry_profile, qc.fetal_sex.call) == (
        "indeterminate", "female_no_chrY_signal", "female"
    )
    assert qc.plasma_profile_status == "pass"
    # The verdicts come from the backend (nipt_quality_checks), as the signed record freezes
    # them.
    assert (qc.fetal_fraction_status, qc.fetal_sex.status) == ("pass", "pass")
    assert qc.de_novo_window is not None and qc.target_coverage is not None
    assert qc.model.reference.startswith("R NIPT-M v0.7.3")
    # chrY at the autosomal level and a low chrX: male DNA, not maternal plasma.
    male_targets = [
        TargetCoverageRow(chrom="X", start=0, end=100, gene="GENEX", attribute="GENEX", mean=500.0, median=500.0),
        TargetCoverageRow(chrom="Y", start=0, end=100, gene="GENEY", attribute="GENEY", mean=1000.0, median=1000.0),
    ]
    _wire(monkeypatch, filtered=_gene_sites(), targets=[*_plasma_targets(), *male_targets])
    male = _qc_out(await get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None))  # type: ignore[arg-type]
    assert male.plasma_profile_status == "fail"
    assert male.fetal_sex.chry_profile == "male_like_not_maternal_plasma"
    # Neither signal tells the fetal sex then: a warning.
    assert (male.fetal_sex.call, male.fetal_sex.status) == ("indeterminate", "warn")


@pytest.mark.asyncio
async def test_without_a_plasma_coverage_table_paternity_cannot_be_assessed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without the plasma's per-target coverage, an allele it did not show has no depth to be
    # judged at. Counting only the alleles it did show would read every rate near 100%: the
    # het ones failed as "too many", the homozygous ones passed whoever the father is.
    from backend.app.routers.families_nipt import _qc_out

    _wire(monkeypatch, filtered=_gene_sites(), targets=[])
    summary = await get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]
    evidence = summary.analysis.paternal_transmission
    assert (evidence.het_transmitted, evidence.het_not_transmitted) == (0, 0)
    assert (evidence.hom_alt_transmitted, evidence.hom_alt_not_transmitted) == (0, 0)
    # His 120 + 2 het alleles and 30 homozygous ones, the plasma's call or none.
    assert evidence.without_plasma_depth == 152
    qc = _qc_out(summary)
    assert qc.paternity.status == "warn"
    assert qc.paternity.message.startswith("Too few paternal-informative sites (0 homozygous, 0 het)")
    assert "The plasma's depth is not known at 152 of the father's sites" in qc.paternity.message


@pytest.mark.asyncio
async def test_the_coverage_page_reads_every_gene_and_a_gene_s_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, filtered=[])
    fetch = nipt_service.fetch_interval_track_rows
    asked: list[object] = []

    async def recording_fetch(*args, **kwargs):
        asked.append(kwargs.get("record_ids"))
        return await fetch(*args, **kwargs)

    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", recording_fetch)
    coverage = await get_family_nipt_coverage(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        all_genes=True,
    )
    assert coverage.targets is not None
    # The weak GENEA and the passing GENEB, GENEC and GENEZ alike.
    assert {gene.gene: gene.weak_targets for gene in coverage.targets.genes} == {
        "GENEA": 1,
        "GENEB": 0,
        "GENEC": 0,
        "GENEZ": 0,
    }

    weak = await get_family_nipt_gene_targets(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        gene="genea",
    )
    [target] = weak.rows
    assert (target.gene, target.chrom, target.start, target.end) == ("GENEA", "7", 0, 120)
    assert target.is_weak(weak.critical_mean_depth)  # mean 250x, below 300x
    # Only that gene's rows are read, not the panel's whole table.
    assert asked[-1] == ["genea"]
    passing = await get_family_nipt_gene_targets(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        gene="GENEB",
    )
    assert [row.is_weak(passing.critical_mean_depth) for row in passing.rows] == [False]
    # A gene the panel does not capture has no target.
    absent = await get_family_nipt_gene_targets(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        gene="NOTAGENE",
    )
    assert absent.rows == []


@pytest.mark.asyncio
async def test_the_coverage_reads_the_target_table(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=[])
    coverage = await get_family_nipt_coverage(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        gene="GENEA",
    )
    assert coverage.targets is not None
    assert coverage.targets.targets == 1
    [gene] = coverage.targets.genes
    assert (gene.gene, gene.weak_targets) == ("GENEA", 1)  # mean 250x, below 300x
    assert coverage.regions.target_region_count == 0


@pytest.mark.asyncio
async def test_the_coverage_of_a_panel_reads_the_targets_of_its_genes(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=[])
    recorded: list[tuple[str, str]] = []

    async def record_use(_session, *, context, panel_id, user, search):
        recorded.append((panel_id, search))

    async def panel_constraints(_session, panel_id, *, assembly_id):
        return PanelFilterConstraints(genes=("GENEB",))

    monkeypatch.setattr(nipt_service, "record_family_panel_use", record_use)
    monkeypatch.setattr(nipt_service, "_fetch_panel_constraints", panel_constraints)
    coverage = await get_family_nipt_coverage(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        panel_id="panel-1",
    )

    # On the family's clinical audit trail (REQ-CARR-003), and the panel's genes' targets only.
    assert recorded == [("panel-1", "nipt_coverage")]
    assert coverage.targets is not None and coverage.targets.targets == 1
    assert [(gene.gene, gene.targets, gene.weak_targets) for gene in coverage.targets.genes] == [("GENEB", 1, 0)]
