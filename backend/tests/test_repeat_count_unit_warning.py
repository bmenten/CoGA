"""A repeat count near a classification boundary is marked while TRGT's counting units at its
locus are not validated (owner's decision, 2026-10-14).

TRGT counts the motif across the region its own catalogue gives a locus, which need not be the
stretch the clinical repeat number counts: at FMR1 it leaves out the AGG interruptions (an
allele reads 1 to 3 below its clinical number, so a 55-repeat premutation can read 53), at HTT
it counts the CAG after the CAA interruption (one above), at POLG four flanking GCT copies. QA
and the laboratory director validate, per catalogue locus, that TRGT's count is the clinical
number. Until a locus is validated, a count within ``COUNT_UNIT_MARGIN`` (3) repeats of a
boundary its classification uses is marked with that boundary; the status is not changed.

- The warning: the boundaries are where the classifier itself changes status, on each side.
- The family table and the genome track: the warning per allele while the locus is not
  validated, none once it is; the statuses as before; what is known of the gene's count.
- The validation record: an admin sets it with a reason, each change a clinical audit event
  on its own chain; the admin list; the routes refuse a viewer.
(integration/test_repeat_count_units_storage.py runs the record and both reads on Postgres.)
All samples and values are synthetic.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.services import repeat_count_units, repeat_expansion_pg
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.repeat_count_units import (
    COUNT_UNIT_MARGIN,
    KNOWN_COUNT_UNIT_DIFFERENCES,
    REPEAT_COUNT_UNITS_AUDIT_CHAIN,
    count_unit_warning,
    known_count_unit_difference,
)
from backend.app.services.repeat_expansion_pg import classify_repeat_count

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
PABPN1 = {"warning_min": 11, "pathogenic_min": 12, "benign_min": 10, "benign_max": 10, "pathogenic_max": 18}
# Pathogenic by contraction: normal at exactly 2, pathogenic at 1 or 3.
VWA1 = {"warning_min": None, "pathogenic_min": 1, "benign_min": 2, "benign_max": 2, "pathogenic_max": 3}
# No catalogue row, or one that states nothing (STRchive's ADTKD_MUC1).
NO_THRESHOLDS = {"warning_min": None, "pathogenic_min": None}


def status_at(locus: dict):
    def classify(count: int) -> str:
        warning_min, pathogenic_min = locus["warning_min"], locus["pathogenic_min"]
        ranges = {key: value for key, value in locus.items() if key not in ("warning_min", "pathogenic_min")}
        return classify_repeat_count(count, warning_min, pathogenic_min, **ranges)

    return classify


def boundaries(count: int | None, locus: dict) -> list[tuple[int, str]] | None:
    warning = count_unit_warning(count, status_at(locus))
    if warning is None:
        return None
    assert warning["repeat_count"] == count
    assert warning["margin"] == COUNT_UNIT_MARGIN
    return [(boundary["repeat_count"], boundary["status"]) for boundary in warning["boundaries"]]


# ---- The warning ---------------------------------------------------------------------------


def test_the_margin_is_three_repeats() -> None:
    # FMR1's AGG interruptions: up to 3 left out, so a premutation of 55 can read 52.
    assert COUNT_UNIT_MARGIN == 3


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        # A 55-repeat premutation that reads 53 reads grey zone: marked, with the boundary.
        (53, [(55, "premutation")]),
        (52, [(55, "premutation")]),
        (51, None),
        # Normal, far from the grey zone.
        (40, None),
        (41, None),
        (42, [(45, "intermediate")]),
        (44, [(45, "intermediate")]),
        (45, [(44, "normal")]),
        (47, [(44, "normal")]),
        (48, None),
        (55, [(54, "intermediate")]),
        (57, [(54, "intermediate")]),
        (58, None),
        (198, [(201, "pathogenic")]),
        (200, [(201, "pathogenic")]),
        (201, [(200, "premutation")]),
        (203, [(200, "premutation")]),
        (204, None),
    ],
)
def test_an_fmr1_count_within_three_repeats_of_a_boundary_is_marked_with_it(count, expected) -> None:
    assert boundaries(count, FMR1) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        # HG002's pure 17-repeat allele reads 18: both far from the grey zone.
        (17, None),
        (18, None),
        (23, None),
        (24, [(27, "intermediate")]),
        (26, [(27, "intermediate")]),
        (35, [(36, "pathogenic")]),
        (38, [(35, "intermediate")]),
        (39, None),
    ],
)
def test_an_htt_count_is_marked_near_the_grey_zone_and_the_pathogenic_threshold(count, expected) -> None:
    assert boundaries(count, HTT) == expected


def test_a_count_between_two_boundaries_is_marked_with_both() -> None:
    # PABPN1: normal 10, grey zone 11, pathogenic from 12; the nearest on each side.
    assert boundaries(11, PABPN1) == [(10, "normal"), (12, "pathogenic")]
    assert boundaries(10, PABPN1) == [(11, "intermediate")]


def test_a_contraction_locus_is_marked_on_each_side_of_its_normal_count() -> None:
    # VWA1 is normal at exactly 2: one repeat either way reads pathogenic.
    assert boundaries(2, VWA1) == [(1, "pathogenic"), (3, "pathogenic")]


def test_a_benign_range_the_classification_uses_is_a_boundary() -> None:
    """Whatever the classification reads a count inside a stated benign range as, its edges
    are boundaries exactly where the classifier says so: no range is assumed here."""

    def benign_ten(count: int) -> str:
        return "normal" if count == 10 else "not_assessed"

    assert count_unit_warning(14, benign_ten) is None
    warning = count_unit_warning(13, benign_ten)
    assert warning is not None and warning["boundaries"] == [{"repeat_count": 10, "status": "normal"}]
    warning = count_unit_warning(10, benign_ten)
    assert warning is not None and warning["boundaries"] == [
        {"repeat_count": 9, "status": "not_assessed"},
        {"repeat_count": 11, "status": "not_assessed"},
    ]


@pytest.mark.parametrize("count", [0, 10, 300, 5000])
def test_a_count_at_a_locus_without_thresholds_is_not_marked(count: int) -> None:
    # Not assessed whatever the count: no boundary, and the status already asks for a look.
    assert boundaries(count, NO_THRESHOLDS) is None


def test_a_no_call_is_not_marked() -> None:
    assert count_unit_warning(None, status_at(FMR1)) is None


def test_a_locus_without_any_threshold_or_range_is_not_classified_again(monkeypatch) -> None:
    """A genome-wide TRGT catalogue holds mostly loci the catalogue lacks: nothing a count could
    cross, so the classifier is not asked six more times per allele there."""
    asked: list[int] = []

    def classify(count, *args, **kwargs):
        asked.append(count)
        return "not_assessed"

    monkeypatch.setattr(repeat_expansion_pg, "classify_repeat_count", classify)
    alleles = [{"repeat_count": 12, "status": "not_assessed"}, {"status": "unknown"}]

    marked = repeat_expansion_pg._with_count_unit_warnings(
        alleles, validated=False, warning_min=None, pathogenic_min=None
    )

    assert [allele["count_unit_warning"] for allele in marked] == [None, None]
    assert [allele["status"] for allele in marked] == ["not_assessed", "unknown"]
    assert asked == []
    # A validated locus is not classified again either; one with a threshold is.
    repeat_expansion_pg._with_count_unit_warnings(alleles, validated=True, warning_min=45, pathogenic_min=201)
    assert asked == []
    repeat_expansion_pg._with_count_unit_warnings(alleles, validated=False, warning_min=45, pathogenic_min=201)
    assert asked


def test_no_count_below_zero_is_read() -> None:
    asked: list[int] = []

    def classify(count: int) -> str:
        asked.append(count)
        return "normal"

    assert count_unit_warning(1, classify) is None
    assert min(asked) == 0


def test_the_known_differences_are_named_per_gene() -> None:
    assert set(KNOWN_COUNT_UNIT_DIFFERENCES) == {"FMR1", "HTT", "POLG"}
    assert "AGG" in known_count_unit_difference("FMR1")
    assert "AGG" in known_count_unit_difference("fmr1")
    assert "CAA" in known_count_unit_difference("HTT")
    assert "14" in known_count_unit_difference("POLG")
    assert known_count_unit_difference("ATXN1") is None
    assert known_count_unit_difference(None) is None


# ---- The family table -------------------------------------------------------------------------

PROBAND_UUID = "00000000-0000-4000-8000-000000000001"
MOTHER_UUID = "00000000-0000-4000-8000-000000000002"


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


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _RowsSession:
    """Answers every query with the given rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(" ".join(str(statement).split()))
        return _Result(self.rows)


def _table_row(sample_uuid: str, counts: list[int], *, gene: str, locus: dict, status: str, **extra) -> dict:
    """A row of the table query: the catalogue row's thresholds, and QA's record for it when
    ``count_units_validated`` is given."""
    return {
        "sample_uuid": sample_uuid,
        "locus_id": gene,
        "gene": gene,
        "display_name": gene,
        "disease": gene,
        "inheritance": None,
        "chr": "X",
        "start": 147_912_049,
        "end": 147_912_111,
        "motif": "CGG",
        "genotype": "1/2",
        "allele_count": len(counts),
        "alleles": [{"repeat_count": count, "status": "normal"} for count in counts],
        "benign_min": None,
        "benign_max": None,
        "pathogenic_max": None,
        "premutation_min": None,
        **locus,
        "status": status,
        **extra,
    }


def _warning(allele) -> list[tuple[int, str]] | None:
    if allele.count_unit_warning is None:
        return None
    return [(boundary.repeat_count, boundary.status) for boundary in allele.count_unit_warning.boundaries]


@pytest.mark.asyncio
async def test_the_family_table_marks_an_fmr1_count_of_53_and_not_one_of_40_while_the_locus_is_not_validated() -> None:
    rows = [
        _table_row(PROBAND_UUID, [40, 53], gene="FMR1", locus=FMR1, status="intermediate"),
        _table_row(MOTHER_UUID, [30, 40], gene="FMR1", locus=FMR1, status="normal"),
    ]
    session = _RowsSession(rows)

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(session, context=_context())

    [locus] = table.loci
    assert locus.count_units_validated is False
    assert locus.count_units_note == KNOWN_COUNT_UNIT_DIFFERENCES["FMR1"]
    proband = locus.calls["PROBAND1"]
    assert [_warning(allele) for allele in proband.alleles] == [None, [(55, "premutation")]]
    assert proband.alleles[1].count_unit_warning.repeat_count == 53
    assert proband.alleles[1].count_unit_warning.margin == 3
    # The statuses are the classification's, unchanged by the mark.
    assert [allele.status for allele in proband.alleles] == ["normal", "intermediate"]
    assert proband.status == locus.status == "intermediate"
    mother = locus.calls["MOTHER1"]
    assert [_warning(allele) for allele in mother.alleles] == [None, None]
    # The table reads QA's record for the catalogue row that classifies the call.
    assert "LEFT JOIN repeat_locus_count_units AS units ON units.locus_id = catalog.locus_id" in session.statements[0]


@pytest.mark.asyncio
async def test_the_family_table_does_not_mark_a_count_at_a_validated_locus() -> None:
    rows = [
        _table_row(PROBAND_UUID, [40, 53], gene="FMR1", locus=FMR1, status="intermediate", count_units_validated=True)
    ]

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_RowsSession(rows), context=_context())

    [locus] = table.loci
    assert locus.count_units_validated is True
    proband = locus.calls["PROBAND1"]
    assert [allele.count_unit_warning for allele in proband.alleles] == [None, None]
    assert [allele.status for allele in proband.alleles] == ["normal", "intermediate"]


@pytest.mark.asyncio
async def test_the_family_table_marks_a_count_near_the_htt_threshold_and_names_what_is_known() -> None:
    rows = [_table_row(PROBAND_UUID, [18, 35], gene="HTT", locus=HTT, status="intermediate")]

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_RowsSession(rows), context=_context())

    [locus] = table.loci
    assert locus.count_units_note == KNOWN_COUNT_UNIT_DIFFERENCES["HTT"]
    assert [_warning(allele) for allele in locus.calls["PROBAND1"].alleles] == [None, [(36, "pathogenic")]]


@pytest.mark.asyncio
async def test_the_family_table_names_nothing_for_a_gene_without_a_known_difference() -> None:
    rows = [_table_row(PROBAND_UUID, [12, 14], gene="ATXN1", locus={"warning_min": 36, "pathogenic_min": 39}, status="normal")]

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_RowsSession(rows), context=_context())

    [locus] = table.loci
    assert locus.count_units_note is None
    assert [allele.count_unit_warning for allele in locus.calls["PROBAND1"].alleles] == [None, None]


# ---- The genome track --------------------------------------------------------------------------


def _stored(counts: list[int], *, status: str, **extra) -> dict:
    """A stored FMR1 call as the track query returns it: the thresholds stored with it, the
    ranges of its catalogue row, and QA's record when given."""
    return {
        "locus_id": "FMR1",
        "gene": "FMR1",
        "display_name": "FMR1",
        "disease": "Fragile X syndrome",
        "chr": "X",
        "start": 147_912_049,
        "end": 147_912_111,
        "motif": "CGG",
        "warning_min": 45,
        "pathogenic_min": 201,
        "benign_min": 5,
        "benign_max": 44,
        "pathogenic_max": 2000,
        "premutation_min": 55,
        "status": status,
        "alleles": [{"repeat_count": count, "status": status} for count in counts],
        **extra,
    }


@pytest.mark.asyncio
async def test_the_genome_track_marks_an_fmr1_count_of_53_with_the_status_it_stored() -> None:
    session = _RowsSession([_stored([30, 53], status="intermediate")])

    response = await repeat_expansion_pg.get_sample_repeat_expansion_track_response(
        session, context=_context(), sample_name="PROBAND1", chromosomes=["X"]
    )

    [item] = response.items
    assert item.status == "intermediate"
    assert item.allele_repeat_counts == [30, 53]
    assert item.count_units_validated is False
    assert item.count_units_note == KNOWN_COUNT_UNIT_DIFFERENCES["FMR1"]
    assert [(warning.repeat_count, [(b.repeat_count, b.status) for b in warning.boundaries]) for warning in item.count_unit_warnings] == [
        (53, [(55, "premutation")])
    ]
    statement = session.statements[0]
    assert "LEFT JOIN repeat_loci AS catalog ON catalog.locus_id = repeat_expansions.locus_id" in statement
    assert "LEFT JOIN repeat_locus_count_units AS units ON units.locus_id = repeat_expansions.locus_id" in statement


@pytest.mark.asyncio
async def test_the_genome_track_does_not_mark_a_count_at_a_validated_locus_or_one_of_40() -> None:
    validated = _RowsSession([_stored([30, 53], status="intermediate", count_units_validated=True)])
    far = _RowsSession([_stored([30, 40], status="normal")])

    [item] = (
        await repeat_expansion_pg.get_sample_repeat_expansion_track_response(
            validated, context=_context(), sample_name="PROBAND1", chromosomes=["X"]
        )
    ).items
    assert item.count_units_validated is True
    assert item.count_unit_warnings == []

    [item] = (
        await repeat_expansion_pg.get_sample_repeat_expansion_track_response(
            far, context=_context(), sample_name="PROBAND1", chromosomes=["X"]
        )
    ).items
    assert item.count_unit_warnings == []


@pytest.mark.asyncio
async def test_the_genome_track_does_not_mark_a_call_stored_without_thresholds() -> None:
    uncatalogued = _stored([12, 300], status="not_assessed")
    uncatalogued.update(
        warning_min=None, pathogenic_min=None, benign_min=None, benign_max=None, pathogenic_max=None, premutation_min=None
    )

    [item] = (
        await repeat_expansion_pg.get_sample_repeat_expansion_track_response(
            _RowsSession([uncatalogued]), context=_context(), sample_name="PROBAND1", chromosomes=["X"]
        )
    ).items
    assert item.status == "not_assessed"
    assert item.count_unit_warnings == []


# ---- The validation record ---------------------------------------------------------------------


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-4000-8000-0000000000a1",
        username="qa-admin",
        email="qa-admin@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


class _One(_Result):
    def one(self):
        return self._rows[0]


class _RecordSession:
    """The validation record's queries: the catalogue row, QA's previous record, the upsert;
    and the clinical audit event's (its chain lock, head and insert)."""

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
            return _Result([self.locus] if self.locus else [])
        if sql.startswith("SELECT validated, reason FROM repeat_locus_count_units"):
            return _Result([self.previous] if self.previous else [])
        if sql.startswith("INSERT INTO repeat_locus_count_units"):
            return _One(
                [
                    {
                        "locus_id": params["locus_id"],
                        "validated": params["validated"],
                        "reason": params["reason"],
                        "updated_by_name": params["user_name"],
                        "updated_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
                    }
                ]
            )
        return _Result([])

    async def commit(self) -> None:
        self.commits += 1

    def writes(self, table: str) -> list[dict]:
        return [params for sql, params in self.statements if sql.startswith(f"INSERT INTO {table}")]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["", "   "])
async def test_a_record_without_a_reason_is_refused_and_nothing_is_written(reason: str) -> None:
    session = _RecordSession(locus={"locus_id": "FMR1", "gene": "FMR1"}, previous=None)

    with pytest.raises(HTTPException) as refused:
        await repeat_count_units.set_repeat_locus_count_units(
            session, locus_id="FMR1", validated=True, reason=reason, user=_admin()
        )

    assert refused.value.status_code == 400
    assert session.statements == [] and session.commits == 0


@pytest.mark.asyncio
async def test_a_record_for_a_locus_the_catalogue_lacks_is_answered_404_and_nothing_is_written() -> None:
    session = _RecordSession(locus=None, previous=None)

    with pytest.raises(HTTPException) as refused:
        await repeat_count_units.set_repeat_locus_count_units(
            session, locus_id="NO_SUCH_LOCUS", validated=True, reason="checked", user=_admin()
        )

    assert refused.value.status_code == 404
    assert session.writes("repeat_locus_count_units") == []
    assert session.writes("clinical_audit_events") == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_validating_a_locus_records_it_with_the_reason_and_audits_it_on_its_own_chain() -> None:
    session = _RecordSession(locus={"locus_id": "FMR1", "gene": "FMR1"}, previous=None)

    record = await repeat_count_units.set_repeat_locus_count_units(
        session, locus_id="FMR1", validated=True, reason="  validation report VAL-TRGT-01  ", user=_admin()
    )

    [upsert] = session.writes("repeat_locus_count_units")
    assert upsert["validated"] is True
    assert upsert["reason"] == "validation report VAL-TRGT-01"
    assert upsert["user_id"] == _admin().id and upsert["user_name"] == "qa-admin"
    [event] = session.writes("clinical_audit_events")
    assert event["family_identifier"] == REPEAT_COUNT_UNITS_AUDIT_CHAIN == "system:repeat-loci"
    assert event["family_id"] is None and event["variant_id"] is None
    assert event["action"] == "repeat_count_units_validated"
    assert event["actor"] == "qa-admin"
    assert event["summary"] == "TRGT's counting units at FMR1 (FMR1): not validated → validated"
    assert json.loads(event["before"]) == {"locus_id": "FMR1", "gene": "FMR1", "validated": False, "reason": None}
    assert json.loads(event["after"]) == {
        "locus_id": "FMR1",
        "gene": "FMR1",
        "validated": True,
        "reason": "validation report VAL-TRGT-01",
    }
    assert json.loads(event["metadata"]) == {"locus_id": "FMR1", "gene": "FMR1"}
    assert session.commits == 1
    assert record["validated"] is True and record["reason"] == "validation report VAL-TRGT-01"
    assert record["updated_by"] == "qa-admin"


@pytest.mark.asyncio
async def test_withdrawing_a_validation_is_audited_as_such() -> None:
    session = _RecordSession(
        locus={"locus_id": "HD_HTT", "gene": "HTT"}, previous={"validated": True, "reason": "checked"}
    )

    await repeat_count_units.set_repeat_locus_count_units(
        session, locus_id="HD_HTT", validated=False, reason="new TRGT catalogue", user=_admin()
    )

    [event] = session.writes("clinical_audit_events")
    assert event["action"] == "repeat_count_units_not_validated"
    assert event["summary"] == "TRGT's counting units at HD_HTT (HTT): validated → not validated"
    assert json.loads(event["before"])["reason"] == "checked"


@pytest.mark.asyncio
async def test_confirming_the_same_state_with_a_new_reason_is_audited_too() -> None:
    session = _RecordSession(
        locus={"locus_id": "FMR1", "gene": "FMR1"}, previous={"validated": True, "reason": "checked"}
    )

    await repeat_count_units.set_repeat_locus_count_units(
        session, locus_id="FMR1", validated=True, reason="checked again on TRGT 5", user=_admin()
    )

    [event] = session.writes("clinical_audit_events")
    assert event["action"] == "repeat_count_units_validated"
    assert event["summary"] == "TRGT's counting units at FMR1 (FMR1): validated, confirmed"


@pytest.mark.asyncio
async def test_the_admin_list_names_each_locus_its_record_its_calls_and_the_history() -> None:
    loci = [
        {
            "locus_id": "FMR1",
            "gene": "FMR1",
            "display_name": "FMR1",
            "disease": "Fragile X syndrome",
            "motif": "CGG",
            "warning_min": 45,
            "pathogenic_min": 201,
            "benign_min": None,
            "benign_max": None,
            "premutation_min": 55,
            "source": None,
            "validated": None,
            "reason": None,
            "updated_by": None,
            "updated_at": None,
            "call_count": 3,
        },
        {
            "locus_id": "HD_HTT",
            "gene": "HTT",
            "display_name": "HTT",
            "disease": "Huntington disease",
            "motif": "CAG",
            "warning_min": 27,
            "pathogenic_min": 36,
            "benign_min": 6,
            "benign_max": 26,
            "premutation_min": None,
            "source": "STRchive",
            "validated": True,
            "reason": "checked",
            "updated_by": "qa-admin",
            "updated_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
            "call_count": 0,
        },
    ]
    changes = [
        {
            "created_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
            "actor": "qa-admin",
            "action": "repeat_count_units_validated",
            "summary": "TRGT's counting units at HD_HTT (HTT): not validated → validated",
            "before": None,
            "after": json.dumps({"locus_id": "HD_HTT", "gene": "HTT", "validated": True, "reason": "checked"}),
        }
    ]

    class _ListSession:
        async def execute(self, statement, params=None):
            sql = " ".join(str(statement).split())
            if "FROM clinical_audit_events" in sql:
                assert params["chain"] == "system:repeat-loci"
                return _Result(changes)
            return _Result(loci)

    listed = await repeat_count_units.list_repeat_locus_count_units(_ListSession())

    assert listed["margin"] == 3
    fmr1, htt = listed["loci"]
    assert (fmr1["validated"], fmr1["source"], fmr1["call_count"]) == (False, "built-in", 3)
    assert fmr1["known_difference"] == KNOWN_COUNT_UNIT_DIFFERENCES["FMR1"]
    assert (htt["validated"], htt["source"], htt["reason"]) == (True, "STRchive", "checked")
    assert htt["known_difference"] == KNOWN_COUNT_UNIT_DIFFERENCES["HTT"]
    [change] = listed["changes"]
    assert (change["locus_id"], change["validated"], change["reason"], change["changed_by"]) == (
        "HD_HTT",
        True,
        "checked",
        "qa-admin",
    )


# ---- The routes ----------------------------------------------------------------------------------


class _FakeSession:
    async def rollback(self) -> None:
        return None


@contextmanager
def _client_as(role: str):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    user = CurrentUser(
        id="00000000-0000-4000-8000-0000000000a2",
        username=f"{role}@example.com",
        email=f"{role}@example.com",
        role=role,
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_user():
        return user

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides = original_overrides


def test_a_viewer_can_neither_list_nor_set_the_record(monkeypatch) -> None:
    from backend.app.routers import admin as admin_router

    calls: list[dict] = []

    async def _set(session, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(admin_router, "set_repeat_locus_count_units", _set)
    with _client_as("viewer") as client:
        listed = client.get("/api/admin/repeat-loci")
        updated = client.put("/api/admin/repeat-loci/FMR1/count-units", json={"validated": True, "reason": "x"})
    assert (listed.status_code, updated.status_code) == (403, 403)
    assert calls == []


def test_an_admin_sets_the_record_through_the_route(monkeypatch) -> None:
    from backend.app.routers import admin as admin_router

    calls: list[dict] = []

    async def _set(session, **kwargs):
        calls.append(kwargs)
        return {
            "locus_id": kwargs["locus_id"],
            "gene": "FMR1",
            "validated": kwargs["validated"],
            "reason": kwargs["reason"],
            "updated_by": kwargs["user"].username,
            "updated_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
        }

    async def _list(session):
        return {"margin": 3, "loci": [], "changes": []}

    monkeypatch.setattr(admin_router, "set_repeat_locus_count_units", _set)
    monkeypatch.setattr(admin_router, "list_repeat_locus_count_units", _list)
    with _client_as("admin") as client:
        updated = client.put("/api/admin/repeat-loci/FMR1/count-units", json={"validated": True, "reason": "checked"})
        listed = client.get("/api/admin/repeat-loci")
        missing_reason = client.put("/api/admin/repeat-loci/FMR1/count-units", json={"validated": True})

    assert updated.status_code == 200, updated.text
    assert updated.json()["validated"] is True
    [call] = calls
    assert (call["locus_id"], call["validated"], call["reason"], call["user"].role) == ("FMR1", True, "checked", "admin")
    assert listed.status_code == 200 and listed.json() == {"margin": 3, "loci": [], "changes": []}
    assert missing_reason.status_code == 422
