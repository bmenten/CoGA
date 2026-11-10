"""The signed record freezes each repeat call's near-boundary warning and QA's counting-units record.

TRGT's count need not be the clinical repeat number (at FMR1 it leaves out the AGG
interruptions, so a 55-repeat premutation can read 53), so until QA validates a catalogue locus
the repeat page marks a count within 3 repeats of a classification boundary
(``repeat_count_units``). The signed record's ``repeat_expansions`` section
(``signed_record_sections.repeat_expansion_calls``) holds, as they stood at sign-out:

* per allele the count classified (``repeat_count``: TRGT's count with QA's correction for the
  locus, CLIN-57) and TRGT's own count beside it (``trgt_repeat_count``);
* beside each allele its warning (``count_unit_warning``: the corrected count, the nearest count
  within the margin on each side that reads another status, the margin), null when there is
  none; the allele's own status is the one on its side of the boundary;
* per locus QA's record (``count_units``): the correction (``count_offset``), validated or not,
  the reason, by whom and when, as ``repeat_locus_count_units`` held it, ``count_offset: 0``,
  ``validated: false`` and the rest null without a record;
* the margin in force (``count_unit_margin``).

The record holds the counts and warnings the repeat page serves, made by the family table's own
code, beside QA's record as the table's own statement read it with the calls: no second read
can freeze a record the calls were not classified with. A validation, a withdrawal or a
correction since sign-out changes what the check compares, and none of these fields is "not
captured". The check itself is tested in test_report_signout.py, and all of it on Postgres and
ClickHouse in e2e/test_e2e_signed_repeat_count_units.py. All samples and values are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import FamilyRepeatExpansionTableOut, RepeatExpansionRowOut, RepeatExpansionSampleCallOut
from backend.app.services import report_signout_service as rss
from backend.app.services import signed_record_sections as srs
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.repeat_count_units import COUNT_UNIT_MARGIN
from backend.tests._query_results import MappingRows as _Result

PROBAND_UUID = "00000000-0000-4000-8000-000000000001"
MOTHER_UUID = "00000000-0000-4000-8000-000000000002"
RECORDED_AT = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)

# STRchive's ranges (data/ref-data/STRchive-loci.json), with the premutation threshold the
# catalogue adds to FMR1.
FMR1 = {
    "warning_min": 45,
    "pathogenic_min": 201,
    "benign_min": 5,
    "benign_max": 44,
    "pathogenic_max": 2000,
    "premutation_min": 55,
}
HTT = {
    "warning_min": 27,
    "pathogenic_min": 36,
    "benign_min": 6,
    "benign_max": 26,
    "pathogenic_max": 250,
    "premutation_min": None,
}

# The warning beside a grey-zone FMR1 count of 53: a premutation of 55 can read 53.
WARNING_53 = {"repeat_count": 53, "margin": 3, "boundaries": [{"repeat_count": 55, "status": "premutation"}]}


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id="FAM1",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": PROBAND_UUID, "sample_id": "PROBAND1", "role": "proband", "affected": True, "sex": "female"},
            {"sample_uuid": MOTHER_UUID, "sample_id": "MOTHER1", "role": "mother", "affected": False, "sex": "female"},
        ],
        sample_uuid_to_name={PROBAND_UUID: "PROBAND1", MOTHER_UUID: "MOTHER1"},
        sample_name_to_uuid={"PROBAND1": PROBAND_UUID, "MOTHER1": MOTHER_UUID},
        affected_sample_names=["PROBAND1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _table_row(
    sample_uuid: str,
    counts: list[int],
    *,
    locus_id: str,
    gene: str,
    locus: dict[str, Any],
    validated: bool = False,
    units: dict[str, Any] | None = None,
    chrom: str = "X",
    start: int = 147_912_049,
) -> dict[str, Any]:
    """A row of the family table's query: the catalogue row's thresholds, and QA's record for that
    row as the query read it with the call (``units``, from ``_units``; none: no record, or only
    ``validated`` given)."""
    return {
        "sample_uuid": sample_uuid,
        "locus_id": locus_id,
        "gene": gene,
        "display_name": gene,
        "disease": f"{gene} disorder",
        "inheritance": None,
        "chr": chrom,
        "start": start,
        "end": start + 60,
        "motif": "CGG" if gene == "FMR1" else "CAG",
        **locus,
        "count_offset": 0,
        "count_units_validated": validated,
        "count_units_reason": None,
        "count_units_updated_by": None,
        "count_units_updated_at": None,
        **(units or {}),
        "status": "normal",
        "genotype": "1/2",
        "allele_count": len(counts),
        "alleles": [{"repeat_count": count, "bp_length": count * 3, "status": "normal"} for count in counts],
        "locus_not_resolved": None,
    }


def _units(*, validated: bool, reason: str, by: str = "qa-lead", count_offset: int = 0) -> dict[str, Any]:
    """QA's ``repeat_locus_count_units`` row, as the family table's query reads it with a call."""
    return {
        "count_offset": count_offset,
        "count_units_validated": validated,
        "count_units_reason": reason,
        "count_units_updated_by": by,
        "count_units_updated_at": RECORDED_AT,
    }


class _RepeatSession:
    """Answers the family table's query and the catalogue's, each by a fragment of its SQL (in
    that order: the table's query joins the catalogue and QA's record); keeps what it was asked."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.answers = [
            ("FROM repeat_expansions", rows),
            ("FROM repeat_loci", [{"locus_id": "FXS_FMR1", "gene": "FMR1", "source": "STRchive"}]),
        ]
        self.executed: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.executed.append((sql, params))
        for fragment, rows in self.answers:
            if fragment in sql:
                return _Result(rows)
        return _Result([])


def _freeze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The section a sign-out freezes, read through the family table's own code."""
    return asyncio.run(srs.repeat_expansion_calls(_RepeatSession(rows), _context()))


def _locus(section: dict[str, Any], locus_id: str) -> dict[str, Any]:
    [locus] = [entry for entry in section["loci"] if entry["locus_id"] == locus_id]
    return locus


def _alleles(locus: dict[str, Any], sample: str) -> list[dict[str, Any]]:
    [call] = [call for call in locus["calls"] if call["sample"] == sample]
    return call["alleles"]


# ---- What the record freezes ----------------------------------------------------------------


def test_a_count_near_a_boundary_is_frozen_with_its_warning_while_the_locus_is_not_validated() -> None:
    rows = [
        _table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1),
        _table_row(MOTHER_UUID, [30, 40], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1),
    ]
    section = _freeze(rows)

    assert section["count_unit_margin"] == COUNT_UNIT_MARGIN == 3
    locus = _locus(section, "FXS_FMR1")
    # No QA record for the locus: no correction, not validated, and nothing else to say.
    assert locus["count_units"] == {
        "count_offset": 0,
        "validated": False,
        "reason": None,
        "updated_by": None,
        "updated_at": None,
    }
    proband = _alleles(locus, "PROBAND1")
    # The 53 reads grey zone; the warning names the premutation at 55, the status it would read.
    # Without a correction the count classified is TRGT's own.
    assert [(allele["repeat_count"], allele["trgt_repeat_count"], allele["status"]) for allele in proband] == [
        (30, 30, "normal"),
        (53, 53, "intermediate"),
    ]
    assert [allele["count_unit_warning"] for allele in proband] == [None, WARNING_53]
    assert [allele["count_unit_warning"] for allele in _alleles(locus, "MOTHER1")] == [None, None]
    # The status is the classification's: the warning changes none.
    assert locus["status"] == "intermediate"


def test_the_record_holds_the_warning_the_repeat_page_serves() -> None:
    rows = [_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)]
    from backend.app.services.repeat_expansion_pg import get_family_repeat_expansion_table_response

    table = asyncio.run(get_family_repeat_expansion_table_response(_RepeatSession(rows), context=_context()))
    served = [
        allele.count_unit_warning.model_dump(mode="json") if allele.count_unit_warning else None
        for allele in table.loci[0].calls["PROBAND1"].alleles
    ]
    frozen = [allele["count_unit_warning"] for allele in _alleles(_locus(_freeze(rows), "FXS_FMR1"), "PROBAND1")]
    assert frozen == served == [None, WARNING_53]


def test_a_validated_locus_is_frozen_with_qas_record_and_no_warning() -> None:
    rows = [
        _table_row(
            PROBAND_UUID,
            [30, 53],
            locus_id="FXS_FMR1",
            gene="FMR1",
            locus=FMR1,
            units=_units(validated=True, reason="Validation report 13"),
        )
    ]
    section = _freeze(rows)

    locus = _locus(section, "FXS_FMR1")
    assert locus["count_units"] == {
        "count_offset": 0,
        "validated": True,
        "reason": "Validation report 13",
        "updated_by": "qa-lead",
        "updated_at": "2026-10-20T09:30:00+00:00",
    }
    assert [allele["count_unit_warning"] for allele in _alleles(locus, "PROBAND1")] == [None, None]
    # The statuses are the same, validated or not.
    assert [allele["status"] for allele in _alleles(locus, "PROBAND1")] == ["normal", "intermediate"]


def test_a_withdrawn_validation_is_frozen_with_its_reason_and_the_warning_back() -> None:
    rows = [
        _table_row(
            PROBAND_UUID,
            [30, 53],
            locus_id="FXS_FMR1",
            gene="FMR1",
            locus=FMR1,
            units=_units(validated=False, reason="New TRGT catalogue"),
        )
    ]
    section = _freeze(rows)

    locus = _locus(section, "FXS_FMR1")
    assert locus["count_units"] == {
        "count_offset": 0,
        "validated": False,
        "reason": "New TRGT catalogue",
        "updated_by": "qa-lead",
        "updated_at": "2026-10-20T09:30:00+00:00",
    }
    assert [allele["count_unit_warning"] for allele in _alleles(locus, "PROBAND1")] == [None, WARNING_53]


def test_each_locus_is_frozen_with_its_own_record() -> None:
    rows = [
        _table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1),
        # HTT 35 would be marked near the pathogenic threshold at 36, but HTT is validated.
        _table_row(
            PROBAND_UUID,
            [18, 35],
            locus_id="HD_HTT",
            gene="HTT",
            locus=HTT,
            units=_units(validated=True, reason="Validation report 7"),
            chrom="4",
            start=3_074_876,
        ),
    ]
    session = _RepeatSession(rows)
    section = asyncio.run(srs.repeat_expansion_calls(session, _context()))

    assert [locus["locus_id"] for locus in section["loci"]] == ["HD_HTT", "FXS_FMR1"]
    htt, fmr1 = section["loci"]
    assert (htt["count_units"]["validated"], htt["count_units"]["reason"]) == (True, "Validation report 7")
    assert [allele["count_unit_warning"] for allele in _alleles(htt, "PROBAND1")] == [None, None]
    assert fmr1["count_units"]["validated"] is False
    assert [allele["count_unit_warning"] for allele in _alleles(fmr1, "PROBAND1")] == [None, WARNING_53]
    # QA's records are read with the calls, in the family table's one statement, and in no other:
    # what is frozen beside a call is the record it was classified and marked with.
    [table_sql] = [sql for sql, _params in session.executed if "FROM repeat_expansions" in sql]
    assert "LEFT JOIN repeat_locus_count_units AS units ON units.locus_id = catalog.locus_id" in table_sql
    assert not [
        sql for sql, _params in session.executed if "repeat_locus_count_units" in sql and "FROM repeat_expansions" not in sql
    ]


def test_a_family_without_repeat_calls_reads_no_record_and_holds_the_margin() -> None:
    session = _RepeatSession([])
    section = asyncio.run(srs.repeat_expansion_calls(session, _context()))
    assert section["loci"] == []
    assert section["count_unit_margin"] == 3
    assert not [sql for sql, _params in session.executed if "FROM repeat_locus_count_units" in sql]


# ---- The record and the calls come from one statement ------------------------------------------


def _stub_table(monkeypatch, *, validated: bool, count_offset: int, counts: tuple[int, int], warning) -> None:
    """The family table as its own statement read it: QA's record said ``validated`` and the
    correction ``count_offset`` then, and the calls were classified and marked with it."""

    async def _table(session, *, context):
        return FamilyRepeatExpansionTableOut(
            loci=[
                RepeatExpansionRowOut(
                    locus_id="FXS_FMR1",
                    gene="FMR1",
                    display_name="FMR1",
                    disease="Fragile X syndrome",
                    chr="chrX",
                    start=147_912_049,
                    end=147_912_111,
                    motif="CGG",
                    status="intermediate",
                    count_offset=count_offset,
                    count_units_validated=validated,
                    count_units_reason="Validation report 13",
                    count_units_updated_by="qa-lead",
                    count_units_updated_at=RECORDED_AT,
                    calls={
                        "PROBAND1": RepeatExpansionSampleCallOut(
                            sample="PROBAND1",
                            genotype="1/2",
                            alleles=[
                                {"repeat_count": counts[0], "trgt_repeat_count": 30, "status": "normal"},
                                {
                                    "repeat_count": counts[1],
                                    "trgt_repeat_count": 53,
                                    "status": "intermediate",
                                    "count_unit_warning": warning,
                                },
                            ],
                            status="intermediate",
                        )
                    },
                    **FMR1,
                )
            ]
        )

    monkeypatch.setattr(srs, "get_family_repeat_expansion_table_response", _table)


@pytest.mark.parametrize(
    ("validated", "count_offset", "counts", "warning"),
    [
        (False, 0, (30, 53), WARNING_53),
        (True, 0, (30, 53), None),
        # A correction is frozen with the counts it made, whatever an administrator commits after
        # the table's statement: the section reads nothing else.
        (False, 1, (31, 54), {"repeat_count": 54, "margin": 3, "boundaries": [{"repeat_count": 55, "status": "premutation"}]}),
    ],
)
def test_the_record_frozen_beside_the_calls_is_the_one_the_tables_statement_read(
    monkeypatch, validated, count_offset, counts, warning
) -> None:
    _stub_table(monkeypatch, validated=validated, count_offset=count_offset, counts=counts, warning=warning)
    session = _RepeatSession([])
    section = asyncio.run(srs.repeat_expansion_calls(session, _context()))

    locus = _locus(section, "FXS_FMR1")
    assert locus["count_units"] == {
        "count_offset": count_offset,
        "validated": validated,
        "reason": "Validation report 13",
        "updated_by": "qa-lead",
        "updated_at": "2026-10-20T09:30:00+00:00",
    }
    alleles = _alleles(locus, "PROBAND1")
    assert [(allele["repeat_count"], allele["trgt_repeat_count"]) for allele in alleles] == [(counts[0], 30), (counts[1], 53)]
    assert [allele["count_unit_warning"] for allele in alleles] == [None, warning]
    # No record is read apart from the calls (the catalogue identity is all the section reads).
    assert not [sql for sql, _params in session.executed if "repeat_locus_count_units" in sql]


# ---- What the check compares ------------------------------------------------------------------


def test_a_validation_a_withdrawal_or_a_correction_since_sign_out_changes_what_the_check_compares() -> None:
    rows = [_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)]
    signed = _freeze(rows)
    validated = _freeze([{**row, **_units(validated=True, reason="Validation report 13")} for row in rows])
    withdrawn = _freeze([{**row, **_units(validated=False, reason="New TRGT catalogue")} for row in rows])
    corrected = _freeze([{**row, **_units(validated=False, reason="Offset from VAL-7", count_offset=2)} for row in rows])
    fingerprints = {rss._section_fingerprint(section) for section in (signed, validated, withdrawn, corrected)}
    assert len(fingerprints) == 4
    # The correction moved the 53 to 55: a premutation now, which the check sees.
    assert [allele["status"] for allele in _alleles(_locus(corrected, "FXS_FMR1"), "PROBAND1")] == [
        "normal",
        "premutation",
    ]


# ---- None of it is "not captured" ---------------------------------------------------------------


def test_the_warnings_and_qas_records_are_no_gap() -> None:
    section = _freeze([_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)])
    assert srs.record_section_gaps({"repeat_expansions": section}) == []
    assert rss.snapshot_gaps({"repeat_expansions": section}) == []
