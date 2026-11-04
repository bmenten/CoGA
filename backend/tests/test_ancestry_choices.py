"""A user's choice of how the ancestry PCA reads one individual: BED files, panels' regions and
what each choice does to the projection. All values are synthetic."""
from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi import HTTPException

from backend.app.ancestry_schemas import AncestrySampleChoiceIn
from backend.app.services import ancestry_callsets as callsets
from backend.app.services import ancestry_choices as choices
from backend.tests.test_ancestry_callsets import EXOME, N, member, reference, rows

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _few_markers(monkeypatch):
    monkeypatch.setattr(callsets, "MIN_MARKERS", 20)


def test_a_bed_file_is_read_as_one_based_inclusive_intervals_skipping_headers():
    bed = b"track name=kit\n# comment\nbrowser position chr1:1-10\nchr1\t0\t10\nchr1 19 30 extra\nchrX\t5\t6\n\n"
    assert choices.parse_bed(bed) == {"1": [(1, 10), (20, 30)], "X": [(6, 6)]}


@pytest.mark.parametrize("bed,message", [
    (b"chr1\t10\n", "Line 1 of the BED file is not an interval."),
    (b"chr1\t1\t2\nchr1\tten\t20\n", "Line 2 of the BED file is not an interval."),
    (b"chr1\t20\t10\n", "Line 1 of the BED file is not an interval."),
    (b"chr1\t-1\t10\n", "Line 1 of the BED file is not an interval."),
    (b"# only a header\n", "The BED file holds no interval."),
    (b"chr1\t\xff\t1\n", "The BED file is not UTF-8 text."),
])
def test_a_bed_file_that_is_no_list_of_intervals_is_refused_by_line_never_by_content(bed, message):
    with pytest.raises(HTTPException) as refused:
        choices.parse_bed(bed)
    assert refused.value.detail == message and b"ten" not in message.encode()


def test_a_bed_file_beyond_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(choices, "MAX_BED_BYTES", 10)
    with pytest.raises(HTTPException) as refused:
        choices.parse_bed(b"chr1\t0\t100\n")
    assert refused.value.status_code == 413


def test_the_markers_inside_regions_and_only_coding_ones_for_a_panel():
    model = reference()
    # Markers sit at positions 1..100 on chromosome 1; the mask holds the first 80.
    intervals = {"1": [(5, 9), (8, 12), (78, 85)], "2": [(1, 100)]}
    assert choices.marker_loci_in(model, intervals, coding_only=False) == [f"1:{p}" for p in [*range(5, 13), *range(78, 86)]]
    assert choices.marker_loci_in(model, intervals, coding_only=True) == [f"1:{p}" for p in [*range(5, 13), *range(78, 81)]]


def test_regions_with_no_marker_are_refused_and_the_record_keeps_no_file():
    with pytest.raises(HTTPException, match="none of the reference markers"):
        choices.regions_record(source="bed", name="kit.bed", loci=[], interval_count=3, reference_sha256="0" * 64)
    record = choices.regions_record(source="bed", name="kit.bed", loci=["1:5"], interval_count=3,
                                    reference_sha256="0" * 64, data=b"chr1\t4\t5\n")
    assert record["covered_markers"] == 1 and len(record["sha256"]) == 64 and "data" not in record


@pytest.mark.parametrize("name,label", [
    ("kits/Twist exome.bed", "Twist exome.bed"), ("C:\\\\kits\\\\a.bed", "a.bed"), ("", "BED file"),
    ("bad\x00name\n.bed", "badname.bed"), ("x" * 300, "x" * 120),
])
def test_a_bed_files_name_is_shown_as_its_printable_last_part(name, label):
    assert choices.file_label(name) == label


@pytest.mark.parametrize("member_row,choice,status", [
    (None, AncestrySampleChoiceIn(mode="auto"), 404),
    ({"sample_metadata": {"assay": "nipt_cfdna"}}, AncestrySampleChoiceIn(mode="exclude"), 409),
    ({"sample_metadata": {}}, AncestrySampleChoiceIn(mode="callset"), 422),
    ({"sample_metadata": {}}, AncestrySampleChoiceIn(mode="auto", callset="clair3"), 422),
    ({"sample_metadata": {}}, AncestrySampleChoiceIn(mode="exclude", panel_id="p"), 422),
])
def test_a_choice_names_an_individual_of_the_family_and_is_consistent(member_row, choice, status):
    with pytest.raises(HTTPException) as refused:
        choices.check_choice(member_row, choice)
    assert refused.value.status_code == status


def test_a_consistent_choice_is_accepted():
    choices.check_choice({"sample_metadata": {}}, AncestrySampleChoiceIn(mode="callset", callset="glimpse2", panel_id="p"))


def _choice(mode, callset=None, loci=None):
    regions = None if loci is None else {"source": "bed", "name": "kit.bed", "panel_id": None, "panel_version": None,
                                         "sha256": "0" * 64, "interval_count": 1, "covered_markers": len(loci),
                                         "reference_sha256": "0" * 64, "loci": loci}
    return {"mode": mode, "callset": callset, "regions": regions, "chosen_at": NOW}


def test_a_panel_callset_is_read_inside_the_regions_a_user_gave():
    model = reference()
    # A panel: the callset holds markers 0..39 only, too few of the exome to be read as one.
    data = rows("PROBAND", range(40))
    auto = callsets.project_family(data, model, [member("PROBAND")])[0]
    assert auto.status == "insufficient_data" and auto.kind == "partial"
    given = _choice("callset", "clair3", [f"1:{p}" for p in range(1, 41)])
    chosen = callsets.project_family(data, model, [member("PROBAND")], {"PROBAND": given})[0]
    scan = chosen.candidates[0]
    assert (scan.kind, scan.regions_ratio, scan.covered_markers) == ("regions", 1.0, 40)
    assert scan.qc["assumed_ref"] == 10 and scan.qc["outside_covered"] == N - 40
    assert chosen.status == "projected" and chosen.kind == "regions"
    assert chosen.choice is not None and chosen.choice.regions.name == "kit.bed" and chosen.choice.chosen_at == NOW


def test_regions_the_callset_does_not_cover_are_refused():
    model = reference()
    data = rows("PROBAND", range(40))
    # Regions over markers the callset holds no call at: far fewer than expected there.
    given = _choice("callset", "clair3", [f"1:{p}" for p in range(41, 101)])
    result = callsets.project_family(data, model, [member("PROBAND")], {"PROBAND": given})[0]
    assert result.kind == "partial" and result.status == "insufficient_data"
    assert result.reason == callsets.REASON_REGIONS and result.candidates[0].regions_ratio == 0.0


def test_a_chosen_callset_is_read_even_where_another_is_preferred():
    model = reference()
    both = rows("PROBAND", range(N)) + rows("PROBAND", range(N), callset="glimpse2", site_filter=())
    result = callsets.project_family(both, model, [member("PROBAND")], {"PROBAND": _choice("callset", "glimpse2")})[0]
    assert result.callset == "glimpse2" and result.status == "projected"
    missing = callsets.project_family(rows("PROBAND", range(N)), model, [member("PROBAND")],
                                      {"PROBAND": _choice("callset", "nipt")})[0]
    assert missing.status == "insufficient_data" and missing.reason == callsets.NO_CHOSEN_CALLSET
    assert [c.callset for c in missing.candidates] == ["clair3"]


def test_an_embryo_is_read_only_when_a_callset_is_chosen_and_a_user_may_leave_anyone_out():
    model = reference()
    family = [member("PROBAND"), member("EMB1", "embryo")]
    data = rows("PROBAND", range(N)) + rows("EMB1", range(N))
    default = {r.sample_id: r for r in callsets.project_family(data, model, family)}
    # Left out, but its callsets are shown, so one can be chosen.
    assert default["EMB1"].status == "excluded" and [c.callset for c in default["EMB1"].candidates] == ["clair3"]
    chosen = {r.sample_id: r for r in callsets.project_family(
        data, model, family, {"EMB1": _choice("callset", "clair3"), "PROBAND": _choice("exclude")})}
    assert chosen["EMB1"].status == "projected" and chosen["EMB1"].choice.mode == "callset"
    assert (chosen["PROBAND"].status, chosen["PROBAND"].reason) == ("excluded", callsets.EXCLUDED_BY_CHOICE)
    assert chosen["PROBAND"].pcs is None and chosen["PROBAND"].candidates


def test_a_cfdna_sample_stays_out_whatever_is_stored_for_it():
    model = reference()
    family = [member("PLASMA", "mother", {"assay": "nipt_cfdna"})]
    result = callsets.project_family(rows("PLASMA", range(N)), model, family, {"PLASMA": _choice("callset", "clair3")})[0]
    assert (result.status, result.reason, result.candidates) == ("excluded", callsets.EXCLUDED_CFDNA, [])


def test_the_regions_mask_marks_the_markers_named():
    mask = callsets.regions_mask(reference(), ["1:1", "1:100", "2:5"])
    assert mask.sum() == 2 and mask[0] and mask[-1] and not np.any(mask[1:-1])
    assert EXOME == 80
