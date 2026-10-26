from backend.app.services import reference_metadata_service


def _gene_row(
    transcript_id: str,
    *,
    symbol: str = "GENE1",
    start: int = 100,
    end: int = 200,
    exons: list[dict[str, int | str]] | None = None,
    extra: dict[str, object] | None = None,
    gene_info_extra: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": transcript_id,
        "gene_id": transcript_id,
        "hgnc_symbol": symbol,
        "chr": "1",
        "start": start,
        "end": end,
        "exons": exons or [{"start": start, "end": end, "name": "exon1"}],
        "strand": 1,
        "extra": {"transcript_id": transcript_id, **(extra or {})},
        "gene_info_extra": gene_info_extra or {},
    }


def test_gene_region_prefers_mane_select_over_canonical_and_other_transcripts() -> None:
    # HGNC names the gene's MANE Select transcripts, an Ensembl and a RefSeq one, which
    # recognises an uploaded RefSeq row too.
    hgnc = {"hgnc_identifiers": {"mane_select": ["ENST_MANE.4", "NM_MANE.2"]}}
    rows = [
        _gene_row("ENST_OTHER", end=500, gene_info_extra=hgnc),
        _gene_row("ENST_CANON.2", extra={"tags": ["basic", "Ensembl_canonical"]}, gene_info_extra=hgnc),
        _gene_row("NM_MANE.1", gene_info_extra=hgnc),
    ]

    selected = reference_metadata_service._select_preferred_gene_rows(rows)

    assert [row["gene_id"] for row in selected] == ["NM_MANE.1"]


def test_gene_region_uses_canonical_when_mane_select_is_absent() -> None:
    rows = [
        _gene_row("ENST_LONG", end=1000),
        # GENCODE tags it so; its import keeps the tags (gencode_import).
        _gene_row(
            "ENST_CANON",
            end=250,
            extra={"tags": ["basic", "Ensembl_canonical"]},
        ),
    ]

    selected = reference_metadata_service._select_preferred_gene_rows(rows)

    # ENG-8. Before: the canonical transcript was read from keys no import writes, never
    # found, and the longest transcript won.
    assert [row["gene_id"] for row in selected] == ["ENST_CANON"]


def test_gene_region_keeps_one_transcript_per_gene() -> None:
    rows = [
        _gene_row("GENE1_TX1", symbol="GENE1"),
        _gene_row("GENE1_TX2", symbol="GENE1", extra={"tags": ["Ensembl_canonical"]}),
        _gene_row("GENE2_TX1", symbol="GENE2"),
    ]

    selected = reference_metadata_service._select_preferred_gene_rows(rows)

    assert [row["gene_id"] for row in selected] == ["GENE1_TX2", "GENE2_TX1"]


def test_gene_region_reads_mane_select_as_gencode_writes_it_and_no_key_no_import_writes() -> None:
    # GENCODE's import marks the MANE Select transcript's row (`mane_select`); keys no import
    # writes name nothing (ENG-8).
    rows = [
        _gene_row("ENST_LONG", end=1000, gene_info_extra={"ensembl_canonical_transcript": "ENST_LONG"}),
        _gene_row("ENST_MANE", end=250, extra={"mane_select": True}),
    ]
    assert [row["gene_id"] for row in reference_metadata_service._select_preferred_gene_rows(rows)] == ["ENST_MANE"]

    rows = [
        _gene_row("ENST_LONG", end=1000),
        _gene_row("ENST_NAMED", end=250, gene_info_extra={"ensembl_canonical_transcript": "ENST_NAMED"}),
    ]
    assert [row["gene_id"] for row in reference_metadata_service._select_preferred_gene_rows(rows)] == ["ENST_LONG"]


def test_require_region_window_rejects_missing_window() -> None:
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        reference_metadata_service._require_region_window(0, 0)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        reference_metadata_service._require_region_window(500, 100)
    # A real window (start < end) is accepted.
    reference_metadata_service._require_region_window(100, 200)
