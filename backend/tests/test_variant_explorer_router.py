from __future__ import annotations

from backend.app.routers.variant_explorer import (
    _EXPORT_COLUMNS,
    _export_cell,
    _parse_sample_genotype_filters,
)
from backend.app.schemas import GlobalVariantRowOut


def test_export_cell_formats_scalars_lists_and_none() -> None:
    row = GlobalVariantRowOut(
        key="1",
        variant_id="1-100-A-T",
        chr="1",
        pos=100,
        ref="A",
        alt="T",
        rsid=None,
        gene="BRCA1",
        gene_symbols=["BRCA1", "RND1"],
        effects=["missense_variant"],
        tags=["candidate", "review"],
        total_samples=3,
    )
    # Scalars stringify, None becomes empty, lists join on "; ".
    assert _export_cell(row, "gene") == "BRCA1"
    assert _export_cell(row, "pos") == "100"
    assert _export_cell(row, "total_samples") == "3"
    assert _export_cell(row, "rsid") == ""
    assert _export_cell(row, "gene_symbols") == "BRCA1; RND1"
    assert _export_cell(row, "tags") == "candidate; review"


def test_export_columns_cover_every_row_field() -> None:
    # Every export field must exist on the row model so headers never desync.
    valid_fields = set(GlobalVariantRowOut.model_fields)
    for field, label in _EXPORT_COLUMNS:
        assert field in valid_fields, f"unknown export field {field!r}"
        assert label.strip()


def test_sample_genotype_values_allow_a_colon_in_the_sample_id() -> None:
    # The page sends "<sample>:<mode>". Split on the first ":", "LAB:1:hom" would read as
    # sample "LAB"; the mode is the text after the last ":", when it is one.
    assert _parse_sample_genotype_filters(["LAB:1:hom", "S1:het", "S2:het_hom"]) == [
        ("LAB:1", "hom"),
        ("S1", "het"),
        ("S2", "het_hom"),
    ]
    # Without a mode, the whole value is the sample id and both genotypes count.
    assert _parse_sample_genotype_filters(["LAB:1", "S3", "S4:", " S5 : HOM ", ""]) == [
        ("LAB:1", "het_hom"),
        ("S3", "het_hom"),
        ("S4", "het_hom"),
        ("S5", "hom"),
    ]


def test_the_export_says_how_many_families_its_summary_did_not_hold_as_they_were(monkeypatch) -> None:
    import asyncio

    from backend.app.routers import variant_explorer as router
    from backend.app.services.variant_explorer_service import GlobalVariantExport, GlobalVariantFilters

    async def export(*_args, **_kwargs):
        return GlobalVariantExport(assembly_name="GRCh38", rows=[], truncated_reason=None, limit=50_000, pending_families=3)

    monkeypatch.setattr(router, "export_global_small_variants", export)
    response = asyncio.run(
        router.export_global_small_variants_csv(filters=GlobalVariantFilters(), session=None, user=None)  # type: ignore[arg-type]
    )
    assert response.headers["X-CoGA-Export-Pending-Families"] == "3"


def test_the_views_read_the_filters_of_the_variant_view(monkeypatch) -> None:
    import asyncio

    from backend.app.routers import variant_explorer as router
    from backend.app.services.variant_explorer_service import GlobalVariantFilters

    seen: dict[str, dict] = {}

    def recorder(name: str):
        async def view(_session, **kwargs):
            seen[name] = kwargs
            return name

        return view

    for name in ("explorer_gene_view", "explorer_transcript_view", "explorer_frequency_view"):
        monkeypatch.setattr(router, name, recorder(name))
    filters = GlobalVariantFilters(gene="SYNGENE1", min_total_samples=2, gnomad_af_class="absent")
    asyncio.run(router.list_explorer_genes(assembly_id="a1", sort="carriers", order="asc", offset=100, page_size=50, filters=filters, session=None, user=None))  # type: ignore[arg-type]
    asyncio.run(router.list_explorer_transcripts(assembly_id="a1", transcripts="mane", sort="variants", order="desc", offset=0, page_size=100, filters=filters, session=None, user=None))  # type: ignore[arg-type]
    asyncio.run(router.list_explorer_frequency(assembly_id="a1", filters=filters, session=None, user=None))  # type: ignore[arg-type]
    assert seen["explorer_gene_view"] == {
        "user": None, "filters": filters, "assembly_id": "a1", "sort": "carriers", "order": "asc", "offset": 100, "page_size": 50,
    }
    assert seen["explorer_transcript_view"]["transcripts"] == "mane"
    assert seen["explorer_frequency_view"] == {"user": None, "filters": filters, "assembly_id": "a1"}
