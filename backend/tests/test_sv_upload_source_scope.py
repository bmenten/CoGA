"""The per-sample SV upload reads and rewrites only its own source's rows (unit, fakes).

``_fetch_structural_variant_rows`` builds its WHERE from ``_structural_variant_where_clauses``,
which has no clause for ``filters.source``: that is a display filter (a case-insensitive
substring match) the page functions apply afterwards. The fake fetch below reproduces the
real query in that respect: it ignores ``filters.source`` and narrows the rows only for an
``exact_source`` scope. The upload used to pass the display filter, so its "already
exists" check and its overwrite merge saw every source's SVs while its replace deleted
only the uploaded source's rows. ``test_e2e_sv_upload_source_scope`` shows the same
against real ClickHouse, including the part merge that then dropped the sample's calls.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services import admin_service, clickhouse_family_variants, variant_upload_service
from backend.app.services.clickhouse_variant_records import (
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

_SAMPLES = {"PROBAND": "uuid-proband", "MOTHER": "uuid-mother", "FATHER": "uuid-father"}
_DEL_7010 = "1-7010-11990-DEL---"
_DEL_30000 = "1-30000-31000-DEL---"
_DUP_50000 = "1-50000-50500-DUP---"

_SNIFFLES_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)


def _sniffles(*records: str) -> UploadFile:
    body = _SNIFFLES_HEADER + "".join(f"{record}\n" for record in records)
    return UploadFile(file=BytesIO(body.encode()), filename="calls.sniffles.vcf")


_DEL_30000_HET = "1\t30000\tS.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t0/1"
_DEL_30000_HOM = "1\t30000\tS.1\tN\t<DEL>\t42\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=11\tGT\t1/1"
_DUP_50000_HET = "1\t50000\tS.2\tN\t<DUP>\t35\tPASS\tSVTYPE=DUP;SVLEN=500;END=50500;SUPPORT=7\tGT\t0/1"


def _record(variant_id: str, source: str, calls: dict[str, str]) -> StructuralVariantRecord:
    chrom, start, end, sv_type = (variant_id.split("-") + ["", "", "", ""])[:4]
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom if start.isdigit() else "1",
        start=int(start) if start.isdigit() else 7000,
        end=int(end) if end.isdigit() else 12000,
        sv_type=sv_type or "DEL",
        source=source,
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=None,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[
            StructuralVariantCall(sample=sample, gt=gt, qual=None, read_support=None, filter=None)
            for sample, gt in sorted(calls.items())
        ],
    )


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM",
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={uuid: name for name, uuid in _SAMPLES.items()},
        sample_name_to_uuid=dict(_SAMPLES),
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _sample_context(sample: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=_SAMPLES[sample],
        sample_id=sample,
        family_uuid="family-uuid",
        family_id="FAM",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


class _Result:
    def scalar_one_or_none(self) -> None:
        return None


class _Session:
    async def execute(self, *_args, **_kwargs) -> _Result:
        return _Result()

    async def commit(self) -> None:
        return None


class _Store:
    """The family's stored SV records, served the way the real fetch serves them."""

    def __init__(self, records: list[StructuralVariantRecord]) -> None:
        self.records = records
        self.fetches: list[dict[str, Any]] = []
        self.replaces: list[dict[str, Any]] = []

    async def fetch(self, _context, filters, *, exact_source=None, **_kwargs):
        self.fetches.append({"display_source": filters.source, "exact_source": exact_source})
        # Like the real query: filters.source never reaches the WHERE; exact_source does.
        return [
            record
            for record in self.records
            if exact_source is None or record.source == exact_source
        ]

    async def replace(self, _assembly, _family, _projects, records, *, source=None) -> None:
        self.replaces.append({"source": source, "records": list(records)})

    def written(self) -> dict[str, tuple[str | None, dict[str, str]]]:
        assert len(self.replaces) == 1, self.replaces
        return {
            record.variant_id: (record.source, {call.sample: call.gt for call in record.calls})
            for record in self.replaces[0]["records"]
        }


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    def install(records: list[StructuralVariantRecord]) -> _Store:
        fake = _Store(records)
        monkeypatch.setattr(variant_upload_service, "_fetch_structural_variant_rows", fake.fetch)
        monkeypatch.setattr(variant_upload_service, "replace_family_structural_variants", fake.replace)

        async def no_genes(*_args, **_kwargs):
            return {}

        monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", no_genes)
        return fake

    return install


async def _upload(sample: str, file: UploadFile, *, overwrite: bool, format_hint: str = "sniffles"):
    return await variant_upload_service.upload_structural_variant_file(
        _Session(),  # type: ignore[arg-type]
        family_context=_family_context(),
        sample_context=_sample_context(sample),
        file=file,
        overwrite=overwrite,
        format_hint=format_hint,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_first_upload_is_judged_and_merged_against_its_own_source_only(store) -> None:
    fake = store(
        [
            _record("SVDEL1", "needlr", {"PROBAND": "0/1", "MOTHER": "0/0", "FATHER": "0/1"}),
            _record(_DEL_30000, "sniffles", {"MOTHER": "0/1"}),
        ]
    )

    # Before the fix: 409, because PROBAND's NeedlR call counted as a Sniffles one.
    result = await _upload("PROBAND", _sniffles(_DEL_30000_HET), overwrite=False)

    assert result == {"processed": 1, "created": 0, "merged": 1, "source_format": "sniffles"}
    assert fake.fetches == [{"display_source": None, "exact_source": "sniffles"}]
    assert fake.replaces[0]["source"] == "sniffles"
    assert fake.written() == {_DEL_30000: ("sniffles", {"MOTHER": "0/1", "PROBAND": "0/1"})}


@pytest.mark.asyncio
async def test_overwrite_rewrites_only_the_uploaded_sources_records(store) -> None:
    fake = store(
        [
            _record("SVDEL1", "needlr", {"PROBAND": "0/1", "MOTHER": "0/0", "FATHER": "0/1"}),
            _record(_DEL_30000, "sniffles", {"PROBAND": "0/1", "MOTHER": "0/1"}),
            _record(_DEL_7010, "sniffles", {"PROBAND": "0/1"}),
        ]
    )

    await _upload("PROBAND", _sniffles(_DEL_30000_HOM, _DUP_50000_HET), overwrite=True)

    # The replace deletes the Sniffles rows only, so it must be handed Sniffles records
    # only. Before the fix SVDEL1 (NeedlR, without PROBAND's call) was in this set and was
    # stored a second time beside the original.
    assert fake.replaces[0]["source"] == "sniffles"
    assert fake.written() == {
        _DEL_30000: ("sniffles", {"MOTHER": "0/1", "PROBAND": "1/1"}),
        _DUP_50000: ("sniffles", {"PROBAND": "0/1"}),
    }


@pytest.mark.asyncio
async def test_repeat_upload_without_overwrite_conflicts_and_writes_nothing(store) -> None:
    fake = store([_record(_DEL_30000, "sniffles", {"PROBAND": "0/1"})])

    with pytest.raises(HTTPException) as exc:
        await _upload("PROBAND", _sniffles(_DEL_30000_HET), overwrite=False)

    assert exc.value.status_code == 409
    assert fake.replaces == []


@pytest.mark.asyncio
async def test_manual_uploads_are_scoped_to_the_manual_upload_label(store) -> None:
    fake = store(
        [
            _record(_DEL_30000, "sniffles", {"PROBAND": "0/1"}),
            _record("1-100-200-DEL---", "manual_upload", {"MOTHER": "0/1"}),
        ]
    )
    manual = UploadFile(
        file=BytesIO(b"#id\tchrom\tstart\tend\tref\talt\tsvtype\tgt\nm1\t1\t100\t200\tN\t<DEL>\tDEL\t0/1\n"),
        filename="calls.tsv",
    )

    result = await _upload("PROBAND", manual, overwrite=False, format_hint="auto")

    assert result["source_format"] == "manual"
    assert fake.fetches == [{"display_source": None, "exact_source": "manual_upload"}]
    assert fake.replaces[0]["source"] == "manual_upload"
    assert fake.written() == {
        "1-100-200-DEL---": ("manual_upload", {"MOTHER": "0/1", "PROBAND": "0/1"})
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("format_hint", ["needlr", "hificnv", "Sniffles"])
async def test_unknown_source_format_is_refused_before_any_rows_are_read(
    store, format_hint: str
) -> None:
    # Only the upload's own labels may scope its read and delete. Before the fix the value
    # went through unchecked: the existing rows were read under it (a NeedlR 409 here) and
    # the parser failed afterwards with a 500.
    fake = store([_record("SVDEL1", "needlr", {"PROBAND": "0/1"})])

    with pytest.raises(HTTPException) as exc:
        await _upload("PROBAND", _sniffles(_DEL_30000_HET), overwrite=False, format_hint=format_hint)

    assert exc.value.status_code == 400
    assert fake.fetches == []
    assert fake.replaces == []


@pytest.mark.asyncio
async def test_fetch_scopes_rows_to_the_exact_source_in_sql_before_grouping(monkeypatch) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(query: str, params: dict[str, Any]):
        captured.append((query, dict(params)))
        return []

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", fake_execute)
    filters = StructuralVariantQueryFilters(page=1, page_size=1)

    await clickhouse_family_variants._fetch_structural_variant_rows(
        _family_context(), filters, exact_source="sniffles"
    )
    await clickhouse_family_variants._fetch_structural_variant_rows(_family_context(), filters)

    scoped_query, scoped_params = captured[0]
    # Exact equality, bound as a parameter, in the WHERE (so before the GROUP BY).
    assert "e.source = %(exact_source)s" in scoped_query
    assert scoped_query.index("e.source = %(exact_source)s") < scoped_query.index("GROUP BY")
    assert scoped_params["exact_source"] == "sniffles"
    assert "'sniffles'" not in scoped_query
    # The display reads are unchanged.
    unscoped_query, unscoped_params = captured[1]
    assert "exact_source" not in unscoped_query
    assert "exact_source" not in unscoped_params


@pytest.mark.asyncio
async def test_admin_sample_delete_reads_every_source_and_replaces_family_wide(monkeypatch) -> None:
    # The admin per-sample SV delete does not share the upload's defect: it reads every
    # source and hands the result to a family-wide replace (no source), which deletes every
    # source's rows. Pin both halves so they cannot drift apart.
    fake = _Store(
        [
            _record("SVDEL1", "needlr", {"PROBAND": "0/1", "MOTHER": "0/0", "FATHER": "0/1"}),
            _record(_DEL_30000, "sniffles", {"MOTHER": "0/1"}),
            _record(_DUP_50000, "sniffles", {"PROBAND": "0/1", "MOTHER": "0/1"}),
        ]
    )

    async def sample_row(_session, _sample_id):
        return {
            "sample_uuid": _SAMPLES["MOTHER"],
            "sample_id": "MOTHER",
            "family_uuid": "family-uuid",
            "family_id": "FAM",
        }

    async def sample_rows(_session, family_uuids):
        return {family_uuid: [] for family_uuid in family_uuids}

    async def contexts(_session, **_kwargs):
        return [_family_context()]

    async def count(*_args, **_kwargs):
        return 3

    monkeypatch.setattr(admin_service, "_sample_row_or_404", sample_row)
    monkeypatch.setattr(admin_service, "_sample_rows_by_family", sample_rows)
    monkeypatch.setattr(admin_service, "_family_assembly_contexts", contexts)
    monkeypatch.setattr(admin_service, "_fetch_structural_variant_rows", fake.fetch)
    monkeypatch.setattr(admin_service, "count_family_structural_variants", count)
    monkeypatch.setattr(admin_service, "replace_family_structural_variants", fake.replace)

    await admin_service.delete_sample_data_by_type(
        _Session(),  # type: ignore[arg-type]
        "MOTHER",
        "structural_variants",
        True,
    )

    assert fake.fetches == [{"display_source": None, "exact_source": None}]
    assert fake.replaces[0]["source"] is None
    assert fake.written() == {
        "SVDEL1": ("needlr", {"FATHER": "0/1", "PROBAND": "0/1"}),
        _DUP_50000: ("sniffles", {"PROBAND": "0/1"}),
    }
