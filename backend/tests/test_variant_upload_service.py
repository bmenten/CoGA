import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services.clickhouse_variant_ids import build_small_variant_id
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services import clickhouse_variant_storage, haplotype_block_builder, variant_upload_service
from backend.tests._event_loop_probe import LoopProbe
from backend.app.services.annotation_table_parser import _coerce_int, _vep_location_allele_key
from backend.app.services.haplotype_block_builder import (
    _haplotype_state_end,
    _haplotype_state_matches_block,
    _new_haplotype_state,
    _phased_haplotype_alleles,
)
from backend.app.services.variant_upload_service import (
    _detect_small_variant_format_from_upload,
    _iter_upload_text_lines,
    _parse_float_list,
    _parse_int_list,
    _parse_qual,
    _parse_vep_tsv_annotation_upload,
)


@pytest.fixture(autouse=True)
def _no_family_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The family's write lock is Postgres's; test_family_variant_writes_serialized has it.
    So is the write of the parents' phase corrections onto the family."""

    async def no_lock(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", no_lock)
    monkeypatch.setattr(variant_upload_service, "record_haplotype_phase_corrections", no_lock)


def test_coerce_int_tolerates_floats_and_junk() -> None:
    assert _coerce_int("42") == 42
    assert _coerce_int("42.0") == 42
    assert _coerce_int(".") is None
    assert _coerce_int("") is None
    assert _coerce_int(None) is None
    assert _coerce_int("not-a-number") is None


def test_parse_float_list_keeps_every_allele_fraction_or_none_without_raising() -> None:
    # A single bad AF token must not abort the whole ingest with a 500.
    assert _parse_float_list("0.1,0.3") == [0.1, 0.3]
    # Nor may it move a fraction onto another allele (DATA-7): leaving the bad one out gave
    # the second allele's 0.3 to the first. A list with one gone keeps none.
    assert _parse_float_list("0.1,junk,0.3") == []
    assert _parse_float_list(".,0.3") == []
    assert _parse_float_list("inf,0.5") == []  # non-finite
    assert _parse_float_list("0.1,") == []
    assert _parse_float_list(".") == []
    assert _parse_float_list(None) == []


def test_parse_int_list_coerces_bad_tokens_to_zero() -> None:
    # AD is positional; a bad token maps to 0 rather than raising or shifting alignment.
    assert _parse_int_list("5,10,20") == [5, 10, 20]
    assert _parse_int_list("5,junk,20") == [5, 0, 20]
    assert _parse_int_list(".") == []


def _line_upload(data: bytes) -> UploadFile:
    return UploadFile(file=BytesIO(data))


def test_iter_upload_text_lines_rejects_overlong_line(monkeypatch) -> None:
    # A VCF with no newline (or a gzip inflating to one giant line) must not buffer an
    # unbounded line into memory — it is rejected at the per-line cap.
    monkeypatch.setattr(variant_upload_service, "MAX_UPLOAD_LINE_BYTES", 16)
    upload = _line_upload(b"x" * 64)  # 64 bytes, no newline, cap 16
    with pytest.raises(HTTPException) as exc:
        list(_iter_upload_text_lines(upload, kind="VCF"))
    assert exc.value.status_code == 413


def test_iter_upload_text_lines_allows_normal_lines(monkeypatch) -> None:
    monkeypatch.setattr(variant_upload_service, "MAX_UPLOAD_LINE_BYTES", 1024)
    upload = _line_upload(b"##fileformat=VCFv4.2\nchr1\t1\t.\tA\tT\n")
    lines = list(_iter_upload_text_lines(upload, kind="VCF"))
    assert lines[0].startswith("##fileformat")
    assert lines[1].startswith("chr1")


class _NoopSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_non_overwrite_conflict_does_not_delete_existing_rows(monkeypatch) -> None:
    # A non-overwrite upload against a family that already has this source must 409
    # WITHOUT the compensating cleanup deleting the pre-existing rows it protects.
    deleted = {"called": False}

    async def fake_count(*args, **kwargs):
        return 5  # existing rows present

    async def fake_delete(*args, **kwargs):
        deleted["called"] = True

    monkeypatch.setattr(variant_upload_service, "count_family_small_variants", fake_count)
    monkeypatch.setattr(variant_upload_service, "delete_family_small_variants", fake_delete)

    context = SimpleNamespace(
        assembly_name="GRCh38",
        family_uuid="uuid-1",
        family_id="F1",
        project_ids=[],
    )
    with pytest.raises(HTTPException) as exc:
        await variant_upload_service.upload_family_small_variant_file(
            _NoopSession(),
            context=context,
            sample_contexts={},
            file=_line_upload(b"##fileformat=VCFv4.2\n"),
            overwrite=False,
            format_hint="clair3",
        )
    assert exc.value.status_code == 409
    assert deleted["called"] is False  # existing rows were NOT touched


def test_parse_qual_reads_float_and_treats_missing_as_none() -> None:
    assert _parse_qual("42.5") == 42.5
    assert _parse_qual("0") == 0.0
    assert _parse_qual(".") is None
    assert _parse_qual("") is None
    assert _parse_qual(None) is None
    assert _parse_qual("not-a-number") is None


def test_haplotype_state_end_uses_next_variant_on_same_chromosome() -> None:
    state = {
        "start": 100,
        "last_pos": 200,
        "chr": "1",
    }

    assert _haplotype_state_end(
        state,
        next_chrom="1",
        next_start=500,
        chromosome_sizes={"1": 1_000},
    ) == 500


def test_haplotype_state_end_uses_previous_chromosome_size_on_chromosome_change() -> None:
    state = {
        "start": 800,
        "last_pos": 950,
        "chr": "1",
    }

    assert _haplotype_state_end(
        state,
        next_chrom="2",
        next_start=10,
        chromosome_sizes={"1": 1_000, "2": 2_000},
    ) == 1_000


def test_haplotype_state_end_falls_back_to_last_variant_without_chromosome_size() -> None:
    state = {
        "start": 800,
        "last_pos": 950,
        "chr": "1",
    }

    assert _haplotype_state_end(
        state,
        next_chrom=None,
        next_start=None,
        chromosome_sizes={},
    ) == 951


def test_haplotype_blocks_follow_phase_set_not_individual_genotype_state() -> None:
    state = _new_haplotype_state(chrom="1", start=100, hap1="0", hap2="1", ps=42)

    assert _phased_haplotype_alleles("1|0") == ("1", "0")
    assert _haplotype_state_matches_block(state, chrom="1", ps=42) is True
    assert _haplotype_state_matches_block(state, chrom="1", ps=43) is False


def test_haplotype_blocks_without_phase_set_stay_chromosome_contiguous() -> None:
    state = _new_haplotype_state(chrom="1", start=100, hap1="0", hap2="1", ps=None)

    assert _haplotype_state_matches_block(state, chrom="1", ps=None) is True
    assert _haplotype_state_matches_block(state, chrom="2", ps=None) is False
    assert _phased_haplotype_alleles("0/1") is None


def test_gt_only_shapeit_vcf_is_detected_as_glimpse2() -> None:
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##source=SHAPEIT5 phase_common 1.1\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Phased genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\tGT\t0|1\t0|0\n"
    )

    # The header's SHAPEIT5 source alone says so, whatever the file is called.
    upload = UploadFile(file=BytesIO(vcf_text.encode()), filename="FAM001.vcf")
    assert _detect_small_variant_format_from_upload(upload, "auto") == "glimpse2"

    upload = UploadFile(file=BytesIO(vcf_text.encode()), filename="FAM001_phased_final.vcf")
    assert _detect_small_variant_format_from_upload(upload, "auto") == "glimpse2"


def test_gt_only_clair3_vcf_is_not_detected_as_glimpse2() -> None:
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##source=clair3\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
    )

    upload = UploadFile(file=BytesIO(vcf_text.encode()), filename="S1.vcf")
    assert _detect_small_variant_format_from_upload(upload, "auto") == "clair3"


def test_segregation_haplotype_switch_requires_repeated_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(haplotype_block_builder, "SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS", 2)
    monkeypatch.setattr(haplotype_block_builder, "SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN", 10)

    state = haplotype_block_builder._empty_segregation_side_state()
    assert (
        haplotype_block_builder._observe_segregation_haplotype(
            state,
            chrom="1",
            start=100,
            hap="0",
        )
        == (100, "0")
    )
    assert (
        haplotype_block_builder._observe_segregation_haplotype(
            state,
            chrom="1",
            start=120,
            hap="1",
        )
        is None
    )
    assert (
        haplotype_block_builder._observe_segregation_haplotype(
            state,
            chrom="1",
            start=130,
            hap="0",
        )
        is None
    )
    assert (
        haplotype_block_builder._observe_segregation_haplotype(
            state,
            chrom="1",
            start=200,
            hap="1",
        )
        is None
    )
    assert (
        haplotype_block_builder._observe_segregation_haplotype(
            state,
            chrom="1",
            start=215,
            hap="1",
        )
        == (200, "1")
    )


@pytest.mark.asyncio
async def test_glimpse2_upload_stores_haplotype_blocks_separate_from_snv_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted_variant_batches = []
    inserted_haplotype_rows = []
    upserted_sources = []

    async def fake_count_family_small_variants(*_args, **_kwargs):
        return 0

    async def fake_get_track_presence_by_sample(*_args, **_kwargs):
        return set()

    async def fake_insert_small_variant_records(*_args, **kwargs):
        inserted_variant_batches.append(list(_args[3]))
        assert kwargs["annotation_version"] == "vcf_info"

    async def fake_refresh_family_small_variant_summaries(*_args, **_kwargs):
        return None

    async def fake_insert_interval_track_rows(_assembly_name, rows):
        inserted_haplotype_rows.extend(rows)

    async def fake_upsert_interval_track_source(*_args, **kwargs):
        upserted_sources.append(kwargs)

    async def fake_fetch_chromosome_sizes(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        variant_upload_service,
        "count_family_small_variants",
        fake_count_family_small_variants,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "get_track_presence_by_sample",
        fake_get_track_presence_by_sample,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "insert_small_variant_records",
        fake_insert_small_variant_records,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "refresh_family_small_variant_summaries",
        fake_refresh_family_small_variant_summaries,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "insert_interval_track_rows",
        fake_insert_interval_track_rows,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "upsert_interval_track_source",
        fake_upsert_interval_track_source,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "_fetch_chromosome_sizes",
        fake_fetch_chromosome_sizes,
    )

    class FakeSession:
        commits = 0

        async def commit(self) -> None:
            self.commits += 1

    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={"sample-uuid": "S1"},
        sample_name_to_uuid={"S1": "sample-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    sample_contexts = {
        "S1": SampleMetadataContext(
            sample_uuid="sample-uuid",
            sample_id="S1",
            family_uuid="family-uuid",
            family_id="FAM001",
            sex="und",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
    }
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Phased genotype\">\n"
        "##FORMAT=<ID=GP,Number=G,Type=Float,Description=\"Genotype probabilities\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\tGT:GP\t0|1:0.01,0.98,0.01\n"
        "1\t200\t.\tC\tT\t.\tPASS\t.\tGT:GP\t1|0:0.01,0.98,0.01\n"
        "1\t300\t.\tG\tA\t.\tPASS\t.\tGT:GP\t0|0:0.98,0.01,0.01\n"
    )
    upload = UploadFile(file=BytesIO(vcf_text.encode()), filename="glimpse2.vcf")

    result = await variant_upload_service.upload_family_small_variant_file(
        FakeSession(),  # type: ignore[arg-type]
        context=context,
        sample_contexts=sample_contexts,
        file=upload,
        overwrite=False,
        format_hint="auto",
    )

    assert result["source_format"] == "glimpse2"
    assert result["inserted"] == 3
    assert result["haplotypes_inserted"] == 1
    assert len(inserted_variant_batches[0]) == 3
    assert inserted_haplotype_rows == [
        {
            "sample_id": "sample-uuid",
            "family_id": "family-uuid",
            "assembly_id": "assembly-uuid",
            "track_type": "haplotype",
            "source": "glimpse2",
            "chr": "1",
            "start": 100,
            "end": 301,
            "hap1": "0",
            "hap2": "1",
            "ps": None,
            "metadata_json": inserted_haplotype_rows[0]["metadata_json"],
        }
    ]
    assert upserted_sources[0]["track_type"] == "haplotype"
    assert upserted_sources[0]["row_count"] == 1


_GLIMPSE2_VCF = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Phased genotype">\n'
    '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype probabilities">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "1\t100\t.\tA\tG\t.\tPASS\t.\tGT:GP\t0|1:0.01,0.98,0.01\n"
)
_CLAIR3_VCF = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "1\t100\t.\tA\tG\t.\tPASS\t.\tGT:DP\t0/1:30\n"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vcf_text, expected_source, expect_haplotype_block_delete",
    [
        (_GLIMPSE2_VCF, "glimpse2", True),
        (_CLAIR3_VCF, "clair3", False),
    ],
)
async def test_overwrite_scopes_delete_to_uploaded_source(
    monkeypatch: pytest.MonkeyPatch,
    vcf_text: str,
    expected_source: str,
    expect_haplotype_block_delete: bool,
) -> None:
    # #329: re-importing one small-variant callset must not wipe the other. The
    # overwrite branch must delete only the uploaded source's rows, and only clear
    # haplotype blocks for glimpse2 (which owns them).
    count_sources: list[str | None] = []
    deleted_sources: list[str | None] = []
    haplotype_block_deletes: list[bool] = []

    async def fake_count_family_small_variants(*_args, **kwargs):
        count_sources.append(kwargs.get("source"))
        return 5  # rows already exist -> overwrite branch fires

    async def fake_delete_family_small_variants(_assembly, _family, *, source=None):
        deleted_sources.append(source)

    async def fake_delete_family_haplotype_blocks(*_args, **_kwargs):
        haplotype_block_deletes.append(True)

    async def fake_get_track_presence_by_sample(*_args, **_kwargs):
        return set()

    async def fake_noop(*_args, **_kwargs):
        return None

    async def fake_fetch_chromosome_sizes(*_args, **_kwargs):
        return {}

    for name, fn in {
        "count_family_small_variants": fake_count_family_small_variants,
        "delete_family_small_variants": fake_delete_family_small_variants,
        "_delete_family_haplotype_blocks": fake_delete_family_haplotype_blocks,
        "get_track_presence_by_sample": fake_get_track_presence_by_sample,
        "insert_small_variant_records": fake_noop,
        "refresh_family_small_variant_summaries": fake_noop,
        "insert_interval_track_rows": fake_noop,
        "upsert_interval_track_source": fake_noop,
        "_fetch_chromosome_sizes": fake_fetch_chromosome_sizes,
    }.items():
        monkeypatch.setattr(variant_upload_service, name, fn)

    class FakeSession:
        async def commit(self) -> None:
            return None

    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={"sample-uuid": "S1"},
        sample_name_to_uuid={"S1": "sample-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    sample_contexts = {
        "S1": SampleMetadataContext(
            sample_uuid="sample-uuid",
            sample_id="S1",
            family_uuid="family-uuid",
            family_id="FAM001",
            sex="und",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
    }
    upload = UploadFile(file=BytesIO(vcf_text.encode()), filename="variants.vcf")

    result = await variant_upload_service.upload_family_small_variant_file(
        FakeSession(),  # type: ignore[arg-type]
        context=context,
        sample_contexts=sample_contexts,
        file=upload,
        overwrite=True,
        format_hint="auto",
    )

    assert result["source_format"] == expected_source
    # Existence check and delete are both scoped to the uploaded source only.
    assert count_sources == [expected_source]
    assert deleted_sources == [expected_source]
    # Haplotype blocks are cleared only for the glimpse2 loader that owns them.
    assert bool(haplotype_block_deletes) is expect_haplotype_block_delete


@pytest.mark.asyncio
async def test_the_upload_reports_the_records_and_the_bytes_it_has_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A package import estimates a dataset's time left from the bytes of its file read
    # (import_progress). The records left out are counted as read: half a long-read
    # callset is RefCall blocks, and a NIPT father's file is mostly noise.
    monkeypatch.setattr(variant_upload_service, "SMALL_VARIANT_PROGRESS_INTERVAL", 2)
    monkeypatch.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", 1)

    async def no_rows(*_args, **_kwargs):
        return 0

    async def no_tracks(*_args, **_kwargs):
        return set()

    async def fake_noop(*_args, **_kwargs):
        return None

    for name, fn in {
        "count_family_small_variants": no_rows,
        "get_track_presence_by_sample": no_tracks,
        "insert_small_variant_records": fake_noop,
        "refresh_family_small_variant_summaries": fake_noop,
    }.items():
        monkeypatch.setattr(variant_upload_service, name, fn)

    class FakeSession:
        async def commit(self) -> None:
            return None

    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={"sample-uuid": "S1"},
        sample_name_to_uuid={"S1": "sample-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    sample_contexts = {
        "S1": SampleMetadataContext(
            sample_uuid="sample-uuid",
            sample_id="S1",
            family_uuid="family-uuid",
            family_id="FAM001",
            sex="und",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
    }
    records = "".join(
        f"1\t{100 + index}\t.\tA\tG\t.\t{'RefCall' if index % 2 else 'PASS'}\t.\tGT:DP\t0/1:30\n"
        for index in range(6)
    )
    vcf_text = _CLAIR3_VCF.splitlines(keepends=True)
    data = ("".join(vcf_text[:-1]) + records).encode()
    reports: list[dict] = []

    async def progress(stats: dict) -> None:
        reports.append(stats)

    result = await variant_upload_service.upload_family_small_variant_file(
        FakeSession(),  # type: ignore[arg-type]
        context=context,
        sample_contexts=sample_contexts,
        file=UploadFile(file=BytesIO(data), filename="variants.vcf"),
        overwrite=True,
        format_hint="clair3",
        exclude_filters=["RefCall"],
        progress=progress,
    )

    reading = [report for report in reports if "bytes_read" in report]
    # Every second record read, the left-out ones too; stored: those flushed before it.
    assert [(report["processed"], report["inserted"]) for report in reading] == [(2, 1), (4, 2), (6, 3)]
    assert {report["bytes_total"] for report in reading} == {len(data)}
    assert all(0 < report["bytes_read"] <= len(data) for report in reading)
    assert result["inserted"] == 3
    assert result["skipped_filtered"] == 3
    # The last report is the result.
    assert reports[-1] == result


@pytest.mark.asyncio
async def test_glimpse2_upload_derives_child_haplotype_blocks_from_parental_segregation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted_haplotype_rows = []
    monkeypatch.setattr(haplotype_block_builder, "SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS", 1)
    monkeypatch.setattr(haplotype_block_builder, "SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN", 0)

    async def fake_count_family_small_variants(*_args, **_kwargs):
        return 0

    async def fake_get_track_presence_by_sample(*_args, **_kwargs):
        return set()

    async def fake_insert_small_variant_records(*_args, **_kwargs):
        return None

    async def fake_refresh_family_small_variant_summaries(*_args, **_kwargs):
        return None

    async def fake_insert_interval_track_rows(_assembly_name, rows):
        inserted_haplotype_rows.extend(rows)

    async def fake_upsert_interval_track_source(*_args, **_kwargs):
        return None

    async def fake_fetch_chromosome_sizes(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        variant_upload_service,
        "count_family_small_variants",
        fake_count_family_small_variants,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "get_track_presence_by_sample",
        fake_get_track_presence_by_sample,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "insert_small_variant_records",
        fake_insert_small_variant_records,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "refresh_family_small_variant_summaries",
        fake_refresh_family_small_variant_summaries,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "insert_interval_track_rows",
        fake_insert_interval_track_rows,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "upsert_interval_track_source",
        fake_upsert_interval_track_source,
    )
    monkeypatch.setattr(
        variant_upload_service,
        "_fetch_chromosome_sizes",
        fake_fetch_chromosome_sizes,
    )

    class FakeSession:
        async def commit(self) -> None:
            return None

    members = [
        ("father-uuid", "FATHER", "father", False, "male"),
        ("mother-uuid", "MOTHER", "mother", False, "female"),
        ("affected-uuid", "AFFECTED", "proband", True, "female"),
        ("embryo-uuid", "EMBRYO1", "embryo", False, "und"),
    ]
    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[
            {
                "sample_uuid": sample_uuid,
                "sample_id": sample_id,
                "role": role,
                "affected": affected,
                "sex": sex,
            }
            for sample_uuid, sample_id, role, affected, sex in members
        ],
        sample_uuid_to_name={sample_uuid: sample_id for sample_uuid, sample_id, *_ in members},
        sample_name_to_uuid={sample_id: sample_uuid for sample_uuid, sample_id, *_ in members},
        affected_sample_names=["AFFECTED"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    sample_contexts = {
        sample_id: SampleMetadataContext(
            sample_uuid=sample_uuid,
            sample_id=sample_id,
            family_uuid="family-uuid",
            family_id="FAM001",
            sex=sex,
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for sample_uuid, sample_id, _role, _affected, sex in members
    }
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Phased genotype\">\n"
        "##FORMAT=<ID=GP,Number=G,Type=Float,Description=\"Genotype probabilities\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tFATHER\tMOTHER\tAFFECTED\tEMBRYO1\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\tGT:GP\t0|1:.\t0|0:.\t0|1:.\t0|1:.\n"
        "1\t150\t.\tC\tT\t.\tPASS\t.\tGT:GP\t0|0:.\t0|1:.\t0|1:.\t0|1:.\n"
        "1\t200\t.\tG\tA\t.\tPASS\t.\tGT:GP\t0|1:.\t0|0:.\t0|1:.\t0|0:.\n"
        "1\t250\t.\tT\tC\t.\tPASS\t.\tGT:GP\t0|0:.\t0|1:.\t0|1:.\t0|0:.\n"
    )
    upload = UploadFile(file=BytesIO(vcf_text.encode()), filename="glimpse2.vcf")

    result = await variant_upload_service.upload_family_small_variant_file(
        FakeSession(),  # type: ignore[arg-type]
        context=context,
        sample_contexts=sample_contexts,
        file=upload,
        overwrite=False,
        format_hint="auto",
    )

    assert result["haplotypes_inserted"] >= 6
    rows_by_uuid: dict[str, list[dict[str, object]]] = {}
    for row in inserted_haplotype_rows:
        rows_by_uuid.setdefault(str(row["sample_id"]), []).append(row)

    embryo_rows = rows_by_uuid["embryo-uuid"]
    assert [(row["start"], row["end"], row["hap1"], row["hap2"]) for row in embryo_rows] == [
        (100, 150, "1", "?"),
        (150, 200, "1", "1"),
        (200, 250, "0", "1"),
        (250, 251, "0", "0"),
    ]
    affected_rows = rows_by_uuid["affected-uuid"]
    assert affected_rows[-1]["hap1"] == "1"
    assert affected_rows[-1]["hap2"] == "1"
    assert rows_by_uuid["father-uuid"][0]["hap1"] == "0"
    assert rows_by_uuid["father-uuid"][0]["hap2"] == "1"
    assert rows_by_uuid["mother-uuid"][0]["hap1"] == "0"
    assert rows_by_uuid["mother-uuid"][0]["hap2"] == "1"


def _vep_upload(text: str) -> UploadFile:
    return UploadFile(file=BytesIO(text.encode()), filename="vep.tsv")


def test_parse_vep_tsv_annotation_upload_indexes_by_variant_id_and_locus_allele() -> None:
    lookup = _parse_vep_tsv_annotation_upload(
        _vep_upload(
            "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tIMPACT\tSYMBOL\tCANONICAL\n"
            "chr1_101_A/G\tchr1:101\tG\tENSG1\tENST1\tTranscript\tmissense_variant\tMODERATE\tGENE1\tYES\n"
        )
    )
    try:
        variant_id = build_small_variant_id("1", 101, "A", "G")
        assert lookup.row_count == 1
        by_id = lookup.get(variant_id, "1", 101, "A", "G")
        assert by_id is not None and by_id[0]["gene"] == "GENE1"
        # A non-matching variant_id falls back to the (chrom, start, allele) index.
        by_locus = lookup.get("no-such-variant", "1", 101, "A", "G")
        assert by_locus is not None and by_locus[0]["effect"] == "missense_variant"
    finally:
        lookup.close()


def test_vep_location_allele_key_normalizes_indels() -> None:
    # SNV: position and alt allele are unchanged.
    assert _vep_location_allele_key("1", 101, "A", "G") == "1:101:G"
    # Insertion A>AT: VEP strips the shared anchor, allele "T" at the same start.
    assert _vep_location_allele_key("1", 10084, "C", "CT") == "1:10084:T"
    assert _vep_location_allele_key("1", 10095, "C", "CCT") == "1:10095:CT"
    # Deletion AC>A: dash allele at the shifted (post-anchor) position.
    assert _vep_location_allele_key("1", 10146, "AC", "A") == "1:10147:-"
    assert _vep_location_allele_key("1", 10807, "CTAG", "C") == "1:10808:-"
    # Shared suffix is trimmed before classifying (CAT>CGT is a SNV at 102).
    assert _vep_location_allele_key("1", 101, "CAT", "CGT") == "1:102:G"


def test_parse_vep_tsv_annotation_upload_matches_indels_by_normalized_locus() -> None:
    # VEP left-aligns/trims indels, so the Uploaded_variation position is shifted
    # off the VCF POS: the insertion VCF 10084 C>CT is reported at 10085 with
    # Location 10084-10085 / Allele "T", and the deletion VCF 10146 AC>A at
    # Location 10147 / Allele "-". The exact variant_id lookup therefore misses
    # and the normalized Location+Allele fallback must recover both.
    lookup = _parse_vep_tsv_annotation_upload(
        _vep_upload(
            "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tIMPACT\tSYMBOL\tCANONICAL\n"
            "chr1_10085_C/CT\tchr1:10084-10085\tT\tENSG1\tENST1\tTranscript\tintron_variant\tMODIFIER\tGENE1\tYES\n"
            "chr1_10147_AC/A\tchr1:10147\t-\tENSG2\tENST2\tTranscript\tframeshift_variant\tHIGH\tGENE2\tYES\n"
        )
    )
    try:
        ins_id = build_small_variant_id("1", 10084, "C", "CT")
        insertion = lookup.get(ins_id, "1", 10084, "C", "CT")
        assert insertion is not None and insertion[0]["gene"] == "GENE1"

        del_id = build_small_variant_id("1", 10146, "AC", "A")
        deletion = lookup.get(del_id, "1", 10146, "AC", "A")
        assert deletion is not None and deletion[0]["effect"] == "frameshift_variant"
    finally:
        lookup.close()


def test_parse_vep_tsv_annotation_upload_only_marks_true_mane_flags() -> None:
    lookup = _parse_vep_tsv_annotation_upload(
        _vep_upload(
            "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tIMPACT\tSYMBOL\tMANE_SELECT\n"
            "chr1_101_A/G\tchr1:101\tG\tENSG1\tENST1\tTranscript\tmissense_variant\tMODERATE\tGENE1\tNM_000001.1\n"
            "chr1_101_A/G\tchr1:101\tG\tENSG1\tENST2\tTranscript\tsynonymous_variant\tLOW\tGENE1\t-\n"
        )
    )
    try:
        variant_id = build_small_variant_id("1", 101, "A", "G")
        annotations = lookup.get(variant_id, "1", 101, "A", "G")
        assert annotations is not None and len(annotations) == 2
        assert annotations[0].get("mane_select") is True
        assert "mane_select" not in annotations[1]
    finally:
        lookup.close()


def test_a_vep_tsv_parse_that_fails_leaves_no_temporary_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lookup is an SQLite file in the temporary directory, which on Cloud Run is memory.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    header = "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tIMPACT\tSYMBOL\n"
    row = "chr1_101_A/G\tchr1:101\tG\tENSG1\tENST1\tTranscript\tmissense_variant\tMODERATE\tGENE1\n"
    lookup = _parse_vep_tsv_annotation_upload(_vep_upload(header + row))
    assert len(list(tmp_path.iterdir())) == 1
    lookup.close()
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(HTTPException) as missing_header:
        _parse_vep_tsv_annotation_upload(_vep_upload(row))
    assert missing_header.value.status_code == 400
    assert list(tmp_path.iterdir()) == []

    # A line past the length cap, after a row has been indexed.
    monkeypatch.setattr(variant_upload_service, "MAX_UPLOAD_LINE_BYTES", 200)
    with pytest.raises(HTTPException) as too_long:
        _parse_vep_tsv_annotation_upload(_vep_upload(header + row + "x" * 300 + "\n"))
    assert too_long.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# A multi-sample VCF: each sample's call and the site's QUAL (REQ-DATA-001)
# --------------------------------------------------------------------------- #

_TRIO = ("PROBAND", "MOTHER", "FATHER")

_TRIO_VCF = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth of a reference block">\n'
    '##FORMAT=<ID=MED_DP,Number=1,Type=Integer,Description="Median depth of a reference block">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Alternate allele fractions">\n'
    '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant allele fractions">\n'
    '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\tMOTHER\tFATHER\n"
    # Every per-call field, in each sample's own column.
    "chr1\t1000\t.\tA\tG\t47.3\tPASS\t.\tGT:GQ:DP:AD:AF\t0/1:35:30:16,14:0.467\t0/0:40:28:28,0:0\t1/1:20:12:0,12:1\n"
    # Another FORMAT order, two alternate alleles, and a call with every field missing.
    "chr1\t2000\t.\tC\tT,G\t12\tPASS\t.\tAD:DP:GT:GQ:AF\t2,10,9:21:1/2:15:0.476,0.429\t11,9,0:20:0/1:30:0.45,0\t.:.:./.:.:.\n"
    # DeepVariant's VAF; the depth from DP, else a reference block's MED_DP, else its
    # MIN_DP; no QUAL.
    "chr2\t3000\t.\tG\tA\t.\tPASS\t.\tGT:GQ:DP:MIN_DP:MED_DP:AD:VAF\t"
    "0/1:28:25:20:23:12,13:0.52\t0/0:50:.:17:19:.:.\t0/0:45:.:22:.:.:.\n"
    # A call that stops before its FORMAT does.
    "chr2\t4000\t.\tT\tC\t30\tPASS\t.\tGT:GQ:DP:AD:AF\t0/1:30\t0/0:42:31:31,0:0\t0/1:33:29:15,14:0.483\n"
    # Phased calls in one phase set.
    "chr3\t5000\t.\tA\tT\t60\tPASS\t.\tGT:PS:DP\t0|1:4800:22\t1|0:4800:24\t0|0:.:20\n"
    # A FORMAT without GT.
    "chr3\t6000\t.\tG\tC\t8.5\tLowQual\t.\tDP:AD\t25:20,5\t30:30,0\t27:27,0\n"
    # A row that lacks a sample's column.
    "chr4\t7000\t.\tC\tA\t50\tPASS\t.\tGT:DP\t0/1:30\t0/0:28\n"
)


async def _upload_trio_vcf(monkeypatch: pytest.MonkeyPatch, text: str) -> tuple[dict, list]:
    """Upload ``text`` as the trio's family VCF; returns the result and the records stored."""
    stored: list = []

    async def no_rows(*_args, **_kwargs):
        return 0

    async def store(_assembly, _family, _projects, records, **_kwargs):
        stored.extend(records)

    async def no_op(*_args, **_kwargs):
        return None

    for name, fn in {
        "count_family_small_variants": no_rows,
        "insert_small_variant_records": store,
        "refresh_family_small_variant_summaries": no_op,
    }.items():
        monkeypatch.setattr(variant_upload_service, name, fn)

    class FakeSession:
        async def commit(self) -> None:
            return None

    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={f"{name.lower()}-uuid": name for name in _TRIO},
        sample_name_to_uuid={name: f"{name.lower()}-uuid" for name in _TRIO},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    sample_contexts = {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM001",
            sex="und",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in _TRIO
    }
    result = await variant_upload_service.upload_family_small_variant_file(
        FakeSession(),  # type: ignore[arg-type]
        context=context,
        sample_contexts=sample_contexts,
        file=UploadFile(file=BytesIO(text.encode()), filename="family.vcf"),
        overwrite=False,
        format_hint="clair3",
    )
    return result, stored


def _calls_by_sample(record) -> dict[str, tuple]:
    return {call.sample: (call.gt, call.gq, call.dp, call.ad, call.af, call.ps) for call in record.calls}


@pytest.mark.asyncio
async def test_a_multi_sample_vcf_gives_each_sample_its_own_call_and_the_site_its_qual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, records = await _upload_trio_vcf(monkeypatch, _TRIO_VCF)

    # The row without the father's column is left out and counted, not stored with its
    # calls paired to the wrong samples.
    assert (result["inserted"], result["skipped_malformed"]) == (6, 1)
    assert [(record.chr, record.start, record.ref, record.alt) for record in records] == [
        ("1", 1000, "A", "G"),
        ("1", 2000, "C", "T,G"),
        ("2", 3000, "G", "A"),
        ("2", 4000, "T", "C"),
        ("3", 5000, "A", "T"),
        ("3", 6000, "G", "C"),
    ]
    # One call per sample column, in the column order of the #CHROM line.
    assert all([call.sample for call in record.calls] == list(_TRIO) for record in records)
    # The site's QUAL, None where the column holds ".".
    assert [record.qual for record in records] == [47.3, 12.0, None, 30.0, 60.0, 8.5]

    # (GT, GQ, DP, AD, AF, PS), each read by its FORMAT key from the sample's own column.
    snv, multiallelic, deepvariant, short_call, phased, no_gt = (_calls_by_sample(record) for record in records)
    assert snv == {
        "PROBAND": ("0/1", 35.0, 30, [16, 14], [0.467], None),
        "MOTHER": ("0/0", 40.0, 28, [28, 0], [0.0], None),
        "FATHER": ("1/1", 20.0, 12, [0, 12], [1.0], None),
    }
    # AD keeps a depth per allele and AF a fraction per alternate allele; a missing
    # field is missing, never zero.
    assert multiallelic == {
        "PROBAND": ("1/2", 15.0, 21, [2, 10, 9], [0.476, 0.429], None),
        "MOTHER": ("0/1", 30.0, 20, [11, 9, 0], [0.45, 0.0], None),
        "FATHER": ("./.", None, None, [], [], None),
    }
    # The depth is DP, else a reference block's MED_DP, else its MIN_DP; the alt fraction
    # is AF, else DeepVariant's VAF.
    assert deepvariant == {
        "PROBAND": ("0/1", 28.0, 25, [12, 13], [0.52], None),
        "MOTHER": ("0/0", 50.0, 19, [], [], None),
        "FATHER": ("0/0", 45.0, 22, [], [], None),
    }
    # A call with fewer values than its FORMAT has keys lacks the rest; nothing shifts.
    assert short_call["PROBAND"] == ("0/1", 30.0, None, [], [], None)
    assert short_call["FATHER"] == ("0/1", 33.0, 29, [15, 14], [0.483], None)
    assert phased == {
        "PROBAND": ("0|1", None, 22, [], [], 4800),
        "MOTHER": ("1|0", None, 24, [], [], 4800),
        "FATHER": ("0|0", None, 20, [], [], None),
    }
    # Without a GT the call is a no-call, with its depths.
    assert no_gt == {
        "PROBAND": ("./.", None, 25, [20, 5], [], None),
        "MOTHER": ("./.", None, 30, [30, 0], [], None),
        "FATHER": ("./.", None, 27, [27, 0], [], None),
    }


@pytest.mark.asyncio
async def test_the_stored_row_keeps_each_call_under_its_own_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services.clickhouse_variant_storage import small_variant_record_entries

    _result, records = await _upload_trio_vcf(monkeypatch, _TRIO_VCF)

    snv, multiallelic, deepvariant = small_variant_record_entries(
        "GRCh38", "family-uuid", ["project-uuid"], records[:3]
    )
    assert snv["qual"] == 47.3
    assert snv["calls.sampleId"] == list(_TRIO)
    assert snv["calls.gt"] == ["0/1", "0/0", "1/1"]
    assert snv["calls.gq"] == [35, 40, 20]
    assert snv["calls.dp"] == [30, 28, 12]
    assert snv["calls.ad"] == [[16, 14], [28, 0], [0, 12]]
    assert snv["calls.af"] == [[0.467], [0.0], [1.0]]
    assert multiallelic["calls.ad"] == [[2, 10, 9], [11, 9, 0], []]
    assert multiallelic["calls.dp"] == [21, 20, None]
    assert deepvariant["qual"] is None
    assert deepvariant["calls.dp"] == [25, 19, 22]


# --------------------------------------------------------------------------- #
# Off the event loop (OPS-9)
# --------------------------------------------------------------------------- #
#
# An import parsed every VCF line, and built every batch's rows, on the event loop that
# serves every other request, which waited for each batch. The upload reads and parses the
# file in a worker thread now, a batch at a time, and builds each batch's rows in one: the
# event loop only awaits the inserts and the progress reports.

_TRIO_HEADER = _TRIO_VCF[: _TRIO_VCF.index("#CHROM")] + (
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\tMOTHER\tFATHER\n"
)


def _trio_records(count: int, *, start: int = 0) -> str:
    return "".join(
        f"chr{index % 22 + 1}\t{1_000 + index}\t.\tA\tG\t{30 + index % 7}\tPASS\tRS={index}\t"
        f"GT:GQ:DP:AD:AF\t0/1:35:{20 + index % 9}:16,14:0.467\t0/0:40:28:28,0:0\t1/1:20:12:0,12:1\n"
        for index in range(start, start + count)
    )


async def _upload_trio_through_the_insert(
    monkeypatch: pytest.MonkeyPatch, text: str, *, progress=None, on_write=None
) -> tuple[dict, list[tuple[str, list]]]:
    """Upload ``text`` as the trio's family VCF through the real insert, whose ClickHouse
    write alone is faked: the result, and every write as (table, rows), each also handed to
    ``on_write`` as it is made."""
    writes: list[tuple[str, list]] = []

    async def write(query, params=None, data=None):
        writes.append((query.split("(")[0].split()[-1], list(data or [])))
        if on_write is not None:
            on_write(*writes[-1])

    async def no_rows(*_args, **_kwargs):
        return 0

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(variant_upload_service, "count_family_small_variants", no_rows)
    monkeypatch.setattr(variant_upload_service, "refresh_family_small_variant_summaries", no_op)
    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", no_op)
    monkeypatch.setattr(clickhouse_variant_storage, "_execute", write)

    class FakeSession:
        async def commit(self) -> None:
            return None

    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={f"{name.lower()}-uuid": name for name in _TRIO},
        sample_name_to_uuid={name: f"{name.lower()}-uuid" for name in _TRIO},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    sample_contexts = {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM001",
            sex="und",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in _TRIO
    }
    result = await variant_upload_service.upload_family_small_variant_file(
        FakeSession(),  # type: ignore[arg-type]
        context=context,
        sample_contexts=sample_contexts,
        file=UploadFile(file=BytesIO(text.encode()), filename="family.vcf"),
        overwrite=False,
        format_hint="clair3",
        progress=progress,
    )
    return result, writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module, stage",
    [
        (variant_upload_service, "_parse_format"),  # each call's FORMAT fields
        (clickhouse_variant_storage, "_small_variant_entry_rows"),  # each batch's rows
    ],
    ids=["parse", "rows"],
)
async def test_the_upload_parses_its_records_and_builds_their_rows_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, module, stage: str
) -> None:
    # The stage's first call waits for another coroutine to run on the loop meanwhile: it
    # can only when the stage runs in a worker thread.
    probe = LoopProbe()
    monkeypatch.setattr(module, stage, probe.wrap(getattr(module, stage)))

    result, writes = await probe.run(
        _upload_trio_through_the_insert(monkeypatch, _TRIO_HEADER + _trio_records(5))
    )

    assert not probe.held, f"{stage} held the event loop"
    assert result["inserted"] == 5
    assert sum(len(rows) for table, rows in writes if table.endswith("/SNV_INDEL/entries`")) == 5


def _stored(writes: list[tuple[str, list]]) -> list[tuple[str, list]]:
    # The family's data version is a random number on every write, by design.
    return [(table, rows) for table, rows in writes if not table.endswith("family_data_version`")]


@pytest.mark.asyncio
async def test_the_upload_stores_and_reports_what_it_did_when_it_read_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Read in a worker thread, the file gives the rows, batches, progress reports and
    # counts it gave read on the event loop: a malformed and a left-out row included.
    monkeypatch.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", 4)
    monkeypatch.setattr(variant_upload_service, "SMALL_VARIANT_PROGRESS_INTERVAL", 6)
    text = (
        _TRIO_HEADER
        + _trio_records(11)
        + "chr3\tnot-a-position\t.\tA\tG\t30\tPASS\t.\tGT\t0/1\t0/0\t1/1\n"
        + "chr4\t500\t.\tC\tT\t30\tPASS\t.\tGT\t0/1\t0/0\n"
        + _trio_records(1, start=11)
    )

    async def upload() -> tuple[dict, list, list]:
        reports: list[dict] = []

        async def progress(stats: dict) -> None:
            reports.append(dict(stats))

        result, writes = await _upload_trio_through_the_insert(monkeypatch, text, progress=progress)
        return result, _stored(writes), reports

    threaded = await upload()

    async def on_the_loop(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", on_the_loop)
    inline = await upload()

    assert threaded == inline
    result, writes, reports = threaded
    assert (result["inserted"], result["skipped_malformed"]) == (12, 2)
    assert [len(rows) for table, rows in writes if table.endswith("/SNV_INDEL/entries`")] == [4, 4, 4]
    assert [report["processed"] for report in reports if "bytes_read" in report] == [6, 12]


@pytest.mark.asyncio
async def test_the_upload_reads_no_record_beyond_the_batch_it_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    # The reader runs ahead of nothing: as each batch is inserted, the records read are the
    # earlier batches' and its own, so an upload holds one batch whatever the file's size.
    monkeypatch.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", 3)
    parsed: list[str] = []
    parse_format = variant_upload_service._parse_format

    def counted(fmt: str, sample_field: str):
        parsed.append(sample_field)
        return parse_format(fmt, sample_field)

    monkeypatch.setattr(variant_upload_service, "_parse_format", counted)
    inserts: list[tuple[int, int]] = []

    def on_write(table: str, rows: list) -> None:
        if table.endswith("/SNV_INDEL/entries`"):
            inserts.append((len(rows), len(parsed) // len(_TRIO)))

    result, _writes = await _upload_trio_through_the_insert(
        monkeypatch, _TRIO_HEADER + _trio_records(10), on_write=on_write
    )

    assert result["inserted"] == 10
    assert inserts == [(3, 3), (3, 6), (3, 9), (1, 10)]


@pytest.mark.asyncio
async def test_a_cancelled_read_waits_for_its_worker_thread() -> None:
    # A thread cannot be stopped: a cancelled upload's clean-up, and its caller closing the
    # file, wait until the step the thread reads has ended.
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()

    def step() -> str:
        entered.set()
        release.wait(5)
        ended.set()
        return "read"

    task = asyncio.create_task(variant_upload_service._in_worker_thread(step))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ended.is_set()
