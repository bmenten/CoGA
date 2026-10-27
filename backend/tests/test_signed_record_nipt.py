"""The NIPT section of the signed analysis record (``signed_record_nipt``).

A monogenic NIPT family is signed out through the family sign-out; its record also holds
``nipt``: the NIPT analysis as the NIPT page shows it (the fetal fraction, the quality checks
with their verdicts, the coverage, the category counts and the funnel), each reported
variant as the NIPT variant list classifies it (with its de novo triage and the recessive
fetal risk of its gene), and the inputs it ran with (the per-target coverage tables, the
artifact list, the model). The section must say what the page said, computed by the same
code, and survive the JSONB round trip its content hash is checked after. Synthetic data
only: a per-sample callset of a father and a maternal plasma at a fetal fraction of 20%.
"""

from __future__ import annotations

import dataclasses
import json
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError, ProgrammingError

from backend.app.schemas import FamilyMemberOut, FamilyOut
from backend.app.services import nipt_service
from backend.app.services import report_signout_service as rss
from backend.app.services import sample_integrity_service
from backend.app.services import signed_record_nipt as srn
from backend.app.services import signed_record_sections as srs
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.hash_chain import canonical_hash
from backend.app.services.nipt_analysis import NIPT_MODEL_REFERENCE, NiptQualityThresholds
from backend.app.services.nipt_target_coverage import TargetCoverageRow
from backend.app.services.sample_integrity_qc import evaluate_nipt_category_qc

FATHER = "FATHER1"
PLASMA = "CFDNA1"
FAMILY_UUID = "family-uuid"


def _call(sample: str, ad: list[int], *, tlod: float = 300.0) -> SmallVariantCall:
    return SmallVariantCall(
        sample=sample, gt="0/1", gq=None, dp=sum(ad), af=[], ad=ad, ps=None, filters=["PASS"], metrics={"TLOD": tlod}
    )


def _record(
    variant_id: str,
    calls: list[SmallVariantCall],
    *,
    genes: list[str] | None = None,
    annotations: list[dict] | None = None,
    source: str = "nipt",
) -> SmallVariantRecord:
    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos), ref=ref, alt=alt,
        source=source, rsid=None, filters=[], gene_symbols=genes or [], annotations=annotations or [],
        calls=calls, qual=None,
    )


def _ff_sites() -> list[SmallVariantRecord]:
    """60 paternal het alleles the fetus inherited (FF 0.20), 60 it did not, 30 hom ones seen."""
    inherited = [
        _record(f"1-{1000 + i}-A-G", [_call(PLASMA, [1400 - 5 * (i % 5), 140 + 5 * (i % 5)]), _call(FATHER, [150, 150])])
        for i in range(60)
    ]
    missed = [_record(f"1-{5000 + i}-A-G", [_call(FATHER, [150, 150])]) for i in range(60)]
    homs = [_record(f"1-{8000 + i}-A-G", [_call(PLASMA, [1350, 150]), _call(FATHER, [1, 299])]) for i in range(30)]
    return [*inherited, *missed, *homs]


def _gene_sites() -> list[SmallVariantRecord]:
    return [
        # GENEA: the mother's allele (inherited) and the father's (inherited).
        _record("7-100-A-G", [_call(PLASMA, [1020, 980])], genes=["GENEA"]),
        _record("7-200-C-T", [_call(PLASMA, [1350, 150]), _call(FATHER, [150, 150])], genes=["GENEA"]),
        # GENEB: the mother's allele, not inherited, and the father's, not inherited.
        _record("7-300-A-G", [_call(PLASMA, [1220, 780])], genes=["GENEB"]),
        _record("7-400-C-T", [_call(FATHER, [150, 150])], genes=["GENEB"]),
        # A de novo candidate: in the fetal window, no paternal call, a rare missense.
        _record("7-500-G-A", [_call(PLASMA, [1360, 140])], genes=["GENEC"], annotations=[{"impact": "MODERATE"}]),
    ]


def _paternal_x_sites() -> list[SmallVariantRecord]:
    """Nine non-PAR chrX alleles of the father the plasma shows at the fetal level: a daughter."""
    return [
        _record(f"X-{3_000_000 + i}-A-G", [_call(PLASMA, [1350, 150]), _call(FATHER, [1, 299])])
        for i in range(9)
    ]


def _targets(*, y_mean: float = 108.0, x_mean: float = 1300.0) -> list[TargetCoverageRow]:
    return [
        TargetCoverageRow(chrom="1", start=4900, end=5200, gene="GENEZ", attribute="GENEZ", mean=1500.0, median=1500.0),
        TargetCoverageRow(chrom="7", start=350, end=450, gene="GENEB", attribute="GENEB", mean=1500.0, median=1500.0),
        TargetCoverageRow(chrom="7", start=0, end=120, gene="GENEA", attribute="GENEA;NM_1", mean=250.0, median=240.0,
                          proportion_covered=100.0),
        TargetCoverageRow(chrom="X", start=0, end=100, gene="GENEX", attribute="GENEX", mean=x_mean, median=x_mean),
        TargetCoverageRow(chrom="Y", start=0, end=100, gene="GENEY", attribute="GENEY", mean=y_mean, median=y_mean),
    ]


def _sample_rows(*, assay_panel: str | None = "panel-v2") -> list[dict[str, Any]]:
    plasma_metadata: dict[str, Any] = {"assay": "nipt_cfdna"}
    if assay_panel:
        plasma_metadata["assay_panel"] = assay_panel
    return [
        {"sample_id": FATHER, "role": "father", "sample_metadata": {}},
        {"sample_id": PLASMA, "role": "mother", "sample_metadata": plasma_metadata},
        {"sample_id": "FETUS1", "role": "embryo", "sample_metadata": None},
    ]


def _context(rows: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        family_uuid=FAMILY_UUID,
        family_id="NIPTFAM1",
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        sample_name_to_uuid={FATHER: "father-uuid", PLASMA: "cfdna-uuid"},
        sample_rows=rows if rows is not None else _sample_rows(),
    )


def _family() -> FamilyOut:
    return FamilyOut(
        id=FAMILY_UUID,
        family_id="NIPTFAM1",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id=FATHER, role="father", affected=False),
            FamilyMemberOut(
                sample_id=PLASMA, role="mother", affected=False,
                sample_metadata={"assay": "nipt_cfdna", "assay_panel": "panel-v2"},
            ),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
    )


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


_LOADED = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


class _Session:
    """Answers the family's analysis type, its target-table sources and its small-variant
    reviews (variant id -> tags)."""

    def __init__(
        self, *, analysis_type: str | None = "monogenic_nipt", reviews: dict[str, list[str]] | None = None
    ) -> None:
        self.analysis_type = analysis_type
        self.reviews = reviews or {}
        self.executed: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.executed.append((sql, params))
        if "track_type = 'haplotype'" in sql:
            # No member has haplotype blocks: the fetus, read as an embryo, has no data at all.
            return _Result([])
        if "sample_interval_track_sources" in sql:
            return _Result([{"sample_id": PLASMA, "filename": "cfdna.targets.tsv", "row_count": 5, "uploaded_at": _LOADED}])
        if "FROM families" in sql:
            return _Result([{"analysis_type": self.analysis_type}])
        if "FROM small_variant_reviews" in sql:
            return _Result(
                [
                    {"variant_id": variant_id, "classification": None, "tags": tags, "note": None,
                     "compound_het_classification": None, "compound_het_tags": [], "compound_het_note": None}
                    for variant_id, tags in self.reviews.items()
                ]
            )
        return _Result([])


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sites: list[SmallVariantRecord] | None = None,
    targets: list[TargetCoverageRow] | None = None,
    artifacts: set[str] | None = None,
    other_carriers: dict[str, int] | None = None,
) -> None:
    """The family's data, as the NIPT page and the signed record both read it."""
    filtered = sites if sites is not None else _gene_sites()
    cohort = _ff_sites() + filtered
    rows = targets if targets is not None else _targets()

    async def fake_family_record(_session, _family_id, _user):
        return _family()

    async def fake_context(_session, *, family_identifier, user, project_id=None):
        return _context()

    async def fake_fetch(_context, filters, *, limit=None, include_variant_ids=None, exclude_variant_ids=(), **_kwargs):
        # The review selection's ids, kept or left out in the load, as ClickHouse does.
        rows = cohort if filters.gene is None else filtered
        return [
            record
            for record in rows
            if (include_variant_ids is None or record.variant_id in include_variant_ids)
            and record.variant_id not in exclude_variant_ids
        ]

    async def fake_tracks(_assembly, *, sample_uuid=None, track_type=None, include_metadata=False, record_ids=None, **_kw):
        assert track_type == "target_coverage"
        if sample_uuid != "cfdna-uuid":
            return []
        return [
            {"chr": row.chrom, "start": row.start, "end": row.end, "record_id": row.gene, "value": row.mean,
             "metadata_json": json.dumps({"median": row.median, "proportion_covered": row.proportion_covered})}
            for row in rows
        ]

    async def listed_artifacts(_session, *, assembly_id, assay_key):
        assert assay_key == "panel-v2"  # the plasma's assay panel scopes the list
        return set(artifacts or ())

    async def no_hydration(_session, *, context, variants):
        return None

    async def carriers(_session, *, assay_key):
        return {"other-cfdna": "OTHER1"}

    async def recurrence(_assembly, variant_ids, *, carrier_samples, exclude_family_uuid):
        assert exclude_family_uuid == FAMILY_UUID
        return {variant_id: count for variant_id, count in (other_carriers or {}).items() if variant_id in variant_ids}

    monkeypatch.setattr(nipt_service, "get_family_record", fake_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_context)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fake_fetch)
    monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", fake_tracks)
    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", listed_artifacts)
    monkeypatch.setattr(nipt_service, "_hydrate_small_variant_outs", no_hydration)
    monkeypatch.setattr(nipt_service, "assay_cfdna_carrier_samples", carriers)
    monkeypatch.setattr(nipt_service, "count_cfdna_carriers", recurrence)


async def _section(reported: list[str], session: _Session | None = None) -> dict[str, Any]:
    return await srn.nipt_results(session or _Session(), _context(), reported_ids=reported)


async def _page_summary():
    from backend.app.routers.families_nipt import _qc_out

    summary = await nipt_service.get_family_nipt_summary(session=None, family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]
    return summary, _qc_out(summary)


async def _page_variants(**kwargs) -> dict[str, Any]:
    result = await nipt_service.get_family_nipt_variants(
        session=None,  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        query_filters={"gene": "GENEA,GENEB,GENEC"},
        **kwargs,
    )
    return {item.record.variant_id: item for item in result.variants}


# --- What the page showed -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_section_says_what_the_nipt_page_showed(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    section = await _section(["7-100-A-G", "7-200-C-T"])
    summary, qc = await _page_summary()

    assert section["samples"] == {"father": FATHER, "maternal_plasma": PLASMA, "fetus": "FETUS1"}
    ff = section["fetal_fraction"]
    page_ff = summary.analysis.fetal_fraction
    assert (ff["ff"], ff["ff_computed"], ff["n_sites"], ff["ci_low"], ff["ci_high"], ff["ff_median"]) == (
        page_ff.ff, page_ff.ff_computed, page_ff.n_sites, page_ff.ci_low, page_ff.ci_high, page_ff.ff_median,
    )
    assert ff["ff_computed"] == pytest.approx(0.20, abs=0.01)
    assert (ff["status"], ff["low_confidence"]) == (qc.fetal_fraction_status, False) == ("pass", False)
    assert section["de_novo_window"] == qc.de_novo_window.model_dump()

    checks = section["checks"]
    assert (checks["paternity"]["status"], checks["paternity"]["message"]) == (qc.paternity.status, qc.paternity.message)
    assert (checks["paternity"]["het_transmitted"], checks["paternity"]["het_not_transmitted"]) == (61, 61)
    # No paternal-X site; a male fetal chrY signal: male, read from the chrY coverage.
    assert (checks["fetal_sex"]["call"], checks["fetal_sex"]["status"]) == (qc.fetal_sex.call, qc.fetal_sex.status)
    assert (checks["fetal_sex"]["call"], checks["fetal_sex"]["chry"]) == ("male", "male")
    assert checks["fetal_sex"]["chry_fetal_fraction"] == qc.fetal_sex.chry_fetal_fraction
    assert (checks["plasma_sample"]["status"], checks["plasma_sample"]["message"]) == (
        qc.plasma_profile_status, qc.plasma_profile_message,
    )
    assert checks["plasma_sample"]["profile"] == "female_with_male_fetal_signal"

    assert section["category_counts"] == {str(key): value for key, value in summary.analysis.category_counts.items()}
    assert section["filter_counts"] == summary.analysis.filter_counts
    assert section["quality_failures"] == qc.quality_failures == {}
    coverage = section["target_coverage"]
    assert qc.target_coverage is not None
    assert (coverage["targets"], coverage["below_critical"], coverage["median_mean"]) == (
        qc.target_coverage.targets, qc.target_coverage.below_critical, qc.target_coverage.median_mean,
    )
    # The genes with a weak target, weakest first, as the page names them: the chrY target's
    # and GENEA's.
    assert [(gene["gene"], gene["weak_targets"], gene["targets"], gene["min_mean"]) for gene in coverage["genes"]] == [
        (gene.gene, gene.weak_targets, gene.targets, gene.min_mean) for gene in qc.target_coverage.genes
    ]
    assert [gene["gene"] for gene in coverage["genes"]] == ["GENEY", "GENEA"]


@pytest.mark.asyncio
async def test_each_reported_variant_is_frozen_as_the_variant_list_classifies_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    section = await _section(["7-100-A-G", "7-200-C-T", "7-400-C-T"])
    page = await _page_variants()
    frozen = {entry["variant_id"]: entry for entry in section["variants"]}
    assert list(frozen) == ["7-100-A-G", "7-200-C-T", "7-400-C-T"]
    for variant_id, entry in frozen.items():
        classification = page[variant_id].classification
        site = page[variant_id].site
        assert site is not None
        for field in (
            "category", "category_label", "maternal_state", "fetal_inheritance", "expected_vaf", "observed_vaf",
            "confidence", "runner_up_category", "runner_up_confidence", "paternal_transmission_probability",
            "maternal_allele_probability", "fetal_hom_alt_probability", "fetal_genotype_posterior",
        ):
            assert entry[field] == getattr(classification, field), (variant_id, field)
        assert entry["flags"] == classification.flags
        assert entry["plasma"] == {"alt_reads": site.cf_alt_reads, "depth": site.cf_dp, "depth_estimated": site.cf_depth_estimated}
        assert entry["father"] == {"state": site.father_state, "vaf": site.father_vaf, "depth": site.father_dp}
    # The mother's allele, inherited; the father's, inherited; his other one, not.
    assert (frozen["7-100-A-G"]["maternal_state"], frozen["7-100-A-G"]["category"]) == ("het", 3)
    assert frozen["7-200-C-T"]["category"] == 7
    assert frozen["7-400-C-T"]["fetal_inheritance"] == "paternal_not_transmitted"
    # The paternal allele the plasma did not show: its depth read from the plasma's targets.
    assert frozen["7-400-C-T"]["plasma"] == {"alt_reads": 0, "depth": 1500, "depth_estimated": True}
    assert (frozen["7-100-A-G"]["genes"], frozen["7-100-A-G"]["chrom"], frozen["7-100-A-G"]["position"]) == (["GENEA"], "7", 100)
    assert section["not_classified"] == []


@pytest.mark.asyncio
async def test_a_reported_de_novo_candidate_is_frozen_with_its_triage(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    [entry] = (await _section(["7-500-G-A"]))["variants"]
    [candidate] = (await _page_variants(inheritance="de_novo")).values()
    assert candidate.de_novo is not None
    assert entry["de_novo"] == {
        "window": candidate.de_novo.window,
        "score": candidate.de_novo.score,
        "label": "high",
        "reasons": candidate.de_novo.reasons,
        "other_cfdna_carriers": 0,
    }
    # The mother's allele has no paternal call either, but sits above the fetal window: no
    # de novo candidate (nipt_triage), and no triage, as on the page.
    [maternal] = (await _section(["7-100-A-G"]))["variants"]
    assert maternal["de_novo"] is None
    # Nor has one the father carries.
    [paternal] = (await _section(["7-200-C-T"]))["variants"]
    assert paternal["de_novo"] is None


@pytest.mark.asyncio
async def test_a_recurrent_de_novo_candidate_keeps_its_triage_in_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    # Another pregnancy of the assay carries it: the de novo view leaves it out as recurrent,
    # but a reported one is frozen with that triage, not without one.
    _wire(monkeypatch, other_carriers={"7-500-G-A": 1})
    assert await _page_variants(inheritance="de_novo") == {}
    [entry] = (await _section(["7-500-G-A"]))["variants"]
    assert entry["de_novo"]["label"] == "excluded_recurrent"
    assert entry["de_novo"]["other_cfdna_carriers"] == 1


@pytest.mark.asyncio
async def test_a_reported_variant_the_analysis_does_not_classify_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    joint_without_plasma = _record("7-600-T-C", [_call(FATHER, [150, 150])], genes=["GENED"], source="clair3")
    _wire(monkeypatch, sites=[*_gene_sites(), joint_without_plasma], artifacts={"7-300-A-G"})
    section = await _section(["7-300-A-G", "7-600-T-C", "9-999-A-G", "7-100-A-G"])
    assert [entry["variant_id"] for entry in section["variants"]] == ["7-100-A-G"]
    assert section["not_classified"] == [
        {"variant_id": "7-300-A-G", "reason": "artifact_list"},
        {"variant_id": "7-600-T-C", "reason": "no_plasma_call"},
        {"variant_id": "9-999-A-G", "reason": "not_in_data"},
    ]
    artifacts = section["inputs"]["artifact_list"]
    assert (artifacts["assay_key"], artifacts["entries"], artifacts["removed"], artifacts["kept"]) == ("panel-v2", 1, 1, [])


@pytest.mark.asyncio
async def test_the_variant_lists_reported_view_is_what_a_sign_out_freezes(monkeypatch: pytest.MonkeyPatch) -> None:
    # UI-4: the NIPT page lists the variants tagged report (review_tag=report, no other
    # filter), so the analyst sees before signing out what the record will hold: each
    # classified one as frozen, and each the analysis does not classify, with the same why.
    joint_without_plasma = _record("7-600-T-C", [_call(FATHER, [150, 150])], genes=["GENED"], source="clair3")
    _wire(monkeypatch, sites=[*_gene_sites(), joint_without_plasma], artifacts={"7-300-A-G"})
    # The sign-out freezes the variants whose review holds the report tag.
    reported = ["7-100-A-G", "7-200-C-T", "7-300-A-G", "7-500-G-A", "7-600-T-C", "9-999-A-G"]
    reviews = {variant_id: ["report"] for variant_id in reported}
    reviews.update({"7-400-C-T": ["review"], "1-1000-A-G": ["excluded"]})

    section = await _section(reported)
    listed = await nipt_service.get_family_nipt_variants(
        session=_Session(reviews=reviews),  # type: ignore[arg-type]
        family_id="NIPTFAM1",
        user=None,  # type: ignore[arg-type]
        review_tags=["report"],
        page_size=500,
    )

    frozen = {entry["variant_id"]: entry for entry in section["variants"]}
    shown = {item.record.variant_id: item for item in listed.variants}
    assert set(shown) == set(frozen) == {"7-100-A-G", "7-200-C-T", "7-500-G-A"}
    assert listed.total == len(frozen)
    for variant_id, entry in frozen.items():
        classification = shown[variant_id].classification
        for field in (
            "category", "maternal_state", "fetal_inheritance", "expected_vaf", "observed_vaf", "confidence",
            "paternal_transmission_probability", "maternal_allele_probability", "fetal_hom_alt_probability",
        ):
            assert entry[field] == getattr(classification, field), (variant_id, field)
        assert entry["flags"] == classification.flags
    assert [
        {"variant_id": variant_id, "reason": reason} for variant_id, reason in sorted(listed.not_classified.items())
    ] == section["not_classified"] == [
        {"variant_id": "7-300-A-G", "reason": "artifact_list"},
        {"variant_id": "7-600-T-C", "reason": "no_plasma_call"},
        {"variant_id": "9-999-A-G", "reason": "not_in_data"},
    ]


@pytest.mark.asyncio
async def test_the_recessive_risk_of_the_reported_variants_genes(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    section = await _section(["7-100-A-G", "7-200-C-T", "7-500-G-A"])
    page = {gene.gene: gene for gene in (await nipt_service.get_family_nipt_variants(
        session=None, family_id="NIPTFAM1", user=None,  # type: ignore[arg-type]
        query_filters={"gene": "GENEA"}, inheritance="recessive_at_risk",
    )).recessive_genes}
    [gene] = section["recessive_genes"]
    assert gene["gene"] == "GENEA"
    assert gene["risk"] == page["GENEA"].risk and gene["risk"] > 0.9
    assert (gene["maternal_variant_id"], gene["paternal_variant_id"]) == ("7-100-A-G", "7-200-C-T")
    assert [allele["variant_id"] for allele in gene["maternal"]] == ["7-100-A-G"]
    assert [allele["variant_id"] for allele in gene["paternal"]] == ["7-200-C-T"]
    # One parent's allele alone makes no carrier couple.
    assert (await _section(["7-100-A-G"]))["recessive_genes"] == []


@pytest.mark.asyncio
async def test_the_inputs_the_analysis_ran_with(monkeypatch: pytest.MonkeyPatch) -> None:
    protected = _record("7-700-A-G", [_call(PLASMA, [1360, 140])], genes=["GENEE"], annotations=[{"clinvar": "Pathogenic"}])
    _wire(monkeypatch, sites=[*_gene_sites(), protected], artifacts={"7-300-A-G", "7-700-A-G", "8-1-C-T"})
    session = _Session()
    inputs = (await _section([], session))["inputs"]
    assert inputs["target_tables"] == [
        {"sample": PLASMA, "file": "cfdna.targets.tsv", "targets": 5, "loaded_at": _LOADED.isoformat()}
    ]
    [(sql, params)] = [(sql, params) for sql, params in session.executed if "sample_interval_track_sources" in sql]
    assert params == {"family_uuid": FAMILY_UUID, "track_type": "target_coverage", "samples": sorted([PLASMA, FATHER])}
    assert "ANY(CAST(:samples AS text[]))" in sql
    artifacts = inputs["artifact_list"]
    assert artifacts["entries"] == 3
    # The list states no version: its alleles' digest names it.
    assert artifacts["digest"] == canonical_hash(sorted({"7-300-A-G", "7-700-A-G", "8-1-C-T"}))
    assert artifacts["removed"] == 1
    assert artifacts["kept"] == [{"variant_id": "7-700-A-G", "reason": "clinvar"}]



def test_the_model_the_analysis_ran_with() -> None:
    model = srn._model(NiptQualityThresholds())
    assert model["reference"] == NIPT_MODEL_REFERENCE
    assert model["quality_filter"] == dict(sorted(dataclasses.asdict(NiptQualityThresholds()).items()))
    assert model["fetal_fraction"] == {"hard_floor": 5, "min_sites": 30, "max_ci_halfwidth": 0.03}


# --- A section a record can hold, and check ----------------------------------------------


@pytest.mark.asyncio
async def test_the_section_is_json_safe_deterministic_and_survives_the_jsonb_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch)
    section = await _section(["7-500-G-A", "7-100-A-G", "7-200-C-T"])
    # JSONB holds no NaN or infinity: the section never does either.
    encoded = json.dumps(section, allow_nan=False)
    # Read back as the store hands it back, it hashes as it was signed.
    assert canonical_hash(json.loads(encoded)) == canonical_hash(section)
    # Built again from the same data, it is the same: the sign-out check compares it.
    again = await _section(["7-200-C-T", "7-100-A-G", "7-500-G-A"])
    assert canonical_hash(again) == canonical_hash(section)


@pytest.mark.asyncio
async def test_a_plasma_without_a_target_table_has_no_coverage_check_and_no_sex_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, targets=[])
    section = await _section(["7-400-C-T"])
    assert section["target_coverage"] is None
    plasma = section["checks"]["plasma_sample"]
    assert (plasma["status"], plasma["message"], plasma["profile"], plasma["x_ratio"]) == (
        "unknown", "No per-target coverage: no sex profile.", None, None,
    )
    assert section["checks"]["fetal_sex"]["chry_profile"] is None
    # The father's allele the plasma did not show has no plasma depth to read it against.
    [entry] = section["variants"]
    assert entry["plasma"] == {"alt_reads": 0, "depth": None, "depth_estimated": True}
    assert "no_plasma_depth" in entry["flags"]


def test_a_load_time_is_frozen_as_written() -> None:
    assert srn._iso(_LOADED) == "2026-10-01T09:00:00+00:00"
    assert srn._iso("2026-10-01 09:00:00+00") == "2026-10-01 09:00:00+00"
    assert srn._iso(None) is None


def test_numbers_jsonb_cannot_hold_are_frozen_as_null_and_a_negative_zero_as_zero() -> None:
    assert srn._number(float("nan")) is None
    assert srn._number(float("inf")) is None and srn._number(float("-inf")) is None
    zero = srn._number(-0.0)
    assert zero == 0.0 and math.copysign(1.0, zero) == 1.0
    assert (srn._number(3), srn._number(0.25), srn._number(None), srn._number(True)) == (3, 0.25, None, True)


@pytest.mark.asyncio
async def test_a_failed_check_computed_from_the_data_is_frozen_with_its_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    # The father's X alleles read a daughter, the chrY coverage a son: discordant; and the
    # plasma's chrY at the autosomal level with a low chrX: male DNA.
    _wire(monkeypatch, sites=[*_gene_sites(), *_paternal_x_sites()], targets=_targets(y_mean=1000.0, x_mean=500.0))
    section = await _section([])
    assert section["checks"]["plasma_sample"]["status"] == "fail"
    assert section["checks"]["fetal_sex"]["paternal_x"] == "female"
    assert srn.nipt_signout_concerns(section) == [
        (
            f"Sample {PLASMA} (maternal plasma)",
            "Male DNA (chrY at the autosomal level, chrX low): this sample cannot be maternal plasma.",
        )
    ]
    # A male fetal chrY signal beside the daughter's paternal X: the sex check fails.
    _wire(monkeypatch, sites=[*_gene_sites(), *_paternal_x_sites()])
    section = await _section([])
    assert (section["checks"]["fetal_sex"]["call"], section["checks"]["fetal_sex"]["status"]) == ("discordant", "fail")
    assert [subject for subject, _message in srn.nipt_signout_concerns(section)] == ["NIPT fetal sex"]


# --- The family and its trio ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_family_is_a_nipt_family_by_its_analysis_type() -> None:
    assert await srn.is_nipt_family(_Session(), _context()) is True
    for other in (None, "wgs", "monogenic-nipt"):
        assert await srn.is_nipt_family(_Session(analysis_type=other), _context()) is False

    class _NoFamily(_Session):
        async def execute(self, statement: Any, params: Any = None) -> _Result:
            return _Result([])

    assert await srn.is_nipt_family(_NoFamily(), _context()) is False


def test_the_trio_is_resolved_from_the_familys_active_members() -> None:
    trio, assay_key = srn.nipt_trio_of(_context())  # type: ignore[misc]
    assert (trio.father_sample_id, trio.cfdna_sample_id, trio.fetus_sample_id, assay_key) == (
        FATHER, PLASMA, "FETUS1", "panel-v2",
    )
    # No assay panel: the default artifact-list scope.
    assert srn.nipt_trio_of(_context(_sample_rows(assay_panel=None)))[1] == "nipt_cfdna"  # type: ignore[index]
    # No cfDNA tag: the mother's sample is the plasma, as the NIPT page reads it.
    untagged = [
        {"sample_id": FATHER, "role": "father", "sample_metadata": {}},
        {"sample_id": "MOTHER1", "role": "mother", "sample_metadata": "not a mapping"},
        {"sample_id": "CHILD1", "role": "proband", "sample_metadata": {}},
    ]
    trio, assay_key = srn.nipt_trio_of(_context(untagged))  # type: ignore[misc]
    assert (trio.cfdna_sample_id, trio.fetus_sample_id, assay_key) == ("MOTHER1", "CHILD1", "nipt_cfdna")
    # No father: no trio.
    assert srn.nipt_trio_of(_context([{"sample_id": PLASMA, "role": "mother", "sample_metadata": {"assay": "nipt_cfdna"}}])) is None


@pytest.mark.asyncio
async def test_a_trio_that_cannot_be_resolved_is_frozen_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _must_not_load(*args, **kwargs):
        raise AssertionError("no trio, no analysis")

    monkeypatch.setattr(srn, "get_family_nipt_record", _must_not_load)
    context = _context([{"sample_id": PLASMA, "role": "mother", "sample_metadata": {"assay": "nipt_cfdna"}}])
    assert await srn.nipt_results(_Session(), context, reported_ids=["7-100-A-G"]) == {"unavailable": srn.TRIO_UNRESOLVED}


# --- What gates the sign-out ---------------------------------------------------------------


def _checked(ff: str = "pass", fetal_sex: str = "pass", plasma: str = "pass", sample: str | None = PLASMA) -> dict:
    return {
        "samples": {"maternal_plasma": sample},
        "fetal_fraction": {"status": ff, "message": "Not estimated, from 2 category-7 sites (an estimate needs at least 5)."},
        "checks": {
            "fetal_sex": {"status": fetal_sex, "message": "The two signals disagree."},
            "plasma_sample": {"status": plasma, "message": "Male DNA: this sample cannot be maternal plasma."},
        },
    }


def test_each_nipt_check_that_failed_gates_the_sign_out_naming_what_it_read() -> None:
    assert srn.nipt_signout_concerns(_checked(ff="fail", fetal_sex="fail", plasma="fail")) == [
        ("NIPT fetal fraction", "Not estimated, from 2 category-7 sites (an estimate needs at least 5)."),
        ("NIPT fetal sex", "The two signals disagree."),
        (f"Sample {PLASMA} (maternal plasma)", "Male DNA: this sample cannot be maternal plasma."),
    ]
    # A plasma sample the record does not name.
    assert srn.nipt_signout_concerns(_checked(plasma="fail", sample=None)) == [
        ("NIPT maternal plasma sample", "Male DNA: this sample cannot be maternal plasma.")
    ]


@pytest.mark.parametrize(
    "section",
    [
        _checked(),
        _checked(ff="warn", fetal_sex="warn", plasma="warn"),
        _checked(plasma="unknown"),
        None,
        "not a section",
        {"unavailable": "The NIPT results could not be read."},
        {"fetal_fraction": None, "checks": "garbled", "samples": None},
    ],
)
def test_nipt_checks_that_did_not_fail_or_cannot_be_read_do_not_gate(section: Any) -> None:
    assert srn.nipt_signout_concerns(section) == []


def test_a_failed_check_without_its_message_still_gives_a_reason() -> None:
    bare = {
        "fetal_fraction": {"status": "fail"},
        "checks": {"fetal_sex": {"status": "fail"}, "plasma_sample": {"status": "fail"}},
    }
    assert [message for _subject, message in srn.nipt_signout_concerns(bare)] == [
        "not estimated.",
        "the two signals disagree.",
        "this sample cannot be maternal plasma.",
    ]


# --- One analysis for the sign-out's Sample QC and its record (#900, OPS-9) ----------------
#
# The analysis reads the family's whole callset. A sign-out ran it twice, for the Sample QC's
# NIPT checks (paternity, the fetal sex from the father's X, the category distribution),
# which the sign-out gates on, and for the record's NIPT section, and the two could read
# different inputs: the assay's artifact list is curated apart from the family's data.


class _SignOutSession(_Session):
    """The sign-out's session: each record section reads in a savepoint of its own."""

    @asynccontextmanager
    async def _savepoint(self):
        yield

    def begin_nested(self):
        return self._savepoint()


def _sign_out_context() -> FamilyMetadataContext:
    """The family as the sign-out reads it, for its Sample QC and its record alike."""
    return FamilyMetadataContext(
        family_uuid=FAMILY_UUID,
        family_id="NIPTFAM1",
        project_ids=["project-uuid"],
        sample_rows=[
            {**row, "sex": {"father": "male", "mother": "female"}.get(row["role"], "")}
            for row in _sample_rows()
        ],
        sample_uuid_to_name={"father-uuid": FATHER, "cfdna-uuid": PLASMA},
        sample_name_to_uuid={FATHER: "father-uuid", PLASMA: "cfdna-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _wire_sign_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything a sign-out snapshot reads besides the NIPT analysis (``_wire``) and the
    record's NIPT section: nothing to report on, the other sections empty."""

    async def context(_session, *, family_identifier, user, project_id=None):
        return _sign_out_context()

    async def family(_session, _family_id, _user):
        return _family()

    async def nipt_callset(_context):
        return ["nipt"]

    async def no_sites(_context, *, scope, limit, source):
        return []

    async def manifest(_session, *, family_id, user, project_id=None):
        return {"assembly": "GRCh38", "modules": []}

    async def no_drift(_session, *, family_id, user, project_id=None):
        nothing = {"checked": 0, "drifted_count": 0, "drifted": []}
        return {**nothing, "structural": dict(nothing)}

    async def reported(_session, _family_uuid):
        return [{"variant_id": "7-100-A-G", "acmg_class": "acmg_class_4", "evidence_snapshot": {"annotation_set_hash": "h"}}]

    async def nothing_reported(_session, _family_uuid):
        return []

    async def sequencing_qc(_session, _context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    async def complete(_session, _family_uuid):
        return None

    async def unfinished(_session, _family_uuid):
        return {}

    async def section(*_args, **_kwargs):
        return {}

    for module in (nipt_service, sample_integrity_service, rss):
        monkeypatch.setattr(module, "build_family_metadata_context", context)
    monkeypatch.setattr(sample_integrity_service, "get_family_record", family)
    monkeypatch.setattr(sample_integrity_service, "fetch_family_variant_sources", nipt_callset)
    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", no_sites)
    monkeypatch.setattr(rss, "get_family_annotation_manifest", manifest)
    monkeypatch.setattr(rss, "evaluate_classification_drift", no_drift)
    monkeypatch.setattr(rss, "_reported_reviews", reported)
    monkeypatch.setattr(rss, "_reported_structural_reviews", nothing_reported)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", sequencing_qc)
    monkeypatch.setattr(rss, "_import_incomplete_state", complete)
    monkeypatch.setattr(rss, "_import_unfinished_state", unfinished)
    for name in ("variant_descriptions", "_gene_panels", "reference_data", "repeat_expansion_calls",
                 "paraphase_results", "coverage_tracks"):
        monkeypatch.setattr(srs, name, section)

    async def no_pgt_section(*_args, **_kwargs):  # pragma: no cover - must not be read
        raise AssertionError("the fetus, read as an embryo, is no PGT embryo")

    # The fetus is an embryo member (_sample_rows): its record held a PGT section once (#922).
    monkeypatch.setattr(srs, "pgt_embryo_results", no_pgt_section)


@pytest.mark.asyncio
async def test_a_sign_out_reads_one_nipt_analysis_for_its_sample_qc_and_its_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lab takes one of the father's transmitted alleles (1-1000-A-G) off the artifact
    # list while the sign-out runs. Computed once, the Sample QC frozen in the record, which
    # the sign-out gates on, and the record's NIPT section read the same list and say the
    # same: run twice, the second read had the allele back and counted it.
    _wire(monkeypatch)
    _wire_sign_out(monkeypatch)
    list_reads: list[str] = []

    async def curated_meanwhile(_session, *, assembly_id, assay_key):
        list_reads.append(assay_key)
        return {"1-1000-A-G"} if len(list_reads) == 1 else set()

    analyses: list[str] = []
    analyse = nipt_service._analyse_family_sites

    def counted(data, trio, qc, artifact_lookup):
        analyses.append(trio.father_sample_id)
        return analyse(data, trio, qc, artifact_lookup)

    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", curated_meanwhile)
    monkeypatch.setattr(nipt_service, "_analyse_family_sites", counted)

    snapshot = await rss.build_report_snapshot(_SignOutSession(), family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]

    # One read of the list, one analysis of the family's callset.
    assert (list_reads, analyses) == (["panel-v2"], [FATHER])
    qc, section = snapshot["sample_qc"], snapshot["nipt"]
    assert qc["application"] == "nipt" and "NIPT cfDNA analysis could not run." not in qc["notes"]
    # The fetus is an embryo member without haplotype blocks: no PGT section (TRACE-19).
    assert "pgt_analysis" not in snapshot
    paternity = section["checks"]["paternity"]
    counts = ("hom_alt_transmitted", "hom_alt_not_transmitted", "het_transmitted", "het_not_transmitted")
    assert qc["paternity_check"] == {
        "father": FATHER,
        **{key: paternity[key] for key in (*counts, "status", "message")},
    }
    # The allele the list held when the analysis read it is left out of both.
    assert (paternity["het_transmitted"], section["inputs"]["artifact_list"]["entries"]) == (60, 1)
    fetal_sex = section["checks"]["fetal_sex"]
    assert (
        qc["fetal_sex_check"]["inferred_sex"],
        qc["fetal_sex_check"]["x_transmitted"],
        qc["fetal_sex_check"]["x_not_transmitted"],
        qc["fetal_sex_check"]["informative_sites"],
    ) == (
        fetal_sex["paternal_x"],
        fetal_sex["x_transmitted"],
        fetal_sex["x_not_transmitted"],
        fetal_sex["informative_sites"],
    )
    category_counts = {int(category): count for category, count in section["category_counts"].items()}
    assert qc["category_qc_check"] == dataclasses.asdict(evaluate_nipt_category_qc(category_counts))


@pytest.mark.asyncio
async def test_a_nipt_analysis_that_failed_in_a_sign_out_is_read_as_failed_by_its_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Sample QC could not run the analysis: the record's NIPT section does not run it
    # again and report what the Sample QC, which the sign-out gates on, could not read.
    _wire(monkeypatch)
    _wire_sign_out(monkeypatch)
    loads: list[str] = []

    async def unreadable(_context, _filters, *, limit=None, **_kwargs):
        loads.append("records")
        raise RuntimeError("ClickHouse is not answering")

    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", unreadable)

    snapshot = await rss.build_report_snapshot(_SignOutSession(), family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]

    assert loads == ["records"]
    assert "NIPT cfDNA analysis could not run." in snapshot["sample_qc"]["notes"]
    assert snapshot["sample_qc"]["paternity_check"] is None
    assert snapshot["nipt"] == {"unavailable": "The NIPT results could not be read."}


class _PostgresSignOutSession(_SignOutSession):
    """The sign-out's session as Postgres runs its transaction: a statement that fails aborts
    it, and every later statement fails until it is rolled back, whole or to a savepoint. The
    NIPT artifact list cannot be read (its table refuses the read)."""

    def __init__(self) -> None:
        super().__init__()
        self.aborted = False

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        if self.aborted:
            raise DBAPIError(sql, params, Exception("current transaction is aborted"))
        if "nipt_artifact_variants" in sql:
            self.aborted = True
            raise ProgrammingError(sql, params, Exception("permission denied for table"))
        return await super().execute(statement, params)

    @asynccontextmanager
    async def _savepoint(self):
        aborted = self.aborted
        try:
            yield
        except BaseException:
            self.aborted = aborted  # ROLLBACK TO SAVEPOINT
            raise
        if self.aborted:  # RELEASE SAVEPOINT fails in an aborted transaction
            raise DBAPIError("RELEASE SAVEPOINT", None, Exception("current transaction is aborted"))


@pytest.mark.asyncio
async def test_a_database_error_in_the_sample_qc_nipt_step_leaves_the_sign_out_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Sample QC's NIPT analysis reads the artifact list from Postgres, outside a savepoint:
    # a database error there aborted the sign-out's transaction, and its next statement failed
    # the sign-out (ENG-19). In a savepoint of its own, the error reads like the analysis's
    # other failures: the Sample QC's fixed note, a NIPT check the gate lists as one that could
    # not run, and the record's NIPT section frozen as unavailable.
    from backend.app.services import nipt_artifact_pg

    _wire(monkeypatch)
    _wire_sign_out(monkeypatch)
    # The list read as the analysis reads it: by its own query.
    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", nipt_artifact_pg.load_nipt_artifact_ids)
    reads_after_the_qc: list[str] = []

    async def reported(session, _family_uuid):
        # The sign-out's next statement, in the same transaction.
        await session.execute("SELECT variant_id FROM small_variant_reviews")
        reads_after_the_qc.append("reviews")
        return []

    monkeypatch.setattr(rss, "_reported_reviews", reported)
    session = _PostgresSignOutSession()

    snapshot = await rss.build_report_snapshot(session, family_id="NIPTFAM1", user=None)  # type: ignore[arg-type]

    assert reads_after_the_qc == ["reviews"] and session.aborted is False
    qc = snapshot["sample_qc"]
    assert qc["notes"] == ["NIPT cfDNA analysis could not run."]
    assert (qc["paternity_check"], qc["fetal_sex_check"], qc["category_qc_check"]) == (None, None, None)
    assert "permission denied" not in json.dumps(snapshot)
    assert rss._unverifiable_swap_checks(qc) == [
        "NIPT sample integrity could not be assessed — the trio could not be resolved or the cfDNA "
        "analysis did not run (no paternity or maternal-lineage check)."
    ]
    # The record's section reads the analysis that failed, and is frozen as unavailable.
    assert snapshot["nipt"] == {"unavailable": "The NIPT results could not be read."}
