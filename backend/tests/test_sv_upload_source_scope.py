"""The per-sample SV upload reads and rewrites only its own source's rows (unit, fakes).

The upload reads the family's stored rows with ``fetch_family_structural_variant_rows``
and writes them back with ``rewrite_family_structural_variants``. Both are faked here with
the real scope (``_sv_fakes.SvStore``): the read returns exactly the requested source's rows
(every source when none is given), and the rewrite records the rows and the source whose rows
it deletes. The "already exists" check and the merge must see the uploaded source's rows only,
so that the source-scoped rewrite is handed exactly the rows it deletes.
``test_e2e_sv_upload_source_scope`` shows the same against real ClickHouse, including the part
merge that would drop the sample's calls were the two scopes to differ. The admin per-sample SV
delete reads every source and rewrites family-wide:
``test_sv_rewrite_keeps_every_call.py::test_admin_delete_writes_back_every_other_call_and_field``
pins both halves.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services import clickhouse_variant_storage, variant_upload_service
from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord
from backend.tests._sv_fakes import (
    SvStore,
    UploadSession,
    install_sv_upload_fakes,
    upload_family_context,
    upload_sample_context,
)

_SAMPLES = {"PROBAND": "uuid-proband", "MOTHER": "uuid-mother", "FATHER": "uuid-father"}
_DEL_7010 = "1-7010-11990-DEL---"
_DEL_30000 = "1-30000-31000-DEL---"
_DUP_50000 = "1-50000-50500-DUP---"
# The golden trio's NeedlR deletion (SVDEL1), by the id its coordinates give it (CLIN-43).
_NEEDLR_DEL = "1-7000-12000-DEL---"

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


def _written(store: SvStore) -> dict[str, tuple[str | None, dict[str, str]]]:
    """What the one rewrite wrote: each SV's source and its calls (sample: genotype)."""
    return {
        row.record.variant_id: (row.record.source, {call.sample: call.gt for call in row.record.calls})
        for row in store.written()
    }


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    def install(records: list[StructuralVariantRecord]) -> SvStore:
        # The file's sample columns are checked against the family (test_sv_upload_sample_column).
        return install_sv_upload_fakes(monkeypatch, SvStore.of_records(records), known_samples=_SAMPLES)

    return install


async def _upload(sample: str, file: UploadFile, *, overwrite: bool, format_hint: str = "sniffles"):
    return await variant_upload_service.upload_structural_variant_file(
        UploadSession(),  # type: ignore[arg-type]
        family_context=upload_family_context(_SAMPLES),
        sample_context=upload_sample_context(_SAMPLES, sample),
        file=file,
        overwrite=overwrite,
        format_hint=format_hint,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_first_upload_is_judged_and_merged_against_its_own_source_only(store) -> None:
    fake = store(
        [
            _record(_NEEDLR_DEL, "needlr", {"PROBAND": "0/1", "MOTHER": "0/0", "FATHER": "0/1"}),
            _record(_DEL_30000, "sniffles", {"MOTHER": "0/1"}),
        ]
    )

    # No 409: PROBAND's NeedlR call does not count as a Sniffles one.
    result = await _upload("PROBAND", _sniffles(_DEL_30000_HET), overwrite=False)

    counts = {key: result[key] for key in ("processed", "created", "merged", "source_format")}
    assert counts == {"processed": 1, "created": 0, "merged": 1, "source_format": "sniffles"}
    assert fake.reads == ["sniffles"]
    assert fake.rewrites[0]["source"] == "sniffles"
    assert _written(fake) == {_DEL_30000: ("sniffles", {"MOTHER": "0/1", "PROBAND": "0/1"})}


@pytest.mark.asyncio
async def test_overwrite_rewrites_only_the_uploaded_sources_records(store) -> None:
    fake = store(
        [
            _record(_NEEDLR_DEL, "needlr", {"PROBAND": "0/1", "MOTHER": "0/0", "FATHER": "0/1"}),
            _record(_DEL_30000, "sniffles", {"PROBAND": "0/1", "MOTHER": "0/1"}),
            _record(_DEL_7010, "sniffles", {"PROBAND": "0/1"}),
        ]
    )

    await _upload("PROBAND", _sniffles(_DEL_30000_HOM, _DUP_50000_HET), overwrite=True)

    # The rewrite deletes the Sniffles rows only, so it must be handed Sniffles rows only.
    # When the upload read every source, the NeedlR deletion (without PROBAND's call) was in this
    # set and was stored a second time beside the original.
    assert fake.rewrites[0]["source"] == "sniffles"
    assert _written(fake) == {
        _DEL_30000: ("sniffles", {"MOTHER": "0/1", "PROBAND": "1/1"}),
        _DUP_50000: ("sniffles", {"PROBAND": "0/1"}),
    }


@pytest.mark.asyncio
async def test_repeat_upload_without_overwrite_conflicts_and_writes_nothing(store) -> None:
    fake = store([_record(_DEL_30000, "sniffles", {"PROBAND": "0/1"})])

    with pytest.raises(HTTPException) as exc:
        await _upload("PROBAND", _sniffles(_DEL_30000_HET), overwrite=False)

    assert exc.value.status_code == 409
    assert fake.rewrites == []


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
    assert fake.reads == ["manual_upload"]
    assert fake.rewrites[0]["source"] == "manual_upload"
    assert _written(fake) == {
        "1-100-200-DEL---": ("manual_upload", {"MOTHER": "0/1", "PROBAND": "0/1"})
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("format_hint", ["needlr", "hificnv", "Sniffles"])
async def test_unknown_source_format_is_refused_before_any_rows_are_read(
    store, format_hint: str
) -> None:
    # Only the upload's own labels may scope its read and delete: an unchecked value would read
    # the existing rows under it (a NeedlR 409 here) and fail in the parser afterwards with a 500.
    fake = store([_record(_NEEDLR_DEL, "needlr", {"PROBAND": "0/1"})])

    with pytest.raises(HTTPException) as exc:
        await _upload("PROBAND", _sniffles(_DEL_30000_HET), overwrite=False, format_hint=format_hint)

    assert exc.value.status_code == 400
    assert fake.reads == []
    assert fake.rewrites == []


@pytest.mark.asyncio
async def test_storage_read_scopes_rows_to_the_exact_source_in_sql(monkeypatch) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(query: str, params: dict[str, Any] | None = None, data=None):
        captured.append((query, dict(params or {})))
        return []

    async def tables_ready(*_args, **_kwargs):
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", tables_ready)

    await clickhouse_variant_storage.fetch_family_structural_variant_rows(
        "GRCh38", "family-uuid", source="sniffles"
    )
    await clickhouse_variant_storage.fetch_family_structural_variant_rows("GRCh38", "family-uuid")

    scoped_query, scoped_params = captured[0]
    # Exact equality, bound as a parameter: the predicate the source-scoped delete uses,
    # for the rows and for the details read with them.
    assert scoped_query.count("AND sign = 1 AND source = %(source)s") == 2
    assert scoped_params == {"family_guid": "family-uuid", "source": "sniffles"}
    assert "'sniffles'" not in scoped_query
    # Without a source the read covers every source, like the family-wide delete.
    unscoped_query, unscoped_params = captured[1]
    assert "source = %(source)s" not in unscoped_query
    assert unscoped_params == {"family_guid": "family-uuid"}
