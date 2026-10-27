"""The repeat genome track reads a call as the family table does, and a review says why (CLIN-14).

The track showed the status and the thresholds stored with a call at import, and the ranges of
the catalogue row of its stored locus ID, while the table reclassified the call against the
catalogue row that classifies it now. The two disagreed once the catalogue was reseeded: an
FMR1 call of exactly 200, stored as pathogenic under the built-in row before #799, read
pathogenic on the track and a premutation in the table. Both now read the call the same way:
the same catalogue row (one SQL fragment), the alleles reclassified against it, the count-unit
warnings and the male chrX note.

A call reads ``review`` for one of two reasons: a count the catalogue cannot classify, or a
man's two different chrX alleles. The page said "outside catalogued ranges" for both; the call,
its locus and the track now carry the reason (``review_reason``).

The classifier reads a count between a row's benign range and its pathogenic threshold, where
the row states no grey zone, as the grey zone (the owner's decision of 2026-10-27): see
test_repeat_not_assessed.py. All samples and values are synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.family_metadata_context import FamilyMetadataContext

PROBAND = "00000000-0000-4000-8000-000000000001"
FATHER = "00000000-0000-4000-8000-000000000002"


def _context(*, proband_sex: str = "female") -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id="FAM1",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": PROBAND, "sample_id": "PROBAND1", "role": "proband", "affected": True, "sex": proband_sex},
            {"sample_uuid": FATHER, "sample_id": "FATHER1", "role": "father", "affected": False, "sex": "male"},
        ],
        sample_uuid_to_name={PROBAND: "PROBAND1", FATHER: "FATHER1"},
        sample_name_to_uuid={"PROBAND1": PROBAND, "FATHER1": FATHER},
        affected_sample_names=["PROBAND1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Session:
    """Answers the table's and the track's query with the rows given: a call as the query
    returns it, read against the catalogue row the query found."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def execute(self, _statement: Any, _params: Any = None) -> _Result:
        return _Result(self.rows)


def _fmr1(counts: list[int], *, stored: str, sample: str = PROBAND) -> dict[str, Any]:
    """An FMR1 call stored as ``stored`` under thresholds of its time, read against the
    current row: grey zone from 45, premutation from 55, pathogenic from 201 (#799)."""
    return {
        "sample_uuid": sample,
        "locus_id": "FMR1",
        "gene": "FMR1",
        "display_name": "FMR1",
        "disease": "Fragile X syndrome",
        "inheritance": "XLD",
        "chr": "chrX",
        "start": 147_912_049,
        "end": 147_912_111,
        "motif": "CGG",
        "warning_min": 45,
        "pathogenic_min": 201,
        "benign_min": 5,
        "benign_max": 44,
        "pathogenic_max": 2000,
        "premutation_min": 55,
        "count_units_validated": True,
        "status": stored,
        "genotype": "1/2",
        "allele_count": len(counts),
        "alleles": [{"repeat_count": count, "status": stored} for count in counts],
        "locus_not_resolved": None,
    }


def _vwa1(counts: list[int], *, stored: str) -> dict[str, Any]:
    """A VWA1 call: pathogenic by contraction, normal exactly 2, pathogenic 1 or 3."""
    return {
        **_fmr1(counts, stored=stored),
        "locus_id": "VWA1",
        "gene": "VWA1",
        "display_name": "VWA1",
        "disease": "Neuropathy",
        "inheritance": "AR",
        "chr": "chr1",
        "start": 1_435_798,
        "end": 1_435_822,
        "warning_min": None,
        "pathogenic_min": 1,
        "benign_min": 2,
        "benign_max": 2,
        "pathogenic_max": 3,
        "premutation_min": None,
    }


async def _table(rows: list[dict[str, Any]], **context: Any):
    return await repeat_expansion_pg.get_family_repeat_expansion_table_response(
        _Session(rows), context=_context(**context)
    )


async def _track(rows: list[dict[str, Any]], **context: Any):
    return await repeat_expansion_pg.get_sample_repeat_expansion_track_response(
        _Session(rows), context=_context(**context), sample_name="PROBAND1", chromosomes=["X"]
    )


@pytest.mark.asyncio
async def test_the_track_reads_a_stored_call_against_the_row_that_classifies_it_now() -> None:
    # Stored pathogenic under the built-in row's threshold of 200; the row now says 201.
    stored = _fmr1([30, 200], stored="pathogenic")

    [item] = (await _track([stored])).items
    [locus] = (await _table([stored])).loci

    # Before: the track showed "pathogenic", the status stored at import.
    assert item.status == locus.status == locus.calls["PROBAND1"].status == "premutation"
    assert (item.warning_min, item.pathogenic_min) == (locus.warning_min, locus.pathogenic_min) == (45, 201)


@pytest.mark.asyncio
async def test_the_track_reads_a_count_between_the_benign_range_and_the_threshold_as_the_grey_zone() -> None:
    # A row without a grey zone of its own: STRchive's ABCD3, benign up to 44, pathogenic 118.
    abcd3 = {
        **_fmr1([20, 60], stored="normal"),
        "locus_id": "OPDM5_ABCD3",
        "gene": "ABCD3",
        "display_name": "ABCD3",
        "chr": "chr1",
        "warning_min": None,
        "pathogenic_min": 118,
        "benign_min": 2,
        "benign_max": 44,
        "pathogenic_max": 694,
        "premutation_min": None,
    }

    [item] = (await _track([abcd3])).items
    [locus] = (await _table([abcd3])).loci

    assert item.status == locus.status == "intermediate"


@pytest.mark.asyncio
async def test_a_mans_two_chrx_alleles_read_review_for_that_reason() -> None:
    call = _fmr1([20, 30], stored="normal")

    [locus] = (await _table([call], proband_sex="male")).loci
    [item] = (await _track([call], proband_sex="male")).items

    proband = locus.calls["PROBAND1"]
    assert (proband.status, proband.review_reason) == ("review", "male_x_alleles")
    assert proband.note == repeat_expansion_pg.MALE_X_DISTINCT_ALLELES_NOTE
    assert (locus.status, locus.review_reason) == ("review", "male_x_alleles")
    # Before: the track showed the stored "normal", and no reason.
    assert (item.status, item.review_reason) == ("review", "male_x_alleles")


@pytest.mark.asyncio
async def test_a_count_the_catalogue_cannot_classify_reads_review_for_that_reason() -> None:
    call = _vwa1([2, 5], stored="normal")

    [locus] = (await _table([call])).loci

    assert (locus.calls["PROBAND1"].status, locus.calls["PROBAND1"].review_reason) == ("review", "outside_ranges")
    assert (locus.status, locus.review_reason) == ("review", "outside_ranges")


@pytest.mark.asyncio
async def test_a_locus_gives_the_catalogue_reason_before_the_note() -> None:
    # Two calls at one locus: one a count no range covers, one a man's two chrX alleles.
    unclassified = {**_vwa1([2, 5], stored="normal"), "chr": "chrX", "start": 147_912_049}
    mans = {**_vwa1([2, 2], stored="normal"), "sample_uuid": FATHER, "chr": "chrX", "start": 147_912_049}
    mans["alleles"] = [{"repeat_count": 2, "bp_length": 6, "status": "normal"}, {"repeat_count": 2, "bp_length": 9, "status": "normal"}]

    [locus] = (await _table([mans, unclassified])).loci

    assert locus.calls["FATHER1"].review_reason == "male_x_alleles"
    assert locus.calls["PROBAND1"].review_reason == "outside_ranges"
    assert (locus.status, locus.review_reason) == ("review", "outside_ranges")


@pytest.mark.asyncio
async def test_no_other_status_carries_a_reason() -> None:
    [locus] = (await _table([_fmr1([30, 60], stored="normal")], proband_sex="male")).loci

    # A premutation stays one, the note beside it.
    assert (locus.status, locus.review_reason) == ("premutation", None)
    assert locus.calls["PROBAND1"].review_reason is None
