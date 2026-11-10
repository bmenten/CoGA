"""The reference datasets of an assembly (``reference_metadata_service``): the transcript a gene's
region is read from, the region window, and an admin's upload of a dataset.

A gene's region is its MANE Select transcript's, as HGNC names it or GENCODE marks it, else its
Ensembl-canonical transcript's, else its longest; one transcript per gene (ENG-8). The release
each import records is tested in ``test_reference_import_versions.py``, the pinned files the
start loads in ``test_reference_file_pins.py``. All values are synthetic.
"""

import asyncio
import json

import pytest
from fastapi import HTTPException

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

    # ENG-8: the canonical transcript is read from the keys an import writes, and wins over
    # the longest transcript.
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
    with pytest.raises(HTTPException) as exc:
        reference_metadata_service._require_region_window(0, 0)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        reference_metadata_service._require_region_window(500, 100)
    # A real window (start < end) is accepted.
    assert reference_metadata_service._require_region_window(100, 200) is None


# ---- A reference upload (apply_reference_dataset_text) ------------------------------------------
#
# An upload replaces a loaded dataset only when the admin asks to overwrite it, deleting the
# loaded rows before it inserts the new ones; a file without one usable row is refused before
# anything is written; a clinical CNV's details are stored with their HTML made safe (they are
# rendered as HTML).

ASSEMBLY_ID = "00000000-0000-4000-8000-0000000000a8"


class _UploadSession:
    """Records each statement with its parameters, and whether the upload committed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.calls.append((" ".join(str(statement).split()), params))

    async def commit(self) -> None:
        self.committed = True

    def statements(self) -> list[str]:
        return [sql.split(" (")[0].split(" WHERE")[0] for sql, _params in self.calls]

    def rows(self, table: str) -> list[dict]:
        rows: list[dict] = []
        for sql, params in self.calls:
            if f"INSERT INTO {table} " in sql:
                rows.extend(params if isinstance(params, list) else [params])
        return rows


def _loaded(monkeypatch: pytest.MonkeyPatch, rows_loaded: int) -> None:
    """The GRCh38 assembly, holding ``rows_loaded`` rows of the dataset uploaded."""

    async def assembly(_session, assembly_id):
        return {"id": assembly_id, "assembly_name": "GRCh38", "version": "p14"}

    async def loaded(*_args, **_kwargs):
        return rows_loaded

    monkeypatch.setattr(reference_metadata_service, "_get_assembly_by_id", assembly)
    monkeypatch.setattr(reference_metadata_service, "_assembly_dataset_count", loaded)


def _upload(session: _UploadSession, dataset_type: str, text_value: str, *, overwrite: bool):
    return asyncio.run(
        reference_metadata_service.apply_reference_dataset_text(
            session,  # type: ignore[arg-type]
            assembly_id=ASSEMBLY_ID,
            dataset_type=dataset_type,  # type: ignore[arg-type]
            text_value=text_value,
            overwrite=overwrite,
            performed_by="admin@example.org",
            source="upload",
        )
    )


_CLINICAL_CNV = "chr7\t73330452\t74799773\tWilliams-Beuren\tISCA\tISCA-37446\t.\t.\t.\n"


def test_an_upload_over_a_loaded_dataset_is_refused_without_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    _loaded(monkeypatch, rows_loaded=3)
    session = _UploadSession()

    with pytest.raises(HTTPException) as refused:
        _upload(session, "clinical_cnvs", _CLINICAL_CNV, overwrite=False)

    assert (refused.value.status_code, refused.value.detail) == (409, "clinical cnvs already exist for this assembly")
    assert (session.calls, session.committed) == ([], False)


def test_an_overwrite_deletes_the_loaded_rows_before_it_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    _loaded(monkeypatch, rows_loaded=3)
    session = _UploadSession()

    result = _upload(session, "clinical_cnvs", _CLINICAL_CNV, overwrite=True)

    assert session.statements() == [
        "DELETE FROM clinical_cnvs",
        "INSERT INTO clinical_cnvs",
        "INSERT INTO reference_dataset_imports",
    ]
    assert session.calls[0][1] == {"assembly_id": ASSEMBLY_ID}
    assert (result.inserted, result.replaced) == (1, True)
    [record] = session.rows("reference_dataset_imports")
    assert (record["replaced"], record["inserted"], record["performed_by"]) == (True, 1, "admin@example.org")
    assert session.committed


@pytest.mark.parametrize(
    ("dataset_type", "text_value", "detail"),
    [
        ("cytobands", "#chrom\tstart\tend\tname\tstain\nchr1\tx\t10\tp36.33\tgneg\n", "No valid cytoband rows found"),
        ("genes", "chr17\t43044295\t43125364\tBRCA1\n", "No valid gene rows found"),
        ("blacklist", "chrom\tstart\tend\tlabel\n", "No valid blacklist rows found"),
        ("clinical_cnvs", "track name=cnvs\nchr7\tstart\t74799773\tWilliams-Beuren\n", "No valid clinical CNV rows found"),
        # A ClinGen recurrent-CNV row (orange): only the black LCR anchors are duplications.
        (
            "segmental_duplications",
            "chr22\t18900000\t21500000\t22q11.2 recurrent region\t0\t.\t18900000\t21500000\t255,128,0\n",
            "No valid segmental duplication rows found",
        ),
        ("dgv", "variantaccession\tchr\tstart\tend\tvarianttype\tvariantsubtype\n", "No valid DGV variant rows found"),
    ],
)
def test_a_file_without_one_usable_row_is_refused_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch, dataset_type: str, text_value: str, detail: str
) -> None:
    _loaded(monkeypatch, rows_loaded=0)
    session = _UploadSession()

    with pytest.raises(HTTPException) as refused:
        _upload(session, dataset_type, text_value, overwrite=True)

    assert (refused.value.status_code, refused.value.detail) == (400, detail)
    assert (session.calls, session.committed) == ([], False)


def test_an_uploaded_clinical_cnv_is_stored_with_its_html_made_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    # The bedDetail-like format: type, ..., label and the details HTML in columns 10 and 11.
    details = '<b>Williams</b><script>alert(1)</script><a href="javascript:alert(1)">OMIM</a>'
    row = "\t".join(["chr7", "73330452", "74799773", "deletion", "0", ".", "0", "0", "0", "Williams-Beuren", details])
    _loaded(monkeypatch, rows_loaded=0)
    session = _UploadSession()

    _upload(session, "clinical_cnvs", row + "\n", overwrite=False)

    [stored] = session.rows("clinical_cnvs")
    assert (stored["chr"], stored["start"], stored["end"]) == ("7", 73330452, 74799773)
    assert (stored["type"], stored["label"]) == ("deletion", "Williams-Beuren")
    assert "<b>Williams</b>" in stored["details_html"]
    assert "<script" not in stored["details_html"]
    assert "javascript:" not in stored["details_html"]


def test_a_gene_table_upload_reads_each_transcript_with_its_exons(monkeypatch: pytest.MonkeyPatch) -> None:
    # The UCSC-table text the reference import converts a refGene track to: chrom, start, end,
    # gene, score, strand, CCDS, transcript, exon count, exons, intron count, introns.
    text_value = (
        "#chrom\tstart\tend\tgene\n"
        "chr17\t43044295\t43125364\tBRCA1\t0\t-\tCCDS11453\tNM_007294\t2\t43044295-43045802,bad,43047643-43049194\t1\t43045802-43047643\n"
        "chr17\tx\t43125364\tBRCA1\t0\t-\t\tNM_000000\t0\t\t0\t\n"
    )
    _loaded(monkeypatch, rows_loaded=0)
    session = _UploadSession()

    result = _upload(session, "genes", text_value, overwrite=False)

    [gene] = session.rows("genes")
    assert result.inserted == 1
    assert (gene["gene_id"], gene["hgnc_symbol"], gene["chr"], gene["strand"]) == ("NM_007294", "BRCA1", "17", -1)
    # A malformed exon interval is left out, not the transcript; each exon keeps the number of
    # its place in the list.
    assert json.loads(gene["exons"]) == [
        {"name": "exon1", "start": 43044295, "end": 43045802},
        {"name": "exon3", "start": 43047643, "end": 43049194},
    ]
    extra = json.loads(gene["extra"])
    assert (extra["ccds_id"], extra["transcript_id"], extra["exon_count"], extra["intron_count"]) == (
        "CCDS11453",
        "NM_007294",
        2,
        1,
    )
