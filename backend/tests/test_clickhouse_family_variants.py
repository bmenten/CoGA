from __future__ import annotations

import pytest

from backend.app.services.clickhouse_family_variants import (
    SmallVariantCall,
    SmallVariantRecord,
    _apply_small_inheritance_filter,
    _compound_het_partner_map,
    _fetch_small_variant_rows,
    _normalize_small_variant_inheritance,
    get_family_compound_het_candidates,
    get_family_small_variants_page,
    get_family_structural_variants_page,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


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


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="demo_family",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "male"},
            {"sample_id": "MOM", "role": "mother", "affected": False, "sex": "female"},
            {"sample_id": "DAD", "role": "father", "affected": False, "sex": "male"},
            {"sample_id": "SIB", "role": "sibling", "affected": False, "sex": "female"},
        ],
        sample_uuid_to_name={},
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


@pytest.mark.asyncio
async def test_get_family_small_variants_page_uses_clickhouse_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        queries.append((query, dict(params)))
        if "SELECT count()" in query:
            return [(3,)]
        return [
            (
                2,
                "v2",
                "1",
                101,
                "A",
                "G",
                "clair3",
                [],
                [],
                None,
                '{"annotations":[{"gene":"GENE2"}]}',
                ["GENE2"],
                ["PROBAND"],
                ["0/1"],
                [99],
                [30],
                [0.5],
                [[0.5]],
                [[12, 18]],
                [None],
            )
        ]

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
        page=2,
        page_size=1,
        chr="1",
    )

    assert page.total == 3
    assert [str(variant.id) for variant in page.variants] == ["v2"]
    assert len(queries) == 2
    assert "LIMIT %(limit)s OFFSET %(offset)s" in queries[1][0]
    assert queries[1][1]["limit"] == 1
    assert queries[1][1]["offset"] == 1


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
    assert "LIMIT %(limit)s OFFSET %(offset)s" in queries[1][0]
    assert queries[1][1]["limit"] == 1
    assert queries[1][1]["offset"] == 1


@pytest.mark.asyncio
async def test_fetch_small_variant_rows_uses_details_source_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_execute_clickhouse(query: str, params: dict[str, object]):
        captured["query"] = query
        captured["params"] = params
        return []

    monkeypatch.setattr(
        "backend.app.services.clickhouse_family_variants._execute_clickhouse",
        fake_execute_clickhouse,
    )

    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="demo_family",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={},
        sample_name_to_uuid={},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    rows = await _fetch_small_variant_rows(
        context,
        SmallVariantQueryFilters(
            page=1,
            page_size=1,
            chromosome="1",
            start=0,
            end=100,
            overlap=True,
        ),
    )

    assert rows == []
    assert "any(d.source) AS source" in str(captured["query"])
    assert "any(e.source) AS source" not in str(captured["query"])


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

    filtered = _apply_small_inheritance_filter(
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

    dominant_filtered = _apply_small_inheritance_filter(
        records,
        inheritance="de_novo_dominant",
        affected_samples=["PROBAND"],
        unaffected_samples=["MOM", "DAD", "SIB"],
        sample_rows=sample_rows,
    )
    assert [record.variant_id for record in dominant_filtered] == ["dom-1"]

    x_linked_filtered = _apply_small_inheritance_filter(
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
        page_size=50,
        inheritance="compound_het",
    )

    assert page.total == 1
    assert page.variants == []
    assert len(page.variant_groups) == 1
    assert page.variant_groups[0].gene == "GENE1"
    assert [str(variant.id) for variant in page.variant_groups[0].variants] == ["v1", "v2"]


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

    async def fake_fetch_small_variant_rows(*_args, **_kwargs):
        return records

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


@pytest.mark.asyncio
async def test_get_family_small_variants_page_excludes_review_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _small_variant("v1", "GENE1", calls=[_small_call("PROBAND", "0/1")]),
        _small_variant("v2", "GENE2", calls=[_small_call("PROBAND", "0/1")]),
        _small_variant("v3", "GENE3", calls=[_small_call("PROBAND", "0/1")]),
    ]

    async def fake_fetch_small_variant_rows(*_args, **_kwargs):
        return records

    async def fake_list_matching_review_ids(*_args, **kwargs):
        if kwargs.get("tags") == ["excluded"]:
            return {"v2"}
        return set()

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
