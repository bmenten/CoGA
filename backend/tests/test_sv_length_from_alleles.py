"""A structural variant written as a sequence allele without SVLEN is measured by its alleles
(CLIN-41).

VCF 4.4 writes SVLEN for a symbolic allele only, and ``.`` for a sequence allele, whose bases
hold its length. CoGA measured a call without SVLEN by END − POS (#827's rule), and an
insertion written as its sequence has its END at its POS: it was 0 bp in the SV list and to
the length filters, so any minimum length dropped it. A deletion written as its sequence
measured its REF in the uploads (its END at POS + len(REF) − 1 since #831), but one bp in
the package's NeedlR and HiFiCNV files (END at POS + 1) and none on the mtDNA page (END at
POS).

The owner's decision (2026-10-14): such a call is measured by its alleles. Every SV reader
stores it with len(ALT) − len(REF) as its SVLEN (``svLen``, ``record_sv_len``): the bases an
insertion inserts, and those a deletion deletes, negative as Sniffles2 and pbsv write a
deletion's SVLEN. The SV list shows that length, the length filters measure it without its
sign, the CSV export and the report sentence read the list's, and the mtDNA page shows it
too. A call that writes SVLEN keeps it as written; a symbolic allele or a breakend without
one, or alleles as long as each other, is still measured by END − POS; no call's END or id
changes. A call stored before this change keeps the SVLEN it was stored with (none) until
its file is imported again: the read side derives nothing.

The helper tests import the new helpers inside the test; every other test reads the records
through the readers, the upload, the filters, the list, the export and the mtDNA page as
they stand. The records and the family are synthetic.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import UploadFile
import pytest

from backend.app.services import annotation_manifest_service, variant_upload_service
from backend.app.services.clickhouse_family_variants import (
    export_family_structural_variants,
    get_family_structural_variants_page,
)
from backend.app.services.clickhouse_variant_queries import _structural_record_matches
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ParsedPed
from backend.app.services.family_package_variants import (
    _iter_cnv_structural_records,
    _iter_mito_sv_records,
    _iter_needlr_per_sample_records,
    _iter_needlr_structural_records,
)
from backend.app.services.family_variant_filters import (
    StructuralVariantQueryFilters,
    structural_variant_length,
)
from backend.app.services.mitochondrial_analysis import _structural_variant_out as _mtdna_sv_out
from backend.app.services.structural_variant_ingest import (
    iter_structural_variant_records,
    sv_type_from_alt,
)

POS = 100_000
# A 350 bp insertion and a 100 bp deletion, written as their sequences after a padding base.
_INSERTED = "T" * 350
_INS_REF, _INS_ALT = "A", "A" + _INSERTED
_DEL_REF, _DEL_ALT = "G" + "C" * 100, "G"
_UPLOAD_FORMATS = ["sniffles", "spectre"]
_VCF_HEADER = "##fileformat=VCFv4.4\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n"


def _vcf(*records: str) -> str:
    """A one-sample VCF 4.4 of ``records`` (CHROM to INFO), each called 0/1."""
    return _VCF_HEADER + "".join(f"{record}\tGT\t0/1\n" for record in records)


def _record(ref: str, alt: str, info: str = "", *, chrom: str = "1", pos: int = POS) -> str:
    return f"{chrom}\t{pos}\tSV1\t{ref}\t{alt}\t50\tPASS\t{info or 'SUPPORT=9'}"


def _uploaded(text_value: str, record_format: str) -> StructuralVariantRecord:
    """The record the SV upload stores for the one call of ``text_value``: the POS, END,
    type and SVLEN its parser reads (``upload_structural_variant_file``)."""
    (parsed,) = iter_structural_variant_records(text_value, record_format)  # type: ignore[arg-type]
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=f"{parsed.chrom}-{parsed.start}-{parsed.end}-{parsed.svtype}",
        chr=parsed.chrom,
        start=parsed.start,
        end=parsed.end,
        sv_type=parsed.svtype,
        source=record_format,
        remote_chr=parsed.remote_chr,
        remote_start=parsed.remote_start,
        remote_end=parsed.remote_end,
        sv_len=parsed.svlen,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[StructuralVariantCall(sample="PROBAND", gt=parsed.gt, qual=None, read_support=None, filter=None)],
    )


def _kept(records: list[StructuralVariantRecord], **filters: Any) -> list[str]:
    query = StructuralVariantQueryFilters(page=1, page_size=100, **filters)
    return [record.variant_id for record in records if _structural_record_matches(record, query, [], [])]


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ref", "alt", "sv_len"),
    [
        (_INS_REF, _INS_ALT, 350),
        (_DEL_REF, _DEL_ALT, -100),
        # Bases on both sides of the event: the difference is what it inserts or deletes.
        ("ACGTACGT", "AT", -6),
        ("AT", "ACGTACGTAC", 8),
        ("a", "acgt", 3),
        ("N", "NNNNN", 4),
        (" A ", " ATTT ", 3),
    ],
)
def test_a_sequence_allele_states_its_length_by_its_alleles(ref: str, alt: str, sv_len: int) -> None:
    from backend.app.services.structural_variant_ingest import sequence_allele_sv_len

    assert sequence_allele_sv_len(ref, alt) == sv_len


@pytest.mark.parametrize(
    ("ref", "alt"),
    [
        ("N", "<INS>"),
        ("N", "<DEL>"),
        ("N", "<INS:ME:ALU>"),
        ("N", "N]5:3000000]"),
        ("G", "G."),
        ("G", ".G"),
        ("A", "AT,ATT"),
        ("A", "."),
        ("A", "*"),
        ("A", ""),
        (".", "ACGT"),
        # As long as REF: the alleles state no length.
        ("AC", "GT"),
        ("A", "T"),
    ],
)
def test_an_allele_that_is_no_sequence_or_as_long_as_ref_states_no_length(ref: str, alt: str) -> None:
    from backend.app.services.structural_variant_ingest import sequence_allele_sv_len

    assert sequence_allele_sv_len(ref, alt) is None


def test_a_written_svlen_is_kept_and_a_missing_one_taken_from_the_alleles() -> None:
    from backend.app.services.structural_variant_ingest import record_sv_len

    assert record_sv_len(sv_len=345, ref=_INS_REF, alt=_INS_ALT) == 345
    assert record_sv_len(sv_len=0, ref=_INS_REF, alt=_INS_ALT) == 0
    assert record_sv_len(sv_len=None, ref=_INS_REF, alt=_INS_ALT) == 350
    assert record_sv_len(sv_len=None, ref=_DEL_REF, alt=_DEL_ALT) == -100
    assert record_sv_len(sv_len=-5000, ref="N", alt="<DEL>") == -5000
    assert record_sv_len(sv_len=None, ref="N", alt="<INS>") is None


@pytest.mark.parametrize(
    ("ref", "alt", "sv_type"),
    [
        (_INS_REF, _INS_ALT, "INS"),
        (_DEL_REF, _DEL_ALT, "DEL"),
        ("ACGTACGT", "AT", "DEL"),
        ("AT", "ACGTACGTAC", "INS"),
        ("AC", "GT", None),
    ],
)
def test_a_sequence_allele_s_type_and_length_agree(ref: str, alt: str, sv_type: str | None) -> None:
    # The type a record without SVTYPE takes from its sequence allele (#831), and the length
    # it is stored with, come from one comparison: an insertion is longer, a deletion shorter.
    from backend.app.services.structural_variant_ingest import sequence_allele_sv_len

    sv_len = sequence_allele_sv_len(ref, alt)
    assert sv_type_from_alt(ref, alt) == sv_type
    assert (sv_len is None) == (sv_type is None)
    assert sv_len is None or (sv_len > 0) == (sv_type == "INS")


# --------------------------------------------------------------------------- #
# Every reader stores the length its alleles state
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
@pytest.mark.parametrize(
    ("ref", "alt", "info", "stored"),
    [
        # Before: no SVLEN, so 0 bp for the insertion (END at POS) and END − POS for the
        # deletion.
        pytest.param(_INS_REF, _INS_ALT, "", ("INS", POS, POS, 350), id="insertion"),
        pytest.param(_INS_REF, _INS_ALT, "SVLEN=.;SVTYPE=INS;SUPPORT=9", ("INS", POS, POS, 350), id="insertion-svlen-dot"),
        pytest.param(_DEL_REF, _DEL_ALT, "", ("DEL", POS, POS + 100, -100), id="deletion"),
        pytest.param(_DEL_REF, _DEL_ALT, f"END={POS + 100};SVLEN=.", ("DEL", POS, POS + 100, -100), id="deletion-with-end"),
    ],
)
def test_an_upload_stores_a_sequence_allele_with_the_length_its_alleles_state(
    record_format: str, ref: str, alt: str, info: str, stored: tuple[str, int, int, int]
) -> None:
    record = _uploaded(_vcf(_record(ref, alt, info)), record_format)

    # The END and so the SV's id are those VCF 4.4 gives it (#831): only the SVLEN is new.
    assert (record.sv_type, record.start, record.end, record.sv_len) == stored


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
@pytest.mark.parametrize(
    ("ref", "alt", "info", "stored"),
    [
        # A written SVLEN is kept as written, even where the alleles say otherwise.
        pytest.param(_INS_REF, _INS_ALT, "SVTYPE=INS;SVLEN=345", ("INS", POS, POS, 345), id="written-svlen"),
        pytest.param("N", "<INS>", "SVTYPE=INS;SVLEN=5000", ("INS", POS, POS, 5000), id="symbolic-insertion"),
        # A symbolic allele, a breakend, or alleles as long as each other state no length.
        pytest.param("N", "<INS>", "SVTYPE=INS", ("INS", POS, POS, None), id="symbolic-without-svlen"),
        pytest.param("N", "N]5:3000000]", "SVTYPE=BND", ("BND", POS, POS, None), id="breakend"),
        pytest.param("AC", "GT", "SVTYPE=INV", ("INV", POS, POS + 1, None), id="as-long-as-ref"),
    ],
)
def test_an_upload_keeps_a_written_svlen_and_states_none_for_other_alleles(
    record_format: str, ref: str, alt: str, info: str, stored: tuple[str, int, int, int | None]
) -> None:
    record = _uploaded(_vcf(_record(ref, alt, info)), record_format)

    assert (record.sv_type, record.start, record.end, record.sv_len) == stored


@pytest.mark.parametrize(
    ("ref", "alt", "end", "sv_len"),
    [
        # Before: a TSV has no SVLEN column, so every row was measured by END − POS.
        (_INS_REF, _INS_ALT, POS, 350),
        (_DEL_REF, _DEL_ALT, POS + 100, -100),
        ("N", "<INS>", POS, None),
        ("N", "<DEL>", POS + 5000, None),
    ],
)
def test_a_manual_tsv_row_written_as_a_sequence_allele_states_its_length(
    ref: str, alt: str, end: int, sv_len: int | None
) -> None:
    sv_type = sv_type_from_alt(ref, alt)
    record = _uploaded(f"tsv-1\t1\t{POS}\t{end}\t{ref}\t{alt}\t{sv_type}\t0/1\n", "manual")

    assert (record.start, record.end, record.sv_len) == (POS, end, sv_len)


def _proband() -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid="sample-proband",
        sample_id="PROBAND",
        family_uuid="family-uuid",
        family_id="FAM001",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


_PED = ParsedPed(family_ids=["FAM001"], members=[], sample_ids=["PROBAND"], text="")
_NEEDLR_CALL = "Query_ID=PROBAND;Genotype=0/1;Alt_Reads=12"
_PACKAGE_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"


def _needlr_family(ref: str, alt: str, info: str) -> StructuralVariantRecord:
    line = f"1\t{POS}\tNeedlR.1\t{ref}\t{alt}\t60\tPASS\t{info}{_NEEDLR_CALL}"
    (record,) = _iter_needlr_structural_records(
        f"{_PACKAGE_HEADER}{line}\n", ped=_PED, sample_contexts={"PROBAND": _proband()}
    )
    return record


def _needlr_per_sample(ref: str, alt: str, info: str) -> StructuralVariantRecord:
    line = f"1\t{POS}\tNeedlR.1\t{ref}\t{alt}\t60\tPASS\t{info}{_NEEDLR_CALL}"
    (record,) = _iter_needlr_per_sample_records(
        [("PROBAND", f"{_PACKAGE_HEADER}{line}\n")], ped=_PED, sample_contexts={"PROBAND": _proband()}
    )
    return record


def _hificnv(ref: str, alt: str, info: str) -> StructuralVariantRecord:
    line = f"chr1\t{POS}\t.\t{ref}\t{alt}\t20\tPASS\t{info or '.'}\tGT:CN\t0/1:1"
    (record,) = _iter_cnv_structural_records(f"{_PACKAGE_HEADER}{line}\n", sample_id="PROBAND")
    return record


def _chrm(ref: str, alt: str, info: str) -> StructuralVariantRecord:
    line = f"chrM\t8470\tSniffles2.1M0\t{ref}\t{alt}\t60\tPASS\t{info}VAF=0.35\tGT:DV\t0/1:140"
    (record,) = _iter_mito_sv_records(f"{_PACKAGE_HEADER}{line}\n", sample_id="PROBAND")
    return record


@pytest.mark.parametrize(
    ("read", "ref", "alt", "info", "stored"),
    [
        # NeedlR ends a call without END where VCF 4.4 puts it, as the uploads do, and its id
        # is theirs (CLIN-39; before: POS + SVLEN, else POS + 1). Before CLIN-41: 1 bp, its
        # END − POS.
        pytest.param(_needlr_family, _INS_REF, _INS_ALT, "", ("1-100000-100000-INS---", 100_000, 350), id="needlr-insertion"),
        pytest.param(_needlr_family, _DEL_REF, _DEL_ALT, "SVLEN=.;", ("1-100000-100100-DEL---", 100_100, -100), id="needlr-deletion"),
        pytest.param(_needlr_per_sample, _INS_REF, _INS_ALT, "SVTYPE=INS;", ("1-100000-100000-INS---", 100_000, 350), id="needlr-per-sample-insertion"),
        pytest.param(_needlr_family, _INS_REF, _INS_ALT, "SVLEN=345;", ("1-100000-100000-INS---", 100_000, 345), id="needlr-written-svlen"),
        pytest.param(_needlr_family, "N", "<INS>", "SVTYPE=INS;", ("1-100000-100000-INS---", 100_000, None), id="needlr-symbolic"),
        # HiFiCNV names an SV without a record ID by its coordinates and its source.
        pytest.param(_hificnv, _DEL_REF, _DEL_ALT, f"END={POS + 100}", ("1-100000-100100-DEL----hificnv", 100_100, -100), id="hificnv-deletion"),
        pytest.param(_hificnv, "N", "<DEL>", f"SVTYPE=DEL;END={POS + 100}", ("1-100000-100100-DEL----hificnv", 100_100, None), id="hificnv-symbolic"),
        # The chrM reader ends a sequence deletion without END on its REF's last base, where
        # VCF 4.4 puts it (CLIN-39; before: on its POS). Before CLIN-41: no length.
        pytest.param(_chrm, "A" + "C" * 500, "A", "", ("M-8470-8970-DEL----mito_sv", 8_970, -500), id="chrm-deletion"),
        pytest.param(_chrm, "N", "<DEL>", "SVLEN=-500;", ("M-8470-8970-DEL----mito_sv", 8_970, -500), id="chrm-written-svlen"),
    ],
)
def test_each_package_reader_stores_a_sequence_allele_with_the_length_its_alleles_state(
    read: Any, ref: str, alt: str, info: str, stored: tuple[str, int, int | None]
) -> None:
    record = read(ref, alt, info)

    assert (record.variant_id, record.end, record.sv_len) == stored


# --------------------------------------------------------------------------- #
# The length is measured the same way wherever CoGA measures one
# --------------------------------------------------------------------------- #

_SEQUENCE_CALLS = [
    _uploaded(_vcf(_record(_INS_REF, _INS_ALT)), "sniffles"),
    _uploaded(_vcf(_record(_DEL_REF, _DEL_ALT, pos=200_000)), "sniffles"),
    _uploaded(_vcf(_record("N", "<INS>", "SVTYPE=INS;SVLEN=5000", pos=300_000)), "sniffles"),
]
_INSERTION_ID, _DELETION_ID, _SYMBOLIC_ID = (record.variant_id for record in _SEQUENCE_CALLS)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        # Before: the insertion was 0 bp, so every minimum above 0 dropped it and no exact
        # length but 0 found it.
        ({"min_length": 50}, [_INSERTION_ID, _DELETION_ID, _SYMBOLIC_ID]),
        ({"min_length": 350}, [_INSERTION_ID, _SYMBOLIC_ID]),
        ({"min_length": 351}, [_SYMBOLIC_ID]),
        ({"length": 350}, [_INSERTION_ID]),
        ({"length": 100}, [_DELETION_ID]),
        ({"length": 0}, []),
        ({"variant_type": "INS", "min_length": 50}, [_INSERTION_ID, _SYMBOLIC_ID]),
    ],
)
def test_the_length_filters_measure_the_alleles_length(filters: dict[str, Any], expected: list[str]) -> None:
    assert _kept(_SEQUENCE_CALLS, **filters) == expected


def test_a_call_stored_without_svlen_is_measured_as_it_was_stored() -> None:
    # A call stored before CLIN-41 holds no SVLEN: the read side derives none, so it is
    # measured by END − POS, as before, until its file is imported again.
    stored = StructuralVariantRecord(
        variant_key=1,
        variant_id="1-100000-100000-INS---",
        chr="1",
        start=POS,
        end=POS,
        sv_type="INS",
        source="sniffles",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=None,
        filters=[],
        gene_symbols=[],
        annotations=[{"info": {"SUPPORT": "9"}}],
        calls=[StructuralVariantCall(sample="PROBAND", gt="0/1", qual=None, read_support=None, filter=None)],
    )

    assert structural_variant_length(sv_len=stored.sv_len, start=stored.start, end=stored.end) == 0
    assert _kept([stored], min_length=1) == []


def _family() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "female"}],
        sample_uuid_to_name={"sample-proband": "PROBAND"},
        sample_name_to_uuid={"PROBAND": "sample-proband"},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


_M = "backend.app.services.clickhouse_family_variants."


def _stored_row(record: StructuralVariantRecord, key: int) -> tuple[Any, ...]:
    """``record`` as ClickHouse returns it to the SV candidate read: the entry's position,
    type and calls, and the SVLEN of its details row (``svLen``)."""
    return (
        key,
        record.variant_id,
        record.chr,
        record.start,
        record.end,
        record.sv_type,
        record.source,
        None,
        None,
        record.sv_len,
        list(record.filters),
        "",
        list(record.gene_symbols),
        [call.sample for call in record.calls],
        [call.gt for call in record.calls],
        [call.qual for call in record.calls],
        [call.read_support for call in record.calls],
        [call.filter for call in record.calls],
        [call.copy_number for call in record.calls],
    )


@pytest.fixture()
def stored(monkeypatch: pytest.MonkeyPatch) -> None:
    """ClickHouse holding the uploaded calls of ``_SEQUENCE_CALLS``; Postgres holds no
    reviews, cytobands or phenotypes, and the native page may not answer."""
    rows = [_stored_row(record, key) for key, record in enumerate(_SEQUENCE_CALLS, start=1)]

    async def execute(_query: str, _params: dict[str, Any]) -> list[tuple[Any, ...]]:
        return rows

    async def native_summary(*_args: Any, **_kwargs: Any):
        raise AssertionError("the native page's SQL applies no length filter")

    async def no_review_ids(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_phenotypes(*_args: Any, **_kwargs: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    monkeypatch.setattr(_M + "_execute_clickhouse", execute)
    monkeypatch.setattr(_M + "_fetch_structural_variant_summary", native_summary)
    monkeypatch.setattr(_M + "list_matching_structural_variant_review_ids", no_review_ids)
    monkeypatch.setattr(_M + "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(_M + "_fetch_structural_cytoband_map", nothing)
    monkeypatch.setattr(_M + "_affected_present_hpo", no_phenotypes)


@pytest.mark.asyncio
@pytest.mark.parametrize("prioritize", [False, True])
async def test_the_list_shows_and_measures_the_alleles_length(stored, prioritize: bool) -> None:
    page = await get_family_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_family(),
        page=1,
        page_size=100,
        filters=StructuralVariantQueryFilters(page=1, page_size=100, min_length=50),
        prioritize=prioritize,
    )

    # The list shows the length the report sentence reads (without its sign): the
    # insertion's inserted bases, the deletion's deleted bases negative, as a deletion's
    # SVLEN is shown. Before: the insertion was 0 bp and dropped by the minimum, and the
    # deletion shown as its END − POS, 100.
    shown = {str(variant.id): variant.length for variant in page.variants}
    assert shown == {_INSERTION_ID: 350, _DELETION_ID: -100, _SYMBOLIC_ID: 5000}
    assert page.total == 3


@pytest.mark.asyncio
async def test_the_export_holds_the_svs_the_list_holds(stored) -> None:
    export = await export_family_structural_variants(
        None,  # type: ignore[arg-type]
        context=_family(),
        filters=StructuralVariantQueryFilters(page=1, page_size=0, length=350),
    )

    assert [(str(row.id), row.length) for row in export.rows] == [(_INSERTION_ID, 350)]


def test_the_mtdna_page_shows_a_chrm_sequence_deletion_by_its_alleles() -> None:
    record = _chrm("A" + "C" * 500, "A", "")

    shown = _mtdna_sv_out(record, member_by_sample={})

    # Before: no SVLEN and its END on its POS, so "length n/a". Its END is where VCF 4.4 puts
    # it, its REF's last base (CLIN-39; before: its POS).
    assert (shown.sv_type, shown.start, shown.end, shown.length) == ("DEL", 8470, 8970, 500)


# --------------------------------------------------------------------------- #
# The upload stores it
# --------------------------------------------------------------------------- #


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
    """Postgres as the upload uses it: the stored-sample lookup of its column check (the
    family is PROBAND alone), the sample's metadata read and update, and the commit."""

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        if "FROM samples WHERE family_id" in " ".join(str(statement).split()):
            return _Result([{"sample_id": "PROBAND"}])
        return _Result()

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_the_upload_stores_each_call_with_the_length_its_alleles_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[StoredStructuralVariantRow] = []

    async def no_rows(*_args: Any, **_kwargs: Any) -> list[StoredStructuralVariantRow]:
        return []

    async def rewrite(_assembly: Any, _family: Any, rows: Any, **_kwargs: Any) -> None:
        written.extend(rows)

    async def no_genes(*_args: Any, **_kwargs: Any) -> dict[str, list[tuple[int, int, str]]]:
        return {}

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(variant_upload_service, "fetch_family_structural_variant_rows", no_rows)
    monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", rewrite)
    monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", no_genes)
    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)

    body = _vcf(
        _record(_INS_REF, _INS_ALT, "SVLEN=.;SUPPORT=9"),
        _record(_DEL_REF, _DEL_ALT, pos=200_000),
        _record("N", "<INS>", "SVTYPE=INS;SVLEN=5000", pos=300_000),
        _record("N", "N]5:3000000]", "SVTYPE=BND", pos=400_000),
    )
    result = await variant_upload_service.upload_structural_variant_file(
        _Session(),  # type: ignore[arg-type]
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM001",
            project_ids=["project-uuid"],
            sample_rows=[],
            sample_uuid_to_name={"sample-proband": "PROBAND"},
            sample_name_to_uuid={"PROBAND": "sample-proband"},
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_context=_proband(),
        file=UploadFile(file=BytesIO(body.encode()), filename="PROBAND.sniffles.vcf"),
        overwrite=False,
        format_hint="sniffles",
    )

    assert (result["processed"], result["created"]) == (4, 4)
    stored = {row.record.variant_id: (row.record.sv_type, row.record.sv_len) for row in written}
    # The ids are the coordinates and type the calls had before: only their SVLEN is new.
    assert stored == {
        "1-100000-100000-INS---": ("INS", 350),
        "1-200000-200100-DEL---": ("DEL", -100),
        "1-300000-300000-INS---": ("INS", 5000),
        "1-400000-400000-BND-5-3000000-3000000": ("BND", None),
    }
