"""The SV upload's record readers (``structural_variant_ingest``): malformed records are
skipped, not allowed to abort the whole ingest, and a manual row's breakend keeps its partner.

A single bad POS/END raising mid-loop would leave partially-flushed ClickHouse rows
behind, so the record iterators coerce coordinates and skip records that
can't be positioned — mirroring the package-import SV path. The Sniffles, Spectre and
manual TSV readers each skip such a record; a non-numeric QUAL or phase set is read as
missing rather than dropping the record, and a record format the upload does not know is
refused.

A manual TSV row of type BND reads its remote breakend partner from its bracketed ALT.
"""

import pytest

from backend.app.services.structural_variant_ingest import (
    _coerce_qual,
    iter_structural_variant_records,
    parse_phase_set,
)
from backend.app.services.vcf_fields import field_int


def test_coerce_int_and_qual_are_tolerant() -> None:
    assert field_int("100") == 100
    assert field_int("100.0") == 100
    assert field_int("junk") is None
    assert field_int(".") is None
    assert _coerce_qual("60") == 60.0
    assert _coerce_qual(".") is None
    assert _coerce_qual("nan") is None
    assert _coerce_qual("junk") is None


def test_a_phase_set_that_is_no_number_is_read_as_missing() -> None:
    assert parse_phase_set("7001") == 7001
    assert parse_phase_set(".") is None
    assert parse_phase_set("") is None
    assert parse_phase_set(None) is None
    assert parse_phase_set("block7") is None


def test_manual_records_skip_unparseable_coordinates() -> None:
    text = "\n".join(
        [
            "SV1 chr1 100 200 N <DEL> DEL 0/1",
            "SV2 chr1 notanumber 300 N <DEL> DEL 0/1",  # bad start -> skipped
            "SV3 chr2 400 500 N <DUP> DUP 1/1",
            "SV4 chr2 600 700 N <DUP> DUP",  # too few fields -> skipped
        ]
    )
    records = list(iter_structural_variant_records(text, "manual"))
    assert [r.variant_id for r in records] == ["SV1", "SV3"]
    assert records[0].start == 100 and records[0].end == 200


def test_manual_bnd_records_capture_remote_partner():
    text = "sv1\t7\t72100000\t72100001\tN\tN[18:14300000[\tBND\t0/1\n"

    records = list(iter_structural_variant_records(text, "manual"))

    assert len(records) == 1
    assert records[0].svtype == "BND"
    assert records[0].remote_chr == "18"
    assert records[0].remote_start == 14_300_000
    assert records[0].remote_end == 14_300_000


def test_sniffles_records_skip_bad_pos_and_tolerate_qual() -> None:
    rows = [
        "chr1\t100\tSV1\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;END=200\tGT\t0/1",
        "chr1\tXX\tSV2\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;END=300\tGT\t0/1",  # bad POS
        "chr2\t400\tSV3\tN\t<DUP>\tbadqual\tPASS\tSVTYPE=DUP;END=500\tGT\t1/1",
    ]
    records = list(iter_structural_variant_records("\n".join(rows), "sniffles"))
    assert [r.variant_id for r in records] == ["SV1", "SV3"]
    # A non-numeric QUAL is tolerated as None rather than aborting the record.
    assert records[1].qual is None
    assert records[0].qual == 60.0


def test_a_sniffles_phase_set_that_is_no_number_keeps_the_record_without_one() -> None:
    rows = [
        "chr1\t100\tSV1\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;END=200\tGT:PS\t0|1:7001",
        "chr1\t300\tSV2\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;END=400\tGT:PS\t0|1:block7",
    ]
    records = list(iter_structural_variant_records("\n".join(rows), "sniffles"))
    assert [(r.variant_id, r.gt, r.phase_set) for r in records] == [("SV1", "0|1", 7001), ("SV2", "0|1", None)]


def test_spectre_records_skip_what_cannot_be_positioned() -> None:
    rows = [
        "chr1\t100\tSP1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=200\tGT\t0/1",
        "chr1\tXX\tSP2\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=300\tGT\t0/1",  # bad POS
        "chr1\t400\tSP3\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;SVLEN=-100;END=abc\tGT\t0/1",  # bad END
        "chr1\t500\tSP4\tN\t<DUP>\t.\tPASS\tSVTYPE=DUP;END=99999999999\tGT\t0/1",  # END no column holds
        "chr2\t600\tSP5\tN\t<DUP>\tbadqual\tPASS\tSVTYPE=DUP;END=700\tGT\t1/1",
    ]
    records = list(iter_structural_variant_records("\n".join(rows), "spectre"))
    assert [(r.variant_id, r.start, r.end) for r in records] == [("SP1", 100, 200), ("SP5", 600, 700)]
    assert records[1].qual is None


def test_a_record_format_the_upload_does_not_know_is_refused() -> None:
    with pytest.raises(ValueError, match="Unsupported structural variant record format: hificnv"):
        iter_structural_variant_records("chr1\t100\tSV1\tN\t<DEL>\t60\tPASS\t.\tGT\t0/1", "hificnv")  # type: ignore[arg-type]
