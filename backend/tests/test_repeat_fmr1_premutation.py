"""An FMR1 premutation has its own status, and a full mutation starts at 201 repeats.

STRchive gives FMR1 one intermediate range, 45-200, which holds both the grey zone (45-54)
and the premutation (55-200). A premutation is what a carrier screen reports for fragile X:
the allele does not cause the disease in its carrier but can expand to a full mutation in a
child. The catalogue adds the premutation threshold (``PREMUTATION_MIN_BY_GENE``), the
classification gives such an allele ``premutation``, which ranks between the grey zone and a
full mutation, and the stored status may hold it (integration/test_repeat_status_storage.py).

A full mutation is more than 200 repeats. FMR1 has two catalogue rows, the built-in ``FMR1``
and STRchive's ``FXS_FMR1``, and the call's TRID decides which one classifies it. Both rows
start the full mutation at 201, so a 200-repeat premutation reads as a full mutation under
neither.
All samples and values are synthetic.
"""

from __future__ import annotations

import json

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.repeat_expansion_catalog import BUILTIN_REPEAT_LOCI, PREMUTATION_MIN_BY_GENE
from backend.app.services.repeat_expansion_pg import (
    REPEAT_STATUS_RANK,
    classify_repeat_count,
    summarize_repeat_status,
)
from backend.tests._repeat_fakes import (
    RecordingSession,
    RowsSession,
    allele_statuses,
    family_context,
    import_lookup,
    shipped_strchive_loci,
    trgt_insert_params,
)

# STRchive's FXS_FMR1 ranges, with the premutation threshold the catalogue adds.
FMR1 = {"warning_min": 45, "pathogenic_min": 201, "benign_min": 5, "benign_max": 44, "pathogenic_max": 2000}


def classify(count: int, *, premutation_min: int | None = 55) -> str:
    return classify_repeat_count(count, **FMR1, premutation_min=premutation_min)


@pytest.mark.parametrize(
    ("count", "status"),
    [(30, "normal"), (44, "normal"), (45, "intermediate"), (54, "intermediate"), (55, "premutation"), (200, "premutation"), (201, "pathogenic")],
)
def test_fmr1_counts_read_as_normal_grey_zone_premutation_or_full_mutation(count: int, status: str) -> None:
    assert classify(count) == status


def test_a_locus_without_a_premutation_range_keeps_its_intermediate_range() -> None:
    assert classify(60, premutation_min=None) == "intermediate"


def test_a_premutation_ranks_between_the_grey_zone_and_a_full_mutation() -> None:
    assert REPEAT_STATUS_RANK["intermediate"] < REPEAT_STATUS_RANK["premutation"] < REPEAT_STATUS_RANK["pathogenic"]
    assert summarize_repeat_status(["normal", "premutation", "intermediate"]) == "premutation"
    assert summarize_repeat_status(["premutation", "pathogenic"]) == "pathogenic"


def test_the_catalogue_sets_the_fmr1_premutation_threshold() -> None:
    assert PREMUTATION_MIN_BY_GENE == {"FMR1": 55}
    assert any(locus["gene"] == "FMR1" for locus in BUILTIN_REPEAT_LOCI)


@pytest.mark.asyncio
async def test_every_catalogue_entry_of_fmr1_carries_the_threshold_and_no_other_locus_does() -> None:
    session = RecordingSession()
    strchive_fmr1 = {"locus_id": "FXS_FMR1", "gene": "FMR1", "metadata": {"source": "STRchive", "benign_max": 44}}
    builtin_fmr1 = next(locus for locus in BUILTIN_REPEAT_LOCI if locus["gene"] == "FMR1")
    htt = next(locus for locus in BUILTIN_REPEAT_LOCI if locus["gene"] == "HTT")

    await repeat_expansion_pg._seed_repeat_catalog_entries(session, [builtin_fmr1, strchive_fmr1, htt])  # type: ignore[arg-type]

    metadata = [json.loads(params["metadata_json"]) for params in session.params]
    assert [entry.get("premutation_min") for entry in metadata] == [55, 55, None]
    # The catalogue's own metadata stays.
    assert metadata[1]["source"] == "STRchive" and metadata[1]["benign_max"] == 44


def _insert_status(*, sex: str, sample_field: str) -> str:
    locus = {
        "locus_id": "FXS_FMR1",
        "gene": "FMR1",
        "display_name": "FMR1",
        "disease": "Fragile X syndrome",
        "inheritance": "XD",
        "motif": "CGG",
        "motif_index": 0,
        "warning_min": 45,
        "pathogenic_min": 201,
        "metadata": {"benign_min": 5, "benign_max": 44, "pathogenic_max": 2000, "premutation_min": 55},
    }
    params = trgt_insert_params(
        {"fxs_fmr1": locus},
        info_field="TRID=FXS_FMR1;END=147912111;MOTIFS=CGG",
        sample_field=sample_field,
        chrom="chrX",
        pos=147912049,
        ref="CGG",
        sex=sex,
        family_id="COUPLE1",
    )
    return str(params["status"])


def test_a_female_partners_premutation_is_stored_as_one() -> None:
    assert _insert_status(sex="female", sample_field="1/2:90,180:30,60") == "premutation"
    assert _insert_status(sex="female", sample_field="1/2:90,150:30,50") == "intermediate"


def test_a_males_premutation_is_one_too() -> None:
    assert _insert_status(sex="male", sample_field="1/1:180,180:60,60") == "premutation"


@pytest.mark.asyncio
async def test_the_family_table_reads_the_premutation_from_the_catalogue() -> None:
    # The premutation threshold rides in the catalogue row's metadata, not on the stored call:
    # the table reads it from there.
    stored = {
        "sample_uuid": "00000000-0000-4000-8000-000000000001",
        "locus_id": "FXS_FMR1",
        "gene": "FMR1",
        "display_name": "FMR1",
        "disease": "Fragile X syndrome",
        "inheritance": "XD",
        "chr": "X",
        "start": 147912049,
        "end": 147912111,
        "motif": "CGG",
        "genotype": "1/2",
        "allele_count": 2,
        "alleles": [
            {"repeat_count": 30, "status": "normal"},
            {"repeat_count": 60, "status": "premutation"},
        ],
        **FMR1,
        "premutation_min": 55,
        "status": "premutation",
    }
    context = family_context(
        ("00000000-0000-4000-8000-000000000001", "MOTHER1", "mother", False, "female"), family_id="COUPLE1"
    )

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(RowsSession([stored]), context=context)

    [locus] = table.loci
    assert locus.premutation_min == 55
    assert locus.status == "premutation"
    call = locus.calls["MOTHER1"]
    assert [allele.status for allele in call.alleles] == ["normal", "premutation"]
    assert call.status == "premutation"


# ---- The full mutation starts at 201, under either catalogue row ---------------------
#
# The classifier reads pathogenic_min as the first pathogenic count (">="), as STRchive's
# inclusive ranges do (FXS_FMR1: intermediate 45-200, pathogenic 201-2000). A full mutation is
# "more than 200" repeats, so its first count is 201. These tests run the catalogue a start
# leaves (seed_builtin_repeat_catalog over the built-in loci and the shipped STRchive file)
# through the import's lookup, so the TRID picks the row as it does at import.


def _fmr1_call(lookup: dict[str, dict], *, trid: str, sex: str, sample_field: str) -> dict:
    return trgt_insert_params(
        lookup,
        info_field=f"TRID={trid};END=147912111;MOTIFS=CGG",
        sample_field=sample_field,
        chrom="chrX",
        pos=147912049,
        ref="CGG",
        sex=sex,
        family_id="COUPLE1",
    )


# The TRIDs of FMR1's two catalogue rows: "FMR1" resolves to the built-in row, "FXS_FMR1"
# (STRchive's id) to the STRchive one.
FMR1_TRIDS = ["FMR1", "FXS_FMR1"]


@pytest.mark.parametrize("trid", FMR1_TRIDS)
@pytest.mark.parametrize(
    ("count", "status"),
    [
        (44, "normal"),
        (45, "intermediate"),
        (54, "intermediate"),
        (55, "premutation"),
        (200, "premutation"),
        (201, "pathogenic"),
    ],
)
@pytest.mark.asyncio
async def test_fmr1_ranges_hold_under_either_catalogue_row(monkeypatch, trid: str, count: int, status: str) -> None:
    lookup = await import_lookup(monkeypatch)

    params = _fmr1_call(lookup, trid=trid, sex="female", sample_field=f"1/2:90,{3 * count}:30,{count}")

    assert params["locus_id"] == trid  # the row the TRID resolved to
    assert allele_statuses(params) == ["normal", status]
    assert params["status"] == status


@pytest.mark.parametrize("trid", FMR1_TRIDS)
@pytest.mark.parametrize(
    ("count", "status"),
    [(200, "premutation"), (201, "pathogenic")],
)
# TRGT run with --karyotype XY writes the allele once; without it, twice.
@pytest.mark.parametrize("ploidy", ["hemizygous", "written twice"])
@pytest.mark.asyncio
async def test_a_males_single_x_allele_is_a_premutation_at_200_and_a_full_mutation_at_201(
    monkeypatch, trid: str, count: int, status: str, ploidy: str
) -> None:
    lookup = await import_lookup(monkeypatch)
    sample_field = (
        f"1:{3 * count}:{count}" if ploidy == "hemizygous" else f"1/1:{3 * count},{3 * count}:{count},{count}"
    )

    params = _fmr1_call(lookup, trid=trid, sex="male", sample_field=sample_field)

    assert params["allele_count"] == 1
    assert params["status"] == status


def test_the_built_in_fmr1_row_and_strchive_start_the_full_mutation_at_201() -> None:
    builtin = next(locus for locus in BUILTIN_REPEAT_LOCI if locus["locus_id"] == "FMR1")
    strchive = next(locus for locus in shipped_strchive_loci() if locus["locus_id"] == "FXS_FMR1")

    # Inclusive ranges: the full mutation starts right after the intermediate range ends.
    assert strchive["metadata"]["intermediate_max"] == 200
    assert (strchive["warning_min"], strchive["pathogenic_min"]) == (45, 201)
    assert (builtin["warning_min"], builtin["pathogenic_min"]) == (45, 201)
