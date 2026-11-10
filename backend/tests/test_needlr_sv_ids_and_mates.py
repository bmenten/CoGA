"""A NeedlR SV is named by its coordinates, never by the record's ID (CLIN-43), and a NeedlR
breakend is stored with its mate, read from its ALT as the uploads read it (CLIN-40).

Both are the package's NeedlR reader (``family_package_variants._needlr_records``, through the
family VCF reader and the per-sample reader), and what makes one NeedlR SV: its id and, for a
breakend, its mate, which the id holds.

**The id (CLIN-43).** A caller numbers its calls per run (Sniffles2 writes
``Sniffles2.DEL.1S0``), so two samples' per-sample files, or two runs in one family VCF, give
one ID to different calls. A NeedlR SV's id is the one an uploaded SV's call of the same event
gets: its chromosome, start, end and type, and a breakend's mate
(``build_structural_variant_id``), whatever the record's ID, which stays in the SV's annotation.
The lines merge by that id:

* two different calls are two SVs, at their own positions and with their own calls, whatever
  their IDs, through the per-sample reader and the family VCF reader;
* one event -- the same coordinates, type, mate, alleles and length -- called in two samples'
  files is one SV with both samples' calls, each sample keeping its own call over the no-call
  or genotype another sample's line gives it as a parent, whichever file comes first;
* calls of one position, type and mate with other alleles, or a symbolic allele of another
  length, keep the discriminator a clash gets;
* a line repeated in the family VCF still merges into one SV.

``e2e/test_e2e_needlr_coordinate_ids.py`` imports such per-sample files through the package
import into Postgres and ClickHouse and reads the SVs back through the API.

**The mate (CLIN-40).** A breakend's ALT holds its mate in brackets, and the NeedlR reader reads
it as the Sniffles and manual uploads do (``parse_bnd_alt``), so a NeedlR translocation names its
partner in the SV list (its *Remote chr* and *Remote start*, the report's remote breakpoint, the
circos link), and a search by the mate's position (``remote_chr``, ``remote_start``) finds it.

A NeedlR record is a breakend when its type is BND, in any capitals, or its ALT is written in
breakend notation (``is_breakend_alt``), and a breakend's mate is read from its ALT as an upload
reads it, with ``remote_end`` equal to ``remote_start``: through the family VCF reader and the
per-sample reader, in the four bracket forms, with SVTYPE BND, ``bnd`` or TRA, and without one
(such a record is typed BND). A single breakend (``G.``) names no mate, and a record that is no
breakend keeps none. Records that merge by id keep their mate. A breakend's id holds its mate
(CLIN-43), as an uploaded breakend's does, so two breakends on one position with other mates
have their own ids. A record without END ends where VCF 4.4 puts it (CLIN-39), as an upload's
does: a breakend, an insertion and a record whose extent is not stated on its POS, so a
breakend's id is an uploaded breakend's.

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
from backend.app.services.hash_chain import canonical_hash
from backend.app.services.structural_variant_evidence import structural_evidence
from backend.app.services.structural_variant_ingest import iter_structural_variant_records

_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
_SAMPLES = ("PROBAND", "SIBLING", "FATHER", "MOTHER")


def _ped() -> ParsedPed:
    def member(iid: str, pid: str, mid: str, sex: str, line_no: int) -> PedMember:
        return PedMember(
            family_id="FAM", iid=iid, pid=pid, mid=mid, sex=sex, phen="0", line_no=line_no, clinical_status="unknown"
        )

    members = [
        member("PROBAND", "FATHER", "MOTHER", "1", 1),
        member("SIBLING", "FATHER", "MOTHER", "2", 2),
        member("FATHER", "0", "0", "1", 3),
        member("MOTHER", "0", "0", "2", 4),
    ]
    return ParsedPed(family_ids=["FAM"], members=members, sample_ids=list(_SAMPLES), text="")


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
        for sample_id in _SAMPLES
    }


def _family_vcf(*lines: str) -> list[StructuralVariantRecord]:
    return _iter_needlr_structural_records(_HEADER + "".join(lines), ped=_ped(), sample_contexts=_contexts())


def _per_sample(*files: tuple[str, str]) -> list[StructuralVariantRecord]:
    """One NeedlR VCF per sample, ``(sample, its lines)``, in that order."""
    return _iter_needlr_per_sample_records(
        [(sample_id, _HEADER + lines) for sample_id, lines in files], ped=_ped(), sample_contexts=_contexts()
    )


def _proband_file(*lines: str) -> list[StructuralVariantRecord]:
    return _per_sample(("PROBAND", "".join(lines)))


# The two readers, each given the lines of one VCF (the per-sample reader: PROBAND's file).
_READERS: dict[str, Callable[..., list[StructuralVariantRecord]]] = {
    "family-vcf": _family_vcf,
    "per-sample": _proband_file,
}


# =============================================================================================
# The id: the call's coordinates and type, never the record's ID (CLIN-43)
# =============================================================================================

# Sniffles2 numbers its calls per run: two runs give their first deletion one ID.
_SHARED_ID = "Sniffles2.DEL.1S0"


def _line(
    pos: int,
    *,
    record_id: str = _SHARED_ID,
    ref: str = "N",
    alt: str = "<DEL>",
    info: str = "",
    query: str = "PROBAND",
    calls: str = "Genotype=0/1;Alt_Reads=9",
) -> str:
    """One NeedlR record on chr1: ``info`` before the query and its calls, as NeedlR writes
    them (``Query_ID`` after the caller's input, ``<sample>_sv_phased``)."""
    return f"chr1\t{pos}\t{record_id}\t{ref}\t{alt}\t60\tPASS\t{info}Query_ID={query}_sv_phased;{calls}\n"


def _deletion(pos: int, end: int, **kwargs: str) -> str:
    return _line(pos, info=f"SVTYPE=DEL;SVLEN=-{end - pos};END={end};", **kwargs)


def _calls(record: StructuralVariantRecord) -> dict[str, tuple[str, int | None]]:
    return {call.sample: (call.gt, call.read_support) for call in record.calls}


def _svs(records: list[StructuralVariantRecord]) -> dict[str, tuple[int, int, dict[str, str]]]:
    return {
        record.variant_id: (record.start, record.end, {call.sample: call.gt for call in record.calls})
        for record in records
    }


def _record_ids(record: StructuralVariantRecord) -> list[str | None]:
    """The record ID each of the SV's annotations keeps (a line without one keeps none)."""
    return [annotation.get("id") for annotation in record.annotations]


# --- different events never merge, whatever their IDs ---------------------------------------


@pytest.mark.parametrize("mother_first", [True, False], ids=["mother-first", "father-first"])
def test_two_deletions_sharing_an_id_in_two_samples_files_stay_two_svs(mother_first: bool) -> None:
    mother = ("MOTHER", _deletion(7000, 12000, query="MOTHER", calls="Genotype=0/1;Alt_Reads=12"))
    father = ("FATHER", _deletion(90000, 96000, query="FATHER", calls="Genotype=1/1;Alt_Reads=20"))

    records = _per_sample(*((mother, father) if mother_first else (father, mother)))

    # Two SVs, each at its own deletion with its own sample's call, whichever file comes first.
    assert _svs(records) == {
        "1-7000-12000-DEL---": (7000, 12000, {"MOTHER": "0/1"}),
        "1-90000-96000-DEL---": (90000, 96000, {"FATHER": "1/1"}),
    }
    # The SV list serves each at its own position, with its own sample's call.
    served = {
        variant.id: (variant.start, variant.end, [(g.sample, g.gt, g.read_support) for g in variant.genotypes])
        for variant in (_structural_variant_out(record, selected_samples=[]) for record in records)
    }
    assert served == {
        "1-7000-12000-DEL---": (7000, 12000, [("MOTHER", "0/1", 12)]),
        "1-90000-96000-DEL---": (90000, 96000, [("FATHER", "1/1", 20)]),
    }


def test_two_deletions_sharing_an_id_in_the_family_vcf_stay_two_svs() -> None:
    records = _family_vcf(
        _deletion(
            7000,
            12000,
            query="PROBAND",
            calls="Genotype=0/1;Alt_Reads=18;Maternal_GT=0/0;Paternal_GT=0/1;Paternal_Alt_Reads=15",
        ),
        _deletion(90000, 96000, query="MOTHER", calls="Genotype=0/1;Alt_Reads=11"),
    )

    # The mother's deletion at 90000 is an SV of its own, and the 0/0 the proband's line gives
    # her at 7000 stays.
    assert _svs(records) == {
        "1-7000-12000-DEL---": (7000, 12000, {"FATHER": "0/1", "MOTHER": "0/0", "PROBAND": "0/1"}),
        "1-90000-96000-DEL---": (90000, 96000, {"MOTHER": "0/1"}),
    }
    assert [_record_ids(record) for record in records] == [[_SHARED_ID], [_SHARED_ID]]


# --- one event is one SV with every call ---------------------------------------------------


@pytest.mark.parametrize(
    ("mother_id", "proband_id"),
    [("Sniffles2.DEL.4S0", "Sniffles2.DEL.9S0"), (_SHARED_ID, _SHARED_ID), (".", ".")],
    ids=["other-ids", "one-id", "no-id"],
)
@pytest.mark.parametrize("mother_first", [True, False], ids=["mother-first", "proband-first"])
def test_one_event_in_two_samples_files_is_one_sv_with_both_calls(
    mother_id: str, proband_id: str, mother_first: bool
) -> None:
    mother = ("MOTHER", _deletion(7000, 12000, record_id=mother_id, query="MOTHER", calls="Genotype=0/1;Alt_Reads=12"))
    # Called without the parents: the proband's line names neither parent's genotype.
    proband = (
        "PROBAND",
        _deletion(7000, 12000, record_id=proband_id, query="PROBAND", calls="Genotype=0/1;Alt_Reads=18"),
    )

    (record,) = _per_sample(*((mother, proband) if mother_first else (proband, mother)))

    assert record.variant_id == "1-7000-12000-DEL---"
    # One SV whatever the IDs, the mother keeping her own call over the no-call the proband's
    # line gives her, whichever file comes first.
    assert _calls(record) == {"FATHER": ("./.", None), "MOTHER": ("0/1", 12), "PROBAND": ("0/1", 18)}
    # Each file's record ID stays in its annotation.
    assert sorted(_record_ids(record), key=str) == sorted(
        (None if record_id == "." else record_id for record_id in (mother_id, proband_id)), key=str
    )


@pytest.mark.parametrize("mother_first", [True, False], ids=["mother-first", "proband-first"])
def test_a_samples_own_call_is_kept_over_the_genotype_another_samples_line_gives_it(mother_first: bool) -> None:
    mother = (
        "MOTHER",
        _deletion(7000, 12000, record_id="Sniffles2.DEL.4S0", query="MOTHER", calls="Genotype=0/1;Alt_Reads=12"),
    )
    # NeedlR read the mother's reads at the proband's deletion and found none of it.
    proband = (
        "PROBAND",
        _deletion(
            7000,
            12000,
            record_id="Sniffles2.DEL.9S0",
            query="PROBAND",
            calls="Genotype=0/1;Alt_Reads=18;Maternal_GT=0/0;Maternal_Alt_Reads=0;Paternal_GT=0/1;Paternal_Alt_Reads=15",
        ),
    )

    (record,) = _per_sample(*((mother, proband) if mother_first else (proband, mother)))

    assert _calls(record) == {"FATHER": ("0/1", 15), "MOTHER": ("0/1", 12), "PROBAND": ("0/1", 18)}


@pytest.mark.parametrize("genotyped_first", [True, False], ids=["genotyped-first", "no-call-first"])
def test_a_parents_genotype_is_kept_over_the_no_call_a_siblings_line_gives_it(genotyped_first: bool) -> None:
    genotyped = _deletion(
        7000,
        12000,
        record_id="Sniffles2.DEL.2S0",
        query="PROBAND",
        calls="Genotype=0/1;Alt_Reads=18;Maternal_GT=0/1;Maternal_Alt_Reads=10",
    )
    not_genotyped = _deletion(
        7000, 12000, record_id="Sniffles2.DEL.5S1", query="SIBLING", calls="Genotype=0/1;Alt_Reads=14"
    )

    (record,) = _family_vcf(*((genotyped, not_genotyped) if genotyped_first else (not_genotyped, genotyped)))

    assert _calls(record) == {
        "FATHER": ("./.", None),
        "MOTHER": ("0/1", 10),
        "PROBAND": ("0/1", 18),
        "SIBLING": ("0/1", 14),
    }


def test_a_line_repeated_in_the_family_vcf_still_merges_into_one_sv() -> None:
    line = _deletion(
        7000, 12000, query="PROBAND", calls="Genotype=0/1;Alt_Reads=18;Maternal_GT=0/0;Paternal_GT=0/1"
    )

    (record,) = _family_vcf(line, line)

    assert record.variant_id == "1-7000-12000-DEL---"
    assert _calls(record) == {"FATHER": ("0/1", None), "MOTHER": ("0/0", None), "PROBAND": ("0/1", 18)}
    assert _record_ids(record) == [_SHARED_ID, _SHARED_ID]


# --- the discriminator for another event on one position -----------------------------------


def test_insertions_of_other_sequences_on_one_position_keep_the_discriminator() -> None:
    def insertion(sequence: str, record_id: str) -> str:
        return _line(20000, record_id=record_id, ref="A", alt=f"A{sequence}", info=f"SVTYPE=INS;SVLEN={len(sequence)};")

    records = _family_vcf(
        insertion("CGTACGT", "Sniffles2.INS.1S0"),
        insertion("GGTACCT", "Sniffles2.INS.1S0"),  # another insertion under the first one's ID
        insertion("TTTACGT", "."),
        insertion("CGTACGT", "Sniffles2.INS.7S2"),  # the first insertion again, under another ID
    )

    # Same position, type and length, other alleles: the first keeps the id, the others get
    # their length, then a count, as a clash does. The first's alleles again merge with it.
    # An insertion without END ends on its POS, as VCF 4.4 and the uploads put it (CLIN-39).
    assert [(record.variant_id, record.annotations[0]["alt"]) for record in records] == [
        ("1-20000-20000-INS---", "ACGTACGT"),
        ("1-20000-20000-INS----7", "AGGTACCT"),
        ("1-20000-20000-INS----7.2", "ATTTACGT"),
    ]
    assert _record_ids(records[0]) == ["Sniffles2.INS.1S0", "Sniffles2.INS.7S2"]


def test_symbolic_insertions_of_other_lengths_on_one_position_are_two_svs() -> None:
    # A symbolic allele does not hold its length: one ALT, one END, two insertions.
    first, second = _family_vcf(
        _line(20000, record_id="Sniffles2.INS.3S0", alt="<INS>", info="SVTYPE=INS;SVLEN=300;END=20000;"),
        _line(
            20000,
            record_id="Sniffles2.INS.3S0",
            alt="<INS>",
            info="SVTYPE=INS;SVLEN=500;END=20000;",
            query="MOTHER",
            calls="Genotype=0/1;Alt_Reads=6",
        ),
    )

    # Two SVs: the proband's 300 bp insertion and the mother's 500 bp one.
    assert (first.variant_id, first.sv_len, _calls(first)["PROBAND"]) == ("1-20000-20000-INS---", 300, ("0/1", 9))
    assert (second.variant_id, second.sv_len, _calls(second)) == (
        "1-20000-20000-INS----500",
        500,
        {"MOTHER": ("0/1", 6)},
    )


def test_breakends_to_one_mate_in_other_orientations_keep_the_discriminator() -> None:
    first, second = _family_vcf(
        _line(20000, record_id=".", alt="N]chr5:3000000]", info="SVTYPE=BND;"),
        _line(20000, record_id=".", alt="[chr5:3000000[N", info="SVTYPE=BND;"),
    )

    # A breakend without END ends on its POS (CLIN-39).
    assert first.variant_id == "1-20000-20000-BND-5-3000000-3000000"
    assert second.variant_id == "1-20000-20000-BND-5-3000000-3000000-15"


# --- the id: the call's coordinates and type ------------------------------------------------


@pytest.mark.parametrize("reader", sorted(_READERS))
@pytest.mark.parametrize("record_id", ["SVDEL1", _SHARED_ID, "1-90000-96000-DEL---", ".", ""])
def test_the_id_is_the_calls_coordinates_and_type_whatever_its_record_id(reader: str, record_id: str) -> None:
    (record,) = _READERS[reader](_deletion(7000, 12000, record_id=record_id))

    # Never the record's ID -- not even one that names another SV's position.
    assert record.variant_id == "1-7000-12000-DEL---"
    assert record.variant_id == build_structural_variant_id("chr1", 7000, 12000, "DEL")
    assert (record.chr, record.start, record.end) == ("1", 7000, 12000)
    # The record's ID stays in its annotation, as written; a record without one has none.
    if record_id in {".", ""}:
        assert "id" not in record.annotations[0]
    else:
        assert record.annotations[0]["id"] == record_id


def test_a_record_without_an_id_keeps_its_id_and_its_annotation() -> None:
    # Named by its coordinates, its id and annotation exactly these, so a review stays attached
    # and a classification's frozen evidence does not drift.
    info = "SVTYPE=DEL;SVLEN=-5000;END=12000;Genes=BRCA2;"
    (record,) = _family_vcf(_line(7000, record_id=".", info=info))

    assert record.variant_id == "1-7000-12000-DEL---"
    stored_before = {
        "source": "needlr",
        "ref": "N",
        "alt": "<DEL>",
        "info": {
            "SVTYPE": "DEL",
            "SVLEN": "-5000",
            "END": "12000",
            "Genes": "BRCA2",
            "Query_ID": "PROBAND_sv_phased",
            "Genotype": "0/1",
            "Alt_Reads": "9",
        },
    }
    assert record.annotations == [stored_before]
    assert structural_evidence(record)["annotation_hash"] == canonical_hash([stored_before])


@pytest.mark.parametrize(
    ("needlr", "sniffles"),
    [
        pytest.param(
            _deletion(7000, 12000),
            "1\t7000\tSniffles2.DEL.4\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-5000;END=12000\tGT\t0/1",
            id="deletion",
        ),
        pytest.param(
            _line(20000, alt="N]chr5:3000000]", info="SVTYPE=BND;END=20000;"),
            "1\t20000\tSniffles2.BND.2\tN\tN]chr5:3000000]\t40\tPASS\tSVTYPE=BND\tGT\t0/1",
            id="breakend",
        ),
    ],
)
def test_the_same_event_uploaded_from_sniffles_has_the_same_id(needlr: str, sniffles: str) -> None:
    (record,) = _family_vcf(needlr)
    (parsed,) = iter_structural_variant_records(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n" + sniffles + "\n",
        "sniffles",
    )
    # As the SV upload names its call (variant_upload_service.upload_structural_variant_file):
    # one event, one id, and so one review of it whichever caller's row it is read on.
    uploaded_id = build_structural_variant_id(
        parsed.chrom,
        parsed.start,
        parsed.end,
        parsed.svtype,
        remote_chr=parsed.remote_chr,
        remote_start=parsed.remote_start,
        remote_end=parsed.remote_end,
    )
    assert record.variant_id == uploaded_id


# =============================================================================================
# A breakend's mate, read from its ALT as an upload reads it (CLIN-40)
# =============================================================================================

# The query's call and its parents' calls, as NeedlR writes them.
_CALLS = "Query_ID=PROBAND_sv_phased;Genotype=0/1;Alt_Reads=7;Maternal_GT=0/0;Paternal_GT=0/1"
_MATE = ("5", 3_000_000, 3_000_000)
_NO_MATE = (None, None, None)

# The four bracket forms of VCF breakend notation, each with its mate on chr5:3000000.
_BRACKET_FORMS = ["N]chr5:3000000]", "]chr5:3000000]N", "N[chr5:3000000[", "[chr5:3000000[N"]


def _breakend_line(
    alt: str,
    info: str = "SVTYPE=BND;",
    *,
    record_id: str = "SV1",
    pos: int = 20000,
    ref: str = "N",
) -> str:
    """One NeedlR record on chr1 (neither SVLEN nor END unless ``info`` writes them)."""
    return f"chr1\t{pos}\t{record_id}\t{ref}\t{alt}\t30\tPASS\t{info}{_CALLS}\n"


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


@pytest.mark.parametrize("reader", sorted(_READERS))
@pytest.mark.parametrize("alt", _BRACKET_FORMS)
def test_a_breakend_has_the_mate_its_alt_names_as_an_upload_reads_it(reader: str, alt: str) -> None:
    # The mate the ALT names, in each bracket form.
    (record,) = _READERS[reader](_breakend_line(alt))
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
    (record,) = _family_vcf(_breakend_line(alt))
    assert _mate(record) == mate


@pytest.mark.parametrize(
    ("info", "sv_type"),
    [
        ("", "BND"),  # no SVTYPE: typed by its ALT
        ("SVTYPE=.;", "BND"),
        ("SVTYPE=bnd;", "bnd"),
        ("SVTYPE=TRA;", "TRA"),  # a translocation written with its mate in brackets
    ],
)
def test_a_breakend_has_its_mate_whatever_its_svtype_says(info: str, sv_type: str) -> None:
    # The mate, whatever SVTYPE says; a record without SVTYPE is a BND with one.
    (record,) = _family_vcf(_breakend_line("N]chr5:3000000]", info))
    assert (record.sv_type, *_mate(record)) == (sv_type, *_MATE)


@pytest.mark.parametrize(
    ("ref", "alt", "info", "sv_type", "end", "sv_len"),
    [
        # A single breakend names no mate.
        pytest.param("G", "G.", "", "BND", 20000, None, id="single-breakend-after"),
        pytest.param("G", ".G", "SVTYPE=BND;", "BND", 20000, None, id="single-breakend-before"),
        # No breakend: unchanged.
        pytest.param("N", "<DEL>", "SVTYPE=DEL;SVLEN=-5000;END=25000;", "DEL", 25000, -5000, id="DEL"),
        pytest.param("N", "<DUP:TANDEM>", "SVTYPE=DUP;SVLEN=500;End_Pos=20500;", "DUP", 20500, 500, id="DUP:TANDEM"),
        # An insertion spans no reference base (CLIN-39).
        pytest.param("N", "<INS>", "SVTYPE=INS;SVLEN=300;", "INS", 20000, 300, id="INS"),
        # An inversion states how far it reaches (one that does not is left out, CLIN-39).
        pytest.param("N", "<INV>", "End_Pos=20500;", "INV", 20500, None, id="INV-without-SVTYPE"),
        # Without SVLEN, a sequence allele is stored with the length its alleles state (CLIN-41).
        pytest.param("A" + "C" * 60, "A", "End_Pos=20060;", "DEL", 20060, -60, id="sequence-deletion"),
    ],
)
def test_a_record_that_names_no_mate_keeps_none(
    ref: str, alt: str, info: str, sv_type: str, end: int, sv_len: int | None
) -> None:
    (record,) = _family_vcf(_breakend_line(alt, info, ref=ref))
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
    # Its coordinates, type and mate (CLIN-43), never the record's ID (SVBND1).
    with_id, without_id = _READERS[reader](
        _breakend_line("N]chr5:3000000]", record_id="SVBND1"),
        _breakend_line("N[chr7:4000000[", record_id=".", pos=30000),
    )
    assert with_id.variant_id == "1-20000-20000-BND-5-3000000-3000000"
    assert without_id.variant_id == "1-30000-30000-BND-7-4000000-4000000"
    assert without_id.variant_id == build_structural_variant_id(
        "chr1", 30000, 30000, "BND", remote_chr="chr7", remote_start=4_000_000, remote_end=4_000_000
    )
    # The id an upload gives the same breakend (CLIN-39: its END is its POS here).
    header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    (uploaded,) = iter_structural_variant_records(
        header + "chr1\t30000\tSV9\tN\tN[chr7:4000000[\t.\tPASS\tSVTYPE=BND\tGT\t0/1\n", "sniffles"
    )
    assert without_id.variant_id == build_structural_variant_id(
        uploaded.chrom,
        uploaded.start,
        uploaded.end,
        uploaded.svtype,
        remote_chr=uploaded.remote_chr,
        remote_start=uploaded.remote_start,
        remote_end=uploaded.remote_end,
    )


def test_two_breakends_on_one_position_keep_their_own_mates() -> None:
    first, second = _family_vcf(
        _breakend_line("N]chr5:3000000]", record_id="."),
        _breakend_line("N]chr7:4000000]", record_id="."),
    )
    # Their mates tell them apart: no discriminator.
    assert (first.variant_id, *_mate(first)) == ("1-20000-20000-BND-5-3000000-3000000", *_MATE)
    assert (second.variant_id, *_mate(second)) == (
        "1-20000-20000-BND-7-4000000-4000000",
        "7",
        4_000_000,
        4_000_000,
    )


@pytest.mark.parametrize("record_id", ["SV1", "."])
def test_a_breakend_two_samples_call_is_one_record_that_keeps_its_mate(record_id: str) -> None:
    # Each file's calls are its sample's; one breakend called in both, with the same alleles,
    # merges by id into one record.
    line = _breakend_line("N]chr5:3000000]", record_id=record_id)
    (record,) = _per_sample(("MOTHER", line), ("FATHER", line))
    assert [call.sample for call in record.calls] == ["FATHER", "MOTHER"]
    assert _mate(record) == _MATE


def test_the_family_vcf_merges_a_repeated_breakend_with_its_mate() -> None:
    (record,) = _family_vcf(_breakend_line("N]chr5:3000000]"), _breakend_line("N]chr5:3000000]"))
    assert len(record.annotations) == 2
    assert _mate(record) == _MATE


def test_the_list_shows_the_mate_and_a_search_by_it_finds_the_breakend() -> None:
    (record,) = _family_vcf(_breakend_line("N]chr5:3000000]"))

    variant = _structural_variant_out(record, selected_samples=[])
    assert (variant.remote_chr, variant.remote_start) == ("5", 3_000_000)

    def found(remote_chr: str | None = None, remote_start: int | None = None) -> bool:
        filters = StructuralVariantQueryFilters(
            page=1, page_size=100, remote_chr=remote_chr, remote_start=remote_start
        )
        return _structural_record_matches(record, filters, [], [])

    # A search by the mate finds it.
    assert found(remote_chr="5") is True
    assert found(remote_chr="chr5", remote_start=3_000_000) is True
    assert found(remote_chr="7") is False
    assert found(remote_start=3_000_001) is False

    # The evidence a CNV classification freezes names the mate too.
    evidence = structural_evidence(record)
    assert (evidence["remote_chrom"], evidence["remote_start"]) == ("5", 3_000_000)
