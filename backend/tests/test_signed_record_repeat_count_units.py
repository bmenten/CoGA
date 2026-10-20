"""The signed record freezes each repeat call's near-boundary warning and QA's counting-units record.

TRGT's count need not be the clinical repeat number (at FMR1 it leaves out the AGG
interruptions, so a 55-repeat premutation can read 53), so until QA validates a catalogue locus
the repeat page marks a count within 3 repeats of a classification boundary
(``repeat_count_units``, #889). The signed record's ``repeat_expansions`` section
(``signed_record_sections.repeat_expansion_calls``) now holds, as they stood at sign-out:

* beside each allele its warning (``count_unit_warning``: the count, the nearest count within the
  margin on each side that reads another status, the margin), null when there is none; the
  allele's own status is the one on its side of the boundary;
* per locus QA's record (``count_units``): validated or not, the reason, by whom and when, as
  ``repeat_locus_count_units`` held it, all null but ``validated: false`` without a record;
* the margin in force (``count_unit_margin``).

The record holds the warning the repeat page serves, made by the family table's own code; a
validation or a withdrawal that commits between the table's read and the record's is frozen
consistently. A validation or a withdrawal since sign-out changes what the check compares; a
record signed before these fields is compared on what it holds
(``repeat_expansions_comparable_with_record``), and nothing it predates is "not captured". The
check itself is tested in test_report_signout.py, and all of it on Postgres and ClickHouse in
e2e/test_e2e_signed_repeat_count_units.py. All samples and values are synthetic.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import FamilyRepeatExpansionTableOut, RepeatExpansionRowOut, RepeatExpansionSampleCallOut
from backend.app.services import report_signout_service as rss
from backend.app.services import signed_record_sections as srs
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.repeat_count_units import COUNT_UNIT_MARGIN

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
    chrom: str = "X",
    start: int = 147_912_049,
) -> dict[str, Any]:
    """A row of the family table's query: the catalogue row's thresholds, and whether QA's record
    for that row said validated when the query read it."""
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
        "count_units_validated": validated,
        "status": "normal",
        "genotype": "1/2",
        "allele_count": len(counts),
        "alleles": [{"repeat_count": count, "bp_length": count * 3, "status": "normal"} for count in counts],
        "locus_not_resolved": None,
    }


def _units(locus_id: str, *, validated: bool, reason: str, by: str = "qa-lead") -> dict[str, Any]:
    """A ``repeat_locus_count_units`` row, as the section's query reads it."""
    return {
        "locus_id": locus_id,
        "validated": validated,
        "reason": reason,
        "updated_by_name": by,
        "updated_at": RECORDED_AT,
    }


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _RepeatSession:
    """Answers the family table's query, the counting-units records' and the catalogue's, each by
    a fragment of its SQL (in that order: the table's query joins both other tables); keeps what
    it was asked."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        units: list[dict[str, Any]] | None = None,
    ) -> None:
        self.answers = [
            ("FROM repeat_expansions", rows),
            ("FROM repeat_locus_count_units", units or []),
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


def _freeze(rows: list[dict[str, Any]], units: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The section a sign-out freezes, read through the family table's own code."""
    return asyncio.run(srs.repeat_expansion_calls(_RepeatSession(rows, units), _context()))


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
    # No QA record for the locus: not validated, and nothing else to say.
    assert locus["count_units"] == {"validated": False, "reason": None, "updated_by": None, "updated_at": None}
    proband = _alleles(locus, "PROBAND1")
    # The 53 reads grey zone; the warning names the premutation at 55, the status it would read.
    assert [(allele["repeat_count"], allele["status"]) for allele in proband] == [(30, "normal"), (53, "intermediate")]
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
    rows = [_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1, validated=True)]
    section = _freeze(rows, [_units("FXS_FMR1", validated=True, reason="Validation report 13")])

    locus = _locus(section, "FXS_FMR1")
    assert locus["count_units"] == {
        "validated": True,
        "reason": "Validation report 13",
        "updated_by": "qa-lead",
        "updated_at": "2026-10-20T09:30:00+00:00",
    }
    assert [allele["count_unit_warning"] for allele in _alleles(locus, "PROBAND1")] == [None, None]
    # The statuses are the same, validated or not.
    assert [allele["status"] for allele in _alleles(locus, "PROBAND1")] == ["normal", "intermediate"]


def test_a_withdrawn_validation_is_frozen_with_its_reason_and_the_warning_back() -> None:
    rows = [_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)]
    section = _freeze(rows, [_units("FXS_FMR1", validated=False, reason="New TRGT catalogue")])

    locus = _locus(section, "FXS_FMR1")
    assert locus["count_units"] == {
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
            PROBAND_UUID, [18, 35], locus_id="HD_HTT", gene="HTT", locus=HTT, validated=True, chrom="4", start=3_074_876
        ),
    ]
    session = _RepeatSession(rows, [_units("HD_HTT", validated=True, reason="Validation report 7")])
    section = asyncio.run(srs.repeat_expansion_calls(session, _context()))

    assert [locus["locus_id"] for locus in section["loci"]] == ["HD_HTT", "FXS_FMR1"]
    htt, fmr1 = section["loci"]
    assert (htt["count_units"]["validated"], htt["count_units"]["reason"]) == (True, "Validation report 7")
    assert [allele["count_unit_warning"] for allele in _alleles(htt, "PROBAND1")] == [None, None]
    assert fmr1["count_units"]["validated"] is False
    assert [allele["count_unit_warning"] for allele in _alleles(fmr1, "PROBAND1")] == [None, WARNING_53]
    # The records are read for the loci the calls are classified under, bound, never spliced in.
    [(sql, params)] = [(sql, params) for sql, params in session.executed if "FROM repeat_locus_count_units" in sql]
    assert "ANY(CAST(:locus_ids AS text[]))" in sql
    assert params == {"locus_ids": ["FXS_FMR1", "HD_HTT"]}


def test_a_family_without_repeat_calls_reads_no_record_and_holds_the_margin() -> None:
    session = _RepeatSession([])
    section = asyncio.run(srs.repeat_expansion_calls(session, _context()))
    assert section["loci"] == []
    assert section["count_unit_margin"] == 3
    assert not [sql for sql, _params in session.executed if "FROM repeat_locus_count_units" in sql]


# ---- A record changed between the two reads ---------------------------------------------------


def _stub_table(monkeypatch, *, validated: bool, warning: dict[str, Any] | None) -> None:
    """The family table as its own statement read it: QA's record said ``validated`` then."""

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
                    count_units_validated=validated,
                    calls={
                        "PROBAND1": RepeatExpansionSampleCallOut(
                            sample="PROBAND1",
                            genotype="1/2",
                            alleles=[
                                {"repeat_count": 30, "status": "normal"},
                                {"repeat_count": 53, "status": "intermediate", "count_unit_warning": warning},
                            ],
                            status="intermediate",
                        )
                    },
                    **FMR1,
                )
            ]
        )

    monkeypatch.setattr(srs, "get_family_repeat_expansion_table_response", _table)


def test_a_validation_committed_between_the_reads_leaves_no_warning_beside_a_validated_record(monkeypatch) -> None:
    # The table read the locus as not validated and marked the 53; QA's validation committed
    # before the record was read.
    _stub_table(monkeypatch, validated=False, warning=WARNING_53)
    session = _RepeatSession([], [_units("FXS_FMR1", validated=True, reason="Validation report 13")])
    section = asyncio.run(srs.repeat_expansion_calls(session, _context()))

    locus = _locus(section, "FXS_FMR1")
    assert locus["count_units"]["validated"] is True
    assert [allele["count_unit_warning"] for allele in _alleles(locus, "PROBAND1")] == [None, None]


def test_a_withdrawal_committed_between_the_reads_puts_the_warning_beside_the_record(monkeypatch) -> None:
    # The table read the locus as validated and marked nothing; the withdrawal committed before
    # the record was read: the warning is made again, by the table's own function.
    _stub_table(monkeypatch, validated=True, warning=None)
    session = _RepeatSession([], [_units("FXS_FMR1", validated=False, reason="New TRGT catalogue")])
    section = asyncio.run(srs.repeat_expansion_calls(session, _context()))

    locus = _locus(section, "FXS_FMR1")
    assert locus["count_units"]["validated"] is False
    assert [allele["count_unit_warning"] for allele in _alleles(locus, "PROBAND1")] == [None, WARNING_53]


# ---- What the check compares ------------------------------------------------------------------


def _before_count_units(section: dict[str, Any]) -> dict[str, Any]:
    """The section as a record signed before CoGA froze the warnings holds it."""
    old = {key: value for key, value in copy.deepcopy(section).items() if key != "count_unit_margin"}
    for locus in old["loci"]:
        locus.pop("count_units")
        for call in locus["calls"]:
            for allele in call["alleles"]:
                allele.pop("count_unit_warning")
    return old


def test_a_validation_or_a_withdrawal_since_sign_out_changes_what_the_check_compares() -> None:
    rows = [_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)]
    signed = _freeze(rows)
    validated = _freeze(
        [{**row, "count_units_validated": True} for row in rows],
        [_units("FXS_FMR1", validated=True, reason="Validation report 13")],
    )
    withdrawn = _freeze(rows, [_units("FXS_FMR1", validated=False, reason="New TRGT catalogue")])
    fingerprints = {rss._section_fingerprint(section) for section in (signed, validated, withdrawn)}
    assert len(fingerprints) == 3
    # A record that holds them is compared whole: nothing is left out, nothing named.
    for current in (validated, withdrawn):
        assert srs.repeat_expansions_comparable_with_record(signed, current) == (current, [])


def test_a_record_signed_before_the_warnings_is_compared_on_what_it_holds() -> None:
    rows = [_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)]
    current = _freeze(rows)
    old = _before_count_units(current)

    comparable, predated = srs.repeat_expansions_comparable_with_record(old, current)
    assert comparable == old
    assert predated == ["repeat_expansions.count_units"]
    assert rss._section_fingerprint(comparable) == rss._section_fingerprint(old)
    # What it holds is still compared: a count that changed since reads as a change.
    changed = _freeze([_table_row(PROBAND_UUID, [30, 56], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)])
    comparable, predated = srs.repeat_expansions_comparable_with_record(old, changed)
    assert predated == ["repeat_expansions.count_units"]
    assert rss._section_fingerprint(comparable) != rss._section_fingerprint(old)
    # The current section itself is left as it was.
    assert current["loci"][0]["count_units"]["validated"] is False


def test_an_older_record_without_calls_names_the_margin_it_predates() -> None:
    current = _freeze([])
    old = _before_count_units(current)
    assert srs.repeat_expansions_comparable_with_record(old, current) == (
        old,
        ["repeat_expansions.count_units"],
    )
    # Both before the warnings: nothing to leave out, nothing to name.
    assert srs.repeat_expansions_comparable_with_record(old, old) == (old, [])


@pytest.mark.parametrize(
    "signed",
    [
        {"unavailable": "The repeat-expansion calls could not be read."},
        None,
        "not a section",
    ],
)
def test_a_record_that_holds_no_readable_section_is_compared_whole(signed) -> None:
    current = _freeze([_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)])
    assert srs.repeat_expansions_comparable_with_record(signed, current) == (current, [])


def test_a_section_that_cannot_be_read_now_is_compared_as_it_is() -> None:
    old = _before_count_units(_freeze([]))
    unavailable = {"unavailable": "The repeat-expansion calls could not be read."}
    assert srs.repeat_expansions_comparable_with_record(old, unavailable) == (unavailable, [])
    assert srs.repeat_expansions_comparable_with_record(old, None) == (None, [])


def test_entries_the_comparison_cannot_read_are_kept_as_they_are() -> None:
    old = {"catalogue": {}, "loci": []}
    current = {
        "catalogue": {},
        "count_unit_margin": 3,
        "loci": [
            "not a locus",
            {"locus_id": "L1", "count_units": {"validated": False}, "calls": "not a list"},
            {"locus_id": "L2", "calls": ["not a call", {"sample": "S1"}, {"sample": "S2", "alleles": ["x", {"a": 1}]}]},
        ],
    }
    comparable, predated = srs.repeat_expansions_comparable_with_record(old, current)
    assert predated == ["repeat_expansions.count_units"]
    assert comparable["loci"] == [
        "not a locus",
        {"locus_id": "L1", "calls": "not a list"},
        {"locus_id": "L2", "calls": ["not a call", {"sample": "S1"}, {"sample": "S2", "alleles": ["x", {"a": 1}]}]},
    ]


# ---- Nothing it predates is "not captured" ------------------------------------------------------


def test_nothing_a_record_holds_or_predates_of_the_warnings_is_a_gap() -> None:
    section = _freeze([_table_row(PROBAND_UUID, [30, 53], locus_id="FXS_FMR1", gene="FMR1", locus=FMR1)])
    assert srs.record_section_gaps({"repeat_expansions": section}) == []
    assert srs.record_section_gaps({"repeat_expansions": _before_count_units(section)}) == []
    assert rss.snapshot_gaps({"repeat_expansions": _before_count_units(section)}) == []
