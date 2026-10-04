"""The NIPT service on a per-sample (two-VCF) callset: the father's genotype from his allele
depths, a paternal allele the plasma did not show, the site depth of a split multi-allelic
record, and the paternal, maternal, de novo and recessive views. Synthetic data only."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.schemas import FamilyMemberOut, FamilyOut
from backend.app.services import nipt_service
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.nipt_service import (
    artifact_protection,
    build_nipt_observations,
    derive_father_state,
    family_artifact_ids,
    get_family_nipt_coverage,
    get_family_nipt_summary,
    get_family_nipt_variants,
    restored_site_depths,
    triage_annotation,
)
from backend.app.services.nipt_target_coverage import TargetCoverageRow, TargetDepthLookup

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
    # The father's MNV reads the plasma's calls of its bases (the weaker base's), and is
    # marked so it is no second piece of evidence.
    mnv = sites["7-1000-AC-GT"]
    assert mnv.representation == "plasma_call_other_representation"
    assert mnv.cf_present and not mnv.cf_depth_estimated
    assert (mnv.cf_alt_reads, mnv.cf_dp) == (120, 1500)


def test_a_plasma_mnv_the_father_calls_in_part() -> None:
    records = [
        # The plasma's MNV; the father calls one of its bases.
        _record("7-1000-AC-GT", [_call(PLASMA, [1380, 120])]),
        _record("7-1001-C-T", [_call(FATHER, [150, 150])]),
    ]
    sites = _sites(records)
    mnv = sites["7-1000-AC-GT"]
    assert (mnv.representation, mnv.father_state) == ("father_call_overlaps", "absent")
    snv = sites["7-1001-C-T"]
    # The plasma's MNV carries this base: the father's SNV is marked, not read off it.
    assert snv.representation == "plasma_call_other_representation"
    assert snv.cf_alt_reads == 120


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


def _family() -> FamilyOut:
    return FamilyOut(
        id="family-uuid",
        family_id="NIPTFAM1",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id=FATHER, role="father", affected=False),
            FamilyMemberOut(sample_id=PLASMA, role="mother", affected=False, sample_metadata={"assay": "nipt_cfdna"}),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
    )


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
        TargetCoverageRow(chrom="1", start=4900, end=5200, gene="GENEZ", attribute="GENEZ", mean=1500.0, median=1500.0),
        TargetCoverageRow(chrom="7", start=350, end=450, gene="GENEB", attribute="GENEB", mean=1500.0, median=1500.0),
        TargetCoverageRow(chrom="7", start=0, end=120, gene="GENEA", attribute="GENEA;NM_1", mean=250.0, median=240.0,
                          proportion_covered=100.0),
    ]


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    filtered: list[SmallVariantRecord],
    targets=None,
    artifacts: set[str] | None = None,
    other_carriers: dict[str, int] | None = None,
) -> None:
    cohort = _ff_sites() + filtered

    async def fake_family_record(_session, _family_id, _user):
        return _family()

    async def fake_context(_session, *, family_identifier, user, project_id=None):
        return SimpleNamespace(
            assembly_id="assembly-uuid", assembly_name="GRCh38", family_uuid="family-uuid",
            sample_name_to_uuid={FATHER: "father-uuid", PLASMA: "cfdna-uuid"},
        )

    async def fake_fetch(_context, filters, *, limit=None, **_kwargs):
        return cohort if filters.gene is None else (filtered if limit is None else filtered[:limit])

    async def fake_tracks(_assembly, *, sample_uuid=None, track_type=None, include_metadata=False, **_kwargs):
        assert track_type == "target_coverage"
        rows = targets if targets is not None else _plasma_targets()
        if sample_uuid != "cfdna-uuid":
            return []
        return [
            {"chr": row.chrom, "start": row.start, "end": row.end, "record_id": row.gene, "value": row.mean,
             "metadata_json": '{"median": %s, "proportion_covered": %s}' % (
                 row.median if row.median is not None else "null",
                 row.proportion_covered if row.proportion_covered is not None else "null",
             ) if include_metadata else None}
            for row in rows
        ]

    async def listed_artifacts(_session, *, assembly_id, assay_key):
        return set(artifacts or ())

    async def no_hydration(_session, *, context, variants):
        return None

    async def carriers(_session, *, assay_key):
        return {"other-cfdna": "OTHER1"}

    async def recurrence(_assembly, variant_ids, *, carrier_samples, exclude_family_uuid):
        assert exclude_family_uuid == "family-uuid"
        return {variant_id: count for variant_id, count in (other_carriers or {}).items() if variant_id in variant_ids}

    monkeypatch.setattr(nipt_service, "get_family_record", fake_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_context)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fake_fetch)
    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_tracks)
    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", listed_artifacts)
    monkeypatch.setattr(nipt_service, "_hydrate_small_variant_outs", no_hydration)
    monkeypatch.setattr(nipt_service, "assay_cfdna_carrier_samples", carriers)
    monkeypatch.setattr(nipt_service, "count_cfdna_carriers", recurrence)


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
    # 60 + 1 het alleles inherited (GENEA's), 60 + 1 not (GENEB's; their plasma depth from
    # the targets), 30 hom ones seen.
    evidence = analysis.paternal_transmission
    assert (evidence.het_transmitted, evidence.het_not_transmitted) == (61, 61)
    assert (evidence.hom_alt_transmitted, evidence.hom_alt_not_transmitted) == (30, 0)
    assert analysis.filter_counts["paternal_only"] == 61  # 60 missed + the GENEB paternal allele
    assert summary.target_coverage is not None and summary.target_coverage.targets == 3
    assert summary.sex_chromosomes is not None and summary.sex_chromosomes.profile == "no_chrY_targets"


@pytest.mark.asyncio
async def test_the_paternal_view_lists_what_the_fetus_inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, filtered=_gene_sites())
    inherited = await _variants(inheritance="paternal_dominant")
    assert [item.record.variant_id for item in inherited.variants] == ["7-200-C-T"]
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
    with pytest.raises(Exception):
        await _variants(inheritance="de_novo", de_novo_priority="urgent")


@pytest.mark.asyncio
async def test_a_de_novo_candidate_another_pregnancy_carries(monkeypatch: pytest.MonkeyPatch) -> None:
    # Another family's plasma of the assay has the call: a recurrent artefact, unless a
    # ClinVar record may call it pathogenic (a de novo hotspot recurs between pregnancies).
    _wire(monkeypatch, filtered=_gene_sites(), other_carriers={"7-500-G-A": 1})
    assert (await _variants(inheritance="de_novo")).variants == []
    sites = _gene_sites()
    sites[4].annotations = [{"impact": "MODERATE", "clinvar": "Pathogenic"}]
    _wire(monkeypatch, filtered=sites, other_carriers={"7-500-G-A": 1})
    [candidate] = (await _variants(inheritance="de_novo")).variants
    assert candidate.de_novo is not None and candidate.de_novo.label == "high"
    assert "ClinVar pathogenic: not excluded as recurrent" in candidate.de_novo.reasons


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
        {"7-1-A-G", "7-2-A-G", "7-9-A-G"},
    )
    assert (removed, protected) == ({"7-2-A-G"}, {"7-1-A-G": "clinvar"})


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
    assert (qc.paternity.het_transmitted, qc.paternity.het_not_transmitted) == (61, 61)
    # No paternal-X site; no chrY signal at an FF of 20%: female, by the chrY coverage.
    assert (qc.fetal_sex.paternal_x, qc.fetal_sex.chry_profile, qc.fetal_sex.call) == (
        "indeterminate", "female_no_chrY_signal", "female"
    )
    assert qc.plasma_profile_status == "pass"
    assert qc.de_novo_window is not None and qc.target_coverage is not None
    assert qc.model.reference.startswith("R NIPT-M v0.5.1")
    # chrY at the autosomal level and a low chrX: male DNA, not maternal plasma.
    male_targets = [
        TargetCoverageRow(chrom="X", start=0, end=100, gene="GENEX", attribute="GENEX", mean=500.0, median=500.0),
        TargetCoverageRow(chrom="Y", start=0, end=100, gene="GENEY", attribute="GENEY", mean=1000.0, median=1000.0),
    ]
    _wire(monkeypatch, filtered=_gene_sites(), targets=[*_plasma_targets(), *male_targets])
    male = _qc_out(await get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None))  # type: ignore[arg-type]
    assert male.plasma_profile_status == "fail"
    assert male.fetal_sex.chry_profile == "male_like_not_maternal_plasma"


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
