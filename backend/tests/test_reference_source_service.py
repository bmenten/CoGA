from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from backend.app.schemas import ReferenceImportSourceAssemblyOut
from backend.app.services import reference_source_service


async def _decline_gencode(client, *, assembly_id: str, ucsc_genome: str):
    """Stand-in for GENCODE being unavailable, so the UCSC fallback is exercised."""
    return None


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


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _RecordingSession:
    def __init__(self, dataset_counts: dict[str, int] | None = None) -> None:
        self.sql: list[str] = []
        self.params: list[dict[str, object] | None] = []
        self.dataset_counts = dataset_counts or {}
        self.committed = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        self.params.append(params)

        if "FROM chromosomes" in sql and "COUNT(*)" in sql:
            return _FakeScalarResult(self.dataset_counts.get("cytobands", 0))
        if "FROM genes" in sql and "COUNT(*)" in sql:
            return _FakeScalarResult(self.dataset_counts.get("genes", 0))
        if "JOIN assemblies" in sql:
            return _FakeExecuteResult([])
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

    async def rollback(self):
        pass


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


@pytest.mark.parametrize("genome", ["hg38", "mm39", "GCF_000001405.40", "danRer11"])
def test_safe_ucsc_genome_accepts_real_identifiers(genome: str) -> None:
    assert reference_source_service._safe_ucsc_genome(genome) == genome


@pytest.mark.parametrize(
    "genome",
    [
        "../../etc/passwd",
        "hg38/../../secret",
        "hg38?x=1",
        "hg38#frag",
        "user@host",
        "hg38 space",
        "",
    ],
)
def test_safe_ucsc_genome_rejects_path_or_url_injection(genome: str) -> None:
    # A tainted assembly identifier must not smuggle path/URL-significant characters
    # into the download path (partial-SSRF guard).
    with pytest.raises(HTTPException) as exc:
        reference_source_service._safe_ucsc_genome(genome)
    assert exc.value.status_code == 400


class _FakeMappings:
    def __init__(self, *, first=None, one=None) -> None:
        self._first = first
        self._one = one

    def first(self):
        return self._first

    def one(self):
        return self._one


class _FakeResult:
    def __init__(self, mappings: _FakeMappings) -> None:
        self._mappings = mappings

    def mappings(self) -> _FakeMappings:
        return self._mappings


class _AssemblyInsertSession:
    """Fake AsyncSession: the existence SELECT finds no row, the INSERT captures its
    params and returns a fresh id — enough to exercise _get_or_create_assembly's
    release_date fallback without a database."""

    def __init__(self) -> None:
        self.calls = 0
        self.insert_params: dict = {}

    async def execute(self, _query, params=None):
        self.calls += 1
        if self.calls == 1:
            return _FakeResult(_FakeMappings(first=None))
        self.insert_params = dict(params or {})
        return _FakeResult(
            _FakeMappings(one={"id": "11111111-1111-1111-1111-111111111111"})
        )


@pytest.mark.asyncio
async def test_get_or_create_assembly_writes_unknown_sentinel_when_release_date_missing() -> None:
    # An unparseable UCSC source date must not fabricate today() as the assembly's
    # release_date (clinical provenance); it writes the honest 0001-01-01 sentinel.
    session = _AssemblyInsertSession()
    assembly_id, created = await reference_source_service._get_or_create_assembly(
        session,
        species_id="22222222-2222-2222-2222-222222222222",
        assembly_name="FooAsm",
        assembly_version="v1",
        release_date=None,
    )

    assert created is True
    assert session.insert_params["release_date"] == date.min
    assert session.insert_params["release_date"] != date.today()


@pytest.mark.asyncio
async def test_get_or_create_assembly_preserves_a_real_release_date() -> None:
    session = _AssemblyInsertSession()
    await reference_source_service._get_or_create_assembly(
        session,
        species_id="22222222-2222-2222-2222-222222222222",
        assembly_name="FooAsm",
        assembly_version="v1",
        release_date=date(2020, 5, 1),
    )

    assert session.insert_params["release_date"] == date(2020, 5, 1)


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
        performed_by=None,
        source=None,
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
    # GENCODE is preferred over the UCSC track; declining it here keeps this test on the
    # UCSC path it was written for (and off an 80 MB live download).
    monkeypatch.setattr(
        reference_source_service,
        "_download_gencode_genes",
        _decline_gencode,
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
async def test_import_reference_from_ucsc_missing_only_skips_loaded_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_reference_source_assemblies(*, tax_id: int):
        return [
            ReferenceImportSourceAssemblyOut(
                scientific_name="Homo sapiens",
                common_name="human",
                tax_id=tax_id,
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
        return {"description": "Dec. 2013 (GRCh38/hg38)"}

    async def forbidden_download(*args, **kwargs):
        raise AssertionError("startup missing-only import should not redownload loaded datasets")

    async def forbidden_apply(*args, **kwargs):
        raise AssertionError("startup missing-only import should not reapply loaded datasets")

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
    monkeypatch.setattr(reference_source_service, "_download_cytobands", forbidden_download)
    monkeypatch.setattr(reference_source_service, "_download_genes", forbidden_download)
    monkeypatch.setattr(reference_source_service, "_download_gencode_genes", forbidden_download)
    monkeypatch.setattr(reference_source_service, "apply_reference_dataset_text", forbidden_apply)

    session = _RecordingSession(dataset_counts={"cytobands": 24, "genes": 2})
    result = await reference_source_service.import_reference_from_ucsc(
        session,
        tax_id=9606,
        ucsc_genome="hg38",
        overwrite=False,
        missing_only=True,
    )

    assert result.cytobands_inserted == 0
    assert result.genes_inserted == 0
    assert result.cytobands_replaced is False
    assert result.genes_replaced is False
    assert session.committed is True


@pytest.mark.asyncio
async def test_startup_reference_bootstrap_falls_back_to_species_assembly_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_import(*args, **kwargs):
        raise RuntimeError("UCSC unavailable")

    monkeypatch.setattr(reference_source_service, "import_reference_from_ucsc", failing_import)

    session = _RecordingSession()
    result = await reference_source_service.ensure_human_grch38_reference_on_startup(session)

    assert result is None
    assert session.committed is True
    assert any("INSERT INTO species" in sql for sql in session.sql)
    assert any("INSERT INTO assemblies" in sql for sql in session.sql)


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
