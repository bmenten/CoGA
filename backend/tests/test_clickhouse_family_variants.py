from __future__ import annotations

import pytest
from clickhouse_connect.driver.binding import bind_query
from clickhouse_connect.driver.exceptions import DatabaseError
from fastapi import HTTPException

from backend.app.services.clickhouse_family_variants import (
    PanelFilterConstraints,
    Region,
    _SMALL_INHERITANCE_MAX_CANDIDATE_ROWS,
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
    _compound_het_partner_map,
    _chromosome_options,
    _execute_clickhouse,
    _read_small_summary_cache,
    _inheritance_result_items,
    _fetch_small_variant_rows,
    _flexible_status_match,
    _small_detail_filter_clauses,
    _small_variant_where_clauses,
    _small_variant_out,
    _small_record_matches,
    _normalize_small_variant_inheritance,
    _prioritized_small_variants_page,
    _prioritized_structural_variants_page,
    get_family_compound_het_candidates,
    get_family_small_variants_page,
    get_family_structural_variants_page,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import (
    SmallVariantQueryFilters,
    StructuralVariantQueryFilters,
    parse_genotype_filter,
)
from backend.app.schemas import SmallVariantSummaryOut


@pytest.fixture(autouse=True)
def _stub_small_variant_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_small_variant_summary(_context):
        return None

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_summary",
        fake_fetch_small_variant_summary,
    )


def _inheritance_filter_records(
    records,
    *,
    inheritance,
    affected_samples,
    unaffected_samples,
    sample_rows=None,
):
    """Adapt the production inheritance selector (_inheritance_result_items, which
    returns kind-tagged items / compound-het pairs) back to the flat,
    original-order record list the (removed) _apply_small_inheritance_filter
    returned, so these tests exercise the live code path. The matched-id union
    is faithful: _compound_het_partner_map is itself built from
    _compound_het_pairs, so a pair's {left, right} ids equal the old partner-map
    keys."""
    if not inheritance:
        return list(records)
    matched_ids: set[str] = set()
    for kind, payload in _inheritance_result_items(
        inheritance=inheritance,
        records=records,
        affected_samples=affected_samples,
        unaffected_samples=unaffected_samples,
        sample_rows=sample_rows or [],
    ):
        if kind == "group":
            matched_ids.add(payload.left.variant_id)
            matched_ids.add(payload.right.variant_id)
        else:
            matched_ids.add(payload.variant_id)
    return [record for record in records if record.variant_id in matched_ids]


def _small_call(sample: str, gt: str) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=[], ps=None)


def _small_variant(
    variant_id: str,
    gene: str,
    *,
    calls: list[SmallVariantCall],
    gene_id: str | None = None,
    start: int = 100,
    chr: str = "1",
) -> SmallVariantRecord:
    annotations = [{"gene": gene}]
    if gene_id:
        annotations[0]["gene_id"] = gene_id
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chr,
        start=start,
        end=start,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=[gene],
        annotations=annotations,
        calls=calls,
    )


def test_mitochondrial_chromosome_options_include_common_aliases() -> None:
    assert _chromosome_options("MT") == ("MT", "chrMT", "M", "chrM")
    assert _chromosome_options("chrM") == ("M", "chrM", "MT", "chrMT")


def test_small_variant_out_primary_in_flat_fields_others_in_transcripts() -> None:
    record = SmallVariantRecord(
        variant_key=None,
        variant_id="v-transcripts",
        chr="13",
        start=32316461,
        end=32316461,
        ref="G",
        alt="A",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=["BRCA2"],
        annotations=[
            {
                "gene": "BRCA2",
                "gene_id": "ENSG00000139618",
                "transcript_id": "ENST00000380152.8",
                "transcript_biotype": "protein_coding",
                "impact": "MODERATE",
                "effect": "missense_variant",
                "hgvsc": "ENST00000380152.8:c.7007G>A",
                "hgvsp": "ENSP00000369497.3:p.Arg2336His",
                "exon": "13/27",
                "canonical": True,
            },
            {
                "gene": "BRCA2",
                "gene_id": "ENSG00000139618",
                "transcript_id": "NM_000059.4",
                "transcript_biotype": "protein_coding",
                "impact": "MODERATE",
                "effect": "missense_variant",
                "hgvsc": "NM_000059.4:c.7007G>A",
                "hgvsp": "NP_000050.3:p.Arg2336His",
                "exon": "13/27",
                "mane_select": True,
            },
        ],
        calls=[_small_call("PROBAND", "0/1")],
    )

    variant = _small_variant_out(record)

    # The primary transcript (MANE select NM_000059.4) is carried by the flat
    # fields and not duplicated in `transcripts`.
    assert variant.transcript_id == "NM_000059.4"
    assert variant.hgvsc == "NM_000059.4:c.7007G>A"
    assert variant.mane_select is True
    # `transcripts` holds only the non-primary transcripts.
    assert [transcript.transcript_id for transcript in variant.transcripts] == [
        "ENST00000380152.8",
    ]
    assert variant.transcripts[0].transcript_source == "Ensembl"
    assert variant.transcripts[0].canonical is True
    assert variant.transcripts[0].hgvsc == "ENST00000380152.8:c.7007G>A"
    assert variant.transcripts[0].primary is False


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="demo_family",
        project_ids=["project-uuid"],
        sample_rows=[
            {
                "sample_id": "PROBAND",
                "role": "proband",
                "affected": True,
                "clinical_status": "affected",
                "carrier_status": "unknown",
                "sex": "male",
            },
            {
                "sample_id": "MOM",
                "role": "mother",
                "affected": False,
                "clinical_status": "unaffected",
                "carrier_status": "unknown",
                "sex": "female",
            },
            {
                "sample_id": "DAD",
                "role": "father",
                "affected": False,
                "clinical_status": "unaffected",
                "carrier_status": "unknown",
                "sex": "male",
            },
            {
                "sample_id": "SIB",
                "role": "sibling",
                "affected": False,
                "clinical_status": "unaffected",
                "carrier_status": "unknown",
                "sex": "female",
            },
        ],
        sample_uuid_to_name={
            "sample-proband": "PROBAND",
            "sample-mom": "MOM",
            "sample-dad": "DAD",
            "sample-sib": "SIB",
        },
        sample_name_to_uuid={
            "PROBAND": "sample-proband",
            "MOM": "sample-mom",
            "DAD": "sample-dad",
            "SIB": "sample-sib",
        },
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _small_entry_row(
    sample_ids: list[str] | None = None,
    *,
    key: int = 2,
    variant_id: str = "v2",
    start: int = 101,
) -> tuple[object, ...]:
    return (
        key,
        variant_id,
        "vep_tsv",
        123456,
        "1",
        start,
        "A",
        "G",
        "clair3",
        None,
        [],
        ["GENE2"],
        sample_ids or ["PROBAND"],
        ["0/1"],
        [99],
        [30],
        [0.5],
        [[0.5]],
        [[12, 18]],
        [None],
        40.0,
    )


def _small_detail_row(
    *,
    key: int = 2,
    gene: str = "GENE2",
) -> tuple[object, ...]:
    return (
        key,
        "vep_tsv",
        123456,
        None,
        f'{{"annotations":[{{"gene":"{gene}"}}]}}',
    )


@pytest.mark.asyncio
async def test_get_family_small_variants_page_uses_clickhouse_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "SELECT count()" in query:
            return [(3,)]
        if "variants/details" in query:
            return [_small_detail_row(), _small_detail_row(key=3, gene="GENE3")]
        return [
            _small_entry_row(),
            _small_entry_row(key=3, variant_id="v3", start=102),
        ]

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    async def fake_fetch_summary(*_args, **_kwargs):
        return SmallVariantSummaryOut(total_variants=3, snv_count=2, indel_count=1)

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_summary",
        fake_fetch_summary,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=2,
        page_size=1,
        chr="1",
    )

    assert page.total == 3
    assert page.total_is_estimated is False
    assert page.unfiltered_total == 3
    assert [str(variant.id) for variant in page.variants] == ["v2"]
    assert len(queries) == 4
    assert "hasAny(e.calls.sampleId, %(visible_sample_ids)s)" in queries[0][0]
    # The list dedups by key (#333) so it agrees with the deduped count.
    assert "GROUP BY e.key" in queries[0][0]
    assert queries[0][1]["visible_sample_ids"] == [
        "PROBAND",
        "sample-proband",
        "MOM",
        "sample-mom",
        "DAD",
        "sample-dad",
        "SIB",
        "sample-sib",
    ]
    assert "LIMIT %(limit)s OFFSET %(offset)s" in queries[0][0]
    assert queries[0][1]["limit"] == 2
    assert queries[0][1]["offset"] == 1


def test_parse_genotype_filter_expands_group_aliases() -> None:
    assert parse_genotype_filter("het") == (["0/1", "1/0", "0|1", "1|0"], False)
    assert parse_genotype_filter("hom") == (["1/1", "1|1"], False)
    assert parse_genotype_filter("wt") == (["0/0", "0|0"], True)


@pytest.mark.asyncio
async def test_get_family_small_variants_page_filters_simple_sample_gt_in_clickhouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "SELECT count()" in query:
            return [(1,)]
        if "variants/details" in query:
            return [_small_detail_row()]
        return [_small_entry_row()]

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )
    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=1,
        sample_filters=["PROBAND:het:20:10:0.2:4", "MOM:ref"],
    )

    assert page.total == 1
    assert page.total_is_estimated is False
    assert [str(variant.id) for variant in page.variants] == ["v2"]
    assert len(queries) == 4
    assert "arrayExists((sample_id, gt, gq, dp, ab, af, ad) -> sample_id IN %(sample_filter_0_samples)s" in queries[0][0]
    assert "arrayMax(af) >= %(sample_filter_0_min_af)s" in queries[0][0]
    assert "ad[2] >= %(sample_filter_0_min_ad_alt)s" in queries[0][0]
    assert "NOT arrayExists(sample_id -> sample_id IN %(sample_filter_1_samples)s" in queries[0][0]
    assert "LIMIT %(limit)s OFFSET %(offset)s" in queries[0][0]
    assert queries[0][1]["sample_filter_0_samples"] == ("PROBAND", "sample-proband")
    assert queries[0][1]["sample_filter_0_gts"] == ("0/1", "1/0", "0|1", "1|0")
    assert queries[0][1]["sample_filter_0_min_gq"] == 20.0
    assert queries[0][1]["sample_filter_0_min_dp"] == 10
    assert queries[0][1]["sample_filter_0_min_af"] == 0.2
    assert queries[0][1]["sample_filter_0_min_ad_alt"] == 4
    assert queries[0][1]["sample_filter_1_samples"] == ("MOM", "sample-mom")
    assert queries[0][1]["sample_filter_1_gts"] == ("0/0", "0|0")


@pytest.mark.asyncio
async def test_get_family_small_variants_page_uses_bounded_total_without_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    async def fake_execute_clickhouse(query: str, _params: dict[str, object]):
        queries.append(query)
        if "SELECT count()" in query:
            return [(10001,)]
        if "variants/details" in query:
            return [_small_detail_row(), _small_detail_row(key=3, gene="GENE3")]
        return [
            _small_entry_row(),
            _small_entry_row(key=3, variant_id="v3", start=102),
        ]

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    async def fake_fetch_summary(*_args, **_kwargs):
        return SmallVariantSummaryOut(total_variants=1001, snv_count=1001, indel_count=0)

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_summary",
        fake_fetch_summary,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=1,
    )

    assert page.total == 10001
    assert page.total_is_estimated is True
    assert page.unfiltered_total == 1001
    assert page.unfiltered_total_is_estimated is False
    assert [str(variant.id) for variant in page.variants] == ["v2"]
    assert len(queries) == 4
    assert "LIMIT %(count_limit)s" in "\n".join(queries)
    assert "LIMIT %(limit)s OFFSET %(offset)s" in queries[0]


@pytest.mark.asyncio
async def test_get_family_small_variants_page_count_only_probes_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    async def fake_execute_clickhouse(query: str, _params: dict[str, object]):
        queries.append(query)
        return [(1,)]

    async def fail_count(*_args, **_kwargs):
        raise AssertionError("count_only must not run the bounded count")

    async def fail_summary(_context):
        raise AssertionError("count_only must not fetch the unfiltered summary")

    async def fail_rows(*_args, **_kwargs):
        raise AssertionError("count_only must not fetch full variant rows")

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._count_small_variant_rows_bounded",
        fail_count,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_summary",
        fail_summary,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fail_rows,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=1,
        count_only=True,
    )

    # Presence-bounded total (>0) with no row payload, resolved by a single cheap
    # family_guid existence probe — no bounded count(), summary, or row fetch.
    assert page.total == 1
    assert page.variants == []
    assert len(queries) == 1
    assert "family_guid = %(family_guid)s" in queries[0]
    assert "LIMIT 1" in queries[0]
    assert "count()" not in queries[0]
    assert "hasAny" not in queries[0]


@pytest.mark.asyncio
async def test_get_family_small_variants_track_mode_stops_before_heavy_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count_limits: list[int] = []

    async def fake_count_small_variant_rows_bounded(*_args, count_limit: int, **_kwargs):
        count_limits.append(count_limit)
        return 1000, True

    async def fake_fetch_small_variant_rows(*_args, **_kwargs):
        raise AssertionError("track mode must not fetch rows when the count is capped")

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._count_small_variant_rows_bounded",
        fake_count_small_variant_rows_bounded,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fake_fetch_small_variant_rows,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=999,
        chr="1",
        start=0,
        end=100,
        track_mode=True,
        track_result_limit=5000,
    )

    assert count_limits == [5000]
    assert page.total == 1000
    assert page.total_is_estimated is True
    assert page.count_limit == 5000
    assert page.variants == []


@pytest.mark.asyncio
async def test_get_family_small_variants_page_filters_vep_annotations_in_clickhouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "SELECT count()" in query:
            return [(1,)]
        if "variants/details" in query:
            return [
                _small_detail_row(
                    key=2,
                    gene="GENE2",
                )
            ]
        if "FROM coga.`GRCh38/SNV_INDEL/entries`" in query:
            return [
                _small_entry_row()
            ]
        return []

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=1,
        inheritance="de_novo_dominant",
        impact=["MODERATE"],
        effect=["missense_variant"],
        clinvar=["pathogenic"],
        max_gnomad_af=0.01,
    )

    assert page.total == 1
    assert [str(variant.id) for variant in page.variants] == ["v2"]
    assert len(queries) == 4
    query, params = queries[0]
    assert "INNER JOIN" not in query
    assert "GRCh38/SNV_INDEL/variants/annotations" in query
    assert "(e.key, e.annotation_version, e.annotationSetHash) IN (SELECT a.key, a.annotation_version, a.annotationSetHash" in query
    assert "GROUP BY e.key" in query  # list dedups by key (#333)
    assert "a.impact IN %(detail_impact_terms)s" in query
    assert "hasAny(a.effects, %(detail_effect_terms)s)" in query
    assert "hasAny(a.clinvar_terms, %(detail_clinvar_terms)s)" in query
    assert "ifNull(a.gnomad_af, 0) <= %(detail_max_gnomad_af)s" in query
    assert "inheritance_affected_het_0_gts" in params
    assert params["detail_impact_terms"] == ("moderate",)
    assert params["detail_effect_terms"] == ["missense_variant"]
    assert params["detail_clinvar_terms"] == ["pathogenic"]
    assert params["limit"] == 2


@pytest.mark.asyncio
async def test_execute_clickhouse_maps_heavy_query_error_to_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_clickhouse(_query: str, _params: dict[str, object]):
        raise DatabaseError(
            "Code: 241. DB::Exception: Memory limit (total) exceeded: "
            "MEMORY_LIMIT_EXCEEDED"
        )

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.execute_clickhouse",
        fake_execute_clickhouse,
    )

    with pytest.raises(HTTPException) as excinfo:
        await _execute_clickhouse("SELECT count()", {"family_guid": "fam-1"})

    assert excinfo.value.status_code == 422
    assert "Narrow the search" in excinfo.value.detail


@pytest.mark.asyncio
async def test_execute_clickhouse_returns_empty_for_missing_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_clickhouse(_query: str, _params: dict[str, object]):
        raise DatabaseError("Code: 60. DB::Exception: UNKNOWN_TABLE")

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.execute_clickhouse",
        fake_execute_clickhouse,
    )

    assert await _execute_clickhouse("SELECT 1", {}) == []


@pytest.mark.asyncio
async def test_read_small_summary_cache_falls_back_on_legacy_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A summary cache table predating the project_guid migration must not 500 the read path."""

    async def fake_execute_clickhouse(_query: str, _params: dict[str, object]):
        raise DatabaseError(
            "Code: 47. DB::Exception: Unknown expression or function identifier "
            "`project_guid` in scope SELECT sample_id FROM "
            "coga.`GRCh38/SNV_INDEL/family_sample_variant_summary` (UNKNOWN_IDENTIFIER)"
        )

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.execute_clickhouse",
        fake_execute_clickhouse,
    )

    # Treated as a cache miss so the caller recomputes from the live entries table.
    assert await _read_small_summary_cache("SELECT 1", {}) == []


@pytest.mark.asyncio
async def test_read_small_summary_cache_reraises_unrelated_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback is scoped to the project_guid case; other failures still surface."""

    async def fake_execute_clickhouse(_query: str, _params: dict[str, object]):
        raise DatabaseError(
            "Code: 47. DB::Exception: Unknown expression or function identifier "
            "`some_other_column` (UNKNOWN_IDENTIFIER)"
        )

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.execute_clickhouse",
        fake_execute_clickhouse,
    )

    with pytest.raises(DatabaseError):
        await _read_small_summary_cache("SELECT 1", {})


def test_clinvar_status_filter_is_exact_not_substring() -> None:
    assert _flexible_status_match("Pathogenic", ["pathogenic"])
    assert not _flexible_status_match("Likely pathogenic", ["pathogenic"])
    assert _flexible_status_match("Pathogenic&risk_factor", ["pathogenic"])
    assert not _flexible_status_match("Conflicting interpretations of pathogenicity", ["pathogenic"])


def test_clinvar_filter_does_not_override_other_annotation_filters() -> None:
    record = _small_variant("v1", "GENE1", calls=[_small_call("PROBAND", "0/1")])
    record.annotations = [
        {
            "gene": "GENE1",
            "impact": "LOW",
            "clinvar": "Pathogenic",
            "gnomad_af": 0.03,
        }
    ]

    assert not _small_record_matches(
        record,
        SmallVariantQueryFilters(
            page=1,
            page_size=100,
            impact=["HIGH"],
            clinvar=["Pathogenic"],
            max_gnomad_af=0.01,
        ),
        [],
        [],
        [],
    )


def test_clickhouse_annotation_filters_are_scoped_to_one_annotation() -> None:
    clauses, params = _small_detail_filter_clauses(
        SmallVariantQueryFilters(
            page=1,
            page_size=100,
            impact=["HIGH"],
            clinvar=["Pathogenic"],
            max_gnomad_af=0.01,
        )
    )
    query = "\n".join(clauses)

    assert "JSONExtract" not in query
    assert "a.impact IN %(detail_impact_terms)s" in query
    assert "hasAny(a.clinvar_terms, %(detail_clinvar_terms)s)" in query
    assert "detail_max_gnomad_af" in query
    assert " AND " in query
    assert params["detail_clinvar_terms"] == ["pathogenic"]


def test_small_annotation_effect_filter_splits_vep_compound_terms() -> None:
    record = _small_variant("v1", "GENE1", calls=[_small_call("PROBAND", "0/1")])
    record.annotations = [
        {
            "gene": "GENE1",
            "impact": "MODERATE",
            "effect": "missense_variant&splice_region_variant",
        }
    ]

    assert _small_record_matches(
        record,
        SmallVariantQueryFilters(page=1, page_size=100, effect=["missense_variant"]),
        [],
        [],
        [],
    )


@pytest.mark.asyncio
async def test_get_family_structural_variants_page_uses_clickhouse_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "GROUP BY sv_type, source_value" in query:
            return [("DEL", "sniffles", 2), ("DUP", "spectre", 1)]
        return [
            (
                2,
                "sv2",
                "1",
                100,
                250,
                "DEL",
                "sniffles",
                None,
                None,
                -150,
                ["PASS"],
                '{"annotations":[]}',
                ["GENE2"],
                ["PROBAND"],
                ["0/1"],
                [42.0],
                [8],
                ["PASS"],
                [None],
            )
        ]

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_fetch_cytoband_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_structural_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_structural_cytoband_map",
        fake_fetch_cytoband_map,
    )

    page = await get_family_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=2,
        page_size=1,
        chr="1",
    )

    assert page.total == 3
    assert page.summary == {"DEL": {"sniffles": 2}, "DUP": {"spectre": 1}}
    assert [str(variant.id) for variant in page.variants] == ["sv2"]
    assert len(queries) == 2
    assert "hasAny(e.calls.sampleId, %(visible_sample_ids)s)" in queries[0][0]
    assert queries[0][1]["visible_sample_ids"] == [
        "PROBAND",
        "sample-proband",
        "MOM",
        "sample-mom",
        "DAD",
        "sample-dad",
        "SIB",
        "sample-sib",
    ]
    assert "LIMIT %(limit)s OFFSET %(offset)s" in queries[1][0]
    assert queries[1][1]["limit"] == 1
    assert queries[1][1]["offset"] == 1


@pytest.mark.asyncio
async def test_get_family_structural_variants_page_count_only_probes_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    async def fake_execute_clickhouse(query: str, _params: dict[str, object]):
        queries.append(query)
        return [(1,)]

    async def fail_summary(*_args, **_kwargs):
        raise AssertionError("count_only must not run the structural summary")

    async def fail_rows(*_args, **_kwargs):
        raise AssertionError("count_only must not fetch full variant rows")

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_structural_variant_summary",
        fail_summary,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_structural_variant_rows",
        fail_rows,
    )

    page = await get_family_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=1,
        count_only=True,
    )

    assert page.total == 1
    assert page.variants == []
    assert page.summary == {}
    # Presence probe only — resolved by a single cheap family_guid existence query.
    assert len(queries) == 1
    assert "family_guid = %(family_guid)s" in queries[0]
    assert "LIMIT 1" in queries[0]
    assert "GROUP BY" not in queries[0]


def _structural_del_row(idx: int) -> tuple:
    return (
        idx,
        f"sv{idx}",
        "1",
        100 + idx,
        250 + idx,
        "DEL",
        "sniffles",
        None,
        None,
        -150,
        ["PASS"],
        '{"annotations":[]}',
        ["GENE2"],
        ["PROBAND"],
        ["0/1"],
        [42.0],
        [8],
        ["PASS"],
        # calls.cn — null for an alignment-based caller that reports no copy number.
        [None],
    )


async def _run_non_native_structural_page(monkeypatch, *, returned_rows: int):
    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        # The non-native path issues a single SV rows fetch (limit = cap + 1).
        return [_structural_del_row(i) for i in range(1, returned_rows + 1)]

    async def fake_review_ids(*_args, **_kwargs):
        return set()

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_fetch_cytoband_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.list_matching_structural_variant_review_ids",
        fake_review_ids,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_structural_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_structural_cytoband_map",
        fake_fetch_cytoband_map,
    )
    return await get_family_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=10,
        type="DEL",  # any of these filters forces the non-native fetch-all path
    )


@pytest.mark.asyncio
async def test_structural_page_non_native_caps_and_flags_estimated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP",
        2,
    )
    # 3 rows returned for a cap of 2 -> fetch was truncated, total is estimated.
    page = await _run_non_native_structural_page(monkeypatch, returned_rows=3)
    assert page.total_is_estimated is True
    assert page.count_limit == 2
    assert page.total == 2
    assert len(page.variants) == 2


@pytest.mark.asyncio
async def test_structural_page_non_native_exact_total_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP",
        2,
    )
    # 2 rows for a cap of 2 -> nothing beyond the cap, exact total.
    page = await _run_non_native_structural_page(monkeypatch, returned_rows=2)
    assert page.total_is_estimated is False
    assert page.count_limit is None
    assert page.total == 2


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_uses_entry_source_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_queries: list[str] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        _ = params
        captured_queries.append(query)
        if "variants/details" in query:
            return [_small_detail_row()]
        return [_small_entry_row()]

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    rows = await _fetch_small_variant_rows(
        _family_context(),
        SmallVariantQueryFilters(
            page=1,
            page_size=1,
            chromosome="1",
            start=0,
            end=100,
            overlap=True,
        ),
    )

    assert len(rows) == 1
    assert rows[0].source == "clair3"
    assert "annotation_version" in captured_queries[1]
    assert "any(e.source) AS source" in captured_queries[0]


def test_small_variant_where_clauses_excludes_imputed_when_requested() -> None:
    clauses, params = _small_variant_where_clauses(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1),
        exclude_imputed=True,
    )
    assert "lowerUTF8(e.source) NOT IN %(imputed_sources)s" in clauses
    assert params["imputed_sources"] == ("glimpse2", "shapeit")


def test_small_variant_where_clauses_keeps_all_sources_by_default() -> None:
    # Internal readers (phased markers, sample-QC) must still see every callset, so
    # the exclusion only applies when explicitly requested.
    clauses, params = _small_variant_where_clauses(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1),
    )
    assert not any("imputed_sources" in clause for clause in clauses)
    assert "imputed_sources" not in params


def test_small_variant_where_clauses_explicit_source_overrides_exclusion() -> None:
    # An explicit source request surfaces that callset even when exclude_imputed is set.
    clauses, params = _small_variant_where_clauses(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1, source="glimpse2"),
        exclude_imputed=True,
    )
    assert "positionCaseInsensitive(e.source, %(source)s) > 0" in clauses
    assert not any("imputed_sources" in clause for clause in clauses)
    assert params["source"] == "glimpse2"
    assert "imputed_sources" not in params


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_excludes_imputed_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_queries: list[str] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        _ = params
        captured_queries.append(query)
        if "variants/details" in query:
            return [_small_detail_row()]
        return [_small_entry_row()]

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    await _fetch_small_variant_rows(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1),
    )

    # The clinician-facing family list hides imputed callsets by default.
    assert any(
        "lowerUTF8(e.source) NOT IN %(imputed_sources)s" in query
        for query in captured_queries
    )


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_maps_stored_sample_uuid_to_display_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_clickhouse(_query: str, _params: dict[str, object]):
        if "variants/details" in _query:
            return [_small_detail_row()]
        return [_small_entry_row(["sample-proband"])]

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    rows = await _fetch_small_variant_rows(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1),
    )

    assert len(rows) == 1
    assert [call.sample for call in rows[0].calls] == ["PROBAND"]


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_pushes_panel_and_review_filters_to_clickhouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "variants/details" in query:
            return [_small_detail_row()]
        return [_small_entry_row()]

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    rows = await _fetch_small_variant_rows(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1),
        panel_constraints=PanelFilterConstraints(
            genes=("GENE2",),
            regions=(Region(chr="1", start=50, end=150),),
        ),
        include_variant_ids={"v2"},
        exclude_variant_ids={"v9"},
        limit=2,
    )

    assert len(rows) == 1
    query, params = queries[0]
    assert "ANY INNER JOIN" not in query
    assert "e.variantId IN %(include_variant_ids)s" in query
    assert "e.variantId NOT IN %(exclude_variant_ids)s" in query
    assert "GRCh38/SNV_INDEL/variants/gene_index" in query
    assert "gi.gene_term IN %(panel_annotation_gene_terms)s" in query
    assert "panel_gene_terms" in params
    assert params["panel_annotation_gene_terms"] == ("gene2",)
    assert "arrayExists((region_chrom, region_start, region_end) ->" in query
    assert "panel_region_chromosomes" in params
    assert "panel_region_chromosomes_0" not in params
    assert params["include_variant_ids"] == ("v2",)
    assert params["exclude_variant_ids"] == ("v9",)


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_keeps_large_panel_region_query_compact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        return []

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    regions = tuple(
        Region(chr=str((index % 22) + 1), start=index * 1000, end=index * 1000 + 100)
        for index in range(3000)
    )

    await _fetch_small_variant_rows(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1),
        panel_constraints=PanelFilterConstraints(regions=regions),
        limit=2,
    )

    query, params = queries[0]
    rendered_query, _ = bind_query(query, params)
    assert "panel_region_chromosomes_0" not in params
    assert len(params["panel_region_chromosomes"]) == 3000
    assert "arrayExists((region_chrom, region_start, region_end) ->" in query
    assert rendered_query.count(" OR ") == 0
    assert len(rendered_query) < 262_144


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_pushes_simple_inheritance_to_clickhouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "variants/details" in query:
            return [_small_detail_row()]
        return [_small_entry_row()]

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    rows = await _fetch_small_variant_rows(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1, inheritance="de_novo_dominant"),
        limit=2,
    )

    assert len(rows) == 1
    query, params = queries[0]
    assert "inheritance_affected_het_0_gts" in params
    assert "inheritance_unaffected_alt_0_gts" in params
    assert "arrayExists((sample_id, gt) ->" in query
    assert "GROUP BY e.key" in query  # list dedups by key (#333)


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_prefilters_expanded_carrier_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "variants/details" in query:
            return [_small_detail_row()]
        return [_small_entry_row(["MOM"])]

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    rows = await _fetch_small_variant_rows(
        _family_context(),
        SmallVariantQueryFilters(page=1, page_size=1, expanded_carrier_screening=True),
        limit=2,
    )

    assert len(rows) == 1
    query, params = queries[0]
    assert "carrier_screen_partner_alt_0_gts" in params
    assert "carrier_screen_partner_alt_1_gts" in params
    assert "length(e.gene_symbols) > 0" in query
    assert " OR " in query


def test_compound_het_partner_map_requires_family_consistent_pairs() -> None:
    records = [
        _small_variant(
            "v1",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
                _small_call("SIB", "0/1"),
            ],
            gene_id="GENE1_ID",
        ),
        _small_variant(
            "v2",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/0"),
            ],
            gene_id="GENE1_ID",
        ),
        _small_variant(
            "v3",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/1"),
            ],
            gene_id="GENE1_ID",
        ),
    ]

    partner_map = _compound_het_partner_map(
        records,
        affected_samples=["PROBAND"],
        unaffected_samples=["MOM", "DAD", "SIB"],
    )

    assert partner_map == {
        "v1": {"v2"},
        "v2": {"v1"},
    }


def test_recessive_inheritance_keeps_homozygous_and_compound_het_variants() -> None:
    records = [
        _small_variant(
            "v1",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
            ],
        ),
        _small_variant(
            "v2",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
            ],
        ),
        _small_variant(
            "v4",
            "GENE2",
            calls=[
                _small_call("PROBAND", "1/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/1"),
            ],
        ),
    ]

    filtered = _inheritance_filter_records(
        records,
        inheritance="recessive",
        affected_samples=["PROBAND"],
        unaffected_samples=["MOM", "DAD"],
    )

    assert [record.variant_id for record in filtered] == ["v1", "v2", "v4"]


def test_normalize_small_variant_inheritance_supports_coga_like_aliases() -> None:
    assert _normalize_small_variant_inheritance("de novo dominant") == "de_novo_dominant"
    assert _normalize_small_variant_inheritance("dominant") == "de_novo_dominant"
    assert _normalize_small_variant_inheritance("recessive_hom") == "recessive_homozygous"
    assert _normalize_small_variant_inheritance("compound heterozygous") == "compound_het"
    assert _normalize_small_variant_inheritance("xlinked") == "x_linked"


def test_de_novo_dominant_and_x_linked_inheritance_filters() -> None:
    records = [
        _small_variant(
            "dom-1",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/0"),
            ],
        ),
        _small_variant(
            "dom-2",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
            ],
        ),
        _small_variant(
            "x-1",
            "GENE2",
            chr="X",
            calls=[
                _small_call("PROBAND", "1/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
                _small_call("SIB", "0/1"),
            ],
        ),
        _small_variant(
            "x-2",
            "GENE2",
            chr="X",
            calls=[
                _small_call("PROBAND", "1/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/1"),
            ],
        ),
    ]
    sample_rows = [
        {"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "male"},
        {"sample_id": "MOM", "role": "mother", "affected": False, "sex": "female"},
        {"sample_id": "DAD", "role": "father", "affected": False, "sex": "male"},
        {"sample_id": "SIB", "role": "sibling", "affected": False, "sex": "female"},
    ]

    dominant_filtered = _inheritance_filter_records(
        records,
        inheritance="de_novo_dominant",
        affected_samples=["PROBAND"],
        unaffected_samples=["MOM", "DAD", "SIB"],
        sample_rows=sample_rows,
    )
    assert [record.variant_id for record in dominant_filtered] == ["dom-1"]

    x_linked_filtered = _inheritance_filter_records(
        records,
        inheritance="x_linked",
        affected_samples=["PROBAND"],
        unaffected_samples=["MOM", "DAD", "SIB"],
        sample_rows=sample_rows,
    )
    assert [record.variant_id for record in x_linked_filtered] == ["x-1"]


@pytest.mark.asyncio
async def test_get_family_small_variants_page_applies_compound_het_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_kwargs: list[dict[str, object]] = []
    records = [
        _small_variant(
            "v1",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
                _small_call("SIB", "0/1"),
            ],
        ),
        _small_variant(
            "v2",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/0"),
            ],
        ),
        _small_variant(
            "v3",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/1"),
            ],
        ),
    ]

    async def fake_fetch_small_variant_rows(_context, filters, **_kwargs):
        fetch_kwargs.append(dict(_kwargs))
        return _inheritance_filter_records(
            records,
            inheritance=filters.inheritance,
            affected_samples=["PROBAND"],
            unaffected_samples=["MOM", "DAD", "SIB"],
            sample_rows=_family_context().sample_rows,
        )

    async def fake_list_matching_review_ids(*_args, **_kwargs):
        return []

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fake_fetch_small_variant_rows,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.list_matching_small_variant_review_ids",
        fake_list_matching_review_ids,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )
    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=50,
        inheritance="compound_het",
    )

    assert page.total == 1
    assert page.variants == []
    assert len(page.variant_groups) == 1
    assert page.variant_groups[0].gene == "GENE1"
    assert [str(variant.id) for variant in page.variant_groups[0].variants] == ["v1", "v2"]
    assert fetch_kwargs[0]["limit"] == 1251


@pytest.mark.asyncio
async def test_get_family_small_variants_page_returns_pair_groups_and_singletons_for_recessive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _small_variant(
            "v1",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
            ],
        ),
        _small_variant(
            "v2",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
            ],
        ),
        _small_variant(
            "v3",
            "GENE2",
            calls=[
                _small_call("PROBAND", "1/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/1"),
            ],
        ),
    ]

    async def fake_fetch_small_variant_rows(*_args, **kwargs):
        excluded_ids = set(kwargs.get("exclude_variant_ids") or [])
        return [record for record in records if record.variant_id not in excluded_ids]

    async def fake_list_matching_review_ids(*_args, **_kwargs):
        return []

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fake_fetch_small_variant_rows,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.list_matching_small_variant_review_ids",
        fake_list_matching_review_ids,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=50,
        inheritance="recessive",
    )

    assert page.total == 2
    assert len(page.variant_groups) == 1
    assert [str(variant.id) for variant in page.variant_groups[0].variants] == ["v1", "v2"]
    assert [str(variant.id) for variant in page.variants] == ["v3"]


@pytest.mark.asyncio
async def test_get_family_small_variants_page_supports_coga_like_inheritance_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _small_variant(
            "dom-1",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/0"),
                _small_call("SIB", "0/0"),
            ],
        ),
        _small_variant(
            "x-1",
            "GENE2",
            chr="X",
            calls=[
                _small_call("PROBAND", "1/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
                _small_call("SIB", "0/1"),
            ],
        ),
        _small_variant(
            "hom-1",
            "GENE3",
            calls=[
                _small_call("PROBAND", "1/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/1"),
            ],
        ),
    ]

    async def fake_fetch_small_variant_rows(_context, filters, **_kwargs):
        return _inheritance_filter_records(
            records,
            inheritance=filters.inheritance,
            affected_samples=["PROBAND"],
            unaffected_samples=["MOM", "DAD", "SIB"],
            sample_rows=_family_context().sample_rows,
        )

    async def fake_list_matching_review_ids(*_args, **_kwargs):
        return []

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    async def fake_count_small_variant_rows_bounded(_context, filters, **_kwargs):
        rows = await fake_fetch_small_variant_rows(_context, filters)
        return len(rows), False

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fake_fetch_small_variant_rows,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.list_matching_small_variant_review_ids",
        fake_list_matching_review_ids,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._count_small_variant_rows_bounded",
        fake_count_small_variant_rows_bounded,
    )

    page_dominant = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=50,
        inheritance="de_novo_dominant",
    )
    assert [str(variant.id) for variant in page_dominant.variants] == ["dom-1"]
    assert page_dominant.variant_groups == []

    page_x_linked = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=50,
        inheritance="x_linked",
    )
    assert [str(variant.id) for variant in page_x_linked.variants] == ["x-1"]
    assert page_x_linked.variant_groups == []

    page_recessive_hom = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=50,
        inheritance="recessive_homozygous",
    )
    assert [str(variant.id) for variant in page_recessive_hom.variants] == ["hom-1"]
    assert page_recessive_hom.variant_groups == []


@pytest.mark.asyncio
async def test_get_family_compound_het_candidates_uses_pair_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _small_variant(
            "v1",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/1"),
                _small_call("DAD", "0/0"),
                _small_call("SIB", "0/1"),
            ],
        ),
        _small_variant(
            "v2",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/0"),
            ],
        ),
        _small_variant(
            "v3",
            "GENE1",
            calls=[
                _small_call("PROBAND", "0/1"),
                _small_call("MOM", "0/0"),
                _small_call("DAD", "0/1"),
                _small_call("SIB", "0/1"),
            ],
        ),
    ]

    async def fake_fetch_small_variant_rows(*_args, **kwargs):
        excluded_ids = set(kwargs.get("exclude_variant_ids") or [])
        return [record for record in records if record.variant_id not in excluded_ids]

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fake_fetch_small_variant_rows,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )
    page = await get_family_compound_het_candidates(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        variant_id="v1",
        limit=10,
    )

    assert page.total == 1
    assert [str(variant.id) for variant in page.variants] == ["v2"]


_COMPOUND_HET_PAIR_CALLS_A = [
    _small_call("PROBAND", "0/1"),
    _small_call("MOM", "0/1"),
    _small_call("DAD", "0/0"),
    _small_call("SIB", "0/1"),
]
_COMPOUND_HET_PAIR_CALLS_B = [
    _small_call("PROBAND", "0/1"),
    _small_call("MOM", "0/0"),
    _small_call("DAD", "0/1"),
    _small_call("SIB", "0/0"),
]


@pytest.mark.asyncio
async def test_compound_het_candidates_scopes_fetch_to_source_gene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v1 = _small_variant("v1", "GENE1", calls=_COMPOUND_HET_PAIR_CALLS_A)
    v2 = _small_variant("v2", "GENE1", calls=_COMPOUND_HET_PAIR_CALLS_B)
    # A different gene that must never be fetched for or paired with v1.
    v_other = _small_variant("vX", "GENE2", calls=_COMPOUND_HET_PAIR_CALLS_B)
    all_records = [v1, v2, v_other]
    fetch_calls: list[dict] = []

    async def fake_fetch(context, filters, **kwargs):
        include = kwargs.get("include_variant_ids")
        fetch_calls.append(
            {
                "gene": filters.gene,
                "include": list(include) if include else None,
                "limit": kwargs.get("limit"),
            }
        )
        if include:
            wanted = set(include)
            return [r for r in all_records if r.variant_id in wanted]
        if filters.gene:  # emulate the SQL gene filter (matches gene_symbols)
            term = filters.gene.upper()
            return [r for r in all_records if term in {g.upper() for g in r.gene_symbols}]
        return all_records

    async def fake_review_map(*_a, **_k):
        return {}

    async def fake_metric_map(*_a, **_k):
        return {}

    monkeypatch.setattr("backend.app.services.clickhouse_family_variants._fetch_small_variant_rows", fake_fetch)
    monkeypatch.setattr("backend.app.services.clickhouse_family_variants.get_small_variant_review_map", fake_review_map)
    monkeypatch.setattr("backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map", fake_metric_map)

    page = await get_family_compound_het_candidates(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        variant_id="v1",
        limit=10,
    )

    # Same partner a whole-family scan would return.
    assert [str(variant.id) for variant in page.variants] == ["v2"]
    # Source variant fetched by id first, then the scan scoped to its gene only.
    assert fetch_calls[0]["include"] == ["v1"]
    assert any(call["gene"] == "GENE1" for call in fetch_calls[1:])
    # The other gene was never queried (no whole-family scan).
    assert all(call["gene"] != "GENE2" for call in fetch_calls)
    assert all(call["include"] is not None or call["gene"] == "GENE1" for call in fetch_calls)
    # P0-6: the gene-scoped partner scan is bounded (not an unbounded fetch).
    scan_calls = [call for call in fetch_calls if call["include"] is None]
    assert scan_calls
    assert all(
        call["limit"] == _SMALL_INHERITANCE_MAX_CANDIDATE_ROWS + 1 for call in scan_calls
    )


@pytest.mark.asyncio
async def test_compound_het_candidates_falls_back_without_gene_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _no_gene_name(variant_id: str, calls: list[SmallVariantCall]) -> SmallVariantRecord:
        return SmallVariantRecord(
            variant_key=None,
            variant_id=variant_id,
            chr="1",
            start=100,
            end=100,
            ref="A",
            alt="G",
            source="clair3",
            rsid=None,
            filters=[],
            gene_symbols=[],  # no gene name; only a gene_id key
            annotations=[{"gene_id": "ENSG1"}],
            calls=calls,
        )

    v1 = _no_gene_name("v1", _COMPOUND_HET_PAIR_CALLS_A)
    v2 = _no_gene_name("v2", _COMPOUND_HET_PAIR_CALLS_B)
    all_records = [v1, v2]
    genes_seen: list[str | None] = []
    scan_limits: list[int | None] = []

    async def fake_fetch(context, filters, **kwargs):
        include = kwargs.get("include_variant_ids")
        if include:
            wanted = set(include)
            return [r for r in all_records if r.variant_id in wanted]
        genes_seen.append(filters.gene)
        scan_limits.append(kwargs.get("limit"))
        return all_records

    async def fake_review_map(*_a, **_k):
        return {}

    async def fake_metric_map(*_a, **_k):
        return {}

    monkeypatch.setattr("backend.app.services.clickhouse_family_variants._fetch_small_variant_rows", fake_fetch)
    monkeypatch.setattr("backend.app.services.clickhouse_family_variants.get_small_variant_review_map", fake_review_map)
    monkeypatch.setattr("backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map", fake_metric_map)

    page = await get_family_compound_het_candidates(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        variant_id="v1",
        limit=10,
    )

    # gene_id-only source still finds the partner via the whole-family fallback.
    assert [str(variant.id) for variant in page.variants] == ["v2"]
    assert genes_seen == [None]
    # P0-6: the gene_id-only whole-family fallback is intentionally left UNBOUNDED — a
    # blind row cap could silently drop a genuine same-gene_id partner (rows are ordered
    # by genomic position). Guard against a regression that re-introduces a silent cap.
    assert scan_limits == [None]


@pytest.mark.asyncio
async def test_get_family_small_variants_page_excludes_review_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _small_variant("v1", "GENE1", calls=[_small_call("PROBAND", "0/1")]),
        _small_variant("v2", "GENE2", calls=[_small_call("PROBAND", "0/1")]),
        _small_variant("v3", "GENE3", calls=[_small_call("PROBAND", "0/1")]),
    ]

    async def fake_fetch_small_variant_rows(*_args, **kwargs):
        excluded_ids = set(kwargs.get("exclude_variant_ids") or [])
        return [record for record in records if record.variant_id not in excluded_ids]

    async def fake_list_matching_review_ids(*_args, **kwargs):
        if kwargs.get("tags") == ["excluded"]:
            return {"v2"}
        return set()

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    async def fake_count_small_variant_rows_bounded(*_args, **_kwargs):
        return 2, False

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fake_fetch_small_variant_rows,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.list_matching_small_variant_review_ids",
        fake_list_matching_review_ids,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._count_small_variant_rows_bounded",
        fake_count_small_variant_rows_bounded,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=50,
        exclude_review_tags=["excluded"],
    )

    assert page.total == 2
    assert [str(variant.id) for variant in page.variants] == ["v1", "v3"]


@pytest.mark.asyncio
async def test_small_variant_track_mode_samples_across_filtered_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _small_variant(
            f"v{index}",
            "GENE1",
            calls=[_small_call("PROBAND", "0/1")],
            start=index,
        )
        for index in range(10)
    ]

    async def fake_fetch_small_variant_rows(*_args, **_kwargs):
        return records

    async def fake_list_matching_review_ids(*_args, **_kwargs):
        return []

    async def fake_get_review_map(*_args, **_kwargs):
        return {}

    async def fake_get_metric_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_small_variant_rows",
        fake_fetch_small_variant_rows,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.list_matching_small_variant_review_ids",
        fake_list_matching_review_ids,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants.get_small_variant_review_map",
        fake_get_review_map,
    )
    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._fetch_gene_constraint_metric_map",
        fake_get_metric_map,
    )

    page = await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        page=1,
        page_size=5,
        track_mode=True,
    )

    assert page.total == 0
    assert [str(variant.id) for variant in page.variants] == ["v0", "v2", "v4", "v6", "v9"]


def test_small_panel_filter_skips_region_inlining_for_large_gene_panels():
    """A large gene panel (e.g. the Mendeliome) must not inline thousands of region
    triples into the query — gene-symbol + gene-index matching covers it."""
    from backend.app.services.clickhouse_family_variants import (
        _PANEL_REGION_INLINE_LIMIT,
        PanelFilterConstraints,
        Region,
        _small_panel_filter_condition,
    )

    context = _family_context()
    filters = SmallVariantQueryFilters(page=1, page_size=100)
    regions = tuple(
        Region(chr="chr1", start=i * 100, end=i * 100 + 50)
        for i in range(_PANEL_REGION_INLINE_LIMIT + 5)
    )
    params: dict = {}
    condition = _small_panel_filter_condition(
        context,
        filters,
        PanelFilterConstraints(genes=("BRCA1", "TP53"), regions=regions),
        params=params,
    )
    assert condition is not None
    # No region arrays were inlined (the expensive, query-bloating part).
    assert not any(key.startswith("panel_region") for key in params)
    # Gene matching is still present.
    assert any(key.startswith("panel_gene") for key in params)


def test_small_panel_filter_keeps_regions_for_normal_panels():
    from backend.app.services.clickhouse_family_variants import (
        PanelFilterConstraints,
        Region,
        _small_panel_filter_condition,
    )

    context = _family_context()
    filters = SmallVariantQueryFilters(page=1, page_size=100)
    params: dict = {}
    condition = _small_panel_filter_condition(
        context,
        filters,
        PanelFilterConstraints(
            genes=("BRCA1",),
            regions=(Region(chr="chr17", start=43044295, end=43125483),),
        ),
        params=params,
    )
    assert condition is not None
    # A normal-sized panel still inlines its regions.
    assert any(key.startswith("panel_region") for key in params)


@pytest.mark.asyncio
async def test_prioritized_small_variants_tie_order_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P0-4: two variants that score identically must rank in a fully-determined,
    # fetch-order-independent order (the rank is frozen into the cache/report). The
    # str(v.id) tiebreaker makes the order total regardless of the fetch order.
    calls = [_small_call("PROBAND", "0/1")]
    rec_a = _small_variant("1-100-A-G", "GENE1", calls=calls, start=100)
    rec_b = _small_variant("1-200-A-G", "GENE1", calls=calls, start=200)

    m = "backend.app.services.clickhouse_family_variants."

    async def _no_hpo(*_a, **_k):
        return (set(), {})

    async def _hashes(*_a, **_k):
        return ("inputs-hash", "base-hash")

    async def _none(*_a, **_k):
        return None

    async def _empty(*_a, **_k):
        return {}

    async def _run(order):
        async def _fetch(*_a, **_k):
            return list(order)

        monkeypatch.setattr(m + "_affected_present_hpo", _no_hpo)
        monkeypatch.setattr(m + "compute_ranking_hashes", _hashes)
        monkeypatch.setattr(m + "get_cached_ranking", _none)
        monkeypatch.setattr(m + "_serve_subpanel_from_superset", _none)
        monkeypatch.setattr(m + "_fetch_small_variant_rows", _fetch)
        monkeypatch.setattr(m + "_fetch_gene_constraint_metric_map", _empty)
        monkeypatch.setattr(m + "_segregation_modes_by_variant", lambda *a, **k: {})
        monkeypatch.setattr(m + "canonical_filters", lambda *a, **k: {})
        monkeypatch.setattr(m + "store_ranking", _none)
        monkeypatch.setattr(m + "_hydrate_small_variant_outs", _none)
        monkeypatch.setattr(m + "_small_record_matches", lambda *a, **k: True)
        page = await _prioritized_small_variants_page(
            None,  # type: ignore[arg-type]
            context=_family_context(),
            filters=SmallVariantQueryFilters(page=1, page_size=50),
            page=1,
            page_size=50,
            panel_constraints=PanelFilterConstraints(),
            review_variant_ids=None,
            excluded_review_variant_ids=set(),
            include_review_filter_active=False,
            include_regions=[],
            exclude_regions=[],
            exclude_gene_regions=[],
            exclude_gene_terms=[],
            small_variant_summary=None,
        )
        return [str(variant.id) for variant in page.variants]

    forward = await _run([rec_a, rec_b])
    reverse_ = await _run([rec_b, rec_a])
    # Determinism: identical scores resolve to the same order regardless of fetch order.
    assert forward == reverse_
    # reverse=True applies to the whole key tuple, so equal-score ties order by
    # DESCENDING variant id.
    assert forward == ["1-200-A-G", "1-100-A-G"]


@pytest.mark.asyncio
async def test_prioritized_structural_variants_tie_order_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P0-4 (SV path): two SVs that score identically must rank in a fully-determined,
    # fetch-order-independent order via the item[0].variant_id tiebreaker.
    def _sv(variant_id: str, *, start: int) -> StructuralVariantRecord:
        return StructuralVariantRecord(
            variant_key=None,
            variant_id=variant_id,
            chr="1",
            start=start,
            end=start + 100,
            sv_type="DEL",
            source="sniffles",
            remote_chr=None,
            remote_start=None,
            remote_end=None,
            sv_len=-100,
            filters=["PASS"],
            gene_symbols=["GENE1"],
            annotations=[{}],
            calls=[
                StructuralVariantCall(
                    sample="PROBAND",
                    gt="0/1",
                    qual=None,
                    read_support=None,
                    filter="PASS",
                )
            ],
        )

    rec_a = _sv("sv-100", start=100)
    rec_b = _sv("sv-200", start=200)

    m = "backend.app.services.clickhouse_family_variants."

    async def _no_hpo(*_a, **_k):
        return (set(), {})

    async def _empty_set(*_a, **_k):
        return set()

    async def _empty(*_a, **_k):
        return {}

    async def _run(order):
        async def _fetch(*_a, **_k):
            return list(order)

        monkeypatch.setattr(m + "_fetch_structural_variant_rows", _fetch)
        monkeypatch.setattr(m + "_structural_record_matches", lambda *a, **k: True)
        monkeypatch.setattr(m + "_affected_present_hpo", _no_hpo)
        monkeypatch.setattr(m + "list_matching_structural_variant_review_ids", _empty_set)
        monkeypatch.setattr(m + "get_structural_variant_review_map", _empty)
        monkeypatch.setattr(m + "_fetch_structural_cytoband_map", _empty)
        page = await _prioritized_structural_variants_page(
            None,  # type: ignore[arg-type]
            context=_family_context(),
            filters=StructuralVariantQueryFilters(page=1, page_size=50),
            page=1,
            page_size=50,
            selected_samples=["PROBAND"],
        )
        return [str(variant.id) for variant in page.variants]

    forward = await _run([rec_a, rec_b])
    reverse_ = await _run([rec_b, rec_a])
    assert forward == reverse_
    # reverse=True -> equal-score ties order by DESCENDING variant id.
    assert forward == ["sv-200", "sv-100"]


def test_clamp_small_variant_page_bounds_deep_offset() -> None:
    from backend.app.services.clickhouse_family_variants import (
        _SMALL_COUNT_LIMIT,
        _clamp_small_variant_page,
        _page_offset,
    )

    # A huge page is clamped so the resulting OFFSET stays bounded (no deep scan+skip);
    # the total is capped at _SMALL_COUNT_LIMIT, so nothing real is lost.
    clamped = _clamp_small_variant_page(10_000_000, 100)
    assert clamped < 10_000_000
    assert _page_offset(clamped, 100) <= _SMALL_COUNT_LIMIT + 100

    # Real pages within the bounded total are preserved unchanged.
    assert _clamp_small_variant_page(1, 100) == 1
    assert _clamp_small_variant_page(5, 100) == 5

    # Degenerate inputs are handled: page < 1 floors to 1; page_size 0 yields no offset
    # anyway (so the page is left as-is).
    assert _clamp_small_variant_page(0, 100) == 1
    assert _clamp_small_variant_page(10_000_000, 0) == 10_000_000
    assert _page_offset(_clamp_small_variant_page(10_000_000, 0), 0) == 0
