"""An uploaded SV VCF is read from the uploaded sample's own column (unit, fakes).

``POST /structural-variants/upload/{sample_id}`` stores one sample's calls from one Sniffles
or Spectre VCF. It used to read the file's first sample column whatever that column was
named, and store it under the sample in the URL: a joint Sniffles2 VCF stored its first
member's calls as this sample's, and another member's file replaced this sample's calls
without a word. The column is now chosen by the rule of the TRGT upload and the package's
per-sample files (``per_sample_vcf_column``), before anything is read or written:

* the column named after the sample (as is, or with a tool suffix such as ``_sv_phased``) is
  read, at any position;
* a lone column that names no known sample (a caller's ``SAMPLE``, a read file's name) is
  the sample's;
* a file whose columns name other samples -- a family member, a sample of another family --
  and none this one, or two columns for it, is refused (400), as is a VCF without exactly
  one ``#CHROM`` line before its records. Nothing is then read from or written to the store,
  and the sample's metadata is not touched.

A manual TSV has no sample column and stays the sample's. The SV storage is faked as in
test_sv_upload_source_scope. Postgres answers the stored-sample lookup
(``known_vcf_sample_ids``) as the ``samples`` table would: the family's members, and any
stored sample a column is named after. ``e2e/test_e2e_sv_upload_sample_column.py`` uploads
through the API against Postgres and ClickHouse.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services import annotation_manifest_service, variant_upload_service
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.structural_variant_ingest import iter_structural_variant_records

_FAMILY = "family-uuid"
_SAMPLES = {"PROBAND": "uuid-proband", "MOTHER": "uuid-mother", "FATHER": "uuid-father"}
# The samples table: each stored sample id and its family. Sample ids are unique in CoGA.
_STORED_SAMPLES = {**{sample: _FAMILY for sample in _SAMPLES}, "OTHER_CHILD": "other-family-uuid"}
_DEL_30000 = "1-30000-31000-DEL---"
_DUP_50000 = "1-50000-50500-DUP---"

# Each member's own call (GT:PS) on the deletion and on the duplication, so a stored call
# shows whose column it was read from.
_CALLS = {
    "MOTHER": ("0|1:1001", "1/1:."),
    "PROBAND": ("1|1:2002", "0/0:."),
    "FATHER": ("0/0:.", "0/1:."),
}
# What the uploaded sample's own column holds, as stored: {SV: {sample: (GT, phase set)}}.
_PROBANDS_OWN = {_DEL_30000: {"PROBAND": ("1|1", 2002)}, _DUP_50000: {"PROBAND": ("0/0", None)}}


def _vcf(columns: list[str], records: list[list[str]], *, source: str = "Sniffles2_2.2") -> str:
    """A VCF whose ``#CHROM`` line names ``columns``; each record lists its sample fields."""
    header = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT" + "".join(f"\t{c}" for c in columns)
    sites = [
        "1\t30000\tSV.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT:PS",
        "1\t50000\tSV.2\tN\t<DUP>\t35\tPASS\tSVTYPE=DUP;SVLEN=500;END=50500;SUPPORT=7\tGT:PS",
    ]
    lines = ["##fileformat=VCFv4.2", f"##source={source}", header]
    lines += ["\t".join([site, *fields]) for site, fields in zip(sites, records)]
    return "\n".join(lines) + "\n"


def _joint_vcf(columns: list[tuple[str, str]], *, source: str = "Sniffles2_2.2") -> str:
    """A VCF whose columns, ``(name, member)`` in file order, each hold that member's calls."""
    return _vcf(
        [name for name, _member in columns],
        [[_CALLS[member][site] for _name, member in columns] for site in (0, 1)],
        source=source,
    )


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid=_FAMILY,
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
        family_uuid=_FAMILY,
        family_id="FAM",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one_or_none(self) -> None:
        return None


class _Session:
    """Postgres as the upload uses it: the stored-sample lookup a file's columns are checked
    against, the sample's metadata read and update, and the commit."""

    def __init__(self) -> None:
        self.lookups: list[dict[str, Any]] = []
        self.statements: list[str] = []
        self.committed = False

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        if "FROM samples WHERE family_id" in sql:  # known_vcf_sample_ids
            self.lookups.append(dict(params))
            names = set(params["names"])
            return _Result(
                [
                    {"sample_id": sample}
                    for sample, family in _STORED_SAMPLES.items()
                    if family == params["family_uuid"] or sample in names
                ]
            )
        self.statements.append(sql)
        return _Result()

    async def commit(self) -> None:
        self.committed = True


class _Store:
    """The family's stored SV rows, served by source; the rewrites made."""

    def __init__(self, records: list[StructuralVariantRecord]) -> None:
        self.records = records
        self.fetches: list[str | None] = []
        self.rewrites: list[list[StoredStructuralVariantRow]] = []

    async def fetch(self, _assembly, _family, *, source=None):
        self.fetches.append(source)
        return [
            StoredStructuralVariantRow(project_id="project-uuid", record=record)
            for record in self.records
            if source is None or record.source == source
        ]

    async def rewrite(self, _assembly, _family, rows, *, source=None) -> None:
        self.rewrites.append(list(rows))

    def written(self) -> dict[str, dict[str, tuple[str, int | None]]]:
        assert len(self.rewrites) == 1, self.rewrites
        return {
            row.record.variant_id: {call.sample: (call.gt, call.phase_set) for call in row.record.calls}
            for row in self.rewrites[0]
        }


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    def install(records: list[StructuralVariantRecord] | None = None) -> _Store:
        fake = _Store(records or [])
        monkeypatch.setattr(variant_upload_service, "fetch_family_structural_variant_rows", fake.fetch)
        monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", fake.rewrite)

        async def nothing(*_args, **_kwargs):
            return None

        async def no_genes(*_args, **_kwargs):
            return {}

        monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", no_genes)
        monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
        # The family's write lock is Postgres's; test_family_variant_writes_serialized has it.
        monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", nothing)
        return fake

    return install


async def _upload(
    session: _Session,
    sample: str,
    body: str,
    *,
    format_hint: str = "sniffles",
    overwrite: bool = False,
    filename: str = "calls.vcf",
) -> dict[str, Any]:
    return await variant_upload_service.upload_structural_variant_file(
        session,  # type: ignore[arg-type]
        family_context=_family_context(),
        sample_context=_sample_context(sample),
        file=UploadFile(file=BytesIO(body.encode()), filename=filename),
        overwrite=overwrite,
        format_hint=format_hint,  # type: ignore[arg-type]
    )


def _stored_proband_call() -> StructuralVariantRecord:
    """The proband's Sniffles call on the deletion, stored by an earlier upload."""
    return StructuralVariantRecord(
        variant_key=7,
        variant_id=_DEL_30000,
        chr="1",
        start=30000,
        end=31000,
        sv_type="DEL",
        source="sniffles",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-1000,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[StructuralVariantCall(sample="PROBAND", gt="0/1", qual=40.0, read_support=9, filter=None)],
    )


# --- The sample's own column ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("format_hint", "source"), [("sniffles", "Sniffles2_2.2"), ("spectre", "Spectre")]
)
@pytest.mark.parametrize(
    "columns",
    [
        [("MOTHER", "MOTHER"), ("PROBAND", "PROBAND"), ("FATHER", "FATHER")],
        [("FATHER_sv_phased", "FATHER"), ("MOTHER_sv_phased", "MOTHER"), ("PROBAND_sv_phased", "PROBAND")],
    ],
    ids=["sample-ids", "tool-suffix"],
)
async def test_a_joint_vcf_stores_the_uploaded_samples_own_column(
    store, format_hint: str, source: str, columns: list[tuple[str, str]]
) -> None:
    fake = store()
    session = _Session()

    result = await _upload(session, "PROBAND", _joint_vcf(columns, source=source), format_hint=format_hint)

    assert (result["processed"], result["created"], result["source_format"]) == (2, 2, format_hint)
    # Before the fix: the first column's genotypes and phase set, stored as PROBAND's.
    assert fake.written() == _PROBANDS_OWN
    # The columns were checked against the sample's family and the samples they name.
    [lookup] = session.lookups
    assert lookup["family_uuid"] == _FAMILY
    assert {name for name, _member in columns} <= set(lookup["names"])
    assert session.committed


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["SAMPLE", "Sample0", "flowcell7_reads"])
async def test_a_lone_column_that_names_no_known_sample_is_the_uploaded_samples(
    store, column: str
) -> None:
    # A caller's placeholder, or a column named after a read file rather than the sample.
    fake = store()

    await _upload(_Session(), "PROBAND", _vcf([column], [[_CALLS["PROBAND"][0]], [_CALLS["PROBAND"][1]]]))

    assert fake.written() == _PROBANDS_OWN


# --- Refused before anything is read or written ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("columns", "format_hint", "detail"),
    [
        # Another member's own file.
        (
            [("MOTHER", "MOTHER")],
            "sniffles",
            "Sniffles VCF has no sample column for PROBAND: 'MOTHER' is MOTHER. "
            "Upload this sample's own file.",
        ),
        (
            [("MOTHER_sv_phased", "MOTHER")],
            "spectre",
            "Spectre VCF has no sample column for PROBAND: 'MOTHER_sv_phased' is MOTHER. "
            "Upload this sample's own file.",
        ),
        # A joint VCF of the other members.
        (
            [("MOTHER", "MOTHER"), ("FATHER", "FATHER")],
            "sniffles",
            "Sniffles VCF has no sample column for PROBAND: 'MOTHER' is MOTHER, 'FATHER' is FATHER. "
            "Upload this sample's own file.",
        ),
        # A sample of another family (sample ids are unique across CoGA).
        (
            [("OTHER_CHILD", "MOTHER")],
            "sniffles",
            "Sniffles VCF has no sample column for PROBAND: 'OTHER_CHILD' is OTHER_CHILD. "
            "Upload this sample's own file.",
        ),
        # Two columns for the sample: which one holds its calls cannot be told.
        (
            [("PROBAND", "PROBAND"), ("PROBAND_sort", "MOTHER")],
            "sniffles",
            "Sniffles VCF has more than one sample column for PROBAND: ['PROBAND', 'PROBAND_sort']",
        ),
        # Several columns, none of them a known sample.
        (
            [("run1", "MOTHER"), ("run2", "PROBAND")],
            "sniffles",
            "Sniffles VCF has no sample column for PROBAND: its columns ['run1', 'run2'] name no "
            "known sample. Upload this sample's own file.",
        ),
    ],
    ids=["member", "member-suffix-spectre", "joint-without-it", "other-family", "two-columns", "no-known-column"],
)
async def test_a_file_without_one_column_for_the_uploaded_sample_is_refused_and_nothing_is_written(
    store, columns: list[tuple[str, str]], format_hint: str, detail: str
) -> None:
    fake = store([_stored_proband_call()])
    session = _Session()
    source = "Spectre" if format_hint == "spectre" else "Sniffles2_2.2"

    with pytest.raises(HTTPException) as exc:
        await _upload(
            session, "PROBAND", _joint_vcf(columns, source=source), format_hint=format_hint, overwrite=True
        )

    # Before the fix: 200, the first column's calls stored as PROBAND's in place of its own.
    assert (exc.value.status_code, exc.value.detail) == (400, detail)
    assert fake.fetches == []
    assert fake.rewrites == []
    assert session.statements == []
    assert not session.committed


@pytest.mark.asyncio
async def test_a_vcf_with_no_sample_column_is_refused_and_nothing_is_written(store) -> None:
    fake = store([_stored_proband_call()])
    session = _Session()
    sites_only = (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t30000\tSV.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000\n"
    )

    with pytest.raises(HTTPException) as exc:
        await _upload(session, "PROBAND", sites_only, overwrite=True)

    # Before the fix: refused too, but only after reading the stored rows, for having no record.
    assert (exc.value.status_code, exc.value.detail) == (400, "Sniffles VCF has no sample column")
    assert (fake.fetches, fake.rewrites, session.statements, session.committed) == ([], [], [], False)


_MOTHERS_DEL = "\t".join(
    ["1", "30000", "SV.1", "N", "<DEL>", "40", "PASS", "SVTYPE=DEL;SVLEN=-1000;END=31000", "GT:PS", "1/1:."]
)


_PROBANDS_VCF = _vcf(["PROBAND"], [[_CALLS["PROBAND"][0]], [_CALLS["PROBAND"][1]]])
_MOTHERS_VCF = _vcf(["MOTHER"], [[_CALLS["MOTHER"][0]], [_CALLS["MOTHER"][1]]])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "detail"),
    [
        # No #CHROM line: whose calls the records are cannot be checked.
        ("##fileformat=VCFv4.2\n" + _MOTHERS_DEL + "\n", "Sniffles VCF has no #CHROM line"),
        # A record the reader reads before the #CHROM line names the columns.
        (_MOTHERS_DEL + "\n" + _PROBANDS_VCF, "Sniffles VCF has a record before its #CHROM line"),
        # The sample's file with another member's appended, header and all.
        (_PROBANDS_VCF + _MOTHERS_VCF, "Sniffles VCF has more than one #CHROM line"),
    ],
    ids=["no-header", "record-before-header", "two-headers"],
)
async def test_a_vcf_without_one_chrom_line_before_its_records_is_refused(
    store, body: str, detail: str
) -> None:
    fake = store([_stored_proband_call()])
    session = _Session()

    with pytest.raises(HTTPException) as exc:
        await _upload(session, "PROBAND", body, overwrite=True)

    # Before the fix: 200, every record's first sample field stored as PROBAND's.
    assert (exc.value.status_code, exc.value.detail) == (400, detail)
    assert (fake.fetches, fake.rewrites, session.statements, session.committed) == ([], [], [], False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix",
    ["﻿", "\n", "exported by a spreadsheet\n"],
    ids=["byte-order-mark", "blank-line", "short-line"],
)
async def test_a_line_no_reader_reads_before_the_chrom_line_is_not_a_record(store, prefix: str) -> None:
    # Lines of fewer than ten fields are skipped by the reader, so they need no header.
    fake = store()

    await _upload(_Session(), "PROBAND", prefix + _PROBANDS_VCF)

    assert fake.written() == _PROBANDS_OWN


# --- A manual TSV has no sample column ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_manual_tsv_stays_the_uploaded_samples_without_a_column_check(store) -> None:
    fake = store()
    session = _Session()
    manual = "#id\tchrom\tstart\tend\tref\talt\tsvtype\tgt\nm1\t1\t100\t200\tN\t<DEL>\tDEL\t0/1\n"

    result = await _upload(session, "PROBAND", manual, format_hint="auto", filename="calls.tsv")

    assert result["source_format"] == "manual"
    assert fake.written() == {"1-100-200-DEL---": {"PROBAND": ("0/1", None)}}
    assert session.lookups == []


# --- The reader ------------------------------------------------------------------------


@pytest.mark.parametrize("record_format", ["sniffles", "spectre"])
def test_the_vcf_readers_read_the_chosen_sample_column(record_format: str) -> None:
    text = _joint_vcf([("MOTHER", "MOTHER"), ("PROBAND", "PROBAND"), ("FATHER", "FATHER")])
    # A record that lacks the chosen column is skipped, not read from another one.
    text += "1\t70000\tSV.3\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;END=71000\tGT:PS\t0/1:.\n"

    records = list(iter_structural_variant_records(text, record_format, sample_column=1))

    assert [(r.start, r.gt, r.phase_set) for r in records] == [(30000, "1|1", 2002), (50000, "0/0", None)]
    assert [r.gt for r in iter_structural_variant_records(text, record_format)] == ["0|1", "1/1", "0/1"]
