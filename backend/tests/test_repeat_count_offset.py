"""QA's count correction per repeat catalogue locus (CLIN-57, the owner's decision of 2026-11-04).

TRGT counts the motif across the region its own catalogue gives a locus: at HTT one CAG more than
the usual count, at POLG four flanking GCT copies. QA records, per catalogue locus, an offset in
repeat units (``repeat_locus_count_units.count_offset``) that is added to TRGT's count before the
count is classified, at every place a count is classified or compared to a range. TRGT's own count
stays beside the corrected one.

- The corrected count: TRGT's count plus the offset, a no-call stays one, never below 0.
- Classification with an offset at each boundary: HTT's grey zone and pathogenic threshold, the
  POLG benign count, FMR1's grey zone, premutation and full mutation, a contraction locus.
- The near-boundary warning is made from the corrected count; validation lifts it, the
  correction stays applied.
- The family table, the genome track and the import read the correction of the catalogue row
  that classifies the call.
- The record: an admin sets the correction with the validation and a reason; each change is a
  clinical audit event with the correction before and after; a correction out of bounds is
  refused; nothing is recorded in advance.
(integration/test_repeat_count_units_storage.py runs the record and the reads on Postgres;
e2e/test_e2e_signed_repeat_count_units.py the sign-out.) All samples and values are synthetic.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.schemas import RepeatLocusCountUnitsUpdate
from backend.app.services import repeat_count_units, repeat_expansion_pg
from backend.app.services.access_control import CurrentUser
from backend.app.services.repeat_count_units import (
    COUNT_OFFSET_LIMIT,
    count_offset_note,
    corrected_repeat_count,
    describe_count_offset,
)
from backend.tests._repeat_fakes import Result, RowsSession, family_context, trgt_insert_params

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
HTT = {"warning_min": 27, "pathogenic_min": 36, "benign_min": 6, "benign_max": 26, "pathogenic_max": 250}
# STRchive's POLG: no threshold, a benign count of exactly 10.
POLG = {"warning_min": None, "pathogenic_min": None, "benign_min": 10, "benign_max": 10, "pathogenic_max": None}
# Pathogenic by contraction: normal at exactly 2, pathogenic at 1 or 3.
VWA1 = {"warning_min": None, "pathogenic_min": 1, "benign_min": 2, "benign_max": 2, "pathogenic_max": 3}

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "db" / "schema" / "postgres"
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"


# ---- The corrected count ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trgt", "offset", "expected"),
    [
        (18, -1, 17),
        (14, -4, 10),
        (29, 2, 31),
        (40, 0, 40),
        (40, None, 40),
        # A no-call stays a no-call, whatever the correction.
        (None, -1, None),
        (None, 3, None),
        # Never below 0: no allele has fewer than no repeats.
        (3, -4, 0),
        (4, -4, 0),
        (0, -1, 0),
        (0, 0, 0),
    ],
)
def test_the_count_classified_is_trgts_count_plus_the_correction_never_below_zero(trgt, offset, expected) -> None:
    assert corrected_repeat_count(trgt, offset) == expected


def test_a_correction_reads_with_its_sign() -> None:
    assert describe_count_offset(0) == "no correction"
    assert describe_count_offset(-1) == "TRGT's count −1"
    assert describe_count_offset(4) == "TRGT's count +4"


# ---- Classification with a correction, at each boundary ----------------------------------------


def _reclassified(counts: list[int | None], locus: dict, offset: int) -> list[tuple[int | None, int | None, str]]:
    """Each stored allele as every reader reads it: (count classified, TRGT's count, status)."""
    alleles = repeat_expansion_pg._reclassify_repeat_alleles(
        [{"repeat_count": count, "status": "unknown"} for count in counts], count_offset=offset, **locus
    )
    return [(allele["repeat_count"], allele["trgt_repeat_count"], allele["status"]) for allele in alleles]


@pytest.mark.parametrize(
    ("trgt", "expected"),
    [
        # HTT reads one above the usual count: with -1, TRGT's 27 is 26, the last normal count.
        (27, (26, "normal")),
        (28, (27, "intermediate")),
        (36, (35, "intermediate")),
        (37, (36, "pathogenic")),
        (18, (17, "normal")),
    ],
)
def test_htt_with_minus_one_is_classified_at_each_boundary_from_the_corrected_count(trgt, expected) -> None:
    [(count, trgt_count, status)] = _reclassified([trgt], HTT, -1)
    assert (count, status) == expected
    assert trgt_count == trgt


def test_without_a_correction_the_same_htt_counts_read_as_before() -> None:
    assert _reclassified([27, 28, 36, 37], HTT, 0) == [
        (27, 27, "intermediate"),
        (28, 28, "intermediate"),
        (36, 36, "pathogenic"),
        (37, 37, "pathogenic"),
    ]


@pytest.mark.parametrize(
    ("trgt", "expected"),
    [
        # POLG's common 10-repeat allele reads 14: with -4 it is 10, the benign count.
        (14, (10, "normal")),
        (13, (9, "not_assessed")),
        (15, (11, "not_assessed")),
        # Taken below 0, it reads 0: outside the benign count, not assessed, as any count below 0 would.
        (2, (0, "not_assessed")),
    ],
)
def test_polg_with_minus_four_reads_its_benign_count_normal_and_any_other_not_assessed(trgt, expected) -> None:
    [(count, _trgt, status)] = _reclassified([trgt], POLG, -4)
    assert (count, status) == expected


@pytest.mark.parametrize(
    ("trgt", "expected"),
    [
        # A correction QA might enter elsewhere, +2, at FMR1's boundaries (FMR1 itself has no
        # constant correction): each status changes exactly where the corrected count crosses.
        (42, (44, "normal")),
        (43, (45, "intermediate")),
        (52, (54, "intermediate")),
        (53, (55, "premutation")),
        (198, (200, "premutation")),
        (199, (201, "pathogenic")),
    ],
)
def test_a_correction_moves_every_boundary_of_a_locus_with_a_premutation_range(trgt, expected) -> None:
    [(count, _trgt, status)] = _reclassified([trgt], FMR1, 2)
    assert (count, status) == expected


@pytest.mark.parametrize(
    ("trgt", "expected"),
    [
        (1, (2, "normal")),
        (0, (1, "pathogenic")),
        (2, (3, "pathogenic")),
        (3, (4, "review")),
    ],
)
def test_a_contraction_locus_is_classified_from_the_corrected_count(trgt, expected) -> None:
    [(count, _trgt, status)] = _reclassified([trgt], VWA1, 1)
    assert (count, status) == expected


def test_a_no_call_keeps_its_stored_status_under_a_correction() -> None:
    alleles = repeat_expansion_pg._reclassify_repeat_alleles(
        [{"repeat_count": None, "status": "unknown"}], count_offset=-1, **HTT
    )
    assert alleles == [{"repeat_count": None, "trgt_repeat_count": None, "status": "unknown"}]


# ---- The warning, made from the corrected count -----------------------------------------------


def _marked(counts: list[int], *, offset: int, validated: bool) -> list[dict]:
    alleles = repeat_expansion_pg._reclassify_repeat_alleles(
        [{"repeat_count": count} for count in counts], count_offset=offset, **HTT
    )
    return repeat_expansion_pg._with_count_unit_warnings(alleles, validated=validated, **HTT)


def test_the_warning_names_the_corrected_count_and_its_boundary() -> None:
    # TRGT's 37 is 36 with -1: pathogenic, and 35 (grey zone) is within the margin.
    _short, long_allele = _marked([18, 37], offset=-1, validated=False)
    assert long_allele["repeat_count"] == 36 and long_allele["status"] == "pathogenic"
    assert long_allele["count_unit_warning"] == {
        "repeat_count": 36,
        "margin": 3,
        "boundaries": [{"repeat_count": 35, "status": "intermediate"}],
    }


def test_a_correction_entered_but_not_validated_keeps_the_mark_and_validation_lifts_it() -> None:
    unvalidated = _marked([37], offset=-1, validated=False)
    validated = _marked([37], offset=-1, validated=True)
    # The correction applies either way: the status is read from 36.
    assert [allele["status"] for allele in unvalidated] == [allele["status"] for allele in validated] == ["pathogenic"]
    assert unvalidated[0]["count_unit_warning"] is not None
    assert validated[0]["count_unit_warning"] is None


# ---- The family table, the genome track and the import ------------------------------------------

PROBAND_UUID = "00000000-0000-4000-8000-000000000001"
RECORDED_AT = datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)


def _context():
    return family_context((PROBAND_UUID, "PROBAND1", "proband", True, "female"))


def _row(counts: list[int], *, locus: dict, gene: str, offset: int, validated: bool = False) -> dict:
    """A row of the family table's and the track's query: the catalogue row's thresholds, and QA's
    record for that row as the query read it with the call."""
    return {
        "sample_uuid": PROBAND_UUID,
        "locus_id": gene,
        "gene": gene,
        "display_name": gene,
        "disease": gene,
        "inheritance": None,
        "chr": "4",
        "start": 3_074_876,
        "end": 3_074_933,
        "motif": "CAG",
        "genotype": "1/2",
        "allele_count": len(counts),
        "alleles": [{"repeat_count": count, "bp_length": count * 3, "status": "normal"} for count in counts],
        "benign_min": None,
        "benign_max": None,
        "pathogenic_max": None,
        "premutation_min": None,
        **locus,
        "count_offset": offset,
        "count_units_validated": validated,
        "count_units_reason": "Offset from validation report VAL-7" if offset or validated else None,
        "count_units_updated_by": "qa-lead" if offset or validated else None,
        "count_units_updated_at": RECORDED_AT if offset or validated else None,
        "status": "intermediate",
    }


@pytest.mark.asyncio
async def test_the_family_table_classifies_the_corrected_count_and_shows_trgts_beside_it() -> None:
    session = RowsSession([_row([18, 37], locus=HTT, gene="HTT", offset=-1)])

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(session, context=_context())

    [locus] = table.loci
    assert (locus.count_offset, locus.count_units_validated) == (-1, False)
    assert (locus.count_units_reason, locus.count_units_updated_by) == ("Offset from validation report VAL-7", "qa-lead")
    assert locus.count_units_updated_at == RECORDED_AT
    call = locus.calls["PROBAND1"]
    assert [(allele.repeat_count, allele.trgt_repeat_count, allele.status) for allele in call.alleles] == [
        (17, 18, "normal"),
        (36, 37, "pathogenic"),
    ]
    assert call.status == locus.status == "pathogenic"
    assert call.alleles[1].count_unit_warning.repeat_count == 36
    # The table reads the correction with the call, from the catalogue row that classifies it.
    statement = session.statements[0]
    assert "COALESCE(units.count_offset, 0) AS count_offset" in statement
    assert "LEFT JOIN repeat_locus_count_units AS units ON units.locus_id = catalog.locus_id" in statement


@pytest.mark.asyncio
async def test_without_a_record_the_family_table_reads_trgts_count() -> None:
    row = _row([18, 36], locus=HTT, gene="HTT", offset=0)
    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(RowsSession([row]), context=_context())

    [locus] = table.loci
    assert (locus.count_offset, locus.count_units_reason, locus.count_units_updated_at) == (0, None, None)
    assert [(a.repeat_count, a.trgt_repeat_count, a.status) for a in locus.calls["PROBAND1"].alleles] == [
        (18, 18, "normal"),
        (36, 36, "pathogenic"),
    ]


@pytest.mark.asyncio
async def test_the_genome_track_draws_the_corrected_counts_with_trgts_beside_them() -> None:
    row = _row([18, 37], locus=HTT, gene="HTT", offset=-1)
    response = await repeat_expansion_pg.get_sample_repeat_expansion_track_response(
        RowsSession([row]), context=_context(), sample_name="PROBAND1", chromosomes=["4"]
    )

    [item] = response.items
    assert item.status == "pathogenic"
    assert (item.allele_repeat_counts, item.allele_trgt_repeat_counts, item.count_offset) == ([17, 36], [18, 37], -1)
    assert [warning.repeat_count for warning in item.count_unit_warnings] == [36]


def _male_x_row(counts: list[int], *, offset: int) -> dict:
    row = _row(counts, locus=FMR1, gene="FMR1", offset=offset)
    row.update(chr="X", start=147_912_049, end=147_912_111)
    for allele in row["alleles"]:
        allele["bp_length"] = None
    return row


@pytest.mark.asyncio
async def test_two_different_male_chrx_alleles_stay_two_when_a_correction_takes_both_to_zero() -> None:
    # TRGT's 1 and 3 both read 0 with -4: they are still two alleles, and the call asks for review.
    context = family_context((PROBAND_UUID, "PROBAND1", "proband", True, "male"))
    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(
        RowsSession([_male_x_row([1, 3], offset=-4)]), context=context
    )
    call = table.loci[0].calls["PROBAND1"]
    assert [allele.repeat_count for allele in call.alleles] == [0, 0]
    assert call.note == repeat_expansion_pg.MALE_X_DISTINCT_ALLELES_NOTE
    assert call.status == "review"


_HTT_INFO = "TRID=HD_HTT;END=3074933;MOTIFS=CAG;STRUC=(CAG)n"


def _htt_lookup(offset: int) -> repeat_expansion_pg.RepeatLocusLookup:
    lookup = repeat_expansion_pg.RepeatLocusLookup()
    lookup["hd_htt"] = {
        "locus_id": "HD_HTT",
        "gene": "HTT",
        "display_name": "HTT",
        "disease": "Huntington disease",
        "inheritance": "AD",
        "motif": "CAG",
        "motif_index": 0,
        "warning_min": 27,
        "pathogenic_min": 36,
        "aliases": [],
        "metadata": {"benign_min": 6, "benign_max": 26, "pathogenic_max": 250},
        "count_offset": offset,
    }
    return lookup


@pytest.mark.parametrize(("offset", "statuses"), [(-1, ["normal", "pathogenic"]), (0, ["normal", "pathogenic"])])
def test_the_import_stores_trgts_count_and_reads_its_status_from_the_corrected_one(offset, statuses) -> None:
    params = trgt_insert_params(
        _htt_lookup(offset), info_field=_HTT_INFO, sample_field="0/1:54,111:18,37", chrom="chr4", pos=3074877, ref="C"
    )
    alleles = json.loads(params["alleles_json"])
    # The stored allele keeps TRGT's count; each read applies the correction as it then stands.
    assert [allele["repeat_count"] for allele in alleles] == [18, 37]
    assert [allele["status"] for allele in alleles] == statuses
    assert params["status"] == "pathogenic"


def test_the_import_reads_a_status_the_correction_changes() -> None:
    corrected = trgt_insert_params(
        _htt_lookup(-1), info_field=_HTT_INFO, sample_field="0/1:54,108:18,36", chrom="chr4", pos=3074877, ref="C"
    )
    uncorrected = trgt_insert_params(
        _htt_lookup(0), info_field=_HTT_INFO, sample_field="0/1:54,108:18,36", chrom="chr4", pos=3074877, ref="C"
    )
    assert (corrected["status"], uncorrected["status"]) == ("intermediate", "pathogenic")
    assert [allele["repeat_count"] for allele in json.loads(corrected["alleles_json"])] == [18, 36]


@pytest.mark.asyncio
async def test_the_imports_catalogue_carries_each_rows_correction() -> None:
    session = RowsSession([{**_htt_lookup(-1)["hd_htt"], "aliases": []}])
    lookup = await repeat_expansion_pg._build_repeat_locus_lookup(session)  # type: ignore[arg-type]
    assert lookup["hd_htt"]["count_offset"] == -1
    [statement] = session.statements
    assert "COALESCE(units.count_offset, 0) AS count_offset" in statement
    assert "LEFT JOIN repeat_locus_count_units AS units ON units.locus_id = repeat_loci.locus_id" in statement


# ---- The record -------------------------------------------------------------------------------


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-4000-8000-0000000000a1",
        username="qa-admin",
        email="qa-admin@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


class _RecordSession:
    """The record's queries: the catalogue row, QA's previous record, the upsert; and the clinical
    audit event's (its chain lock, head and insert)."""

    def __init__(self, *, locus: dict | None, previous: dict | None) -> None:
        self.locus = locus
        self.previous = previous
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        self.statements.append((sql, params))
        if "FROM repeat_loci" in sql:
            return Result([self.locus] if self.locus else [])
        if sql.startswith("SELECT validated, count_offset, reason FROM repeat_locus_count_units"):
            return Result([self.previous] if self.previous else [])
        if sql.startswith("INSERT INTO repeat_locus_count_units"):
            return Result(
                [
                    {
                        "locus_id": params["locus_id"],
                        "validated": params["validated"],
                        "count_offset": params["count_offset"],
                        "reason": params["reason"],
                        "updated_by_name": params["user_name"],
                        "updated_at": RECORDED_AT,
                    }
                ]
            )
        return Result([])

    async def commit(self) -> None:
        self.commits += 1

    def writes(self, table: str) -> list[dict]:
        return [params for sql, params in self.statements if sql.startswith(f"INSERT INTO {table}")]


@pytest.mark.asyncio
async def test_a_correction_is_recorded_with_its_reason_and_audited_before_and_after() -> None:
    session = _RecordSession(locus={"locus_id": "HD_HTT", "gene": "HTT"}, previous=None)

    record = await repeat_count_units.set_repeat_locus_count_units(
        session,
        locus_id="HD_HTT",
        validated=False,
        count_offset=-1,
        reason="Offset from validation report VAL-7",
        user=_admin(),
    )

    [upsert] = session.writes("repeat_locus_count_units")
    assert (upsert["count_offset"], upsert["validated"]) == (-1, False)
    [event] = session.writes("clinical_audit_events")
    assert event["family_identifier"] == "system:repeat-loci"
    assert event["action"] == "repeat_count_units_not_validated"
    assert event["summary"] == (
        "TRGT's counting units at HD_HTT (HTT): correction no correction → TRGT's count −1"
    )
    assert json.loads(event["before"]) == {
        "locus_id": "HD_HTT",
        "gene": "HTT",
        "validated": False,
        "count_offset": 0,
        "reason": None,
    }
    assert json.loads(event["after"]) == {
        "locus_id": "HD_HTT",
        "gene": "HTT",
        "validated": False,
        "count_offset": -1,
        "reason": "Offset from validation report VAL-7",
    }
    assert session.commits == 1
    assert (record["count_offset"], record["validated"]) == (-1, False)


@pytest.mark.asyncio
async def test_validating_a_corrected_locus_names_both_changes() -> None:
    session = _RecordSession(
        locus={"locus_id": "HD_HTT", "gene": "HTT"},
        previous={"validated": False, "count_offset": -1, "reason": "Offset from VAL-7"},
    )

    await repeat_count_units.set_repeat_locus_count_units(
        session, locus_id="HD_HTT", validated=True, count_offset=-2, reason="Validation report VAL-8", user=_admin()
    )

    [event] = session.writes("clinical_audit_events")
    assert event["action"] == "repeat_count_units_validated"
    assert event["summary"] == (
        "TRGT's counting units at HD_HTT (HTT): not validated → validated; "
        "correction TRGT's count −1 → TRGT's count −2"
    )
    assert json.loads(event["before"])["count_offset"] == -1
    assert json.loads(event["after"])["count_offset"] == -2


@pytest.mark.asyncio
async def test_removing_a_correction_is_audited_and_a_confirmation_names_the_correction() -> None:
    removed = _RecordSession(
        locus={"locus_id": "HD_HTT", "gene": "HTT"},
        previous={"validated": False, "count_offset": -1, "reason": "Offset from VAL-7"},
    )
    await repeat_count_units.set_repeat_locus_count_units(
        removed, locus_id="HD_HTT", validated=False, count_offset=0, reason="Entered on the wrong locus", user=_admin()
    )
    [event] = removed.writes("clinical_audit_events")
    assert event["summary"] == "TRGT's counting units at HD_HTT (HTT): correction TRGT's count −1 → no correction"

    confirmed = _RecordSession(
        locus={"locus_id": "HD_HTT", "gene": "HTT"},
        previous={"validated": True, "count_offset": -1, "reason": "Validation report VAL-8"},
    )
    await repeat_count_units.set_repeat_locus_count_units(
        confirmed, locus_id="HD_HTT", validated=True, count_offset=-1, reason="Checked on TRGT 5", user=_admin()
    )
    [event] = confirmed.writes("clinical_audit_events")
    assert event["summary"] == "TRGT's counting units at HD_HTT (HTT): validated, TRGT's count −1, confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [COUNT_OFFSET_LIMIT + 1, -COUNT_OFFSET_LIMIT - 1, 100, True, 1.5, "1"])
async def test_a_correction_out_of_bounds_or_not_a_whole_number_is_refused_and_nothing_is_written(offset) -> None:
    session = _RecordSession(locus={"locus_id": "HD_HTT", "gene": "HTT"}, previous=None)

    with pytest.raises(HTTPException) as refused:
        await repeat_count_units.set_repeat_locus_count_units(
            session, locus_id="HD_HTT", validated=False, count_offset=offset, reason="checked", user=_admin()
        )

    assert refused.value.status_code == 400
    assert session.statements == [] and session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [COUNT_OFFSET_LIMIT, -COUNT_OFFSET_LIMIT])
async def test_a_correction_at_the_bound_is_recorded(offset) -> None:
    session = _RecordSession(locus={"locus_id": "HD_HTT", "gene": "HTT"}, previous=None)
    record = await repeat_count_units.set_repeat_locus_count_units(
        session, locus_id="HD_HTT", validated=False, count_offset=offset, reason="checked", user=_admin()
    )
    assert record["count_offset"] == offset


@pytest.mark.parametrize(
    ("body", "accepted"),
    [
        ({"validated": False, "count_offset": -20, "reason": "r"}, True),
        ({"validated": False, "count_offset": 20, "reason": "r"}, True),
        ({"validated": False, "count_offset": 21, "reason": "r"}, False),
        ({"validated": False, "count_offset": -21, "reason": "r"}, False),
        ({"validated": False, "count_offset": 1.5, "reason": "r"}, False),
        ({"validated": False, "count_offset": "1", "reason": "r"}, False),
        ({"validated": False, "count_offset": True, "reason": "r"}, False),
        ({"validated": False, "reason": "r"}, False),
    ],
)
def test_the_request_states_a_whole_correction_within_the_bound(body, accepted) -> None:
    from pydantic import ValidationError

    if accepted:
        assert RepeatLocusCountUnitsUpdate.model_validate(body).count_offset == body["count_offset"]
    else:
        with pytest.raises(ValidationError):
            RepeatLocusCountUnitsUpdate.model_validate(body)


def test_the_bound_is_one_in_the_service_the_request_and_the_tables_check() -> None:
    field = RepeatLocusCountUnitsUpdate.model_fields["count_offset"]
    bounds = {type(item).__name__: item for item in field.metadata}
    assert (bounds["Ge"].ge, bounds["Le"].le) == (-COUNT_OFFSET_LIMIT, COUNT_OFFSET_LIMIT)
    baseline = (_SCHEMA_DIR / "02_reference.sql").read_text()
    assert (
        "CONSTRAINT repeat_locus_count_units_count_offset_check CHECK "
        f"(((count_offset >= '-{COUNT_OFFSET_LIMIT}'::integer) AND (count_offset <= {COUNT_OFFSET_LIMIT})))"
    ) in baseline


def test_fmr1_is_named_as_a_locus_where_a_constant_correction_is_wrong() -> None:
    assert "AGG" in count_offset_note("FMR1") and "constant" in count_offset_note("fmr1")
    assert count_offset_note("HTT") is None and count_offset_note(None) is None


def test_nothing_records_a_correction_in_advance() -> None:
    # No baseline, revision or seed writes QA's record: no correction for HTT, POLG or FMR1
    # exists until QA enters one.
    sources = [path.read_text() for path in sorted(_SCHEMA_DIR.glob("*.sql"))]
    sources += [path.read_text() for path in sorted(_MIGRATIONS_DIR.glob("*.py"))]
    sources.append(Path(repeat_expansion_pg.__file__).read_text())
    sources.append((Path(repeat_expansion_pg.__file__).parent / "repeat_expansion_catalog.py").read_text())
    for source in sources:
        assert not re.search(r"INSERT\s+INTO\s+repeat_locus_count_units", source, re.I)
        assert not re.search(r"UPDATE\s+repeat_locus_count_units", source, re.I)
