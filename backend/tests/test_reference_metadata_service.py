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
    rows = [
        _gene_row("ENST_OTHER", end=500),
        _gene_row(
            "ENST_CANON.2",
            gene_info_extra={"ensembl_canonical_transcript": "ENST_CANON"},
        ),
        _gene_row(
            "NM_MANE.1",
            gene_info_extra={"clingen_gene_facts": {"mane_select_transcript": "NM_MANE"}},
        ),
    ]

    selected = reference_metadata_service._select_preferred_gene_rows(rows)

    assert [row["gene_id"] for row in selected] == ["NM_MANE.1"]


def test_gene_region_uses_canonical_when_mane_select_is_absent() -> None:
    rows = [
        _gene_row("ENST_LONG", end=1000),
        _gene_row(
            "ENST_CANON",
            end=250,
            extra={"canonical": True},
        ),
    ]

    selected = reference_metadata_service._select_preferred_gene_rows(rows)

    assert [row["gene_id"] for row in selected] == ["ENST_CANON"]


def test_gene_region_keeps_one_transcript_per_gene() -> None:
    rows = [
        _gene_row("GENE1_TX1", symbol="GENE1"),
        _gene_row("GENE1_TX2", symbol="GENE1", extra={"canonical": True}),
        _gene_row("GENE2_TX1", symbol="GENE2"),
    ]

    selected = reference_metadata_service._select_preferred_gene_rows(rows)

    assert [row["gene_id"] for row in selected] == ["GENE1_TX2", "GENE2_TX1"]


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
