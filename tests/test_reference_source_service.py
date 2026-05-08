from __future__ import annotations

from datetime import date

import pytest

from backend.app.schemas import ReferenceImportSourceAssemblyOut
from backend.app.services import reference_source_service


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        if not self._rows:
            raise AssertionError("Expected one row")
        return self._rows[0]


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _RecordingSession:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.params: list[dict[str, object] | None] = []
        self.committed = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        self.params.append(params)

        if "FROM species" in sql and "SELECT id::text AS id, name" in sql:
            return _FakeExecuteResult([])
        if "INSERT INTO species" in sql:
            return _FakeExecuteResult([{"id": "species-1", "name": "Homo sapiens"}])
        if "FROM assemblies" in sql and "SELECT id::text AS id" in sql:
            return _FakeExecuteResult([])
        if "INSERT INTO assemblies" in sql:
            return _FakeExecuteResult([{"id": "assembly-1"}])
        raise AssertionError(f"Unexpected SQL: {sql}")

    async def commit(self):
        self.committed = True


def test_build_gene_import_text_converts_ucsc_table_rows() -> None:
    sql_text = """
    CREATE TABLE `refGene` (
      `bin` smallint unsigned NOT NULL,
      `name` varchar(255) NOT NULL,
      `chrom` varchar(255) NOT NULL,
      `strand` char(1) NOT NULL,
      `txStart` int unsigned NOT NULL,
      `txEnd` int unsigned NOT NULL,
      `cdsStart` int unsigned NOT NULL,
      `cdsEnd` int unsigned NOT NULL,
      `exonCount` int unsigned NOT NULL,
      `exonStarts` longblob NOT NULL,
      `exonEnds` longblob NOT NULL,
      `score` int NOT NULL,
      `name2` varchar(255) NOT NULL
    )
    """
    data_text = "0\tNM_000001\tchr1\t+\t100\t240\t100\t240\t2\t100,200,\t150,240,\t0\tGENE1\n"

    converted = reference_source_service._build_gene_import_text(
        track="refGene",
        sql_text=sql_text,
        data_text=data_text,
    )

    assert converted.strip() == "chr1\t100\t240\tGENE1\t\t+\t\tNM_000001\t2\t100-150,200-240\t1\t150-200"


def test_parse_sql_columns_ignores_dump_prelude_before_create_table() -> None:
    sql_text = """
    -- MariaDB dump 10.19
    DROP TABLE IF EXISTS `ncbiRefSeqCurated`;
    CREATE TABLE `ncbiRefSeqCurated` (
      `bin` smallint(5) unsigned NOT NULL,
      `name` varchar(255) NOT NULL,
      `chrom` varchar(255) NOT NULL,
      `strand` char(1) NOT NULL,
      `txStart` int(10) unsigned NOT NULL,
      `txEnd` int(10) unsigned NOT NULL,
      `cdsStart` int(10) unsigned NOT NULL,
      `cdsEnd` int(10) unsigned NOT NULL,
      `exonCount` int(10) unsigned NOT NULL,
      `exonStarts` longblob NOT NULL,
      `exonEnds` longblob NOT NULL,
      `score` int(11) DEFAULT NULL,
      `name2` varchar(255) NOT NULL,
      `cdsStartStat` enum('none','unk','incmpl','cmpl') NOT NULL,
      `cdsEndStat` enum('none','unk','incmpl','cmpl') NOT NULL,
      `exonFrames` longblob NOT NULL,
      KEY `chrom` (`chrom`,`bin`)
    ) ENGINE=MyISAM DEFAULT CHARSET=latin1;
    """

    columns = reference_source_service._parse_sql_columns(sql_text)

    assert columns == [
        "bin",
        "name",
        "chrom",
        "strand",
        "txStart",
        "txEnd",
        "cdsStart",
        "cdsEnd",
        "exonCount",
        "exonStarts",
        "exonEnds",
        "score",
        "name2",
        "cdsStartStat",
        "cdsEndStat",
        "exonFrames",
    ]


def test_build_single_band_cytobands_text_generates_one_band_per_chromosome() -> None:
    text_value = reference_source_service._build_single_band_cytobands_text(
        {
            "chr1": 248956422,
            "chr2": "242193529",
            "": 100,
            "chrBad": "nope",
        }
    )

    assert text_value.splitlines() == [
        "chr1\t0\t248956422\tchr1\tgneg",
        "chr2\t0\t242193529\tchr2\tgneg",
    ]


@pytest.mark.asyncio
async def test_import_reference_from_ucsc_creates_records_and_loads_cytobands_and_genes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_reference_source_assemblies(*, tax_id: int):
        assert tax_id == 9606
        return [
            ReferenceImportSourceAssemblyOut(
                scientific_name="Homo sapiens",
                common_name="human",
                tax_id=9606,
                ucsc_genome="hg38",
                assembly_name="GRCh38",
                assembly_version="hg38",
                release_date=date(2024, 1, 1),
                description="Dec. 2013 (GRCh38/hg38)",
                source_name="Genome Reference Consortium",
                gene_source="UCSC gene tables",
            )
        ]

    async def fake_resolve_find_genome_record(client, *, ucsc_genome: str):
        assert ucsc_genome == "hg38"
        return {
            "description": "GRCh38 Genome Reference Consortium Human Reference 38 GCF_000001405.40_GHCh38.p14 GCF_000001405.40_GRCh38.p14",
        }

    async def fake_download_cytobands(client, *, ucsc_genome: str):
        assert ucsc_genome == "hg38"
        return "chr1\t0\t100\tp36.33\tgneg\n", "https://example.org/cytoBandIdeo.txt.gz"

    async def fake_download_genes(client, *, ucsc_genome: str):
        assert ucsc_genome == "hg38"
        return (
            "chr1\t100\t200\tGENE1\t\t+\t\tNM_000001\t1\t100-200\t0\t\n",
            "https://example.org/refGene.txt.gz",
            "refGene",
        )

    applied_calls: list[tuple[str, str, str, bool, bool]] = []

    async def fake_apply_reference_dataset_text(
        session,
        *,
        assembly_id: str,
        dataset_type: str,
        text_value: str,
        overwrite: bool,
        commit: bool,
    ):
        applied_calls.append((assembly_id, dataset_type, text_value, overwrite, commit))
        if dataset_type == "cytobands":
            return type("Result", (), {"inserted": 1, "replaced": False})()
        return type("Result", (), {"inserted": 1, "replaced": True})()

    monkeypatch.setattr(
        reference_source_service,
        "list_reference_source_assemblies",
        fake_list_reference_source_assemblies,
    )
    monkeypatch.setattr(
        reference_source_service,
        "_resolve_find_genome_record",
        fake_resolve_find_genome_record,
    )
    monkeypatch.setattr(
        reference_source_service,
        "_download_cytobands",
        fake_download_cytobands,
    )
    monkeypatch.setattr(
        reference_source_service,
        "_download_genes",
        fake_download_genes,
    )
    monkeypatch.setattr(
        reference_source_service,
        "apply_reference_dataset_text",
        fake_apply_reference_dataset_text,
    )

    session = _RecordingSession()
    result = await reference_source_service.import_reference_from_ucsc(
        session,
        tax_id=9606,
        ucsc_genome="hg38",
        overwrite=True,
    )

    assert result.species_id == "species-1"
    assert result.assembly_id == "assembly-1"
    assert result.assembly_name == "GRCh38"
    assert result.assembly_version == "p14"
    assert result.created_species is True
    assert result.created_assembly is True
    assert result.cytobands_inserted == 1
    assert result.genes_inserted == 1
    assert result.cytobands_replaced is False
    assert result.genes_replaced is True
    assert session.committed is True
    assert applied_calls == [
        ("assembly-1", "cytobands", "chr1\t0\t100\tp36.33\tgneg\n", True, False),
        ("assembly-1", "genes", "chr1\t100\t200\tGENE1\t\t+\t\tNM_000001\t1\t100-200\t0\t\n", True, False),
    ]


@pytest.mark.asyncio
async def test_download_genes_falls_back_when_first_track_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "https://example.test/ncbiRefSeqCurated.sql": "CREATE TABLE `ncbiRefSeqCurated` (\n  `name` varchar(255),\n  `chrom` varchar(255),\n  `strand` char(1),\n  `txStart` int,\n  `txEnd` int,\n  `exonStarts` longblob,\n  `exonEnds` longblob,\n  `name2` varchar(255)\n)\n",
        "https://example.test/ncbiRefSeqCurated.txt.gz": "ignored",
        "https://example.test/ncbiRefSeq.sql": "CREATE TABLE `ncbiRefSeq` (\n  `name` varchar(255),\n  `chrom` varchar(255),\n  `strand` char(1),\n  `txStart` int,\n  `txEnd` int,\n  `exonStarts` longblob,\n  `exonEnds` longblob,\n  `name2` varchar(255)\n)\n",
        "https://example.test/ncbiRefSeq.txt.gz": "ignored",
    }

    async def fake_get_optional_text(client, url: str):
        return responses.get(url)

    async def fake_get_optional_gzip_text(client, url: str):
        return responses.get(url)

    def fake_build_gene_import_text(*, track: str, sql_text: str, data_text: str) -> str:
        if track == "ncbiRefSeqCurated":
            raise reference_source_service.HTTPException(
                status_code=502,
                detail="No gene rows were parsed from UCSC track ncbiRefSeqCurated",
            )
        assert track == "ncbiRefSeq"
        return "chr1\t1\t2\tGENE1\t\t+\t\tNM_1\t1\t1-2\t0\t\n"

    monkeypatch.setattr(reference_source_service, "UCSC_DOWNLOAD_ROOT", "https://example.test")
    monkeypatch.setattr(reference_source_service, "_get_optional_text", fake_get_optional_text)
    monkeypatch.setattr(reference_source_service, "_get_optional_gzip_text", fake_get_optional_gzip_text)
    monkeypatch.setattr(reference_source_service, "_build_gene_import_text", fake_build_gene_import_text)

    responses = {
        "https://example.test/hg38/database/ncbiRefSeqCurated.sql": responses["https://example.test/ncbiRefSeqCurated.sql"],
        "https://example.test/hg38/database/ncbiRefSeqCurated.txt.gz": responses["https://example.test/ncbiRefSeqCurated.txt.gz"],
        "https://example.test/hg38/database/ncbiRefSeq.sql": responses["https://example.test/ncbiRefSeq.sql"],
        "https://example.test/hg38/database/ncbiRefSeq.txt.gz": responses["https://example.test/ncbiRefSeq.txt.gz"],
    }

    converted, source_url, track = await reference_source_service._download_genes(
        client=None,  # type: ignore[arg-type]
        ucsc_genome="hg38",
    )

    assert track == "ncbiRefSeq"
    assert source_url == "https://example.test/hg38/database/ncbiRefSeq.txt.gz"
    assert converted.startswith("chr1\t1\t2\tGENE1")


@pytest.mark.asyncio
async def test_download_cytobands_falls_back_to_single_band_chromosome_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_optional_gzip_text(client, url: str):
        return None

    async def fake_get_json(client, url: str, *, params=None):
        assert url == "https://api.genome.ucsc.edu/list/chromosomes"
        assert params == {"genome": "noBands1"}
        return {
            "chromosomes": {
                "chr1": 100,
                "chr2": 200,
            }
        }

    monkeypatch.setattr(reference_source_service, "_get_optional_gzip_text", fake_get_optional_gzip_text)
    monkeypatch.setattr(reference_source_service, "_get_json", fake_get_json)

    text_value, source_url = await reference_source_service._download_cytobands(
        client=None,  # type: ignore[arg-type]
        ucsc_genome="noBands1",
    )

    assert source_url == "https://api.genome.ucsc.edu/list/chromosomes?genome=noBands1"
    assert text_value.splitlines() == [
        "chr1\t0\t100\tchr1\tgneg",
        "chr2\t0\t200\tchr2\tgneg",
    ]
