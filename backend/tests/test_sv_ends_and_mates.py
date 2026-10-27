"""Every SV reader ends a record and reads a breakend's mate by one rule (CLIN-39).

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

The readers are driven directly; ``_structural_variant_out`` is the list's row.
"""

from __future__ import annotations

import pytest

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
    iter_structural_variant_records,
)

_UPLOAD_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
_PACKAGE_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
_MATE = ("5", 3_000_000)


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


def _needlr(alt: str, info: str, *, ref: str = "N", chrom: str = "chr1", pos: int = 20000) -> StructuralVariantRecord:
    member = PedMember(
        family_id="FAM", iid="PROBAND", pid="0", mid="0", sex="1", phen="2", line_no=1, clinical_status="affected"
    )
    context = SampleMetadataContext(
        sample_uuid="uuid-proband",
        sample_id="PROBAND",
        family_uuid="family-uuid",
        family_id="FAM",
        sex="male",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    line = f"{chrom}\t{pos}\tSV1\t{ref}\t{alt}\t.\t.\t{info}Query_ID=PROBAND_sv_phased;Genotype=0/1;Alt_Reads=8\n"
    (record,) = _iter_needlr_per_sample_records(
        [("PROBAND", header + line)],
        ped=ParsedPed(family_ids=["FAM"], members=[member], sample_ids=["PROBAND"], text=""),
        sample_contexts={"PROBAND": context},
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


# --- Ends ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "alt", "info", "end"),
    [
        # An insertion and a breakend span no reference base, whatever SVLEN says.
        pytest.param("N", "<INS>", "SVTYPE=INS;SVLEN=300;", 20000, id="symbolic-insertion"),
        pytest.param("A", "A" + "C" * 50, "SVTYPE=INS;SVLEN=50;", 20000, id="sequence-insertion"),
        pytest.param("N", "N]chr5:3000000]", "SVTYPE=BND;", 20000, id="breakend"),
        # A deletion over its SVLEN, as before. (One whose extent is not stated is left out:
        # test_sv_copy_number_and_ids.py.)
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


# --- Mates ---------------------------------------------------------------------------------------


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


# --- Lengths -------------------------------------------------------------------------------------


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
