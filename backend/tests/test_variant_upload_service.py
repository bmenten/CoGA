from io import BytesIO
from types import SimpleNamespace

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services.clickhouse_variant_ids import build_small_variant_id
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services import haplotype_block_builder, variant_upload_service
from backend.app.services.annotation_table_parser import _coerce_int, _vep_location_allele_key
from backend.app.services.haplotype_block_builder import (
    _haplotype_state_end,
    _haplotype_state_matches_block,
    _new_haplotype_state,
    _phased_haplotype_alleles,
)
from backend.app.services.variant_upload_service import (
    _detect_small_variant_format,
    _detect_small_variant_format_from_upload,
    _iter_upload_text_lines,
    _parse_float_list,
    _parse_int_list,
    _parse_qual,
    _parse_vep_tsv_annotation_upload,
)


@pytest.fixture(autouse=True)
def _no_family_write_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The family's write lock is Postgres's; test_family_variant_writes_serialized has it."""

    async def no_lock(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", no_lock)


def test_coerce_int_tolerates_floats_and_junk() -> None:
    assert _coerce_int("42") == 42
    assert _coerce_int("42.0") == 42
    assert _coerce_int(".") is None
    assert _coerce_int("") is None
    assert _coerce_int(None) is None
    assert _coerce_int("not-a-number") is None


def test_parse_float_list_skips_malformed_entries_without_raising() -> None:
    # A single bad AF token must not abort the whole ingest with a 500.
    assert _parse_float_list("0.1,junk,0.3") == [0.1, 0.3]
    assert _parse_float_list("inf,0.5") == [0.5]  # non-finite dropped
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

    assert _detect_small_variant_format(vcf_text, "auto") == "glimpse2"

    upload = UploadFile(file=BytesIO(vcf_text.encode()), filename="co619_phased_final.vcf")
    assert _detect_small_variant_format_from_upload(upload, "auto") == "glimpse2"


def test_gt_only_clair3_vcf_is_not_detected_as_glimpse2() -> None:
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##source=clair3\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "1\t100\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
    )

    assert _detect_small_variant_format(vcf_text, "auto") == "clair3"


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
