"""A NeedlR SV is named by its coordinates, never by the record's ID (CLIN-43).

The package's NeedlR reader (``family_package_variants._needlr_records``) took a record's ID as
its SV id when it had one, and merged the records by that id; its clash check compared only the
alleles. A caller numbers its calls per run (Sniffles2 writes ``Sniffles2.DEL.1S0``), so two
samples' per-sample files, or two runs in one family VCF, give one ID to different calls: two
different deletions that shared an ID became one SV at the first one's position, the second
call attached to it and its own deletion lost.

Now a NeedlR SV's id is the one an uploaded SV's call of the same event gets: its chromosome,
start, end and type, and a breakend's mate (``build_structural_variant_id``), whatever the
record's ID, which stays in the SV's annotation. The lines merge by that id:

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
"""

from __future__ import annotations

from typing import Callable

import pytest

from backend.app.services.clickhouse_variant_ids import build_structural_variant_id
from backend.app.services.clickhouse_variant_queries import _structural_variant_out
from backend.app.services.clickhouse_variant_records import StructuralVariantRecord
from backend.app.services.family_metadata_context import SampleMetadataContext
from backend.app.services.family_package_common import ParsedPed, PedMember
from backend.app.services.family_package_variants import (
    _iter_needlr_per_sample_records,
    _iter_needlr_structural_records,
)
from backend.app.services.hash_chain import canonical_hash
from backend.app.services.structural_variant_evidence import structural_evidence
from backend.app.services.structural_variant_ingest import iter_structural_variant_records

_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
# Sniffles2 numbers its calls per run: two runs give their first deletion one ID.
_SHARED_ID = "Sniffles2.DEL.1S0"
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


def _family_vcf(*lines: str) -> list[StructuralVariantRecord]:
    return _iter_needlr_structural_records(_HEADER + "".join(lines), ped=_ped(), sample_contexts=_contexts())


def _per_sample(*files: tuple[str, str]) -> list[StructuralVariantRecord]:
    """One NeedlR VCF per sample, ``(sample, its lines)``, in that order."""
    return _iter_needlr_per_sample_records(
        [(sample_id, _HEADER + lines) for sample_id, lines in files], ped=_ped(), sample_contexts=_contexts()
    )


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


_READERS: dict[str, Callable[[str], list[StructuralVariantRecord]]] = {
    "family-vcf": lambda line: _family_vcf(line),
    "per-sample": lambda line: _per_sample(("PROBAND", line)),
}


# --- different events never merge, whatever their IDs ---------------------------------------


@pytest.mark.parametrize("mother_first", [True, False], ids=["mother-first", "father-first"])
def test_two_deletions_sharing_an_id_in_two_samples_files_stay_two_svs(mother_first: bool) -> None:
    mother = ("MOTHER", _deletion(7000, 12000, query="MOTHER", calls="Genotype=0/1;Alt_Reads=12"))
    father = ("FATHER", _deletion(90000, 96000, query="FATHER", calls="Genotype=1/1;Alt_Reads=20"))

    records = _per_sample(*((mother, father) if mother_first else (father, mother)))

    # Before: one SV, Sniffles2.DEL.1S0, at the first file's deletion with both samples'
    # calls; the other sample's deletion was lost.
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

    # Before: one SV at 7000, where the mother's deletion at 90000 replaced the 0/0 the
    # proband's line gave her; her own deletion was lost.
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
    # Before: two SVs when the IDs differed; under one ID (or none) with the mother's file
    # first, the no-call the proband's line gives her replaced her own call.
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
    # their length, then a count, as a clash always did. The first's alleles again merge with it.
    # An insertion without END ends on its POS, as VCF 4.4 and the uploads put it (CLIN-39;
    # before: POS + SVLEN).
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

    # Before: one SV of 300 bp holding the mother's call of her 500 bp insertion.
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

    # A breakend without END ends on its POS (CLIN-39; before: POS + 1).
    assert first.variant_id == "1-20000-20000-BND-5-3000000-3000000"
    assert second.variant_id == "1-20000-20000-BND-5-3000000-3000000-15"


# --- the id: the call's coordinates and type ------------------------------------------------


@pytest.mark.parametrize("reader", sorted(_READERS))
@pytest.mark.parametrize("record_id", ["SVDEL1", _SHARED_ID, "1-90000-96000-DEL---", ".", ""])
def test_the_id_is_the_calls_coordinates_and_type_whatever_its_record_id(reader: str, record_id: str) -> None:
    (record,) = _READERS[reader](_deletion(7000, 12000, record_id=record_id))

    # Before: the record's ID, when it had one -- even one that names another SV's position.
    assert record.variant_id == "1-7000-12000-DEL---"
    assert record.variant_id == build_structural_variant_id("chr1", 7000, 12000, "DEL")
    assert (record.chr, record.start, record.end) == ("1", 7000, 12000)
    # The record's ID stays in its annotation, as written; a record without one has none.
    if record_id in {".", ""}:
        assert "id" not in record.annotations[0]
    else:
        assert record.annotations[0]["id"] == record_id


def test_a_record_without_an_id_keeps_its_id_and_its_annotation() -> None:
    # Named by its coordinates before too: its id and annotation are what earlier imports
    # stored, so a review stays attached and a classification's frozen evidence does not drift.
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
