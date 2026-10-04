"""A per-sample small-variant upload replaces only the calls of the samples its file holds.

The mitochondrial calls come one file per sample and are stored as one row per variant
holding every sample's call: two live rows of one variant share a sort key, the family
view reads one of them and a part merge keeps only one. So a sample's upload is merged
into the callset's stored rows: the other samples' calls are written back as stored, and
the uploaded calls join their variant's row. The end-to-end run is
``e2e/test_e2e_mito_import_keeps_every_sample.py``; these pin the rules on their own.

The mito import hands the upload the sample's mutserve annotation as an SQLite lookup in
the temporary directory, which on Cloud Run is memory. The upload leaves a caller's
lookup open, so the import reads the sample's haplogroup from it, and the import removes
it whatever happens.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import clickhouse_variant_storage, family_package_datasets, variant_upload_service
from backend.app.services.annotation_table_parser import parse_mutserve_annotation_lines
from backend.app.services.clickhouse_variant_ids import small_variant_key
from backend.app.services.clickhouse_variant_storage import (
    SMALL_VARIANT_ENTRY_COLUMNS,
    small_variant_entry_with_calls,
    small_variant_entry_without_samples,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ManifestDataset
from backend.app.services.family_package_datasets import DatasetImportJob
from backend.app.services.variant_upload_service import _entries_with_uploaded_calls


def _entry(project: str, pos: int, calls: dict[str, tuple[str, float]], **fields: Any) -> dict[str, Any]:
    """One stored ``entries`` row of chrM ``pos`` A>G: every column, the calls as
    {sample: (gt, af)}, the key the storage gives the variant."""
    row: dict[str, Any] = {column: None for column in SMALL_VARIANT_ENTRY_COLUMNS}
    row.update(
        key=small_variant_key("GRCh38", f"M-{pos}-A-G"),
        variantId=f"M-{pos}-A-G",
        annotation_version="vcf_info",
        annotationSetHash=11,
        project_guid=project,
        family_guid="family-uuid",
        sample_type="WGS",
        xpos=25_000_000_000 + pos,
        chrom="M",
        pos=pos,
        ref="A",
        alt="G",
        source="mito",
        rsid=None,
        is_gnomad_gt_5_percent=False,
        is_annotated_in_any_gene=False,
        gene_symbols=[],
        filters=["GERMLINE"],
        qual=30.0,
        sign=1,
    )
    row["calls.sampleId"] = list(calls)
    row["calls.gt"] = [gt for gt, _af in calls.values()]
    row["calls.gq"] = [30 for _ in calls]
    row["calls.dp"] = [500 for _ in calls]
    row["calls.ab"] = [None for _ in calls]
    row["calls.af"] = [[af] for _gt, af in calls.values()]
    row["calls.ad"] = [[500 - int(af * 500), int(af * 500)] for _gt, af in calls.values()]
    row["calls.ps"] = [None for _ in calls]
    row["calls.filters"] = [[] for _ in calls]
    row["calls.metrics"] = [{} for _ in calls]
    row.update(fields)
    return row


def _calls(row: dict[str, Any]) -> dict[str, tuple[str, float]]:
    return {
        sample: (gt, af[0])
        for sample, gt, af in zip(row["calls.sampleId"], row["calls.gt"], row["calls.af"])
    }


def _by_identity(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    """The rows by (project, chrM position), one per variant and project."""
    by_identity = {(row["project_guid"], row["pos"]): row for row in rows}
    assert len(by_identity) == len(rows), "a variant must stay one row per project"
    return by_identity


def test_entry_without_samples_drops_only_their_calls() -> None:
    entry = _entry("p1", 73, {"MOTHER": ("1/1", 1.0), "PROBAND": ("1/1", 1.0)})
    kept = small_variant_entry_without_samples(entry, {"PROBAND", "proband-uuid"})
    assert kept is not None
    assert _calls(kept) == {"MOTHER": ("1/1", 1.0)}
    assert {k: v for k, v in kept.items() if not k.startswith("calls.")} == {
        k: v for k, v in entry.items() if not k.startswith("calls.")
    }
    assert small_variant_entry_without_samples(entry, {"MOTHER", "PROBAND"}) is None


def test_entry_with_calls_keeps_its_fields_and_sorts_the_calls_by_sample() -> None:
    entry = _entry("p1", 73, {"PROBAND": ("1/1", 1.0)}, qual=18.0)
    other = _entry("p1", 73, {"MOTHER": ("1/1", 0.99)}, qual=40.0, annotationSetHash=22)
    merged = small_variant_entry_with_calls(entry, other)
    assert merged["calls.sampleId"] == ["MOTHER", "PROBAND"]
    assert _calls(merged) == {"MOTHER": ("1/1", 0.99), "PROBAND": ("1/1", 1.0)}
    assert merged["qual"] == 18.0
    assert merged["annotationSetHash"] == 11


def test_uploaded_calls_join_their_variants_row_and_every_other_call_stays() -> None:
    replaced = {"PROBAND", "proband-uuid"}
    stored = [
        # Shared with the mother: the proband's old call is swapped for the new one.
        _entry("p1", 73, {"MOTHER": ("1/1", 1.0), "PROBAND": ("1/1", 1.0)}),
        _entry("p1", 3243, {"MOTHER": ("0/1", 0.15), "proband-uuid": ("0/1", 0.45)}),
        # The proband's alone and not in the new file: gone.
        _entry("p1", 9000, {"PROBAND": ("0/1", 0.3)}),
        # The mother's alone: as stored.
        _entry("p1", 16519, {"MOTHER": ("1/1", 1.0)}),
        # A project the family has left: the proband's old call goes, nothing is added.
        _entry("p0", 73, {"MOTHER": ("1/1", 1.0), "PROBAND": ("1/1", 1.0)}),
    ]
    uploaded = [
        _entry("p1", 73, {"PROBAND": ("1/1", 1.0)}, filters=["PASS"], qual=50.0),
        _entry("p1", 3243, {"PROBAND": ("0/1", 0.5)}, filters=["PASS"]),
        _entry("p1", 10000, {"PROBAND": ("0/1", 0.2)}, filters=["PASS"]),
    ]

    rows = _by_identity(_entries_with_uploaded_calls(stored, replaced, uploaded))

    assert {identity: _calls(row) for identity, row in rows.items()} == {
        ("p1", 73): {"MOTHER": ("1/1", 1.0), "PROBAND": ("1/1", 1.0)},
        ("p1", 3243): {"MOTHER": ("0/1", 0.15), "PROBAND": ("0/1", 0.5)},
        ("p1", 16519): {"MOTHER": ("1/1", 1.0)},
        ("p0", 73): {"MOTHER": ("1/1", 1.0)},
        ("p1", 10000): {"PROBAND": ("0/1", 0.2)},
    }
    # A row the mother's call keeps has its own fields, with both FILTER lists joined.
    assert rows[("p1", 73)]["qual"] == 30.0
    assert rows[("p1", 73)]["filters"] == ["GERMLINE", "PASS"]
    # The mother's row is written back exactly as stored.
    assert rows[("p1", 16519)] == stored[3]
    # A variant new to the callset is the uploaded row itself.
    assert rows[("p1", 10000)] == uploaded[2]


class _FakeSession:
    async def commit(self) -> None:
        return None


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM1",
        project_ids=["p1"],
        sample_rows=[],
        sample_uuid_to_name={"mother-uuid": "MOTHER", "proband-uuid": "PROBAND"},
        sample_name_to_uuid={"MOTHER": "mother-uuid", "PROBAND": "proband-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _sample_contexts() -> dict[str, SampleMetadataContext]:
    return {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM1",
            sex="und",
            project_ids=["p1"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in ("MOTHER", "PROBAND")
    }


_PROBAND_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n"
    "chrM\t73\t.\tA\tG\t50\tPASS\t.\tGT:DP:AD:VAF\t1/1:500:0,500:1\n"
    "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT:DP:AD:VAF\t0/1:500:250,250:0.5\n"
)


@pytest.fixture()
def storage(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake storage around the real upload: the stored rows, and every write it asks for."""
    state: dict[str, Any] = {
        "stored": [
            _entry("p1", 73, {"MOTHER": ("1/1", 1.0), "PROBAND": ("1/1", 1.0)}),
            _entry("p1", 16519, {"MOTHER": ("1/1", 1.0)}),
        ],
        "shared_writes": [],
        "rewrites": [],
        "deletes": [],
        "fail_rewrites": 0,
    }

    async def fetch(_assembly, _family, *, source=None):
        state["fetched_source"] = source
        return [dict(row) for row in state["stored"]]

    async def insert(_assembly, _family, _projects, records, *, annotation_version=None, entries=True):
        state["shared_writes"].append((len(records), entries))

    async def rewrite(_assembly, _family, entries, *, source=None):
        state["rewrites"].append((source, [dict(row) for row in entries]))
        if state["fail_rewrites"]:
            state["fail_rewrites"] -= 1
            raise RuntimeError("ClickHouse went away")

    async def delete(*_args, **kwargs):
        state["deletes"].append(kwargs.get("source"))

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("a per-sample upload does not count or refresh the source itself")

    async def no_lock(*_args, **_kwargs):
        return None

    for name, fn in {
        "fetch_family_small_variant_entries": fetch,
        "insert_small_variant_records": insert,
        "rewrite_family_small_variant_entries": rewrite,
        "delete_family_small_variants": delete,
        "count_family_small_variants": must_not_run,
        "refresh_family_small_variant_summaries": must_not_run,
        # The family's write lock is Postgres's; test_family_variant_writes_serialized has it.
        "lock_family_variant_writes": no_lock,
    }.items():
        monkeypatch.setattr(variant_upload_service, name, fn)
    return state


async def _upload(text: str, *, overwrite: bool) -> dict[str, Any]:
    return await variant_upload_service.upload_family_small_variant_file(
        _FakeSession(),  # type: ignore[arg-type]
        context=_context(),
        sample_contexts=_sample_contexts(),
        file=UploadFile(file=BytesIO(text.encode()), filename="PROBAND.vcf"),
        overwrite=overwrite,
        format_hint="mito",
        overwrite_scope="samples",
    )


@pytest.mark.asyncio
async def test_samples_scope_writes_the_merged_rows_of_its_callset(storage) -> None:
    result = await _upload(_PROBAND_VCF, overwrite=True)

    assert result["inserted"] == 2
    assert storage["fetched_source"] == "mito"
    # The shared annotation rows are written, the family's rows only by the rewrite.
    assert storage["shared_writes"] == [(2, False)]
    assert storage["deletes"] == []
    [(source, rows)] = storage["rewrites"]
    assert source == "mito"
    assert {row["pos"]: _calls(row) for row in rows} == {
        73: {"MOTHER": ("1/1", 1.0), "PROBAND": ("1/1", 1.0)},
        3243: {"PROBAND": ("0/1", 0.5)},
        16519: {"MOTHER": ("1/1", 1.0)},
    }


@pytest.mark.asyncio
async def test_samples_scope_refuses_without_overwrite_when_the_files_samples_have_calls(storage) -> None:
    with pytest.raises(HTTPException) as exc:
        await _upload(_PROBAND_VCF, overwrite=False)
    assert exc.value.status_code == 409
    assert storage["shared_writes"] == [] and storage["rewrites"] == [] and storage["deletes"] == []


@pytest.mark.asyncio
async def test_samples_scope_accepts_without_overwrite_a_sample_with_no_calls(storage) -> None:
    storage["stored"] = [_entry("p1", 16519, {"MOTHER": ("1/1", 1.0)})]
    result = await _upload(_PROBAND_VCF, overwrite=False)
    assert result["inserted"] == 2
    [(_source, rows)] = storage["rewrites"]
    assert {row["pos"]: _calls(row) for row in rows} == {
        73: {"PROBAND": ("1/1", 1.0)},
        3243: {"PROBAND": ("0/1", 0.5)},
        16519: {"MOTHER": ("1/1", 1.0)},
    }


@pytest.mark.asyncio
async def test_a_failed_rewrite_puts_the_stored_rows_back_and_never_clears_the_source(storage) -> None:
    storage["fail_rewrites"] = 1
    with pytest.raises(RuntimeError, match="ClickHouse went away"):
        await _upload(_PROBAND_VCF, overwrite=True)
    # The failed rewrite, then the rows as they were read.
    assert len(storage["rewrites"]) == 2
    assert storage["rewrites"][1] == ("mito", storage["stored"])
    # The upload's own cleanup deletes its whole source; for one sample's file that
    # would be every other sample's calls.
    assert storage["deletes"] == []


@pytest.mark.asyncio
async def test_samples_scope_refuses_a_file_that_names_no_sample(storage) -> None:
    headerless = "##fileformat=VCFv4.2\nchrM\t73\t.\tA\tG\t50\tPASS\t.\tGT\t1/1\n"
    with pytest.raises(HTTPException) as exc:
        await _upload(headerless, overwrite=True)
    assert exc.value.status_code == 400
    assert storage["rewrites"] == []


@pytest.mark.asyncio
async def test_removing_a_samples_calls_keeps_every_other_call(storage) -> None:
    removed = await variant_upload_service.remove_family_small_variant_sample_calls(
        _FakeSession(), _context(), [_sample_contexts()["PROBAND"]], source="mito"  # type: ignore[arg-type]
    )
    assert removed == 1
    [(source, rows)] = storage["rewrites"]
    assert source == "mito"
    assert [_calls(row) for row in rows] == [{"MOTHER": ("1/1", 1.0)}, {"MOTHER": ("1/1", 1.0)}]

    storage["rewrites"].clear()
    storage["stored"] = [_entry("p1", 16519, {"MOTHER": ("1/1", 1.0)})]
    removed = await variant_upload_service.remove_family_small_variant_sample_calls(
        _FakeSession(), _context(), [_sample_contexts()["PROBAND"]], source="mito"  # type: ignore[arg-type]
    )
    # Nothing of the sample's is stored: no write, so the family's data version stays.
    assert removed == 0
    assert storage["rewrites"] == []


@pytest.mark.asyncio
async def test_source_scoped_rewrite_reads_and_writes_only_that_callset(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[tuple[str, Any]] = []

    async def fake_execute(query, params=None, data=None):
        queries.append((" ".join(query.split()), params if data is None else len(data)))
        return []

    async def fake_ensure(_assembly):
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", fake_ensure)

    await clickhouse_variant_storage.fetch_family_small_variant_entries("GRCh38", "family-uuid", source="mito")
    select, params = queries[0]
    assert "AND source = %(source)s" in select and params == {"family_guid": "family-uuid", "source": "mito"}

    queries.clear()
    await clickhouse_variant_storage.rewrite_family_small_variant_entries("GRCh38", "family-uuid", [], source="mito")
    statements = [query for query, _params in queries]
    # Only the callset's rows are deleted, and with no row left to write the summaries are
    # still rebuilt from the other callsets' rows.
    assert "SNV_INDEL/entries` DELETE WHERE family_guid = %(family_guid)s AND source = %(source)s" in statements[0]
    assert any("INSERT INTO" in q and "family_sample_variant_summary" in q for q in statements)


@pytest.mark.asyncio
async def test_entry_columns_are_the_columns_the_insert_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    # small_variant_record_entries names the row builder's values by position with
    # SMALL_VARIANT_ENTRY_COLUMNS, so the insert must write exactly those, in that order.
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    inserts: list[str] = []

    async def fake_execute(query, params=None, data=None):
        if "SNV_INDEL/entries" in query and "INSERT" in query:
            inserts.append(" ".join(query.split()))
        return []

    async def fake_ensure(_assembly):
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", fake_ensure)
    record = SmallVariantRecord(
        variant_key=None,
        variant_id="M-73-A-G",
        chr="M",
        start=73,
        end=73,
        ref="A",
        alt="G",
        source="mito",
        rsid=None,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[],
        calls=[SmallVariantCall(sample="PROBAND", gt="1/1", gq=30.0, dp=500, af=[1.0], ad=[0, 500], ps=None)],
        qual=30.0,
    )
    await clickhouse_variant_storage.insert_small_variant_records("GRCh38", "family-uuid", ["p1"], [record])

    [insert] = inserts
    written = [column.strip().strip("`") for column in insert.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert tuple(written) == SMALL_VARIANT_ENTRY_COLUMNS
    [entry] = clickhouse_variant_storage.small_variant_record_entries(
        "GRCh38", "family-uuid", ["p1"], [record]
    )
    assert (entry["source"], entry["pos"], entry["calls.sampleId"], entry["qual"]) == ("mito", 73, ["PROBAND"], 30.0)


# mutserve's annotation of the proband's two chrM variants. Each row lists the haplogroups
# its variant marks; the sample's haplogroup is the one most rows name (H1, twice).
_MUTSERVE_TSV = (
    "ID\tFilter\tPos\tRef\tVariant\tVariantLevel\tCoverage\tMaplocus\tPhylotree17_haplogroups\n"
    "sample\tGERMLINE\t73\tA\tG\t1.0\t500\tMT-DLOOP2\tH2a2a1,H1\n"
    "sample\tGERMLINE\t3243\tA\tG\t0.5\t500\tMT-TL1\tH1\n"
)

_VEP_TSV = (
    "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tIMPACT\tSYMBOL\n"
    "chrM_3243_A/G\tchrM:3243\tG\tENSG1\tENST1\tTranscript\tnon_coding_transcript_exon_variant\tMODIFIER\tMT-TL1\n"
)


@pytest.fixture()
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The temporary directory the annotation lookups are created in, empty to begin with."""
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


@pytest.mark.asyncio
async def test_the_upload_leaves_a_callers_lookup_open_and_removes_its_own(storage, scratch: Path) -> None:
    lookup = parse_mutserve_annotation_lines(_MUTSERVE_TSV.splitlines(keepends=True))
    try:
        for fail_rewrites in (0, 1):
            storage["fail_rewrites"] = fail_rewrites
            try:
                result = await variant_upload_service.upload_family_small_variant_file(
                    _FakeSession(),  # type: ignore[arg-type]
                    context=_context(),
                    sample_contexts=_sample_contexts(),
                    file=UploadFile(file=BytesIO(_PROBAND_VCF.encode()), filename="PROBAND.vcf"),
                    overwrite=True,
                    format_hint="mito",
                    vep_annotations=lookup,
                    overwrite_scope="samples",
                )
                assert result["annotation_rows"] == 2
            except RuntimeError:
                assert fail_rewrites
            # Succeeded or not, the caller's lookup is still there for it to read and close.
            assert lookup.conn is not None
            assert len(list(scratch.iterdir())) == 1
    finally:
        lookup.close()
    assert list(scratch.iterdir()) == []

    # A VEP table the upload parses itself is the upload's to remove.
    storage["fail_rewrites"] = 0
    await variant_upload_service.upload_family_small_variant_file(
        _FakeSession(),  # type: ignore[arg-type]
        context=_context(),
        sample_contexts=_sample_contexts(),
        file=UploadFile(file=BytesIO(_PROBAND_VCF.encode()), filename="PROBAND.vcf"),
        overwrite=True,
        format_hint="mito",
        annotation_file=UploadFile(file=BytesIO(_VEP_TSV.encode()), filename="PROBAND.vep.tsv"),
        overwrite_scope="samples",
    )
    assert list(scratch.iterdir()) == []


@pytest.fixture()
def recorded_mtdna(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """The mtDNA metadata the import records, by sample."""
    recorded: dict[str, dict[str, Any]] = {}

    async def record(_session, *, sample_context, mtdna):
        recorded[sample_context.sample_id] = mtdna

    monkeypatch.setattr(family_package_datasets, "record_sample_mtdna_metadata", record)
    return recorded


def _mito_job(root: Path, vcf: str, annotation: str = _MUTSERVE_TSV) -> DatasetImportJob:
    """The proband's entry of a package's ``mito`` dataset: its chrM VCF and mutserve annotation."""
    root.mkdir(exist_ok=True)
    (root / "PROBAND.vcf").write_text(vcf, encoding="utf-8")
    (root / "PROBAND_snv_annot.txt").write_text(annotation, encoding="utf-8")
    return DatasetImportJob(
        session=_FakeSession(),  # type: ignore[arg-type]
        bundle=SimpleNamespace(root=root),  # type: ignore[arg-type]
        dataset=ManifestDataset(
            per_sample={"PROBAND": {"vcf": "PROBAND.vcf", "annotation_tsv": "PROBAND_snv_annot.txt"}}
        ),
        summary=FamilyImportDatasetSummary(dataset_type="mito", status="valid"),
        family_context=_context(),
        sample_contexts=_sample_contexts(),
    )


@pytest.mark.asyncio
async def test_the_mito_import_records_the_samples_haplogroup_and_removes_its_lookup(
    storage, scratch: Path, recorded_mtdna, tmp_path: Path
) -> None:
    summary = await family_package_datasets._import_mito_dataset(_mito_job(tmp_path / "package", _PROBAND_VCF))

    assert summary.status == "imported"
    assert summary.summary["PROBAND"]["inserted"] == 2
    # Read from the annotation after the upload, which used to have closed it by then.
    assert recorded_mtdna == {"PROBAND": {"haplogroup": "H1", "haplogroup_support": 2}}
    assert list(scratch.iterdir()) == []


@pytest.mark.asyncio
async def test_a_sample_with_no_chrm_call_leaves_no_lookup(storage, scratch: Path, recorded_mtdna, tmp_path: Path) -> None:
    no_calls = "\n".join(_PROBAND_VCF.splitlines()[:2]) + "\n"

    summary = await family_package_datasets._import_mito_dataset(_mito_job(tmp_path / "package", no_calls))

    assert summary.summary["PROBAND"]["message"] == "No chrM variants called"
    assert summary.summary["PROBAND"]["removed"] == 1
    assert recorded_mtdna == {}
    assert list(scratch.iterdir()) == []


@pytest.mark.asyncio
async def test_a_failed_mito_upload_leaves_no_lookup(storage, scratch: Path, recorded_mtdna, tmp_path: Path) -> None:
    storage["fail_rewrites"] = 1

    with pytest.raises(RuntimeError, match="ClickHouse went away"):
        await family_package_datasets._import_mito_dataset(_mito_job(tmp_path / "package", _PROBAND_VCF))

    assert recorded_mtdna == {}
    assert list(scratch.iterdir()) == []


@pytest.mark.asyncio
async def test_a_file_naming_an_unknown_sample_is_refused_before_its_annotation_is_read(
    storage, scratch: Path, recorded_mtdna, tmp_path: Path
) -> None:
    lines = _PROBAND_VCF.splitlines()
    two_samples = "\n".join(
        [lines[0], lines[1] + "\tSTRANGER"] + [line + "\t1/1:500:0,500:1" for line in lines[2:]]
    ) + "\n"

    with pytest.raises(RuntimeError, match="match no family sample"):
        await family_package_datasets._import_mito_dataset(_mito_job(tmp_path / "package", two_samples))

    assert storage["rewrites"] == [] and recorded_mtdna == {}
    assert list(scratch.iterdir()) == []
