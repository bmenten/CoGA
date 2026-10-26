"""The transcript the annotation filters held on, named beside the primary one (CLIN-51).

A small variant's fields describe its primary transcript (MANE Select, else canonical, else
the most severe impact), while the annotation filters must all hold on one transcript of it,
in SQL and in Python alike. So SIFT *deleterious* keeps a variant whose primary transcript
SIFT calls tolerated and a second one deleterious, and its card and its CSV row read
*tolerated*. The variant now names the transcript the filters held on (``filter_transcript``)
whenever its primary one does not meet them, with that transcript's values. Its own fields
stay the primary transcript's, so the ranking, the ACMG suggestions, the report and the
signed record read what they read before.

Covers the rule (``_small_variant_out``): when a transcript is named, which one, with what;
that every page of the family search names it, whichever path builds the page (ClickHouse's
own page, the candidate read and an inheritance mode's pairs, the track, the ranked search
computed, served from its cache and served from a broader panel's ranking) and the export;
and that the ranking still reads the primary transcript.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.clickhouse_family_variants import (
    _serve_subpanel_from_superset,
    export_family_small_variants,
    get_family_small_variants_page,
)
from backend.app.services.clickhouse_variant_queries import _small_variant_out
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

M = "backend.app.services.clickhouse_family_variants."


def _filters(**values: Any) -> SmallVariantQueryFilters:
    return SmallVariantQueryFilters(page=1, page_size=50, **values)


def _transcript(transcript_id: str, **values: Any) -> dict[str, Any]:
    """One VEP transcript of gene SYN51 as the import stores it: a missense variant."""
    return {
        "gene": "SYN51",
        "gene_id": "ENSG_SYN51",
        "transcript_id": transcript_id,
        "feature_type": "Transcript",
        "impact": "MODERATE",
        "effect": "missense_variant",
        "hgvsc": f"{transcript_id}:c.100C>T",
        "hgvsp": f"{transcript_id.replace('ENST', 'ENSP')}:p.Arg34Trp",
        **values,
    }


# The ready case of test_e2e_in_silico_prediction_filters.py (variant 20-62900601): SIFT and
# PolyPhen call the canonical transcript tolerated and benign, a second one deleterious and
# probably damaging.
PRIMARY = _transcript("ENST_SYN51_1", canonical=True, sift="tolerated(0.2)", polyphen="benign(0.1)")
SECOND = _transcript("ENST_SYN51_2", sift="deleterious(0.02)", polyphen="probably_damaging(0.95)")


def _call(sample: str, gt: str) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=[], ps=None)


def _record(
    variant_id: str,
    annotations: list[dict[str, Any]],
    *,
    start: int = 100,
    calls: list[SmallVariantCall] | None = None,
) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=start,
        ref="C",
        alt="T",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=sorted({str(a["gene"]) for a in annotations if a.get("gene")}),
        annotations=annotations,
        calls=calls or [_call("PROBAND", "1/1")],
    )


def _named(record: SmallVariantRecord, filters: SmallVariantQueryFilters | None) -> str | None:
    """The transcript the variant names as the one its filters held on, if any."""
    variant = _small_variant_out(record, assembly_name="GRCh38", filters=filters)
    return variant.filter_transcript.transcript_id if variant.filter_transcript else None


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "filters",
    [_filters(sift="deleterious"), _filters(polyphen="probably_damaging")],
    ids=["SIFT deleterious", "PolyPhen probably damaging"],
)
def test_a_variant_kept_on_its_second_transcript_names_it_beside_the_primary(
    filters: SmallVariantQueryFilters,
) -> None:
    variant = _small_variant_out(_record("v", [PRIMARY, SECOND]), assembly_name="GRCh38", filters=filters)

    # The card's own values stay the primary transcript's...
    assert (variant.transcript_id, variant.sift, variant.polyphen) == (
        "ENST_SYN51_1",
        "tolerated(0.2)",
        "benign(0.1)",
    )
    # ...and the transcript the filter held on is named beside them, with its predictions.
    assert variant.filter_transcript is not None
    assert variant.filter_transcript.model_dump() == {
        "gene": "SYN51",
        "transcript_id": "ENST_SYN51_2",
        "impact": "MODERATE",
        "effect": "missense_variant",
        "hgvsc": "ENST_SYN51_2:c.100C>T",
        "hgvsp": "ENSP_SYN51_2:p.Arg34Trp",
        "lof": None,
        "cadd_phred": None,
        "revel": None,
        "spliceai_max": None,
        "sift": "deleterious(0.02)",
        "polyphen": "probably_damaging(0.95)",
    }


@pytest.mark.parametrize(
    "filters",
    [
        None,
        _filters(),
        # Filters that ask nothing of a transcript: the variant's genes, its genotypes, the
        # inheritance mode and the excluded ClinVar statuses apply to the variant as a whole.
        _filters(
            gene="SYN51",
            inheritance="recessive",
            exclude_clinvar=["Benign"],
            clinvar_overrides_frequency=True,
            sample_filters=["PROBAND:hom"],
        ),
        # A SIFT or PolyPhen value that names no prediction asks nothing (CLIN-45).
        _filters(sift="0.5", polyphen=" "),
    ],
    ids=["no search", "no filter", "variant-level filters", "a value naming no prediction"],
)
def test_no_transcript_is_named_without_annotation_filters(
    filters: SmallVariantQueryFilters | None,
) -> None:
    assert _named(_record("v", [PRIMARY, SECOND]), filters) is None


@pytest.mark.parametrize(
    ("annotations", "filters"),
    [
        ([PRIMARY, SECOND], _filters(sift="tolerated")),
        ([PRIMARY, SECOND], _filters(polyphen="benign")),
        ([SECOND], _filters(sift="deleterious")),
        # Both transcripts meet the filter: the card's own is the one it held on.
        ([PRIMARY, SECOND], _filters(impact=["MODERATE"])),
    ],
    ids=["SIFT tolerated", "PolyPhen benign", "one transcript", "both transcripts"],
)
def test_no_transcript_is_named_when_the_primary_meets_the_filters(
    annotations: list[dict[str, Any]], filters: SmallVariantQueryFilters
) -> None:
    assert _named(_record("v", annotations), filters) is None


def test_a_variant_without_annotations_names_no_transcript() -> None:
    assert _named(_record("v", []), _filters(impact=["HIGH"])) is None


@pytest.mark.parametrize(
    ("annotations", "filters", "expected"),
    [
        # MANE Select first: the RefSeq MANE transcript of a merged VEP run before a more
        # severe RefSeq one, when only RefSeq transcripts are asked for.
        (
            [
                _transcript("ENST_SYN51_1", mane_select=True, canonical=True),
                _transcript("NM_000002.1", impact="HIGH", effect="stop_gained"),
                _transcript("NM_000001.1", mane_select=True),
            ],
            _filters(transcript="NM_"),
            "NM_000001.1",
        ),
        # Then canonical, before a more severe transcript.
        (
            [
                _transcript("ENST_SYN51_1", mane_select=True, impact="LOW", effect="synonymous_variant"),
                _transcript("ENST_SYN51_3", impact="HIGH", effect="stop_gained"),
                _transcript("ENST_SYN51_2", canonical=True),
            ],
            _filters(impact=["HIGH", "MODERATE"]),
            "ENST_SYN51_2",
        ),
        # Then the most severe.
        (
            [
                _transcript("ENST_SYN51_1", mane_select=True, impact="LOW", effect="synonymous_variant"),
                _transcript("ENST_SYN51_2"),
                _transcript("ENST_SYN51_3", impact="HIGH", effect="stop_gained"),
            ],
            _filters(impact=["HIGH", "MODERATE"]),
            "ENST_SYN51_3",
        ),
        # Of equals, the first stored, as the primary transcript is picked.
        (
            [
                _transcript("ENST_SYN51_1", mane_select=True, impact="LOW", effect="synonymous_variant"),
                _transcript("ENST_SYN51_2"),
                _transcript("ENST_SYN51_3"),
            ],
            _filters(impact=["MODERATE"]),
            "ENST_SYN51_2",
        ),
    ],
    ids=["MANE Select first", "then canonical", "then the most severe", "then the first stored"],
)
def test_the_named_transcript_is_picked_as_the_primary_is(
    annotations: list[dict[str, Any]], filters: SmallVariantQueryFilters, expected: str
) -> None:
    assert _named(_record("v", annotations), filters) == expected


@pytest.mark.parametrize(
    ("filters", "second"),
    [
        (_filters(impact=["HIGH"]), {"impact": "HIGH", "effect": "stop_gained"}),
        (_filters(effect=["stop_gained"]), {"impact": "HIGH", "effect": "stop_gained"}),
        (_filters(transcript="SYN51_2"), {}),
        (_filters(hgvsc="c.100C>T"), {}),
        (_filters(lof_only=True), {"lof": "HC"}),
        (_filters(min_revel=0.5), {"revel": 0.71}),
        (_filters(min_spliceai=0.5), {"spliceai_max": 0.8}),
        (_filters(min_cadd=20), {"cadd_phred": 25.0}),
        (_filters(clinvar=["Pathogenic"]), {"clinvar": "Pathogenic"}),
        (_filters(max_gnomad_af=0.01), {"gnomad_af": 0.001}),
    ],
    ids=[
        "impact", "consequence", "transcript", "HGVS", "LoF only", "REVEL", "SpliceAI",
        "CADD", "ClinVar", "frequency",
    ],
)
def test_any_annotation_filter_held_on_another_transcript_names_it(
    filters: SmallVariantQueryFilters, second: dict[str, Any]
) -> None:
    # The primary transcript (MANE Select and canonical) meets none of them; the second
    # meets each.
    primary = _transcript(
        "ENST_SYN51_1",
        mane_select=True,
        canonical=True,
        impact="LOW",
        effect="synonymous_variant",
        hgvsc="ENST_SYN51_1:c.99G>A",
        revel=0.1,
        spliceai_max=0.01,
        cadd_phred=5.0,
        clinvar="Benign",
        gnomad_af=0.2,
    )
    other = {
        **_transcript(
            "ENST_SYN51_2", revel=0.1, spliceai_max=0.01, cadd_phred=5.0, clinvar="Benign", gnomad_af=0.2
        ),
        **second,
    }
    assert _named(_record("v", [primary, other]), filters) == "ENST_SYN51_2"


@pytest.mark.parametrize(
    ("filters", "primary", "second"),
    [
        # A MANE Select transcript that is not the canonical one.
        (_filters(canonical_only=True), {"mane_select": True}, {"canonical": True}),
        # A canonical transcript that is not MANE, and a MANE Plus Clinical one.
        (_filters(mane_only=True), {"canonical": True}, {"mane_plus_clinical": True}),
    ],
    ids=["canonical only", "MANE only"],
)
def test_a_transcript_flag_filter_held_on_another_transcript_names_it(
    filters: SmallVariantQueryFilters, primary: dict[str, Any], second: dict[str, Any]
) -> None:
    annotations = [_transcript("ENST_SYN51_1", **primary), _transcript("ENST_SYN51_2", **second)]
    assert _named(_record("v", annotations), filters) == "ENST_SYN51_2"


def test_the_named_transcript_carries_its_values_as_the_variant_reads_its_own() -> None:
    # Every value as _small_variant_out reads the primary transcript's: the aliases, the
    # SpliceAI maximum of the four delta scores, another gene's symbol.
    other = {
        "geneSymbol": "SYN51AS",
        "transcriptId": "ENST_SYN51AS_1",
        "impact": "HIGH",
        "consequence": "splice_donor_variant",
        "hgvsc": "ENST_SYN51AS_1:c.50+1G>A",
        "lof": "HC",
        "caddPhred": "31.5",
        "revel": "0.66",
        "spliceai_ds_dl": 0.91,
        "spliceai_ds_ag": 0.12,
        "siftPrediction": "deleterious_low_confidence(0.01)",
        "polyphenPrediction": "possibly_damaging(0.7)",
    }
    variant = _small_variant_out(
        _record("v", [PRIMARY, other]), assembly_name="GRCh38", filters=_filters(lof_only=True)
    )
    alone = _small_variant_out(_record("v", [other]), assembly_name="GRCh38", filters=None)

    assert variant.filter_transcript is not None
    named = variant.filter_transcript.model_dump()
    assert named == {field: getattr(alone, field) for field in named}
    assert named["spliceai_max"] == pytest.approx(0.91)
    assert (named["gene"], named["transcript_id"], named["effect"]) == (
        "SYN51AS",
        "ENST_SYN51AS_1",
        "splice_donor_variant",
    )
    # The card's own fields stay the primary transcript's.
    assert (variant.gene, variant.transcript_id, variant.lof) == ("SYN51", "ENST_SYN51_1", None)


# --------------------------------------------------------------------------- #
# Every page of the family search, and the export
# --------------------------------------------------------------------------- #


def _family_context() -> FamilyMetadataContext:
    rows = [
        ("PROBAND", "proband", True, "male"),
        ("MOM", "mother", False, "female"),
        ("DAD", "father", False, "male"),
    ]
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="CL51",
        project_ids=["project-uuid"],
        sample_rows=[
            {
                "sample_id": sample,
                "role": role,
                "affected": affected,
                "clinical_status": "affected" if affected else "unaffected",
                "carrier_status": "unknown",
                "sex": sex,
            }
            for sample, role, affected, sex in rows
        ],
        sample_uuid_to_name={f"uuid-{sample}": sample for sample, *_ in rows},
        sample_name_to_uuid={sample: f"uuid-{sample}" for sample, *_ in rows},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


# Two heterozygous variants of SYN51 the proband inherited one from each parent (a
# compound-het pair in trans), and one he is homozygous for, each the ready case: kept by
# SIFT deleterious on its second transcript alone.
RECORDS = [
    _record(
        "1-100-C-T",
        [PRIMARY, SECOND],
        start=100,
        calls=[_call("PROBAND", "0/1"), _call("MOM", "0/1"), _call("DAD", "0/0")],
    ),
    _record(
        "1-200-C-T",
        [PRIMARY, SECOND],
        start=200,
        calls=[_call("PROBAND", "0/1"), _call("MOM", "0/0"), _call("DAD", "0/1")],
    ),
    _record(
        "1-300-C-T",
        [PRIMARY, SECOND],
        start=300,
        calls=[_call("PROBAND", "1/1"), _call("MOM", "0/1"), _call("DAD", "0/1")],
    ),
]


@pytest.fixture
def family_search(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The family search over RECORDS, its storage and the ranking cache in memory."""
    cache: dict[str, dict[str, Any]] = {}

    async def fetch(_context, _filters, *, include_variant_ids=None, **_kwargs):
        if include_variant_ids is None:
            return list(RECORDS)
        wanted = {str(variant_id) for variant_id in include_variant_ids}
        return [record for record in RECORDS if record.variant_id in wanted]

    async def count(*_args, **_kwargs):
        return len(RECORDS), False

    async def nothing(*_args, **_kwargs):
        return None

    async def no_metrics(*_args, **_kwargs):
        return {}

    async def no_hpo(*_args, **_kwargs):
        return [], {}

    async def version(*_args, **_kwargs):
        return "1:1"

    async def hashes(*_args, filters, **_kwargs):
        # One cache row per search: the filters that differ here are the inheritance mode
        # and SIFT.
        key = f"{filters.inheritance}-{filters.sift}"
        return f"inputs-{key}", f"base-{key}"

    async def get_cached(_session, *, family_uuid, inputs_hash):
        return cache.get(inputs_hash)

    async def store(_session, *, inputs_hash, total, ranking, **_kwargs):
        cache[inputs_hash] = {
            "total": total,
            "total_is_estimated": False,
            "ranking_truncated": False,
            "ranking": ranking,
            "computed_at": None,
        }

    monkeypatch.setattr(M + "_fetch_small_variant_summary", nothing)
    monkeypatch.setattr(M + "_fetch_small_variant_rows", fetch)
    monkeypatch.setattr(M + "_count_small_variant_rows_bounded", count)
    monkeypatch.setattr(M + "_hydrate_small_variant_outs", nothing)
    monkeypatch.setattr(M + "_fetch_gene_constraint_metric_map", no_metrics)
    monkeypatch.setattr(M + "_affected_present_hpo", no_hpo)
    monkeypatch.setattr(M + "_family_small_variant_data_version", version)
    monkeypatch.setattr(M + "compute_ranking_hashes", hashes)
    monkeypatch.setattr(M + "canonical_filters", lambda *_a, **_k: {})
    monkeypatch.setattr(M + "get_cached_ranking", get_cached)
    monkeypatch.setattr(M + "store_ranking", store)
    return cache


def _page_variants(page) -> list:
    variants = list(page.variants)
    for group in page.variant_groups:
        variants.extend(group.variants)
    return variants


# Each view of the family page, as its query string asks for it, and the path that builds it.
VIEWS: dict[str, dict[str, Any]] = {
    "unranked (ClickHouse's page)": {},
    "compound het (the candidate read, pairs)": {"inheritance": "compound_het"},
    "recessive (pairs and homozygotes)": {"inheritance": "recessive"},
    "homozygous recessive": {"inheritance": "recessive_homozygous"},
    "track": {"track_mode": True, "track_result_limit": 1000},
    "track, sampled from the candidates": {"track_mode": True},
    "ranked": {"prioritize": True},
    "ranked compound het": {"prioritize": True, "inheritance": "compound_het"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("view", list(VIEWS))
async def test_every_view_of_the_family_page_names_the_transcript(family_search, view: str) -> None:
    async def open_page(**filters: Any):
        return await get_family_small_variants_page(
            None,  # type: ignore[arg-type]
            context=_family_context(),
            page=1,
            page_size=50,
            **VIEWS[view],
            **filters,
        )

    page = await open_page(sift="deleterious")
    variants = _page_variants(page)
    assert variants, "the view lists the variants"
    assert {variant.filter_transcript.transcript_id for variant in variants if variant.filter_transcript} == {
        "ENST_SYN51_2"
    }
    assert all(variant.filter_transcript is not None for variant in variants)
    assert {variant.sift for variant in variants} == {"tolerated(0.2)"}

    if VIEWS[view].get("prioritize"):
        # Opened again, the ranked page is served from the ranking cache: the variants are
        # fetched by id and still name the transcript.
        again = await open_page(sift="deleterious")
        assert again.ranking_cached
        assert [v.filter_transcript for v in _page_variants(again)] == [
            v.filter_transcript for v in variants
        ]

    # Without the filter no transcript is named.
    assert all(variant.filter_transcript is None for variant in _page_variants(await open_page()))


@pytest.mark.asyncio
async def test_the_ranking_reads_the_primary_transcript_whatever_the_filters(family_search) -> None:
    # The score of a variant comes from its primary transcript: the SIFT filter that keeps
    # it on its second transcript changes neither its score nor its rank.
    async def ranked(**filters: Any) -> dict[str, Any]:
        page = await get_family_small_variants_page(
            None,  # type: ignore[arg-type]
            context=_family_context(),
            page=1,
            page_size=50,
            prioritize=True,
            **filters,
        )
        return {str(v.id): v.priority.model_dump() for v in _page_variants(page)}

    unfiltered = await ranked()
    family_search.clear()
    assert await ranked(sift="deleterious") == unfiltered


@pytest.mark.asyncio
async def test_a_panel_served_from_a_broader_ranking_names_the_transcript(monkeypatch) -> None:
    record = RECORDS[2]
    priority = {
        "combined_score": 0.5,
        "variant_score": 0.5,
        "pathogenicity_score": 0.5,
        "frequency_score": 1.0,
        "segregation_weight": 1.0,
        "segregation_modes": ["homozygous_recessive"],
    }

    async def supersets(_session, *, family_uuid, base_hash):
        # A ranking cached without a panel serves any panel it holds every variant of. The
        # candidates come without their rankings: the one that passes is read (ENG-13).
        return [{"inputs_hash": "superset", "panel_id": None, "computed_at": None}]

    async def cached(_session, *, family_uuid, inputs_hash):
        assert inputs_hash == "superset"
        return {"ranking": [{"variant_id": record.variant_id, "priority": priority}], "computed_at": None}

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(M + "find_superset_candidates", supersets)
    monkeypatch.setattr(M + "get_cached_ranking", cached)
    monkeypatch.setattr(M + "_hydrate_small_variant_outs", nothing)

    page = await _serve_subpanel_from_superset(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        filters=_filters(sift="deleterious"),
        base_hash="base",
        panel_constraints=PanelFilterConstraints(genes=("SYN51",)),
        records=[record],
        segregation_modes={record.variant_id: ["homozygous_recessive"]},
        items=[("variant", record)],
        pairing=False,
        page=1,
        page_size=50,
        small_variant_summary=None,
    )

    assert page is not None and page.ranking_cached
    [variant] = page.variants
    assert variant.filter_transcript is not None
    assert (variant.sift, variant.filter_transcript.sift) == ("tolerated(0.2)", "deleterious(0.02)")


@pytest.mark.asyncio
@pytest.mark.parametrize("prioritize", [False, True], ids=["unranked", "ranked"])
async def test_the_export_names_the_transcript(family_search, prioritize: bool) -> None:
    export = await export_family_small_variants(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        prioritize=prioritize,
        sift="deleterious",
    )
    assert len(export.rows) == len(RECORDS)
    assert {
        (row.sift, row.filter_transcript.transcript_id, row.filter_transcript.sift)
        for row in export.rows
        if row.filter_transcript
    } == {("tolerated(0.2)", "ENST_SYN51_2", "deleterious(0.02)")}
    assert all(row.filter_transcript is not None for row in export.rows)

