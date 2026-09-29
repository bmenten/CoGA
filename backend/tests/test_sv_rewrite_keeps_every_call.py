"""A per-sample SV rewrite writes back every other call exactly as stored (unit, fakes).

The per-sample SV upload and the admin per-sample SV delete rewrite a family's stored SV
rows to change one sample's calls. They read the rows with
``fetch_family_structural_variant_rows``, which has the scope of the delete that follows and
no family-view filter, and write them back with ``rewrite_family_structural_variants``, each
row under its own project. So the calls of samples the view does not show (an inactive
member's), the phase sets, the genotype as stored and the breakend's remote end survive.
They used to be read through the family view, which drops all of those. The upload also
records its VCF header's caller in the family's annotation manifest.
``test_e2e_sv_rewrite_keeps_every_call`` shows the same against real ClickHouse and Postgres.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services import (
    admin_service,
    annotation_manifest_service,
    clickhouse_variant_storage,
    variant_upload_service,
)
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext

# FATHER is not in the context's sample map: an inactive member the family view hides.
_ACTIVE = {"PROBAND": "uuid-proband", "MOTHER": "uuid-mother"}
_DEL_30000 = "1-30000-31000-DEL---"
_DEL_80000 = "1-80000-80800-DEL---"
_BND_60000 = "1-60000-60000-BND-5-3000000-3000000"

_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    '##INFO=<ID=GENES,Number=.,Type=String,Description="Genes overlapped (GENCODE 45)">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)


def _sniffles(*records: str) -> UploadFile:
    body = _HEADER + "".join(f"{record}\n" for record in records)
    return UploadFile(file=BytesIO(body.encode()), filename="calls.sniffles.vcf")


def _del_30000(gt: str) -> str:
    return f"1\t30000\tS.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t{gt}"


def _call(sample: str, gt: str, ps: int | None = None) -> StructuralVariantCall:
    return StructuralVariantCall(
        sample=sample, gt=gt, qual=40.0, read_support=9, filter="PASS", phase_set=ps
    )


def _record(
    variant_id: str,
    calls: list[StructuralVariantCall],
    *,
    source: str = "sniffles",
    remote_end: int | None = None,
    annotations: list[dict[str, Any]] | None = None,
) -> StructuralVariantRecord:
    chrom, start, end, sv_type = variant_id.split("-")[:4]
    return StructuralVariantRecord(
        variant_key=abs(hash(variant_id)) % 1000,
        variant_id=variant_id,
        chr=chrom,
        start=int(start),
        end=int(end),
        sv_type=sv_type,
        source=source,
        remote_chr="5" if remote_end else None,
        remote_start=remote_end,
        remote_end=remote_end,
        sv_len=-1000,
        filters=["PASS"],
        gene_symbols=[],
        annotations=annotations if annotations is not None else [{"info": {"SUPPORT": "9"}}],
        calls=calls,
    )


def _row(record: StructuralVariantRecord, project: str = "project-uuid") -> StoredStructuralVariantRow:
    return StoredStructuralVariantRow(project_id=project, record=record)


def _family_context(project_ids: list[str] | None = None) -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM",
        project_ids=project_ids if project_ids is not None else ["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={uuid: name for name, uuid in _ACTIVE.items()},
        sample_name_to_uuid=dict(_ACTIVE),
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _sample_context(sample: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=_ACTIVE[sample],
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


class _Storage:
    """Stored rows served by source, and the rewrites made, as in the real storage."""

    def __init__(self, rows: list[StoredStructuralVariantRow]) -> None:
        self.rows = rows
        self.reads: list[str | None] = []
        self.rewrites: list[dict[str, Any]] = []
        self.provenance: list[dict[str, Any]] = []

    async def fetch(self, _assembly, _family, *, source=None):
        self.reads.append(source)
        return [row for row in self.rows if source is None or row.record.source == source]

    async def rewrite(self, _assembly, _family, rows, *, source=None) -> None:
        self.rewrites.append({"source": source, "rows": list(rows)})

    async def record_provenance(self, _session, **kwargs) -> None:
        self.provenance.append(kwargs)

    def written(self) -> dict[tuple[str, str], StructuralVariantRecord]:
        assert len(self.rewrites) == 1, self.rewrites
        return {(row.project_id, row.record.variant_id): row.record for row in self.rewrites[0]["rows"]}


def _calls(record: StructuralVariantRecord) -> dict[str, tuple[str, int | None]]:
    return {call.sample: (call.gt, call.phase_set) for call in record.calls}


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch):
    def install(rows: list[StoredStructuralVariantRow]) -> _Storage:
        fake = _Storage(rows)
        monkeypatch.setattr(variant_upload_service, "fetch_family_structural_variant_rows", fake.fetch)
        monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", fake.rewrite)
        monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", fake.record_provenance)

        async def no_genes(*_args, **_kwargs):
            return {}

        monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", no_genes)
        return fake

    return install


async def _upload(
    sample: str,
    file: UploadFile,
    *,
    overwrite: bool = False,
    format_hint: str = "sniffles",
    project_ids: list[str] | None = None,
):
    return await variant_upload_service.upload_structural_variant_file(
        _Session(),  # type: ignore[arg-type]
        family_context=_family_context(project_ids),
        sample_context=_sample_context(sample),
        file=file,
        overwrite=overwrite,
        format_hint=format_hint,  # type: ignore[arg-type]
    )


# --- the storage read ---------------------------------------------------------------------


def _stored_row_tuple(**overrides: Any) -> tuple[Any, ...]:
    values: dict[str, Any] = {
        "project_guid": "project-uuid",
        "key": 11,
        "variantId": _BND_60000,
        "source": "sniffles",
        "chrom": "1",
        "start": 60000,
        "end": 60000,
        "svType": "BND",
        "gene_symbols": ["GENE1"],
        "sample_ids": ["PROBAND", "uuid-father"],
        "gts": ["0|1", "HET"],
        "quals": [30.0, None],
        "read_supports": [6, None],
        "call_filters": ["PASS", None],
        "phase_sets": [7001, None],
        "copy_numbers": [None, 3],
        "remoteChrom": "5",
        "remoteStart": 3000000,
        "remoteEnd": 3000000,
        "svLen": None,
        "filters": ["PASS"],
        "annotationsJson": json.dumps({"annotations": [{"info": {"SVTYPE": "BND"}}]}),
    }
    values.update(overrides)
    return tuple(values.values())


@pytest.fixture
def storage_sql(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {"queries": [], "rows": []}

    async def fake_execute(query: str, params: dict[str, Any] | None = None, data=None):
        captured["queries"].append((query, dict(params or {})))
        return captured["rows"]

    async def tables_ready(*_args, **_kwargs):
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "_execute", fake_execute)
    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", tables_ready)
    return captured


@pytest.mark.asyncio
async def test_storage_read_has_the_deletes_scope_and_no_family_view_filter(storage_sql) -> None:
    await clickhouse_variant_storage.fetch_family_structural_variant_rows("GRCh38", "family-uuid")

    query, params = storage_sql["queries"][0]
    assert "WHERE family_guid = %(family_guid)s AND sign = 1\n" in query
    assert params == {"family_guid": "family-uuid"}
    # No project or sample-visibility filter: the delete that follows has neither.
    assert "project_guid IN" not in query
    assert "hasAny" not in query
    # Every stored field comes back, the details from their latest version, read for the
    # rows' own keys (the details' sort key).
    for column in ("e.`calls.ps`", "e.`calls.cn`", "d.remoteEnd", "e.project_guid"):
        assert column in query
    assert "FINAL\n            WHERE key IN (" in query


@pytest.mark.asyncio
async def test_storage_read_returns_each_row_exactly_as_stored(storage_sql) -> None:
    storage_sql["rows"] = [_stored_row_tuple()]

    [row] = await clickhouse_variant_storage.fetch_family_structural_variant_rows(
        "GRCh38", "family-uuid"
    )

    assert row.project_id == "project-uuid"
    record = row.record
    assert (record.variant_key, record.variant_id, record.source) == (11, _BND_60000, "sniffles")
    assert (record.remote_chr, record.remote_start, record.remote_end) == ("5", 3000000, 3000000)
    assert record.annotations == [{"info": {"SVTYPE": "BND"}}]
    assert record.gene_symbols == ["GENE1"]
    # Every call, under the id it was stored with, its genotype unnormalised.
    assert record.calls == [
        StructuralVariantCall(
            sample="PROBAND", gt="0|1", qual=30.0, read_support=6, filter="PASS", phase_set=7001
        ),
        StructuralVariantCall(
            sample="uuid-father", gt="HET", qual=None, read_support=None, filter=None, copy_number=3
        ),
    ]


@pytest.mark.asyncio
async def test_storage_read_joins_a_row_stored_twice_in_one_project(storage_sql) -> None:
    # The query orders a project's rows of one SV fullest first. A second live row of the
    # same SV (which a part merge would collapse into one of the two) adds its extra calls.
    storage_sql["rows"] = [
        _stored_row_tuple(sample_ids=["PROBAND", "uuid-father"]),
        _stored_row_tuple(
            sample_ids=["PROBAND", "MOTHER"],
            gts=["1/1", "0/1"],
            phase_sets=[None, None],
            copy_numbers=[None, None],
        ),
        _stored_row_tuple(project_guid="project-old", sample_ids=["PROBAND"], gts=["0|1"]),
    ]

    rows = await clickhouse_variant_storage.fetch_family_structural_variant_rows(
        "GRCh38", "family-uuid"
    )

    assert [(row.project_id, _calls(row.record)) for row in rows] == [
        (
            "project-uuid",
            {"PROBAND": ("0|1", 7001), "uuid-father": ("HET", None), "MOTHER": ("0/1", None)},
        ),
        ("project-old", {"PROBAND": ("0|1", 7001)}),
    ]


@pytest.mark.asyncio
async def test_rewrite_writes_each_row_under_its_own_project(monkeypatch) -> None:
    deletes: list[str | None] = []
    inserts: list[tuple[list[str], list[str]]] = []

    async def fake_delete(_assembly, _family, *, source=None):
        deletes.append(source)

    async def fake_insert(_assembly, _family, project_ids, records):
        inserts.append((list(project_ids), [record.variant_id for record in records]))

    monkeypatch.setattr(clickhouse_variant_storage, "delete_family_structural_variants", fake_delete)
    monkeypatch.setattr(clickhouse_variant_storage, "insert_structural_variant_records", fake_insert)

    await clickhouse_variant_storage.rewrite_family_structural_variants(
        "GRCh38",
        "family-uuid",
        [
            _row(_record(_DEL_30000, [_call("MOTHER", "0/1")]), "project-a"),
            _row(_record(_DEL_80000, [_call("MOTHER", "0/1")]), "project-b"),
            _row(_record(_BND_60000, [_call("MOTHER", "0/1")]), "project-a"),
        ],
        source="sniffles",
    )

    assert deletes == ["sniffles"]
    assert inserts == [
        (["project-a"], [_DEL_30000, _BND_60000]),
        (["project-b"], [_DEL_80000]),
    ]


# --- the per-sample upload ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_writes_back_every_other_call_and_field_as_stored(storage) -> None:
    fake = storage(
        [
            _row(_record(_DEL_30000, [_call("PROBAND", "0|1", 7001), _call("FATHER", "1|0", 9001)])),
            _row(_record(_BND_60000, [_call("PROBAND", "0/1")], remote_end=3000000)),
            _row(_record(_DEL_80000, [_call("FATHER", "0/1")])),
            _row(_record("SVDEL1-0-1-DEL", [_call("PROBAND", "0/1")], source="needlr")),
        ]
    )

    result = await _upload("MOTHER", _sniffles(_del_30000("0/1")))

    assert (result["created"], result["merged"]) == (0, 1)
    assert fake.reads == ["sniffles"]
    assert fake.rewrites[0]["source"] == "sniffles"
    written = fake.written()
    # Before: PROBAND's phase set came back empty, and FATHER (not in the family view) lost
    # his call here and his own deletion altogether.
    assert _calls(written[("project-uuid", _DEL_30000)]) == {
        "FATHER": ("1|0", 9001),
        "MOTHER": ("0/1", None),
        "PROBAND": ("0|1", 7001),
    }
    assert written[("project-uuid", _BND_60000)].remote_end == 3000000
    assert _calls(written[("project-uuid", _DEL_80000)]) == {"FATHER": ("0/1", None)}
    assert set(written) == {
        ("project-uuid", _DEL_30000),
        ("project-uuid", _BND_60000),
        ("project-uuid", _DEL_80000),
    }


@pytest.mark.asyncio
async def test_upload_finds_the_samples_calls_stored_under_its_uuid(storage) -> None:
    rows = [_row(_record(_DEL_30000, [_call("uuid-proband", "0/1"), _call("MOTHER", "0/1")]))]

    fake = storage(rows)
    with pytest.raises(HTTPException) as exc:
        await _upload("PROBAND", _sniffles(_del_30000("1/1")))
    assert exc.value.status_code == 409
    assert fake.rewrites == []

    fake = storage(rows)
    await _upload("PROBAND", _sniffles(_del_30000("1/1")), overwrite=True)
    assert _calls(fake.written()[("project-uuid", _DEL_30000)]) == {
        "MOTHER": ("0/1", None),
        "PROBAND": ("1/1", None),
    }


@pytest.mark.asyncio
async def test_upload_keeps_rows_under_projects_outside_the_familys_current_ones(storage) -> None:
    stored_annotations = [{"info": {"SUPPORT": "12", "CALLER": "first upload"}}]
    fake = storage(
        [
            _row(
                _record(_DEL_30000, [_call("MOTHER", "0/1")], annotations=stored_annotations),
                "project-old",
            ),
            _row(_record(_DEL_80000, [_call("MOTHER", "0/1")]), "project-old"),
        ]
    )

    await _upload("PROBAND", _sniffles(_del_30000("0/1")), project_ids=["project-new"])

    written = fake.written()
    # The stored rows stay in their project: the sample's call joins them there...
    assert _calls(written[("project-old", _DEL_30000)]) == {
        "MOTHER": ("0/1", None),
        "PROBAND": ("0/1", None),
    }
    assert _calls(written[("project-old", _DEL_80000)]) == {"MOTHER": ("0/1", None)}
    # ...and the family's current project gets a row holding the new call only, with the
    # stored record's fields, so both projects' rows of the SV agree on its details.
    new_row = written[("project-new", _DEL_30000)]
    assert _calls(new_row) == {"PROBAND": ("0/1", None)}
    assert new_row.annotations == stored_annotations
    assert set(written) == {
        ("project-old", _DEL_30000),
        ("project-old", _DEL_80000),
        ("project-new", _DEL_30000),
    }


@pytest.mark.asyncio
async def test_overwrite_replaces_a_record_only_the_sample_was_called_in(storage) -> None:
    stored = _record(_DEL_30000, [_call("PROBAND", "0/1")], annotations=[{"info": {"SUPPORT": "3"}}])
    fake = storage([_row(stored)])

    result = await _upload("PROBAND", _sniffles(_del_30000("1/1")), overwrite=True)

    assert (result["created"], result["merged"]) == (1, 0)
    record = fake.written()[("project-uuid", _DEL_30000)]
    # The re-uploaded call brings its own fields; the stored key is kept.
    assert _calls(record) == {"PROBAND": ("1/1", None)}
    assert record.annotations == [{"info": {"SVTYPE": "DEL", "SVLEN": "-1000", "END": "31000", "SUPPORT": "9"}}]
    assert record.variant_key == stored.variant_key


# --- the admin per-sample delete ----------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_delete_writes_back_every_other_call_and_field(monkeypatch) -> None:
    fake = _Storage(
        [
            _row(
                _record(
                    "SVDEL1-0-1-DEL",
                    [_call("FATHER", "0/1"), _call("MOTHER", "0/0"), _call("PROBAND", "0/1")],
                    source="needlr",
                )
            ),
            _row(
                _record(
                    _DEL_30000,
                    [_call("PROBAND", "0|1", 7001), _call("FATHER", "1|0", 9001), _call("uuid-mother", "0/1")],
                )
            ),
            _row(_record(_BND_60000, [_call("PROBAND", "0/1")], remote_end=3000000), "project-old"),
            _row(_record(_DEL_80000, [_call("MOTHER", "0/1")])),
        ]
    )

    async def sample_row(_session, _sample_id):
        return {
            "sample_uuid": "uuid-mother",
            "sample_id": "MOTHER",
            "family_uuid": "family-uuid",
            "family_id": "FAM",
        }

    async def sample_rows(_session, family_uuids):
        return {family_uuid: [] for family_uuid in family_uuids}

    async def contexts(_session, **_kwargs):
        return [_family_context()]

    async def count(*_args, **_kwargs):
        return 4

    monkeypatch.setattr(admin_service, "_sample_row_or_404", sample_row)
    monkeypatch.setattr(admin_service, "_sample_rows_by_family", sample_rows)
    monkeypatch.setattr(admin_service, "_family_assembly_contexts", contexts)
    monkeypatch.setattr(admin_service, "fetch_family_structural_variant_rows", fake.fetch)
    monkeypatch.setattr(admin_service, "count_family_structural_variants", count)
    monkeypatch.setattr(admin_service, "rewrite_family_structural_variants", fake.rewrite)

    await admin_service.delete_sample_data_by_type(
        _Session(),  # type: ignore[arg-type]
        "MOTHER",
        "structural_variants",
        True,
    )

    assert fake.reads == [None]
    assert fake.rewrites[0]["source"] is None
    written = fake.written()
    # MOTHER's calls go under either stored id; FATHER's (not in the view) and every phase
    # set, remote end and project stay.
    assert _calls(written[("project-uuid", "SVDEL1-0-1-DEL")]) == {
        "FATHER": ("0/1", None),
        "PROBAND": ("0/1", None),
    }
    assert _calls(written[("project-uuid", _DEL_30000)]) == {
        "FATHER": ("1|0", 9001),
        "PROBAND": ("0|1", 7001),
    }
    assert written[("project-old", _BND_60000)].remote_end == 3000000
    assert ("project-uuid", _DEL_80000) not in written


# --- provenance ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sv_upload_records_its_vcf_callers_provenance(storage) -> None:
    fake = storage([])

    result = await _upload("PROBAND", _sniffles(_del_30000("0/1")))

    [recorded] = fake.provenance
    assert recorded["family_uuid"] == "family-uuid"
    assert recorded["assembly_id"] == "assembly-uuid"
    assert recorded["modality"] == "sv"
    assert recorded["modules"]["sniffles"] == {"version": "2.2", "detail": "sv caller"}
    assert recorded["modules"]["gencode"]["version"] == "45"
    assert result["annotation_provenance"] == recorded["modules"]


@pytest.mark.asyncio
async def test_manual_tsv_upload_has_no_header_to_record(storage) -> None:
    fake = storage([])
    manual = UploadFile(
        file=BytesIO(b"#id\tchrom\tstart\tend\tref\talt\tsvtype\tgt\nm1\t1\t100\t200\tN\t<DEL>\tDEL\t0/1\n"),
        filename="calls.tsv",
    )

    result = await _upload("PROBAND", manual, format_hint="auto")

    assert result["annotation_provenance"] == {}
    assert [call["modules"] for call in fake.provenance] == [{}]
