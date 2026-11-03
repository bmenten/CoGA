"""Every SV reader positions, types and pairs a record as VCF 4.4 defines it: its END, its
SVTYPE, a breakend's mate and whether it has a length (CLIN-33, CLIN-37, CLIN-39).

Merged from ``test_sv_records_without_end_or_svtype.py`` (CLIN-33, CLIN-37, and CLIN-39's
records whose extent is not stated) and ``test_sv_ends_and_mates.py`` (CLIN-39's one rule for
every reader); each fix's account follows.

**A record that leaves END or SVTYPE out (CLIN-33, CLIN-37).** VCF 4.4 lets a
structural-variant record leave both out: SVTYPE is deprecated as redundant with ALT, and a
symbolic allele without END has it "computed from the SVLEN field". The Sniffles and Spectre
uploads read a record without END as ending at its POS, so a deletion written with SVLEN alone
lay on one base: the upload lists an SV with the genes its POS-END span overlaps, so the
deletion was listed with the genes at its POS only, and a search for a gene it deletes
downstream of its POS missed it (CLIN-33). And every SV reader took a record without SVTYPE to
be of the type its ALT spells, so a breakend was stored as ``N]5:3000000]``, which neither the
type filter nor the translocation rule of the length filters knows (CLIN-37).

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
  readers take a missing type by the same rule.

**One rule for every reader (CLIN-39).**

* **Ends.** The package's NeedlR, HiFiCNV and chrM Sniffles2 readers ended a record without
  END at POS + SVLEN, its sign dropped, whatever its type (without SVLEN at POS + 1, chrM at
  POS), so an insertion or a breakend spanned bases after its POS there, while the uploads put
  it where VCF 4.4 does (``structural_variant_end``): the same call had two ids. Every reader
  now ends it where VCF 4.4 does (``record_end``).
* **Mates.** The uploads read a mate only for an SVTYPE written ``BND``, so a ``bnd``, and a
  TRA written in breakend notation, had none; the chrM and HiFiCNV readers read none at all;
  and a TRA naming its mate in INFO/CHR2 and END (as Sniffles 1 writes one) was stored with
  that END on its own chromosome, so the tracks and Circos drew its end there. Every reader
  now reads the mate by ``breakend_mate``, and such a record ends on its own breakpoint.
* **Lengths.** A translocation showed as 0 or 1 bp in the list (its END − POS); it now has no
  length. The length filters read a negative length without its sign, as the list writes a
  deletion's: an exact length of -5000 matched nothing.
* **A record whose extent is not stated.** A ``<DEL>``, ``<DUP>``, ``<INV>`` or ``<CNV>`` that
  states neither END nor SVLEN does not say how far it reaches. It was stored on its POS, as
  0 bp, and a gene or panel search missed the genes it spans; every reader now leaves it out
  and counts it (the owner's decision, 2026-10-27).

The readers are driven directly; ``_structural_variant_out`` is the list's row. The upload is
read through ``upload_structural_variant_file`` with the SV storage and the gene table faked
(``_sv_fakes``), as in test_sv_upload_sample_column. ``e2e/test_e2e_sv_upload_vcf44_records.py``
uploads through the API against Postgres and ClickHouse and searches by gene, region and type.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services import variant_upload_service
from backend.app.services.clickhouse_variant_ids import build_structural_variant_id
from backend.app.services.clickhouse_variant_queries import _structural_variant_out
from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord
from backend.app.services.family_metadata_context import SampleMetadataContext
from backend.app.services.family_package_common import ParsedPed, PedMember
from backend.app.services.family_package_variants import (
    _iter_cnv_structural_records,
    _iter_mito_sv_records,
    _iter_needlr_per_sample_records,
)
from backend.app.services.family_variant_filters import structural_variant_length_filters_match
from backend.app.services.structural_variant_ingest import (
    ParsedStructuralVariant,
    SkippedRecords,
    is_breakend_alt,
    iter_structural_variant_records,
    structural_variant_end,
    sv_type_from_alt,
)
from backend.tests._sv_fakes import SvStore, UploadSession, install_sv_upload_fakes, upload_family_context

POS = 30000
_UPLOAD_FORMATS = ["sniffles", "spectre"]
_UPLOAD_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
_PACKAGE_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
_NEEDLR_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
_MATE = ("5", 3_000_000)


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


def _uploaded(alt: str, info: str, *, ref: str = "N", chrom: str = "chr1", pos: int = 20000) -> ParsedStructuralVariant:
    text = f"{_UPLOAD_HEADER}{chrom}\t{pos}\tSV1\t{ref}\t{alt}\t60\tPASS\t{info}\tGT\t0/1\n"
    (record,) = iter_structural_variant_records(text, "sniffles")
    return record


def _uploaded_id(record: ParsedStructuralVariant) -> str:
    return build_structural_variant_id(
        record.chrom,
        record.start,
        record.end,
        record.svtype,
        remote_chr=record.remote_chr,
        remote_start=record.remote_start,
        remote_end=record.remote_end,
    )


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


def _needlr(alt: str, info: str, *, ref: str = "N", chrom: str = "chr1", pos: int = 20000) -> StructuralVariantRecord:
    line = f"{chrom}\t{pos}\tSV1\t{ref}\t{alt}\t.\t.\t{info}Query_ID=PROBAND_sv_phased;Genotype=0/1;Alt_Reads=8\n"
    (record,) = _iter_needlr_per_sample_records(
        [("PROBAND", _NEEDLR_HEADER + line)], ped=_proband_ped(), sample_contexts=_proband_contexts()
    )
    return record


def _hificnv(alt: str, info: str, *, ref: str = "N", pos: int = 20000) -> StructuralVariantRecord:
    line = f"chr1\t{pos}\t.\t{ref}\t{alt}\t20\tPASS\t{info}\tGT:CN\t0/1:1\n"
    (record,) = _iter_cnv_structural_records(_PACKAGE_HEADER + line, sample_id="PROBAND")
    return record


def _chrm(alt: str, info: str, *, ref: str = "N", pos: int = 8470) -> StructuralVariantRecord:
    line = f"chrM\t{pos}\tSniffles2.1M0\t{ref}\t{alt}\t60\tPASS\t{info}VAF=0.35\tGT:DV\t0/1:140\n"
    (record,) = _iter_mito_sv_records(_PACKAGE_HEADER + line, sample_id="PROBAND")
    return record


def _mate(record) -> tuple[str | None, int | None]:
    return record.remote_chr, record.remote_start


# --- END: the uploads (CLIN-33) ---------------------------------------------------------------


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


# --- END: the package readers end it where an upload does (CLIN-39) -----------------------------


@pytest.mark.parametrize(
    ("ref", "alt", "info", "end"),
    [
        # An insertion and a breakend span no reference base, whatever SVLEN says.
        pytest.param("N", "<INS>", "SVTYPE=INS;SVLEN=300;", 20000, id="symbolic-insertion"),
        pytest.param("A", "A" + "C" * 50, "SVTYPE=INS;SVLEN=50;", 20000, id="sequence-insertion"),
        pytest.param("N", "N]chr5:3000000]", "SVTYPE=BND;", 20000, id="breakend"),
        # A deletion over its SVLEN, as before. (One whose extent is not stated is left out:
        # test_every_package_reader_leaves_out_a_record_whose_extent_is_not_stated.)
        pytest.param("N", "<DEL>", "SVTYPE=DEL;SVLEN=-5000;", 25000, id="symbolic-deletion"),
    ],
)
def test_needlr_ends_a_record_without_end_where_an_upload_does(ref: str, alt: str, info: str, end: int) -> None:
    record = _needlr(alt, info, ref=ref)
    uploaded = _uploaded(alt, info.rstrip(";") or ".", ref=ref)

    # Before: POS + SVLEN for any type, else POS + 1, and another id than the upload's.
    assert (record.end, uploaded.end) == (end, end)
    assert record.variant_id == _uploaded_id(uploaded)


@pytest.mark.parametrize(
    ("alt", "info", "end"),
    [
        pytest.param("<INS>", "SVTYPE=INS;SVLEN=300", 20000, id="insertion"),
        pytest.param("<DUP>", "SVTYPE=DUP;SVLEN=8000", 28000, id="duplication"),
    ],
)
def test_hificnv_ends_a_record_without_end_where_vcf_4_4_does(alt: str, info: str, end: int) -> None:
    # Before: POS + SVLEN for any type, else POS + 1.
    assert _hificnv(alt, info).end == end


def test_the_chrm_reader_ends_a_sequence_deletion_on_its_refs_last_base() -> None:
    # Before: on its POS, as if it deleted nothing.
    assert _chrm("A", "", ref="A" + "C" * 500).end == 8970


# --- A record whose extent is not stated is left out and counted (CLIN-39) ------------------------

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
        records = _iter_needlr_per_sample_records(
            [("PROBAND", _NEEDLR_HEADER + line)], ped=_proband_ped(), sample_contexts=_proband_contexts(), skipped=skipped
        )
    else:
        reader = _iter_cnv_structural_records if read == "hificnv" else _iter_mito_sv_records
        records = reader(_PACKAGE_HEADER + line, sample_id="PROBAND", skipped=skipped)

    # Before: NeedlR and HiFiCNV stored it at POS..POS+1, the chrM reader on its POS.
    assert (records, skipped.without_extent) == ([], 1)


# --- SVTYPE (CLIN-37) ---------------------------------------------------------------------

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


# The package readers take a missing type by the same rule.


def test_needlr_records_without_svtype_take_the_type_their_alt_states() -> None:
    calls = "Query_ID=PROBAND_sv_phased;Genotype=0/1;Alt_Reads=8"
    text = _NEEDLR_HEADER + "".join(
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
    text = _PACKAGE_HEADER + (
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


# --- Mates (CLIN-39) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alt", "info"),
    [
        pytest.param("N]chr5:3000000]", "SVTYPE=bnd", id="bnd-in-lower-case"),
        pytest.param("N]chr5:3000000]", "SVTYPE=Bnd", id="bnd-capitalised"),
        pytest.param("[chr5:3000000[N", "SVTYPE=TRA", id="tra-in-breakend-notation"),
        pytest.param("<TRA>", "SVTYPE=TRA;CHR2=chr5;END=3000000", id="tra-with-chr2"),
        pytest.param("<BND>", "SVTYPE=BND;CHR2=5;END=3000000", id="bnd-with-chr2"),
    ],
)
def test_an_upload_reads_a_breakends_mate_whatever_its_svtype_is_written(alt: str, info: str) -> None:
    record = _uploaded(alt, info)

    # Before: only SVTYPE=BND read the mate, and a TRA's END was taken as its own.
    assert _mate(record) == _MATE
    assert record.remote_end == 3_000_000
    assert (record.start, record.end) == (20000, 20000)


def test_a_tra_naming_its_mate_in_chr2_ends_on_its_own_breakpoint() -> None:
    record = _uploaded("<TRA>", "SVTYPE=TRA;CHR2=chr5;END=3000000")

    # Before: chr1 from 20,000 to 3,000,000, and no mate.
    assert _uploaded_id(record) == "1-20000-20000-TRA-5-3000000-3000000"
    # CHR2 naming its own chromosome leaves END its own: an intrachromosomal event.
    same = _uploaded("<INV>", "SVTYPE=INV;CHR2=chr1;END=30000")
    assert (same.end, _mate(same)) == (30000, (None, None))


def test_a_manual_tsv_row_reads_its_breakends_mate_in_any_capitals() -> None:
    text = "SV1\tchr1\t20000\t20000\tN\tN]chr5:3000000]\tbnd\t0/1\n"
    (record,) = iter_structural_variant_records(text, "manual")

    # Before: only a type written BND read the mate.
    assert (_mate(record), record.remote_end) == (_MATE, 3_000_000)


@pytest.mark.parametrize(
    ("read", "alt", "info"),
    [
        pytest.param(_needlr, "<TRA>", "SVTYPE=TRA;CHR2=chr5;END=3000000;", id="needlr-tra"),
        pytest.param(_hificnv, "N]chr5:3000000]", "SVTYPE=BND", id="hificnv-breakend"),
        pytest.param(_chrm, "N]chr5:3000000]", "SVTYPE=BND;", id="chrm-breakend"),
    ],
)
def test_every_package_reader_reads_a_breakends_mate(read, alt: str, info: str) -> None:
    record = read(alt, info)

    # Before: the chrM and HiFiCNV readers read no mate, and NeedlR none from CHR2.
    assert _mate(record) == _MATE
    assert record.remote_end == 3_000_000
    assert record.end == record.start
    assert "5-3000000-3000000" in record.variant_id


# --- Lengths (CLIN-39) ---------------------------------------------------------------------------


def _row(sv_type: str, start: int, end: int, sv_len: int | None) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=f"1-{start}-{end}-{sv_type}---",
        chr="1",
        start=start,
        end=end,
        sv_type=sv_type,
        source="sniffles",
        remote_chr="5" if sv_type in {"BND", "TRA"} else None,
        remote_start=3_000_000 if sv_type in {"BND", "TRA"} else None,
        remote_end=3_000_000 if sv_type in {"BND", "TRA"} else None,
        sv_len=sv_len,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[StructuralVariantCall(sample="S1", gt="0/1", qual=None, read_support=None, filter=None)],
    )


@pytest.mark.parametrize(
    ("row", "length"),
    [
        # Before: 0 or 1 bp, its END − POS.
        pytest.param(_row("BND", 20000, 20000, None), None, id="breakend"),
        pytest.param(_row("TRA", 20000, 20001, None), None, id="tra"),
        pytest.param(_row("bnd", 20000, 20000, 1), None, id="breakend-with-svlen"),
        pytest.param(_row("DEL", 20000, 25000, -5000), -5000, id="deletion"),
        pytest.param(_row("INV", 20000, 22000, None), 2000, id="inversion-without-svlen"),
    ],
)
def test_a_translocation_has_no_length_in_the_list(row: StructuralVariantRecord, length: int | None) -> None:
    assert _structural_variant_out(row, selected_samples=[]).length == length


@pytest.mark.parametrize(
    ("length", "min_length", "passes"),
    [
        # The list writes the deletion -5,000 bp: copied from it, the length finds it.
        # Before: a negative exact length matched nothing.
        pytest.param(-5000, None, True, id="exact-negative"),
        pytest.param(5000, None, True, id="exact"),
        pytest.param(-4999, None, False, id="exact-other"),
        pytest.param(None, -5000, True, id="minimum-negative"),
        pytest.param(None, -5001, False, id="minimum-negative-above"),
    ],
)
def test_the_length_filters_read_a_length_without_its_sign(length, min_length, passes: bool) -> None:
    assert (
        structural_variant_length_filters_match(
            sv_type="DEL", sv_len=-5000, start=20000, end=25000, length=length, min_length=min_length
        )
        is passes
    )


# --- The upload: the genes a record spans, and what it left out (CLIN-33, CLIN-39) -----------------

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


async def _upload_records(monkeypatch: pytest.MonkeyPatch, *records: str) -> tuple[dict[str, Any], SvStore]:
    """PROBAND's Sniffles VCF of ``records`` through the upload; the family stores no SV yet."""
    store = install_sv_upload_fakes(monkeypatch, SvStore(), genes=_GENES)
    result = await variant_upload_service.upload_structural_variant_file(
        UploadSession(),  # type: ignore[arg-type]
        family_context=upload_family_context({"PROBAND": "uuid-proband"}),
        sample_context=_proband_contexts()["PROBAND"],
        file=UploadFile(file=BytesIO(_vcf(*records).encode()), filename="PROBAND.sniffles.vcf"),
        overwrite=False,
        format_hint="sniffles",
    )
    return result, store


@pytest.mark.asyncio
async def test_an_uploaded_deletion_without_end_is_listed_with_the_genes_it_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, store = await _upload_records(
        monkeypatch,
        _record("N", "<DEL>", "SVLEN=1000;SUPPORT=12"),
        _record("N", "N]5:3000000]", "SUPPORT=8", pos=60000),
        _record("N", "<INS>", "SVLEN=5000;SUPPORT=9", pos=80000),
    )

    assert (result["processed"], result["created"]) == (3, 3)
    stored = {
        row.record.sv_type: (row.record.start, row.record.end, row.record.gene_symbols, row.record.remote_chr)
        for row in store.written()
    }
    assert stored == {
        # Before: 1:30000-30000, listed with AT_POS alone.
        "DEL": (30000, 31000, ["AT_POS", "SPANNED"], None),
        # Before: of type "N]5:3000000]", without its mate.
        "BND": (60000, 60000, [], "5"),
        # An insertion spans no reference base: the gene it lands in, not the bases after it.
        "INS": (80000, 80000, ["INS_HOST"], None),
    }


@pytest.mark.asyncio
async def test_an_upload_says_how_many_records_it_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    result, store = await _upload_records(
        monkeypatch,
        _record("N", "<DEL>", "SVTYPE=DEL;SUPPORT=12"),
        _record("N", "<DEL>", "SVTYPE=DEL;SVLEN=-1000;SUPPORT=12", pos=50000),
    )

    # Before: two SVs, one of them 0 bp at 1:30000, listed with the gene at its POS alone.
    assert (result["processed"], result["skipped_without_extent"]) == (1, 1)
    assert [(row.record.start, row.record.end) for row in store.written()] == [(50000, 51000)]


@pytest.mark.asyncio
async def test_an_upload_of_records_whose_extent_is_never_stated_is_refused_saying_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _upload_records(monkeypatch, _record("N", "<DEL>", "SVTYPE=DEL;SUPPORT=12"))

    assert refused.value.status_code == 400
    assert refused.value.detail == (
        "No valid structural-variant records found: 1 record(s) of a deletion, duplication, inversion "
        "or CNV state neither END nor SVLEN, so how far they reach is not known"
    )
