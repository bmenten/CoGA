"""A value beyond its ClickHouse column is stored as missing, never a failed write (SAFE-30).

The loaders fill the ClickHouse columns from file values, and the ClickHouse client refuses
a whole insert when one value falls outside its column. A read depth above 65,535 -- a
mitochondrial or amplicon call reads far more -- did not fit the UInt16 ``calls.dp`` and
``calls.ad``; a negative phase set fitted no UInt64, a QUAL or caller metric of 1e39 no
Float32, a negative BED start no UInt64; and a GQ of ``inf`` failed before the insert, at
its conversion to a whole number. Each failed the upload or the import with a 500, after
the rows it replaced were deleted.

Now the depth columns are UInt32, so such a depth is stored as the file has it, and every
other value is bound to its column before the write: one the column cannot hold, or that
is no finite number, is stored as missing. A record whose position no column holds is
skipped as malformed by its loader, and counted.

The ClickHouse here is a fake that enforces what the real client enforces: it records the
tables the backend creates, with the column types their CREATE statements give them, and
serialises every insert for those types with clickhouse-connect itself, so a value its
column cannot hold fails here as it fails before reaching the server.
``e2e/test_e2e_values_beyond_columns.py`` shows the same through the routes and a package
import against real ClickHouse, and ``integration/test_depth_columns_widened_clickhouse.py``
the widening of a table created before. All samples and values are synthetic.
"""

from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
import re
from typing import Any

from clickhouse_connect.datatypes.registry import get_from_name
from clickhouse_connect.driver.insert import InsertContext
from clickhouse_connect.driver.transform import NativeTransform
from fastapi import HTTPException, UploadFile
import pytest

from backend.app.core.clickhouse import _parse_insert_query
from backend.app.services import (
    annotation_manifest_service,
    annotation_table_parser,
    bed_service,
    clickhouse_interval_tracks,
    clickhouse_variant_records,
    clickhouse_variant_storage,
    family_package_common,
    per_sample_small_variants,
    structural_variant_ingest,
    variant_upload_service,
)
from backend.app.services.clickhouse_variant_ids import _xpos
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_tracks import (
    _parse_apcad_interval_row,
    _parse_copy_number_interval_row,
    _parse_pcf_segment_row,
    _parse_wisecondorx_interval_row,
)
from backend.app.services.family_package_variants import (
    _iter_cnv_structural_records,
    _iter_mito_sv_records,
    _iter_needlr_structural_records,
)
from backend.app.services.family_package_common import ParsedPed
from backend.app.services.nipt_target_coverage import parse_target_coverage_table
from backend.app.services.per_sample_small_variants import PerSampleVcf

_ASSEMBLY = "GRCh38"
_SAMPLES = {"S1": "uuid-s1", "S2": "uuid-s2"}


# --- A ClickHouse that holds every value to its column ----------------------------------------


def _ddl_columns(statement: str) -> tuple[str, dict[str, str]]:
    """The table a ``CREATE TABLE IF NOT EXISTS`` statement creates (without its database)
    and its columns' types, in order."""
    match = re.search(r"CREATE TABLE IF NOT EXISTS \S+?\.`([^`]+)`", statement)
    assert match, statement
    columns: dict[str, str] = {}
    for line in statement.splitlines():
        column = re.match(r"\s*`([^`]+)`\s+(.+?)\s*,?\s*$", line)
        if column:
            columns[column.group(1)] = re.sub(r"\s+DEFAULT\s.*$", "", column.group(2))
    return match.group(1), columns


class _TypedClickHouse:
    """The tables the backend creates, and every insert serialised for its columns' types
    as clickhouse-connect serialises it: a value its column cannot hold raises the client's
    own error. Nothing is stored before; ``stored_rows`` makes a count read rows.
    ``column_types`` answers ``system.columns`` for the depth-column check."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, str]] = {}
        self.rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.statements: list[str] = []
        self.stored_rows = 0
        self.column_types: list[tuple[str, str]] = []

    async def execute(self, query: str, params: Any = None, data: Any = None) -> Any:
        text = " ".join(query.split())
        self.statements.append(text)
        if data is not None:
            if data:
                self._insert(query, list(data))
            return None
        if text.startswith("CREATE TABLE IF NOT EXISTS"):
            table, columns = _ddl_columns(query)
            self.tables[table] = columns
            return None
        if "FROM system.columns" in text and "SELECT name, type" in text:
            return list(self.column_types)
        if text.startswith("SELECT count()") and "SNV_INDEL/entries" in text:
            return [(self.stored_rows,)]
        return []

    def _insert(self, query: str, rows: list[tuple[Any, ...]]) -> None:
        table, _database, columns = _parse_insert_query(query)
        types = [get_from_name(self.tables[table][column]) for column in columns]

        def fail(exc: Exception) -> None:
            raise exc

        context = InsertContext(table, columns, types, data=rows)
        for _chunk in NativeTransform.build_insert(context, error_handler=fail):
            pass
        self.rows[table].extend(dict(zip(columns, row)) for row in rows)

    def entries(self) -> list[dict[str, Any]]:
        return self.rows[f"{_ASSEMBLY}/SNV_INDEL/entries"]

    def deletes(self) -> list[str]:
        return [text for text in self.statements if text.startswith("ALTER TABLE") and " DELETE " in text]


@pytest.fixture()
def clickhouse(monkeypatch: pytest.MonkeyPatch) -> _TypedClickHouse:
    fake = _TypedClickHouse()
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake.execute)
    monkeypatch.setattr(clickhouse_variant_storage, "_ensured_variant_table_assemblies", set())
    monkeypatch.setattr(clickhouse_interval_tracks, "_execute", fake.execute)
    monkeypatch.setattr(clickhouse_interval_tracks, "_ensured_interval_table_assemblies", set())

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    # Postgres's side: the family's write lock, and the provenance the VCF header names.
    for module in (variant_upload_service, per_sample_small_variants):
        monkeypatch.setattr(module, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    return fake


class _Result:
    def scalar_one(self) -> int:
        return 0

    def scalar_one_or_none(self) -> None:
        return None


class _Session:
    async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result()

    async def commit(self) -> None:
        return None


def _family() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_BOUNDS",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={uuid: name for name, uuid in _SAMPLES.items()},
        sample_name_to_uuid=dict(_SAMPLES),
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name=_ASSEMBLY,
    )


def _sample(name: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=_SAMPLES[name],
        sample_id=name,
        family_uuid="family-uuid",
        family_id="FAM_BOUNDS",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name=_ASSEMBLY,
    )


_VCF_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
# A deep mitochondrial call: its depth, and each allele's, beyond 65,535 reads.
_MITO_DEEP = "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT:GQ:DP:AD:VAF\t0/1:99:120000:60000,60000:0.5\n"
# A call whose genotype quality is written as infinite.
_INFINITE_GQ = "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT:GQ:DP:AD:VAF\t0/1:inf:500:250,250:0.5\n"


async def _upload_small_variants(vcf: str, *, overwrite: bool = False, format_hint: str = "mito") -> dict[str, Any]:
    return await variant_upload_service.upload_family_small_variant_file(
        _Session(),  # type: ignore[arg-type]
        context=_family(),
        sample_contexts={name: _sample(name) for name in _SAMPLES},
        file=UploadFile(file=BytesIO(vcf.encode()), filename="calls.vcf"),
        overwrite=overwrite,
        format_hint=format_hint,  # type: ignore[arg-type]
    )


def _calls(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        column.removeprefix("calls."): entry[column][0]
        for column in ("calls.gq", "calls.dp", "calls.ad", "calls.af", "calls.ab", "calls.ps", "calls.metrics")
    }


# --- The bounds -------------------------------------------------------------------------------


def test_a_whole_number_is_kept_only_when_its_column_holds_it() -> None:
    from backend.app.services.clickhouse_column_bounds import INT32, UINT16, UINT32, UINT64, column_int

    assert column_int(120_000, UINT32) == 120_000
    assert column_int(120_000, UINT16) is None
    assert column_int(65_535, UINT16) == 65_535
    assert column_int(-1, UINT64) is None
    assert column_int(2**32, UINT32) is None
    assert column_int(-(2**31), INT32) == -(2**31)
    assert column_int(-3_000_000_000, INT32) is None
    # Text and fractions are read as the parsers read them; what is no number is missing.
    assert column_int("4294967295", UINT32) == 4_294_967_295
    assert column_int("12.7", UINT32) == 12
    assert column_int(12.7, UINT32) == 12
    for not_a_number in ("inf", "-inf", "nan", float("inf"), float("nan"), ".", "", "abc", None):
        assert column_int(not_a_number, UINT64) is None


def test_a_decimal_is_kept_only_when_it_is_finite_and_a_float32_holds_it() -> None:
    from backend.app.services.clickhouse_column_bounds import FLOAT32_MAX, column_float32

    assert column_float32(0.5) == 0.5
    assert column_float32("30") == 30.0
    assert column_float32(FLOAT32_MAX) == FLOAT32_MAX
    assert column_float32(-FLOAT32_MAX) == -FLOAT32_MAX
    for unstorable in (1e39, -1e39, "1e39", float("inf"), float("nan"), "inf", "nan", 10**400, ".", None):
        assert column_float32(unstorable) is None


def test_no_number_parser_raises_on_an_infinite_value() -> None:
    # Before the fix each raised OverflowError: a 500, not a value left out.
    for coerce in (
        clickhouse_variant_records._coerce_int,
        annotation_table_parser._coerce_int,
        family_package_common._coerce_int,
        structural_variant_ingest._coerce_int,
    ):
        assert coerce("inf") is None
        assert coerce("-inf") is None
        assert coerce(float("inf")) is None
        assert coerce("12.0") == 12
    assert structural_variant_ingest.parse_svlen("inf") is None
    assert variant_upload_service._first_present_int({"DP": "inf", "MED_DP": "80"}, "DP", "MED_DP") == 80
    assert variant_upload_service._first_present_int({"PS": "-inf"}, "PS") is None
    assert variant_upload_service._first_present_float({"GQ": "inf"}, "GQ") is None
    assert variant_upload_service._first_present_float({"GQ": "nan"}, "GQ") is None
    assert variant_upload_service._first_present_float({"GQ": "50"}, "GQ") == 50.0
    assert variant_upload_service._parse_int_list("10,inf") == [10, 0]
    assert variant_upload_service._parse_qual("inf") is None
    assert variant_upload_service._parse_qual("nan") is None
    assert variant_upload_service._parse_qual("31.5") == 31.5


# --- The depth columns -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_depth_columns_are_created_wide_and_the_genotype_quality_is_not(clickhouse) -> None:
    await clickhouse_variant_storage.ensure_clickhouse_variant_tables(_ASSEMBLY)
    entries = clickhouse.tables[f"{_ASSEMBLY}/SNV_INDEL/entries"]
    # Before the fix both were UInt16: a mitochondrial or amplicon depth failed the insert.
    assert entries["calls.dp"] == "Array(Nullable(UInt32))"
    assert entries["calls.ad"] == "Array(Array(Nullable(UInt32)))"
    # A genotype quality is a phred score, not a count of reads: it stays UInt16.
    assert entries["calls.gq"] == "Array(Nullable(UInt16))"
    # A table created wide is not altered.
    assert not [text for text in clickhouse.statements if "MODIFY COLUMN" in text]


@pytest.mark.asyncio
async def test_a_table_created_with_narrow_depth_columns_is_widened_once(clickhouse) -> None:
    clickhouse.column_types = [
        ("calls.dp", "Array(Nullable(UInt16))"),
        ("calls.ad", "Array(Array(Nullable(UInt16)))"),
    ]
    await clickhouse_variant_storage.ensure_clickhouse_variant_tables(_ASSEMBLY)
    alters = [text for text in clickhouse.statements if "MODIFY COLUMN" in text]
    assert alters == [
        f"ALTER TABLE coga.`{_ASSEMBLY}/SNV_INDEL/entries` "
        "MODIFY COLUMN `calls.ad` Array(Array(Nullable(UInt32))), "
        "MODIFY COLUMN `calls.dp` Array(Nullable(UInt32))"
    ]
    # Once ensured, the process does not look again; a new process finds the wide types.
    await clickhouse_variant_storage.ensure_clickhouse_variant_tables(_ASSEMBLY)
    clickhouse_variant_storage._ensured_variant_table_assemblies.clear()
    clickhouse.column_types = [
        ("calls.dp", "Array(Nullable(UInt32))"),
        ("calls.ad", "Array(Array(Nullable(UInt32)))"),
    ]
    await clickhouse_variant_storage.ensure_clickhouse_variant_tables(_ASSEMBLY)
    assert [text for text in clickhouse.statements if "MODIFY COLUMN" in text] == alters


@pytest.mark.asyncio
async def test_only_the_narrow_type_of_a_depth_column_is_altered(clickhouse) -> None:
    # A column with any other type is not this version's to change.
    clickhouse.column_types = [
        ("calls.dp", "Array(Nullable(UInt32))"),
        ("calls.ad", "Array(Array(Nullable(UInt16)))"),
    ]
    await clickhouse_variant_storage.ensure_clickhouse_variant_tables(_ASSEMBLY)
    assert [text for text in clickhouse.statements if "MODIFY COLUMN" in text] == [
        f"ALTER TABLE coga.`{_ASSEMBLY}/SNV_INDEL/entries` MODIFY COLUMN `calls.ad` Array(Array(Nullable(UInt32)))"
    ]


# --- The small-variant upload ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_mitochondrial_depth_is_stored_as_the_file_has_it(clickhouse) -> None:
    # Before the fix: DataError on the UInt16 depth columns, a 500.
    result = await _upload_small_variants(_VCF_HEADER + _MITO_DEEP)

    assert (result["inserted"], result["skipped_malformed"], result["source_format"]) == (1, 0, "mito")
    [entry] = clickhouse.entries()
    assert (entry["pos"], entry["calls.sampleId"]) == (3243, ["S1"])
    assert _calls(entry) == {
        "gq": 99,
        "dp": 120_000,
        "ad": [60_000, 60_000],
        "af": [0.5],
        "ab": 0.5,
        "ps": None,
        "metrics": {"QUAL": 30.0},
    }


@pytest.mark.asyncio
async def test_an_infinite_genotype_quality_is_stored_as_missing(clickhouse) -> None:
    # Before the fix: OverflowError where the row was built, a 500.
    result = await _upload_small_variants(_VCF_HEADER + _INFINITE_GQ)

    assert result["inserted"] == 1
    [entry] = clickhouse.entries()
    assert _calls(entry) == {
        "gq": None,
        "dp": 500,
        "ad": [250, 250],
        "af": [0.5],
        "ab": 0.5,
        "ps": None,
        "metrics": {"QUAL": 30.0},
    }


@pytest.mark.asyncio
async def test_values_no_column_holds_are_stored_as_missing(clickhouse) -> None:
    record = (
        "chrM\t73\t.\tA\tG\t1e39\tPASS\tTLOD=1e39;FS=1.5\tGT:GQ:DP:AD:AF:PS\t"
        "1/1:70000:5000000000:-5,10:1e39,0.5:-1\n"
    )
    result = await _upload_small_variants(_VCF_HEADER + record)

    assert result["inserted"] == 1
    [entry] = clickhouse.entries()
    assert entry["qual"] is None
    assert _calls(entry) == {
        "gq": None,  # beyond UInt16
        "dp": None,  # beyond UInt32
        "ad": [0, 10],  # a depth no column holds is 0, as a missing one is, so 10 stays the alt depth
        "af": [0.5],  # beyond Float32: left out, as a fraction that is no number is
        "ab": 0.5,
        "ps": None,  # negative
        "metrics": {"FS": 1.5},  # QUAL and TLOD beyond Float32
    }


@pytest.mark.asyncio
async def test_a_record_whose_position_no_column_holds_is_skipped_as_malformed(clickhouse) -> None:
    records = (
        _MITO_DEEP
        + "chrM\t4294967296\t.\tA\tG\t30\tPASS\t.\tGT:DP\t0/1:50\n"
        + "chrM\t-1\t.\tC\tT\t30\tPASS\t.\tGT:DP\t0/1:50\n"
        + "chrM\tinf\t.\tC\tT\t30\tPASS\t.\tGT:DP\t0/1:50\n"
    )
    result = await _upload_small_variants(_VCF_HEADER + records)

    assert (result["inserted"], result["skipped_malformed"]) == (1, 3)
    assert [entry["pos"] for entry in clickhouse.entries()] == [3243]


@pytest.mark.asyncio
async def test_a_file_with_no_storable_position_leaves_the_stored_callset(clickhouse) -> None:
    # The callset holds rows: an overwrite reads the file whole before it deletes them.
    clickhouse.stored_rows = 4
    records = "chrM\t-1\t.\tA\tG\t30\tPASS\t.\tGT:DP\t0/1:50\nchrM\t4294967296\t.\tC\tT\t30\tPASS\t.\tGT:DP\t0/1:50\n"

    with pytest.raises(HTTPException) as excinfo:
        await _upload_small_variants(_VCF_HEADER + records, overwrite=True)

    assert excinfo.value.status_code == 400
    # Before the fix the records were read as storable, the callset deleted, and the
    # insert failed: a 500 with the callset gone.
    assert clickhouse.deletes() == []
    assert clickhouse.entries() == []


@pytest.mark.asyncio
async def test_a_contig_named_by_a_number_out_of_rank_is_stored(clickhouse) -> None:
    vcf = _VCF_HEADER + "-1\t100\t.\tA\tG\t30\tPASS\t.\tGT:DP\t0/1:50\n"
    result = await _upload_small_variants(vcf, format_hint="clair3")

    assert result["inserted"] == 1
    [entry] = clickhouse.entries()
    # Ranked with the other contigs: before the fix its xpos was negative.
    assert entry["xpos"] == 99_000_000_100


def test_xpos_fits_its_column_for_every_contig_name() -> None:
    assert _xpos("chr1", 100) == 1_000_000_100
    assert _xpos("X", 5) == 23_000_000_005
    assert _xpos("chrM", 3243) == 25_000_003_243
    assert _xpos("GL000220.1", 7) == 99_000_000_007
    for name in ("-1", "chr-7", "99999999999999999999"):
        assert _xpos(name, 2**32 - 1) == 99 * 1_000_000_000 + 2**32 - 1
    # The highest rank whose xpos the UInt64 column holds at the last UInt32 position.
    assert _xpos("18446744069", 2**32 - 1) <= 2**64 - 1


# --- The per-sample files of a long-read couple ------------------------------------------------


@pytest.mark.asyncio
async def test_per_sample_files_keep_deep_calls_and_skip_unstorable_positions(clickhouse, tmp_path: Path) -> None:
    def write(name: str, records: str) -> PerSampleVcf:
        path = tmp_path / f"{name}.vcf"
        path.write_text(
            "##fileformat=VCFv4.2\n##contig=<ID=chrM,length=16569>\n"
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{name}\n" + records,
            encoding="utf-8",
        )
        return PerSampleVcf(name, path)

    files = [
        write(
            "S1",
            "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT:GQ:DP:AD:VAF\t1/1:inf:120000:60000,60000:0.5\n"
            "chrM\t4294967296\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\n",
        ),
        write("S2", "chrM\t3243\t.\tA\tG\t1e39\tPASS\t.\tGT:GQ:DP:PS\t0/1:99:70000:-4\n"),
    ]
    result = await per_sample_small_variants.upload_family_per_sample_small_variant_files(
        _Session(),  # type: ignore[arg-type]
        context=_family(),
        sample_contexts={name: _sample(name) for name in _SAMPLES},
        files=files,
        overwrite=True,
    )

    assert (result["inserted"], result["skipped_malformed"]) == (1, 1)
    [entry] = clickhouse.entries()
    # The site's QUAL is the best of its records' QUALs a Float32 holds.
    assert entry["qual"] == 30.0
    assert entry["calls.sampleId"] == ["S1", "S2"]
    assert entry["calls.dp"] == [120_000, 70_000]
    assert entry["calls.gq"] == [None, 99]
    assert entry["calls.ps"] == [None, None]
    assert entry["calls.ad"] == [[60_000, 60_000], []]


# --- Structural variants -----------------------------------------------------------------------


_SNIFFLES = (
    "##fileformat=VCFv4.2\n##source=Sniffles2_2.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    # Its length beyond Int32, its read support beyond UInt32, its QUAL beyond Float32 and a
    # negative phase set: the SV is stored without them.
    "1\t1000\tS.1\tN\t<DEL>\t1e39\tPASS\tSVTYPE=DEL;SVLEN=-3000000000;END=2000;SUPPORT=5000000000\tGT:PS\t0/1:-1\n"
    # An END and a POS no UInt32 holds: the record cannot be placed and is skipped.
    "1\t3000\tS.2\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-10;END=5000000000;SUPPORT=9\tGT\t0/1\n"
    "1\t-5\tS.3\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-10;END=100;SUPPORT=9\tGT\t0/1\n"
)


@pytest.mark.asyncio
async def test_an_sv_upload_stores_values_no_column_holds_as_missing(clickhouse, monkeypatch) -> None:
    async def family_samples(*_args: Any, **_kwargs: Any) -> set[str]:
        return set(_SAMPLES)

    async def no_genes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(variant_upload_service, "known_vcf_sample_ids", family_samples)
    monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", no_genes)

    # Before the fix: DataError on the Int32 SVLEN (and the UInt32 positions).
    result = await variant_upload_service.upload_structural_variant_file(
        _Session(),  # type: ignore[arg-type]
        family_context=_family(),
        sample_context=_sample("S1"),
        file=UploadFile(file=BytesIO(_SNIFFLES.encode()), filename="calls.sniffles.vcf"),
        overwrite=False,
        format_hint="sniffles",
    )

    assert result["processed"] == 1
    [details] = clickhouse.rows[f"{_ASSEMBLY}/SV/variants/details"]
    assert (details["start"], details["end"], details["svLen"]) == (1000, 2000, None)
    [entry] = clickhouse.rows[f"{_ASSEMBLY}/SV/entries"]
    assert entry["calls.qual"] == [None]
    assert entry["calls.readSupport"] == [None]
    assert entry["calls.ps"] == [None]


@pytest.mark.asyncio
async def test_package_sv_readers_skip_unplaceable_records_and_store_the_rest_without_bad_values(clickhouse) -> None:
    hificnv = (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
        "chr1\t1000\tCNV1\tN\t<DUP>\t1e39\tPASS\tSVTYPE=DUP;SVLEN=3000000000;END=5000\tGT:CN\t0/1:70000\n"
        "chr1\t4294967296\tCNV2\tN\t<DEL>\t30\tPASS\tSVTYPE=DEL;END=4294967300\tGT:CN\t0/1:1\n"
    )
    mito = (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
        "chrM\t8470\tM.1\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;SVLEN=-4977;END=13447;SUPPORT=9;VAF=0.3\tGT:DV\t0/1:5000000000\n"
        "chrM\t-3\tM.2\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;SVLEN=-10;END=7;SUPPORT=9\tGT:DV\t0/1:9\n"
    )
    needlr = (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr2\t500\tN.1\tN\t<DEL>\t50\tPASS\tSVTYPE=DEL;SVLEN=-100;END=600;Query_ID=S1;Genotype=0/1;Alt_Reads=inf\n"
        "chr2\t700\tN.2\tN\t<DEL>\t50\tPASS\tSVTYPE=DEL;END=9999999999;Query_ID=S1;Genotype=0/1;Alt_Reads=4\n"
    )
    records = (
        _iter_cnv_structural_records(hificnv, sample_id="S1")
        + _iter_mito_sv_records(mito, sample_id="S1")
        + _iter_needlr_structural_records(
            needlr,
            ped=ParsedPed(family_ids=["FAM_BOUNDS"], members=[], sample_ids=[], text=""),
            sample_contexts={name: _sample(name) for name in _SAMPLES},
        )
    )
    # The unplaceable records are skipped as malformed; before the fix they were read.
    assert [(record.chr, record.start, record.end) for record in records] == [
        ("1", 1000, 5000),
        ("M", 8470, 13447),
        ("2", 500, 600),
    ]

    # Before the fix: DataError on the Int32 SVLEN, the UInt16 copy number and the UInt32
    # read support.
    await clickhouse_variant_storage.insert_structural_variant_records(_ASSEMBLY, "family-uuid", ["project-uuid"], records)

    details = clickhouse.rows[f"{_ASSEMBLY}/SV/variants/details"]
    assert [row["svLen"] for row in details] == [None, -4977, -100]
    entries = clickhouse.rows[f"{_ASSEMBLY}/SV/entries"]
    assert [(row["calls.qual"], row["calls.cn"], row["calls.readSupport"]) for row in entries] == [
        ([None], [None], [None]),
        ([60.0], [None], [None]),
        ([50.0], [None], [None]),
    ]


@pytest.mark.asyncio
async def test_a_stored_sv_whose_span_no_column_holds_is_left_out_of_the_write(clickhouse) -> None:
    # A loader that did not check the span still writes the rest; the record is left out.
    def record(start: int, end: int) -> StructuralVariantRecord:
        return StructuralVariantRecord(
            variant_key=None,
            variant_id=f"1-{start}-{end}-DEL---",
            chr="1",
            start=start,
            end=end,
            sv_type="DEL",
            source="manual_upload",
            remote_chr="5",
            remote_start=2**33,
            remote_end=None,
            sv_len=None,
            filters=[],
            gene_symbols=[],
            annotations=[],
            calls=[StructuralVariantCall(sample="S1", gt="0/1", qual=None, read_support=None, filter=None)],
        )

    await clickhouse_variant_storage.insert_structural_variant_records(
        _ASSEMBLY, "family-uuid", ["project-uuid"], [record(10, 20), record(30, 2**32)]
    )

    details = clickhouse.rows[f"{_ASSEMBLY}/SV/variants/details"]
    assert [(row["start"], row["end"], row["remoteStart"]) for row in details] == [(10, 20, None)]


# --- Small-variant records from any loader -----------------------------------------------------


@pytest.mark.asyncio
async def test_every_value_of_a_small_variant_record_meets_its_column(clickhouse) -> None:
    call = SmallVariantCall(
        sample="S1",
        gt="0/1",
        gq=float("inf"),
        dp=2**40,
        af=[float("nan"), 0.25],
        ad=[-1, 2**40, 7],
        ps=2**64,
        metrics={"TLOD": float("inf"), "FS": 2.0, " ": 1.0},
    )
    annotation = {
        "gene": "MT-TL1",
        "gnomad_af": 1e39,
        "population_frequencies": {"gnomad_af": 0.01},
        "gnomad_ac": -5,
        "gnomad_hom_count": 2**40,
        "cadd_phred": float("inf"),
        "revel": 0.4,
    }
    records = [
        SmallVariantRecord(
            variant_key=None,
            variant_id="",
            chr="chrM",
            start=start,
            end=start,
            ref="A",
            alt="G",
            source="mito",
            rsid=None,
            filters=[],
            gene_symbols=[],
            annotations=[annotation],
            calls=[call],
            qual=float("-inf"),
        )
        for start in (3243, 2**32, -1)
    ]

    await clickhouse_variant_storage.insert_small_variant_records(_ASSEMBLY, "family-uuid", ["project-uuid"], records)

    # The records whose position no column holds are left out; the other is stored.
    [entry] = clickhouse.entries()
    assert entry["qual"] is None
    assert _calls(entry) == {
        "gq": None,
        "dp": None,
        "ad": [0, 0, 7],
        "af": [0.25],
        "ab": 0.25,
        "ps": None,
        "metrics": {"FS": 2.0},
    }
    [stored] = clickhouse.rows[f"{_ASSEMBLY}/SNV_INDEL/variants/annotations"]
    assert stored["gnomad_af"] == 0.01  # the population fallback; 1e39 no Float32 holds
    assert (stored["gnomad_ac"], stored["gnomad_hom_count"]) == (None, None)
    assert (stored["cadd_phred"], stored["revel"]) == (None, 0.4)
    [index] = clickhouse.rows[f"{_ASSEMBLY}/SNV_INDEL/variants/annotation_index"]
    assert (index["max_gnomad_af"], index["max_gnomad_ac"], index["max_cadd_phred"]) == (0.01, None, None)


# --- Interval tracks ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bed_line_with_a_negative_coordinate_is_left_out(clickhouse) -> None:
    bed = "chr1\t-5\t100\t1.0\nchr1\t100\t200\t1.5\nchr1\t300\t18446744073709551616\t2.0\n"

    # Before the fix the negative start reached the insert: DataError after the delete.
    result = await bed_service.upload_bed_data(
        _Session(),  # type: ignore[arg-type]
        sample_context=_sample("S1"),
        bed_type="coverage",
        file=UploadFile(file=BytesIO(bed.encode()), filename="coverage.bed"),
        overwrite=True,
    )

    assert result == {"inserted": 1}
    rows = clickhouse.rows[f"{_ASSEMBLY}/INTERVAL/entries"]
    assert [(row["start"], row["end"], row["value"]) for row in rows] == [(100, 200, 1.5)]


@pytest.mark.asyncio
async def test_a_bed_file_with_no_storable_line_leaves_the_stored_track(clickhouse) -> None:
    with pytest.raises(HTTPException) as excinfo:
        await bed_service.upload_bed_data(
            _Session(),  # type: ignore[arg-type]
            sample_context=_sample("S1"),
            bed_type="coverage",
            file=UploadFile(file=BytesIO(b"chr1\t-5\t100\t1.0\n"), filename="coverage.bed"),
            overwrite=True,
        )
    assert excinfo.value.status_code == 400
    assert clickhouse.deletes() == []


@pytest.mark.asyncio
async def test_a_track_row_is_stored_without_a_phase_set_no_column_holds(clickhouse) -> None:
    def row(start: int, ps: int | None) -> dict[str, Any]:
        return {
            "family_id": "family-uuid",
            "sample_id": "uuid-s1",
            "track_type": "haplotype",
            "source": "glimpse2",
            "chr": "1",
            "start": start,
            "end": start + 10,
            "hap1": "P1",
            "hap2": "M1",
            "ps": ps,
            "metadata_json": "{}",
        }

    await clickhouse_interval_tracks.insert_interval_track_rows(
        _ASSEMBLY, [row(10, -4), row(-10, 5), row(30, 2**64), row(50, 7)]
    )

    rows = clickhouse.rows[f"{_ASSEMBLY}/INTERVAL/entries"]
    assert [(row["start"], row["ps"]) for row in rows] == [(10, None), (30, None), (50, 7)]


def test_package_track_readers_leave_out_a_negative_coordinate() -> None:
    sample = _sample("S1")
    path = Path("track.tsv")
    assert _parse_copy_number_interval_row(
        ["chr1", "-5", "100", "0.5"], header=None, sample_context=sample, track_type="coverage",
        source="qdnaseq", path=path, line_no=1,
    ) is None
    assert _parse_wisecondorx_interval_row(
        ["chr1", "100", "-200", "0.5"], header=None, sample_context=sample, track_type="segments",
        path=path, line_no=1,
    ) is None
    assert _parse_apcad_interval_row(
        ["chr1", "-7", "G", "A", ".", "paternal", "0.5"], header=None, sample_context=sample,
        path=path, line_no=1,
    ) is None
    assert _parse_pcf_segment_row(
        {"chrom": "1", "start.pos": "-1", "end.pos": "100", "mean": "0.5"},
        sample_context=sample, path=path, line_no=1, origin="paternal",
    ) is None
    # A coordinate the column holds is read as before.
    stored = _parse_copy_number_interval_row(
        ["chr1", "5", "100", "0.5"], header=None, sample_context=sample, track_type="coverage",
        source="qdnaseq", path=path, line_no=1,
    )
    assert stored is not None and (stored["start"], stored["end"]) == (5, 100)
    table = parse_target_coverage_table(
        [
            "#chromosome\tstart\tend\tattribute\tmean",
            "chr1\t-20\t100\tGENE1;NM_1\t50",
            "chr1\t100\t200\tGENE2;NM_2\t60",
        ]
    )
    assert [(row.start, row.end) for row in table] == [(100, 200)]
