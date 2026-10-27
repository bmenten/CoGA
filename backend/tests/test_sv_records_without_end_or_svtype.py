"""An SV record that leaves END or SVTYPE out is read as VCF 4.4 defines it (CLIN-33, CLIN-37).

VCF 4.4 lets a structural-variant record leave both out: SVTYPE is deprecated as redundant
with ALT, and a symbolic allele without END has it "computed from the SVLEN field". The
Sniffles and Spectre uploads read a record without END as ending at its POS, so a deletion
written with SVLEN alone lay on one base: the upload lists an SV with the genes its POS-END
span overlaps, so the deletion was listed with the genes at its POS only, and a search for a
gene it deletes downstream of its POS missed it (CLIN-33). And every SV reader took a record
without SVTYPE to be of the type its ALT spells, so a breakend was stored as
``N]5:3000000]``, which neither the type filter nor the translocation rule of the length
filters knows (CLIN-37).

Now (``structural_variant_ingest``):

* a record without END, or with END ``.``, ends at POS + SVLEN (its sign dropped) for a
  ``<DEL>``, ``<DUP>``, ``<INV>`` or ``<CNV>`` allele, subtypes included, and at
  POS + len(REF) - 1 for any other allele: an ``<INS>``, a breakend, a sequence allele. One
  of the four without an SVLEN does not say how far it reaches: it is left out and counted
  (CLIN-39, the owner's decision; it stayed on its POS, 0 bp).
  A written END is kept as written, and a Spectre POS written as a range keeps its end. (An
  END written ``.`` made both readers skip the record.)
* a record without SVTYPE, or with SVTYPE ``.``, takes the type its ALT states: a symbolic
  allele's name, BND for breakend notation (a mate in brackets, or a single breakend such as
  ``G.``), INS or DEL for a sequence longer or shorter than REF, and ``SV`` when the ALT
  states none. A written SVTYPE is kept. The package's NeedlR, HiFiCNV and chrM Sniffles2
  readers take a missing type by the same rule; their END rule is their own.

The upload is read through ``upload_structural_variant_file`` with the SV storage and the
gene table faked, as in test_sv_upload_sample_column. ``e2e/test_e2e_sv_upload_vcf44_records.py``
uploads through the API against Postgres and ClickHouse and searches by gene, region and type.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import UploadFile
import pytest

from backend.app.services import annotation_manifest_service, variant_upload_service
from backend.app.services.clickhouse_variant_records import StoredStructuralVariantRow
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ParsedPed, PedMember
from backend.app.services.family_package_variants import (
    _iter_cnv_structural_records,
    _iter_mito_sv_records,
    _iter_needlr_per_sample_records,
)
from backend.app.services.structural_variant_ingest import (
    ParsedStructuralVariant,
    SkippedRecords,
    is_breakend_alt,
    iter_structural_variant_records,
    structural_variant_end,
    sv_type_from_alt,
)

POS = 30000
_UPLOAD_FORMATS = ["sniffles", "spectre"]


def _vcf(*records: str) -> str:
    """A one-sample VCF of ``records`` (CHROM to INFO), each called 0/1."""
    header = "##fileformat=VCFv4.4\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
    return header + "".join(f"{record}\tGT\t0/1\n" for record in records)


def _record(ref: str, alt: str, info: str, *, chrom: str = "1", pos: int | str = POS) -> str:
    return f"{chrom}\t{pos}\tSV1\t{ref}\t{alt}\t50\tPASS\t{info or '.'}"


def _one(text: str, record_format: str) -> ParsedStructuralVariant:
    records = list(iter_structural_variant_records(text, record_format))  # type: ignore[arg-type]
    assert len(records) == 1, records
    return records[0]


# --- END ----------------------------------------------------------------------------------


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
@pytest.mark.parametrize(
    ("alt", "svlen", "end"),
    [
        ("<DEL>", -1000, POS + 1000),  # VCF 4.2 writes a deletion's SVLEN negative
        ("<DEL>", 1000, POS + 1000),  # VCF 4.4: the number of deleted bases
        ("<DEL:ME>", -300, POS + 300),
        ("<DUP>", 500, POS + 500),
        ("<DUP:TANDEM>", 500, POS + 500),
        ("<INV>", 2000, POS + 2000),
        ("<CNV>", 3000, POS + 3000),
        ("<CNV:TR>", 60, POS + 60),
        ("<del>", 1000, POS + 1000),
    ],
)
def test_a_spanning_symbolic_allele_without_end_ends_svlen_bases_after_its_pos(
    record_format: str, alt: str, svlen: int, end: int
) -> None:
    # Before: END = POS, a 0 bp span whatever the SVLEN.
    record = _one(_vcf(_record("N", alt, f"SVLEN={svlen}")), record_format)
    assert (record.start, record.end, record.svlen) == (POS, end, svlen)


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
@pytest.mark.parametrize(
    ("ref", "alt", "info", "end"),
    [
        # An insertion spans no reference base, whatever it inserts.
        ("N", "<INS>", "SVLEN=5000", POS),
        ("N", "<INS:ME:ALU>", "SVLEN=300", POS),
        ("N", "N]5:3000000]", "SVTYPE=BND", POS),
        ("G", "G.", "", POS),
        # A sequence-resolved deletion spans its REF after the padding base.
        ("A" + "C" * 100, "A", "", POS + 100),
        ("A", "A" + "T" * 50, "", POS),
    ],
)
def test_any_other_allele_without_end_ends_on_the_last_base_of_its_ref(
    record_format: str, ref: str, alt: str, info: str, end: int
) -> None:
    record = _one(_vcf(_record(ref, alt, info)), record_format)
    assert (record.start, record.end) == (POS, end)


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
def test_a_written_end_is_kept_as_written(record_format: str) -> None:
    record = _one(_vcf(_record("N", "<DEL>", "SVTYPE=DEL;SVLEN=-1000;END=30500")), record_format)
    assert record.end == 30500


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
def test_an_end_written_missing_is_computed_and_the_record_kept(record_format: str) -> None:
    # Before: END=. could not be read as a number, and the record was skipped without a word.
    record = _one(_vcf(_record("N", "<DEL>", "SVTYPE=DEL;SVLEN=-1000;END=.")), record_format)
    assert record.end == POS + 1000


def test_a_spectre_pos_written_as_a_range_keeps_its_end() -> None:
    # No SVLEN: the range's end, not the record's POS, is its END.
    record = _one(_vcf(_record("N", "<DUP>", "SVTYPE=DUP", chrom="chr1", pos="chr1:5000-9000")), "spectre")
    assert (record.chrom, record.start, record.end) == ("1", 5000, 9000)


def test_structural_variant_end_follows_vcf_4_4() -> None:
    assert structural_variant_end(pos=100, ref="N", alt="<DUP:TANDEM>", sv_len=-50) == 150
    assert structural_variant_end(pos=100, ref="N", alt="<INS>", sv_len=50) == 100
    assert structural_variant_end(pos=100, ref="N", alt="<DEL>", sv_len=None) == 100
    assert structural_variant_end(pos=100, ref="ACGT", alt="A", sv_len=None) == 103
    # Several ALT alleles, or none: no allele states a span.
    assert structural_variant_end(pos=100, ref="N", alt="<DEL>,<DUP>", sv_len=-50) == 100
    assert structural_variant_end(pos=100, ref=".", alt=".", sv_len=None) == 100


# --- SVTYPE -------------------------------------------------------------------------------

_TYPE_FROM_ALT = [
    # A symbolic deletion or duplication states its length: one without is left out.
    ("N", "<DEL>", "DEL", "SVLEN=-500;"),
    ("N", "<DUP:TANDEM>", "DUP:TANDEM", "SVLEN=500;"),
    ("N", "N]5:3000000]", "BND", ""),
    ("N", "]5:3000000]N", "BND", ""),
    ("N", "N[5:3000000[", "BND", ""),
    ("N", "[5:3000000[N", "BND", ""),
    ("G", "G.", "BND", ""),
    ("G", ".G", "BND", ""),
    ("A", "ATTTTTTTTT", "INS", ""),
    ("ATTTTTTTTT", "A", "DEL", ""),
    ("ac", "acgt", "INS", ""),
]


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
@pytest.mark.parametrize("svtype_field", ["", "SVTYPE=.;"], ids=["absent", "missing"])
@pytest.mark.parametrize(("ref", "alt", "svtype", "extent"), _TYPE_FROM_ALT)
def test_a_record_without_svtype_takes_the_type_its_alt_states(
    record_format: str, svtype_field: str, ref: str, alt: str, svtype: str, extent: str
) -> None:
    # Before: the ALT without <> -- a breakend's type was "N]5:3000000]", a sequence's its
    # bases.
    record = _one(_vcf(_record(ref, alt, f"{svtype_field}{extent}SUPPORT=8")), record_format)
    assert record.svtype == svtype


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
@pytest.mark.parametrize(
    ("ref", "alt"),
    [("AC", "GT"), ("N", "<DEL>,<DUP>"), ("N", "."), ("N", "*")],
    ids=["sequence-as-long-as-ref", "several-alleles", "missing", "star"],
)
def test_an_alt_that_states_no_type_is_stored_as_sv(record_format: str, ref: str, alt: str) -> None:
    record = _one(_vcf(_record(ref, alt, "SUPPORT=8")), record_format)
    assert record.svtype == "SV"


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
def test_a_written_svtype_is_kept(record_format: str) -> None:
    record = _one(_vcf(_record("N", "<DUP:TANDEM>", "SVTYPE=DUP;SVLEN=500")), record_format)
    assert record.svtype == "DUP"


def test_a_breakend_without_svtype_is_stored_as_one_with_its_mate() -> None:
    record = _one(_vcf(_record("N", "N]5:3000000]", "SUPPORT=8", pos=60000)), "sniffles")
    assert (record.svtype, record.start, record.end) == ("BND", 60000, 60000)
    assert (record.remote_chr, record.remote_start, record.remote_end) == ("5", 3_000_000, 3_000_000)


@pytest.mark.parametrize("alt", ["<DEL>", "<INS:ME:ALU>", "N", "ACGT", ".", "*", ""])
def test_other_alleles_are_not_breakends(alt: str) -> None:
    assert not is_breakend_alt(alt)
    assert sv_type_from_alt("N", alt) != "BND"


# --- The package readers take a missing type by the same rule ----------------------------


def _proband_ped() -> ParsedPed:
    member = PedMember(
        family_id="FAM", iid="PROBAND", pid="0", mid="0", sex="1", phen="2", line_no=1, clinical_status="affected"
    )
    return ParsedPed(family_ids=["FAM"], members=[member], sample_ids=["PROBAND"], text="")


def _proband_contexts() -> dict[str, SampleMetadataContext]:
    return {
        "PROBAND": SampleMetadataContext(
            sample_uuid="uuid-proband",
            sample_id="PROBAND",
            family_uuid="family-uuid",
            family_id="FAM",
            sex="male",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
    }


def test_needlr_records_without_svtype_take_the_type_their_alt_states() -> None:
    header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    calls = "Query_ID=PROBAND_sv_phased;Genotype=0/1;Alt_Reads=8"
    text = header + "".join(
        f"chr1\t{pos}\tSV{pos}\t{ref}\t{alt}\t.\t.\t{info}{calls}\n"
        for pos, ref, alt, info in [
            (60000, "N", "N]5:3000000]", ""),
            (70000, "A" + "C" * 100, "A", "End_Pos=70100;"),
            (80000, "N", "<DUP>", "SVTYPE=DUP;SVLEN=500;End_Pos=80500;"),
        ]
    )
    records = _iter_needlr_per_sample_records(
        [("PROBAND", text)], ped=_proband_ped(), sample_contexts=_proband_contexts()
    )
    assert {record.start: record.sv_type for record in records} == {60000: "BND", 70000: "DEL", 80000: "DUP"}


def test_hificnv_records_without_svtype_take_the_type_their_alt_states() -> None:
    header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    text = header + (
        "chr1\t1000\t.\tN\t<DUP>\t20\tPASS\tEND=9000;SVLEN=8000\tGT:CN\t0/1:3\n"
        "chr1\t20000\t.\tN\t.\t20\tPASS\tEND=29000\tGT:CN\t0/0:2\n"
    )
    records = _iter_cnv_structural_records(text, sample_id="HG002")
    # An ALT that states no type keeps the caller's default.
    assert [(record.start, record.sv_type) for record in records] == [(1000, "DUP"), (20000, "CNV")]


def test_chrm_sv_records_without_svtype_take_the_type_their_alt_states() -> None:
    header = "##fileformat=VCFv4.2\n##source=Sniffles2_2.7.3\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    text = header + (
        "chrM\t8470\tS1\tN\t<DEL>\t60\tPASS\tSVLEN=-4977;END=13447;VAF=0.35\tGT:DV\t0/1:140\n"
        "chrM\t3000\tS2\tN\tN]5:3000000]\t60\tPASS\tVAF=0.2\tGT:DV\t0/1:40\n"
    )
    records = _iter_mito_sv_records(text, sample_id="PROBAND")
    assert sorted((record.start, record.sv_type) for record in records) == [(3000, "BND"), (8470, "DEL")]


# --- The upload lists a deletion with the genes it spans ---------------------------------

# The gene table on chromosome 1: (start, end, symbol).
_GENES = {
    "1": [
        (29000, 30001, "AT_POS"),
        (30500, 30800, "SPANNED"),
        (31001, 32000, "AFTER"),
        (79000, 81000, "INS_HOST"),
        (80500, 85000, "DOWNSTREAM"),
    ]
}


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
async def test_an_uploaded_deletion_without_end_is_listed_with_the_genes_it_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[StoredStructuralVariantRow] = []

    async def no_rows(*_args: Any, **_kwargs: Any) -> list[StoredStructuralVariantRow]:
        return []

    async def rewrite(_assembly: Any, _family: Any, rows: Any, **_kwargs: Any) -> None:
        written.extend(rows)

    async def genes(*_args: Any, **_kwargs: Any) -> dict[str, list[tuple[int, int, str]]]:
        return _GENES

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(variant_upload_service, "fetch_family_structural_variant_rows", no_rows)
    monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", rewrite)
    monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", genes)
    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)

    body = _vcf(
        _record("N", "<DEL>", "SVLEN=1000;SUPPORT=12"),
        _record("N", "N]5:3000000]", "SUPPORT=8", pos=60000),
        _record("N", "<INS>", "SVLEN=5000;SUPPORT=9", pos=80000),
    )
    result = await variant_upload_service.upload_structural_variant_file(
        _Session(),  # type: ignore[arg-type]
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM",
            project_ids=["project-uuid"],
            sample_rows=[],
            sample_uuid_to_name={"uuid-proband": "PROBAND"},
            sample_name_to_uuid={"PROBAND": "uuid-proband"},
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_context=_proband_contexts()["PROBAND"],
        file=UploadFile(file=BytesIO(body.encode()), filename="PROBAND.sniffles.vcf"),
        overwrite=False,
        format_hint="sniffles",
    )

    assert (result["processed"], result["created"]) == (3, 3)
    stored = {
        row.record.sv_type: (row.record.start, row.record.end, row.record.gene_symbols, row.record.remote_chr)
        for row in written
    }
    assert stored == {
        # Before: 1:30000-30000, listed with AT_POS alone.
        "DEL": (30000, 31000, ["AT_POS", "SPANNED"], None),
        # Before: of type "N]5:3000000]", without its mate.
        "BND": (60000, 60000, [], "5"),
        # An insertion spans no reference base: the gene it lands in, not the bases after it.
        "INS": (80000, 80000, ["INS_HOST"], None),
    }


# --- A record whose extent is not stated is left out and counted (CLIN-39) ------------------------
#
# A <DEL>, <DUP>, <INV> or <CNV> that states neither END nor SVLEN does not say how far it
# reaches. It was stored on its POS, as 0 bp, and a gene or panel search missed the genes it
# spans; every reader now leaves it out and counts it (the owner's decision, 2026-10-27).

_WITHOUT_EXTENT = [
    pytest.param("<DEL>", "SVTYPE=DEL", id="deletion"),
    pytest.param("<DUP:TANDEM>", "SVTYPE=DUP", id="duplication"),
    pytest.param("<INV>", "", id="inversion-without-svtype"),
    pytest.param("<CNV>", "SVTYPE=CNV;END=.", id="cnv-with-end-missing"),
    pytest.param("<del>", "SVTYPE=DEL;SVLEN=.", id="deletion-with-svlen-missing"),
]


@pytest.mark.parametrize("record_format", _UPLOAD_FORMATS)
@pytest.mark.parametrize(("alt", "info"), _WITHOUT_EXTENT)
def test_an_uploaded_record_whose_extent_is_not_stated_is_left_out_and_counted(
    record_format: str, alt: str, info: str
) -> None:
    skipped = SkippedRecords()
    text = _vcf(_record("N", alt, info), _record("N", "<INS>", "SVTYPE=INS", pos=40000))

    records = list(iter_structural_variant_records(text, record_format, skipped=skipped))  # type: ignore[arg-type]

    # Before: stored on its POS, 0 bp. An insertion without SVLEN stays: it spans no base.
    assert [(record.svtype, record.start) for record in records] == [("INS", 40000)]
    assert skipped.without_extent == 1


@pytest.mark.parametrize(
    ("read", "line"),
    [
        pytest.param(
            "needlr",
            "chr1\t20000\tSV1\tN\t<DEL>\t.\t.\tSVTYPE=DEL;Query_ID=PROBAND_sv_phased;Genotype=0/1;Alt_Reads=8\n",
            id="needlr",
        ),
        pytest.param("hificnv", "chr1\t20000\t.\tN\t<DUP>\t20\tPASS\tSVTYPE=DUP\tGT:CN\t0/1:3\n", id="hificnv"),
        pytest.param("chrm", "chrM\t8470\tS1\tN\t<DEL>\t60\tPASS\tVAF=0.3\tGT:DV\t0/1:40\n", id="chrm"),
    ],
)
def test_every_package_reader_leaves_out_a_record_whose_extent_is_not_stated(read: str, line: str) -> None:
    skipped = SkippedRecords()
    if read == "needlr":
        header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        records = _iter_needlr_per_sample_records(
            [("PROBAND", header + line)], ped=_proband_ped(), sample_contexts=_proband_contexts(), skipped=skipped
        )
    else:
        header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
        reader = _iter_cnv_structural_records if read == "hificnv" else _iter_mito_sv_records
        records = reader(header + line, sample_id="PROBAND", skipped=skipped)

    # Before: NeedlR and HiFiCNV stored it at POS..POS+1, the chrM reader on its POS.
    assert (records, skipped.without_extent) == ([], 1)


async def _upload_records(monkeypatch: pytest.MonkeyPatch, *records: str) -> tuple[dict[str, Any], list[Any]]:
    written: list[StoredStructuralVariantRow] = []

    async def no_rows(*_args: Any, **_kwargs: Any) -> list[StoredStructuralVariantRow]:
        return []

    async def rewrite(_assembly: Any, _family: Any, rows: Any, **_kwargs: Any) -> None:
        written.extend(rows)

    async def genes(*_args: Any, **_kwargs: Any) -> dict[str, list[tuple[int, int, str]]]:
        return _GENES

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(variant_upload_service, "fetch_family_structural_variant_rows", no_rows)
    monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", rewrite)
    monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", genes)
    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    result = await variant_upload_service.upload_structural_variant_file(
        _Session(),  # type: ignore[arg-type]
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM",
            project_ids=["project-uuid"],
            sample_rows=[],
            sample_uuid_to_name={"uuid-proband": "PROBAND"},
            sample_name_to_uuid={"PROBAND": "uuid-proband"},
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_context=_proband_contexts()["PROBAND"],
        file=UploadFile(file=BytesIO(_vcf(*records).encode()), filename="PROBAND.sniffles.vcf"),
        overwrite=False,
        format_hint="sniffles",
    )
    return result, written


@pytest.mark.asyncio
async def test_an_upload_says_how_many_records_it_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    result, written = await _upload_records(
        monkeypatch,
        _record("N", "<DEL>", "SVTYPE=DEL;SUPPORT=12"),
        _record("N", "<DEL>", "SVTYPE=DEL;SVLEN=-1000;SUPPORT=12", pos=50000),
    )

    # Before: two SVs, one of them 0 bp at 1:30000, listed with the gene at its POS alone.
    assert (result["processed"], result["skipped_without_extent"]) == (1, 1)
    assert [(row.record.start, row.record.end) for row in written] == [(50000, 51000)]


@pytest.mark.asyncio
async def test_an_upload_of_records_whose_extent_is_never_stated_is_refused_saying_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        await _upload_records(monkeypatch, _record("N", "<DEL>", "SVTYPE=DEL;SUPPORT=12"))

    assert refused.value.status_code == 400
    assert refused.value.detail == (
        "No valid structural-variant records found: 1 record(s) of a deletion, duplication, inversion "
        "or CNV state neither END nor SVLEN, so how far they reach is not known"
    )
