"""Every reference import records the release it loaded, or that the source states none.

`reference_dataset_imports` carries `source_version` and `source_release_date` so the
platform's reference layer can say which release lay under a result. No import wrote them.
Now each import records what its source states about itself: GENCODE its version and date
from the GTF preamble. A source that states no release (a UCSC table, a GTF without a
preamble, the clinical-CNV knowledgebase built from the current sources, an upload, the
DGV script) records `not stated`, never a silent NULL.

The record also keeps its own `source`: the segmental-duplication and BED-style
clinical-CNV loaders reused that name for a row's column, so an upload was recorded under
whatever its last row held.
"""

from __future__ import annotations

import asyncio
import gzip
import importlib.util
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.schemas import ReferenceImportSourceAssemblyOut
from backend.app.services import reference_metadata_service as rms
from backend.app.services import reference_source_service as rss

REPO_ROOT = Path(__file__).resolve().parents[2]
GENCODE_GTF_URL = "https://example.org/gencode.v50.basic.annotation.gtf.gz"
T2T_GTF_URL = "https://example.org/hs1.ncbiRefSeq.gtf.gz"

_TRANSCRIPT = (
    'chr17\tHAVANA\tgene\t43044295\t43125364\t.\t-\t.\tgene_id "ENSG00000012048.24"; '
    'gene_type "protein_coding"; gene_name "BRCA1"; level 2;\n'
    'chr17\tHAVANA\ttranscript\t43044295\t43125364\t.\t-\t.\tgene_id "ENSG00000012048.24"; '
    'transcript_id "ENST00000357654.9"; gene_type "protein_coding"; gene_name "BRCA1"; '
    'transcript_type "protein_coding"; level 2; tag "basic";\n'
    'chr17\tHAVANA\texon\t43125271\t43125364\t.\t-\t.\tgene_id "ENSG00000012048.24"; '
    'transcript_id "ENST00000357654.9"; exon_number 1;\n'
)
# GENCODE states its release in the preamble; UCSC's RefSeq GTF has none.
GENCODE_GTF = (
    "##description: evidence-based annotation of the human genome (GRCh38), "
    "version 50 (Ensembl 116)\n"
    "##provider: GENCODE\n"
    "##date: 2026-04-08\n" + _TRANSCRIPT
)
HEADERLESS_GTF = _TRANSCRIPT


class _RecordingSession:
    """Records every statement; answers nothing (the lookups are patched)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def import_records(self) -> list[dict[str, Any]]:
        return [
            dict(params)
            for sql, params in self.calls
            if "INSERT INTO reference_dataset_imports" in sql
        ]


def _patch_lookups(monkeypatch: pytest.MonkeyPatch, *, assembly_name: str = "GRCh38") -> None:
    async def fake_assembly(_session, assembly_id):
        return {"id": assembly_id, "assembly_name": assembly_name, "version": "p14"}

    async def no_existing(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(rms, "_get_assembly_by_id", fake_assembly)
    monkeypatch.setattr(rms, "_get_assembly_by_name", lambda *_a, **_k: fake_assembly(None, "assembly-1"))
    monkeypatch.setattr(rms, "_assembly_dataset_count", no_existing)


def _patch_ucsc_import(
    monkeypatch: pytest.MonkeyPatch, *, ucsc_genome: str, gtf_url: str, gtf_text: str
) -> None:
    """A UCSC-catalogue import whose only network access is the GTF below."""

    async def fake_catalogue(*, tax_id: int):
        return [
            ReferenceImportSourceAssemblyOut(
                scientific_name="Homo sapiens",
                common_name="human",
                tax_id=tax_id,
                ucsc_genome=ucsc_genome,
                assembly_name="GRCh38" if ucsc_genome == "hg38" else "T2T-CHM13",
                assembly_version=ucsc_genome,
                release_date=date(2013, 12, 1),
                description="Dec. 2013 (GRCh38/hg38)",
                source_name="Genome Reference Consortium",
                gene_source="GENCODE",
            )
        ]

    async def fake_genome_record(_client, *, ucsc_genome: str):
        return {"description": "Dec. 2013 (GRCh38/hg38)"}

    async def fake_species(_session, **_kwargs):
        return "species-1", "Homo sapiens", False

    async def fake_assembly(_session, **_kwargs):
        return "assembly-1", False

    async def fake_cytobands(_client, *, ucsc_genome: str):
        return "chr1\t0\t100\tp36.33\tgneg\n", f"https://example.org/{ucsc_genome}/cytoBandIdeo.txt.gz"

    async def fake_download(_client, url, **_kwargs):
        if url == gtf_url:
            return gzip.compress(gtf_text.encode())
        raise OSError(f"no network in tests: {url}")

    monkeypatch.setattr(rss, "list_reference_source_assemblies", fake_catalogue)
    monkeypatch.setattr(rss, "_resolve_find_genome_record", fake_genome_record)
    monkeypatch.setattr(rss, "_get_or_create_species", fake_species)
    monkeypatch.setattr(rss, "_get_or_create_assembly", fake_assembly)
    monkeypatch.setattr(rss, "_download_cytobands", fake_cytobands)
    monkeypatch.setattr(rss, "download_bounded_bytes", fake_download)


def _by_dataset(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # A gene import once wrote its dataset type as a literal rather than a parameter.
    return {record.get("dataset_type", "genes"): record for record in records}


def test_a_gencode_import_records_the_release_its_gtf_states(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookups(monkeypatch)
    _patch_ucsc_import(monkeypatch, ucsc_genome="hg38", gtf_url=GENCODE_GTF_URL, gtf_text=GENCODE_GTF)
    monkeypatch.setattr(settings, "reference_gencode_gtf_url", GENCODE_GTF_URL)
    session = _RecordingSession()

    asyncio.run(
        rss.import_reference_from_ucsc(session, tax_id=9606, ucsc_genome="hg38", overwrite=True)  # type: ignore[arg-type]
    )

    records = _by_dataset(session.import_records())
    genes = records["genes"]
    # The label the readers show is unchanged; the version and date are recorded beside it.
    assert genes.get("source") == "gencode v50 (Ensembl 116)"
    assert genes.get("source_url") == GENCODE_GTF_URL
    assert genes.get("source_version") == "v50 (Ensembl 116)"
    assert genes.get("source_release_date") == date(2026, 4, 8)
    # UCSC table dumps state no release.
    cytobands = records["cytobands"]
    assert cytobands.get("source") == "ucsc"
    assert cytobands.get("source_version") == "not stated"
    assert cytobands.get("source_release_date") is None


def test_a_gtf_without_a_preamble_is_recorded_as_not_stated(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookups(monkeypatch, assembly_name="T2T-CHM13")
    _patch_ucsc_import(monkeypatch, ucsc_genome="hs1", gtf_url=T2T_GTF_URL, gtf_text=HEADERLESS_GTF)
    monkeypatch.setattr(settings, "reference_t2t_gtf_url", T2T_GTF_URL)
    session = _RecordingSession()

    asyncio.run(
        rss.import_reference_from_ucsc(session, tax_id=9606, ucsc_genome="hs1", overwrite=True)  # type: ignore[arg-type]
    )

    genes = _by_dataset(session.import_records())["genes"]
    assert genes.get("source") == "ucsc ncbiRefSeq"
    assert genes.get("source_version") == "not stated"
    assert genes.get("source_release_date") is None


@pytest.mark.parametrize(
    ("dataset_type", "text_value"),
    [
        # BED with a name/score column: that column must not become the import's source.
        (
            "segmental_duplications",
            "chr1\t100\t200\tLCR22A\tClinGen\t.\t100\t200\t0,0,0\n"
            "chr1\t300\t400\tLCR22B\tWGAC\t.\t300\t400\t0,0,0\n",
        ),
        # The simplified BED-style clinical-CNV format, whose fifth column is a source.
        (
            "clinical_cnvs",
            "chr7\t73330452\t74799773\tWilliams-Beuren\tISCA\tISCA-37446\t.\t.\t.\n",
        ),
    ],
)
def test_an_upload_is_recorded_as_an_upload_that_states_no_release(
    monkeypatch: pytest.MonkeyPatch, dataset_type: str, text_value: str
) -> None:
    _patch_lookups(monkeypatch)
    session = _RecordingSession()

    asyncio.run(
        rms.apply_reference_dataset_text(
            session,  # type: ignore[arg-type]
            assembly_id="assembly-1",
            dataset_type=dataset_type,  # type: ignore[arg-type]
            text_value=text_value,
            overwrite=True,
            performed_by="admin@example.com",
            source="upload",
        )
    )

    [record] = session.import_records()
    assert record.get("source") == "upload"
    assert record.get("performed_by") == "admin@example.com"
    assert record.get("source_version") == "not stated"
    assert record.get("source_release_date") is None


def test_a_bootstrap_import_names_the_file_it_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_lookups(monkeypatch)
    segdups = tmp_path / "ClinGen_recurrent_CNV_V2.1-hg38.bed"
    segdups.write_text("chr1\t100\t200\tBP1\t0\t.\t100\t200\t0,0,0\n", encoding="utf-8")
    monkeypatch.setattr(settings, "reference_bootstrap_enabled", True)
    monkeypatch.setattr(settings, "reference_bootstrap_assembly_name", "GRCh38")
    monkeypatch.setattr(settings, "reference_segmental_duplications_path", str(segdups))
    monkeypatch.setattr(settings, "reference_clinical_cnvs_path", str(tmp_path / "absent.tsv"))
    monkeypatch.setattr(rms, "REPO_CLINICAL_CNVS_PATH", tmp_path / "absent-too.tsv")
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    [record] = session.import_records()
    assert record.get("dataset_type") == "segmental_duplications"
    assert record.get("source") == "ClinGen_recurrent_CNV_V2.1-hg38.bed"
    assert record.get("source_version") == "not stated"
    assert record.get("source_release_date") is None


def test_the_dgv_script_records_that_its_file_states_no_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = importlib.util.spec_from_file_location("import_dgv", REPO_ROOT / "scripts" / "import_dgv.py")
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    session = _RecordingSession()

    class _SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_exc):
            return False

    async def fake_resolve(_session, _assembly):
        return "assembly-1"

    monkeypatch.setattr(script, "get_postgres_sessionmaker", lambda: _SessionContext)
    monkeypatch.setattr(script, "_resolve_assembly_id", fake_resolve)
    dgv = tmp_path / "GRCh38_hg38_variants_2025-12-01.txt"
    dgv.write_text(
        "variantaccession\tchr\tstart\tend\tvarianttype\tvariantsubtype\treference\n"
        "dgv1n1\t1\t100\t200\tCNV\tloss\tStudy2020\n",
        encoding="utf-8",
    )

    asyncio.run(script.main("GRCh38", str(dgv), True, "dgv-import-script"))

    [record] = session.import_records()
    assert record.get("dataset_type") == "dgv"
    assert record.get("source") == "dgv"
    assert record.get("inserted") == 1
    assert record.get("source_version") == "not stated"
    assert record.get("source_release_date") is None
