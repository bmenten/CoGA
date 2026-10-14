"""A NeedlR breakend is stored with its mate, read from its ALT as the uploads read it (CLIN-40).

The package's NeedlR reader (``family_package_variants._needlr_records``) stored every
breakend without a mate: ``remote_chr`` and ``remote_start`` were always None. A NeedlR
translocation then named no partner in the SV list (its *Remote chr* and *Remote start*, the
report's remote breakpoint, the circos link), and a search by the mate's position
(``remote_chr``, ``remote_start``) never found it, while its ALT, which holds the mate in
brackets, was kept in the annotation. The Sniffles and manual uploads read the mate from the ALT
(``parse_bnd_alt``).

Now a NeedlR record is a breakend when its type is BND, in any capitals, or its ALT is written in
breakend notation (``is_breakend_alt``), and a breakend's mate is read from its ALT as an upload
reads it, with ``remote_end`` equal to ``remote_start``: through the family VCF reader and the
per-sample reader, in the four bracket forms, with SVTYPE BND, ``bnd`` or TRA, and without one
(#831 types such a record BND). A single breakend (``G.``) names no mate, and a record that is no
breakend keeps none. Records that merge by id keep their mate. Since CLIN-43 a NeedlR SV's id is
its coordinates and type, never the record's ID (test_needlr_coordinate_ids.py): a breakend's id
holds its mate, as an uploaded breakend's does, so two breakends on one position with other
mates have their own ids.

``e2e/test_e2e_api_contract.py`` imports the golden trio's NeedlR breakend into Postgres and
ClickHouse, reads its mate back and finds it by a search on the mate's position.
"""

from __future__ import annotations

from typing import Callable

import pytest

from backend.app.services.clickhouse_variant_ids import build_structural_variant_id
from backend.app.services.clickhouse_variant_queries import (
    _structural_record_matches,
    _structural_variant_out,
)
from backend.app.services.clickhouse_variant_records import StructuralVariantRecord
from backend.app.services.family_metadata_context import SampleMetadataContext
from backend.app.services.family_package_common import ParsedPed, PedMember
from backend.app.services.family_package_variants import (
    _iter_needlr_per_sample_records,
    _iter_needlr_structural_records,
)
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters
from backend.app.services.structural_variant_evidence import structural_evidence
from backend.app.services.structural_variant_ingest import iter_structural_variant_records

_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
# The query's call and its parents' calls, as NeedlR writes them.
_CALLS = "Query_ID=PROBAND_sv_phased;Genotype=0/1;Alt_Reads=7;Maternal_GT=0/0;Paternal_GT=0/1"
_MATE = ("5", 3_000_000, 3_000_000)
_NO_MATE = (None, None, None)

# The four bracket forms of VCF breakend notation, each with its mate on chr5:3000000.
_BRACKET_FORMS = ["N]chr5:3000000]", "]chr5:3000000]N", "N[chr5:3000000[", "[chr5:3000000[N"]


def _ped() -> ParsedPed:
    def member(iid: str, pid: str, mid: str, sex: str, line_no: int) -> PedMember:
        return PedMember(
            family_id="FAM", iid=iid, pid=pid, mid=mid, sex=sex, phen="0", line_no=line_no, clinical_status="unknown"
        )

    members = [
        member("PROBAND", "FATHER", "MOTHER", "1", 1),
        member("FATHER", "0", "0", "1", 2),
        member("MOTHER", "0", "0", "2", 3),
    ]
    return ParsedPed(family_ids=["FAM"], members=members, sample_ids=["PROBAND", "FATHER", "MOTHER"], text="")


def _contexts() -> dict[str, SampleMetadataContext]:
    return {
        sample_id: SampleMetadataContext(
            sample_uuid=f"uuid-{sample_id}",
            sample_id=sample_id,
            family_uuid="family-uuid",
            family_id="FAM",
            sex="und",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for sample_id in ("PROBAND", "FATHER", "MOTHER")
    }


def _line(
    alt: str,
    info: str = "SVTYPE=BND;",
    *,
    record_id: str = "SV1",
    pos: int = 20000,
    ref: str = "N",
) -> str:
    """One NeedlR record on chr1 (neither SVLEN nor END unless ``info`` writes them)."""
    return f"chr1\t{pos}\t{record_id}\t{ref}\t{alt}\t30\tPASS\t{info}{_CALLS}\n"


def _family_vcf(*lines: str) -> list[StructuralVariantRecord]:
    return _iter_needlr_structural_records(_HEADER + "".join(lines), ped=_ped(), sample_contexts=_contexts())


def _per_sample(*files: tuple[str, str]) -> list[StructuralVariantRecord]:
    return _iter_needlr_per_sample_records(
        [(sample_id, _HEADER + text) for sample_id, text in files], ped=_ped(), sample_contexts=_contexts()
    )


def _proband_file(*lines: str) -> list[StructuralVariantRecord]:
    return _per_sample(("PROBAND", "".join(lines)))


def _mate(record: StructuralVariantRecord) -> tuple[str | None, int | None, int | None]:
    return (record.remote_chr, record.remote_start, record.remote_end)


def _uploaded_mate(alt: str) -> tuple[str | None, int | None, int | None]:
    """The mate a Sniffles upload stores for a breakend with ``alt``."""
    text = (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n"
        f"1\t20000\tSniffles2.BND.1\tN\t{alt}\t30\tPASS\tSVTYPE=BND\tGT\t0/1\n"
    )
    (record,) = iter_structural_variant_records(text, "sniffles")
    return (record.remote_chr, record.remote_start, record.remote_end)


_READERS: dict[str, Callable[..., list[StructuralVariantRecord]]] = {
    "family-vcf": _family_vcf,
    "per-sample": _proband_file,
}


@pytest.mark.parametrize("reader", sorted(_READERS))
@pytest.mark.parametrize("alt", _BRACKET_FORMS)
def test_a_breakend_has_the_mate_its_alt_names_as_an_upload_reads_it(reader: str, alt: str) -> None:
    # Before: no mate, whatever the ALT.
    (record,) = _READERS[reader](_line(alt))
    assert (record.sv_type, *_mate(record)) == ("BND", *_MATE)
    assert _mate(record) == _uploaded_mate(alt)


@pytest.mark.parametrize(
    ("alt", "mate"),
    [
        ("N[5:3000000[", ("5", 3_000_000, 3_000_000)),
        ("]chrX:154000000]N", ("X", 154_000_000, 154_000_000)),
        ("[chr1:90000[N", ("1", 90_000, 90_000)),  # an intrachromosomal junction
    ],
)
def test_the_mate_s_chromosome_is_stored_without_its_chr(alt: str, mate: tuple[str, int, int]) -> None:
    (record,) = _family_vcf(_line(alt))
    assert _mate(record) == mate


@pytest.mark.parametrize(
    ("info", "sv_type"),
    [
        ("", "BND"),  # no SVTYPE: #831 types it by its ALT
        ("SVTYPE=.;", "BND"),
        ("SVTYPE=bnd;", "bnd"),
        ("SVTYPE=TRA;", "TRA"),  # a translocation written with its mate in brackets
    ],
)
def test_a_breakend_has_its_mate_whatever_its_svtype_says(info: str, sv_type: str) -> None:
    # Before: no mate; a record without SVTYPE was a BND (#831) without one.
    (record,) = _family_vcf(_line("N]chr5:3000000]", info))
    assert (record.sv_type, *_mate(record)) == (sv_type, *_MATE)


@pytest.mark.parametrize(
    ("ref", "alt", "info", "sv_type", "end", "sv_len"),
    [
        # A single breakend names no mate.
        pytest.param("G", "G.", "", "BND", 20001, None, id="single-breakend-after"),
        pytest.param("G", ".G", "SVTYPE=BND;", "BND", 20001, None, id="single-breakend-before"),
        # No breakend: unchanged.
        pytest.param("N", "<DEL>", "SVTYPE=DEL;SVLEN=-5000;END=25000;", "DEL", 25000, -5000, id="DEL"),
        pytest.param("N", "<DUP:TANDEM>", "SVTYPE=DUP;SVLEN=500;End_Pos=20500;", "DUP", 20500, 500, id="DUP:TANDEM"),
        pytest.param("N", "<INS>", "SVTYPE=INS;SVLEN=300;", "INS", 20300, 300, id="INS"),
        pytest.param("N", "<INV>", "", "INV", 20001, None, id="INV-without-SVTYPE"),
        # Without SVLEN, a sequence allele is stored with the length its alleles state (CLIN-41).
        pytest.param("A" + "C" * 60, "A", "End_Pos=20060;", "DEL", 20060, -60, id="sequence-deletion"),
    ],
)
def test_a_record_that_names_no_mate_keeps_none(
    ref: str, alt: str, info: str, sv_type: str, end: int, sv_len: int | None
) -> None:
    (record,) = _family_vcf(_line(alt, info, ref=ref))
    # The id its coordinates and type give it (CLIN-43): with no mate, none in it.
    assert (record.variant_id, record.chr, record.start, record.end, record.sv_type, record.sv_len) == (
        f"1-20000-{end}-{sv_type}---",
        "1",
        20000,
        end,
        sv_type,
        sv_len,
    )
    assert _mate(record) == _NO_MATE


@pytest.mark.parametrize("reader", sorted(_READERS))
def test_a_breakend_s_id_holds_its_mate_as_an_uploaded_breakend_s_does(reader: str) -> None:
    # Before CLIN-43: the record's ID (SVBND1), else its coordinates and type without the mate.
    with_id, without_id = _READERS[reader](
        _line("N]chr5:3000000]", record_id="SVBND1"),
        _line("N[chr7:4000000[", record_id=".", pos=30000),
    )
    assert with_id.variant_id == "1-20000-20001-BND-5-3000000-3000000"
    assert without_id.variant_id == "1-30000-30001-BND-7-4000000-4000000"
    assert without_id.variant_id == build_structural_variant_id(
        "chr1", 30000, 30001, "BND", remote_chr="chr7", remote_start=4_000_000, remote_end=4_000_000
    )


def test_two_breakends_on_one_position_keep_their_own_mates() -> None:
    first, second = _family_vcf(
        _line("N]chr5:3000000]", record_id="."),
        _line("N]chr7:4000000]", record_id="."),
    )
    # Their mates tell them apart: no discriminator (the length of its ALT, before CLIN-43).
    assert (first.variant_id, *_mate(first)) == ("1-20000-20001-BND-5-3000000-3000000", *_MATE)
    assert (second.variant_id, *_mate(second)) == (
        "1-20000-20001-BND-7-4000000-4000000",
        "7",
        4_000_000,
        4_000_000,
    )


@pytest.mark.parametrize("record_id", ["SV1", "."])
def test_a_breakend_two_samples_call_is_one_record_that_keeps_its_mate(record_id: str) -> None:
    # Each file's calls are its sample's; one breakend called in both, with the same alleles,
    # merges by id into one record.
    line = _line("N]chr5:3000000]", record_id=record_id)
    (record,) = _per_sample(("MOTHER", line), ("FATHER", line))
    assert [call.sample for call in record.calls] == ["FATHER", "MOTHER"]
    assert _mate(record) == _MATE


def test_the_family_vcf_merges_a_repeated_breakend_with_its_mate() -> None:
    (record,) = _family_vcf(_line("N]chr5:3000000]"), _line("N]chr5:3000000]"))
    assert len(record.annotations) == 2
    assert _mate(record) == _MATE


def test_the_list_shows_the_mate_and_a_search_by_it_finds_the_breakend() -> None:
    (record,) = _family_vcf(_line("N]chr5:3000000]"))

    variant = _structural_variant_out(record, selected_samples=[])
    assert (variant.remote_chr, variant.remote_start) == ("5", 3_000_000)

    def found(remote_chr: str | None = None, remote_start: int | None = None) -> bool:
        filters = StructuralVariantQueryFilters(
            page=1, page_size=100, remote_chr=remote_chr, remote_start=remote_start
        )
        return _structural_record_matches(record, filters, [], [])

    # Before: a search by the mate never found it.
    assert found(remote_chr="5") is True
    assert found(remote_chr="chr5", remote_start=3_000_000) is True
    assert found(remote_chr="7") is False
    assert found(remote_start=3_000_001) is False

    # The evidence a CNV classification freezes names the mate too.
    evidence = structural_evidence(record)
    assert (evidence["remote_chrom"], evidence["remote_start"]) == ("5", 3_000_000)
