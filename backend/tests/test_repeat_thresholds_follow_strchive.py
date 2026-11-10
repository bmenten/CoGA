"""The built-in repeat loci carry STRchive's thresholds, so a call reads the same whatever its TRID.

A TRGT call's TRID chooses the catalogue row that classifies it: a gene name (as in the demo
data) finds the built-in row (``repeat_expansion_catalog.py``), a STRchive ID such as
``SCA1_ATXN1`` the STRchive row (``data/ref-data/STRchive-loci.json``). The owner decided on
2026-10-09 that the built-in rows take STRchive's thresholds wherever STRchive has the locus, so
the same allele is called the same whatever the pipeline's TRIDs (CLIN-22). A threshold is the
first count of its range (the classifier tests ``>=``), as STRchive's inclusive ranges read:
``warning_min`` starts the intermediate range, ``pathogenic_min`` the pathogenic one.

- The guard: every built-in row has exactly one STRchive row, with the same two thresholds.
- Each side of every threshold at fifteen loci, run through the catalogue a start seeds and the
  import's TRID lookup, under the gene name and under the STRchive ID.
- Every start writes each built-in row with STRchive's thresholds, BEAN1's missing intermediate
  threshold as NULL.
(integration/test_repeat_status_storage.py stores and reads such calls on Postgres.)
All samples and values are synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.app.services.repeat_expansion_catalog import BUILTIN_REPEAT_LOCI
from backend.tests._repeat_fakes import (
    allele_statuses,
    import_lookup,
    seed_a_start,
    shipped_strchive_loci,
    trgt_insert_params,
    upserts,
)

N, I, P = "normal", "intermediate", "pathogenic"

# Built-in loci that STRchive does not list keep thresholds of their own, which the owner sets.
# None today: every built-in locus is in STRchive.
BUILT_IN_LOCI_STRCHIVE_LACKS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ThresholdLocus:
    """A built-in locus that carries STRchive's thresholds (CLIN-22), with counts on each side."""

    gene_trid: str  # the built-in row's locus_id, the TRID of the demo data
    strchive_trid: str  # STRchive's locus ID
    thresholds: tuple[int | None, int]  # (warning_min, pathogenic_min): STRchive's, the built-in row's
    # The status of an allele of each count, on each side of every threshold, under either row.
    statuses: dict[int, str]


THRESHOLD_LOCI = [
    ThresholdLocus("ATXN1", "SCA1_ATXN1", (36, 39), {35: N, 36: I, 38: I, 39: P, 44: P, 45: P}),
    ThresholdLocus("ATXN2", "SCA2_ATXN2", (29, 35), {28: N, 29: I, 31: I, 32: I, 33: I, 34: I, 35: P}),
    ThresholdLocus("CACNA1A", "SCA6_CACNA1A", (19, 21), {18: N, 19: I, 20: I, 21: P}),
    ThresholdLocus("ATXN7", "SCA7_ATXN7", (28, 37), {19: N, 20: N, 27: N, 28: I, 35: I, 36: I, 37: P}),
    ThresholdLocus("TBP", "SCA17_TBP", (41, 49), {40: N, 41: I, 42: I, 48: I, 49: P}),
    ThresholdLocus("ATXN8OS", "SCA8_ATXN8OS", (50, 71), {49: N, 50: I, 70: I, 71: P, 79: P, 80: P}),
    ThresholdLocus("CNBP", "DM2_CNBP", (27, 75), {26: N, 27: I, 54: I, 55: I, 74: I, 75: P}),
    ThresholdLocus("FXN", "FRDA_FXN", (34, 56), {33: N, 34: I, 55: I, 56: P, 65: P, 66: P}),
    ThresholdLocus("C9orf72", "FTDALS1_C9orf72", (24, 31), {23: N, 24: I, 29: I, 30: I, 31: P}),
    ThresholdLocus("ATXN10", "SCA10_ATXN10", (33, 800), {32: N, 33: I, 279: I, 280: I, 799: I, 800: P}),
    # STRchive gives SCA31 no intermediate range: below 110 is normal.
    ThresholdLocus("BEAN1", "SCA31_BEAN1", (None, 110), {109: N, 110: P, 299: P, 300: P, 499: P, 500: P}),
    ThresholdLocus("PPP2R2B", "SCA12_PPP2R2B", (40, 51), {39: N, 40: I, 43: I, 44: I, 50: I, 51: P}),
    ThresholdLocus("NOP56", "SCA36_NOP56", (15, 650), {14: N, 15: I, 30: I, 31: I, 649: I, 650: P}),
    ThresholdLocus("JPH3", "HDL2_JPH3", (29, 40), {28: N, 29: I, 35: I, 36: I, 39: I, 40: P, 41: P}),
    ThresholdLocus("PABPN1", "OPMD_PABPN1", (11, 12), {10: N, 11: I, 12: P, 13: P}),
]
LOCUS_IDS = [locus.gene_trid for locus in THRESHOLD_LOCI]


def _strchive_rows_of(builtin: dict, strchive_rows: list[dict]) -> list[dict]:
    """The STRchive rows of a built-in locus: those whose gene is its gene or one of its aliases."""
    names = {str(name).lower() for name in (builtin["gene"], *builtin.get("aliases", []))}
    return [row for row in strchive_rows if str(row["gene"]).lower() in names]


# ---- The guard -------------------------------------------------------------------------------


def test_every_built_in_row_has_one_strchive_row_with_the_same_thresholds() -> None:
    """Fails whenever a built-in row's thresholds drift from its STRchive row's: change the
    built-in row and STRchive-loci.json together."""
    strchive_rows = shipped_strchive_loci()
    assert strchive_rows, "the shipped STRchive file did not load"

    unmatched: dict[str, list[str]] = {}
    differ: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    for builtin in BUILTIN_REPEAT_LOCI:
        rows = _strchive_rows_of(builtin, strchive_rows)
        if builtin["locus_id"] in BUILT_IN_LOCI_STRCHIVE_LACKS:
            assert rows == [], f"{builtin['locus_id']} is in STRchive: take its thresholds"
            continue
        if len(rows) != 1:
            unmatched[builtin["locus_id"]] = [row["locus_id"] for row in rows]
            continue
        [row] = rows
        built_in = (builtin.get("warning_min"), builtin.get("pathogenic_min"))
        strchive = (row["warning_min"], row["pathogenic_min"])
        if built_in != strchive:
            differ[f"{builtin['locus_id']} / {row['locus_id']}"] = {"built-in": built_in, "STRchive": strchive}

    assert unmatched == {}, "each built-in locus needs exactly one STRchive row (or an entry in BUILT_IN_LOCI_STRCHIVE_LACKS)"
    assert differ == {}


def test_each_case_below_pairs_a_built_in_row_with_its_strchive_row_and_its_thresholds() -> None:
    strchive_by_id = {row["locus_id"]: row for row in shipped_strchive_loci()}
    builtin_by_id = {locus["locus_id"]: locus for locus in BUILTIN_REPEAT_LOCI}
    for locus in THRESHOLD_LOCI:
        strchive = strchive_by_id[locus.strchive_trid]
        assert strchive["gene"].lower() == builtin_by_id[locus.gene_trid]["gene"].lower()
        assert (strchive["warning_min"], strchive["pathogenic_min"]) == locus.thresholds


# ---- Through the catalogue a start seeds and the import's TRID lookup ---------------------------


def _call(lookup: dict[str, dict], *, trid: str, strchive: dict, count: int) -> dict:
    """A woman's TRGT call at the locus: a normal allele of 5 repeats and one of ``count``.

    The record is the same under either TRID: STRchive's coordinates and motif."""
    motif = str(strchive["motif"])
    start = int(strchive["metadata"]["start_hg38"])
    return trgt_insert_params(
        lookup,
        info_field=f"TRID={trid};END={start + len(motif) * count};MOTIFS={motif}",
        sample_field=f"1/2:{5 * len(motif)},{count * len(motif)}:5,{count}",
        chrom=str(strchive["metadata"]["chrom"]),
        pos=start,
        ref=motif,
    )


@pytest.mark.parametrize("trid_kind", ["gene name", "STRchive ID"])
@pytest.mark.parametrize("locus", THRESHOLD_LOCI, ids=LOCUS_IDS)
@pytest.mark.asyncio
async def test_each_side_of_every_threshold_reads_the_same_under_either_trid(
    monkeypatch, locus: ThresholdLocus, trid_kind: str
) -> None:
    lookup = await import_lookup(monkeypatch)
    strchive = lookup[locus.strchive_trid.lower()]
    trid = locus.gene_trid if trid_kind == "gene name" else locus.strchive_trid

    statuses: dict[int, str] = {}
    stored_thresholds: set[tuple[int | None, int | None]] = set()
    for count in locus.statuses:
        params = _call(lookup, trid=trid, strchive=strchive, count=count)
        assert params["locus_id"] == trid  # the row the TRID resolved to
        assert allele_statuses(params)[0] == N
        statuses[count] = str(params["status"])
        stored_thresholds.add((params["warning_min"], params["pathogenic_min"]))

    assert statuses == locus.statuses
    # The call is stored with the thresholds that classified it.
    assert stored_thresholds == {locus.thresholds}


@pytest.mark.asyncio
async def test_each_start_writes_every_built_in_row_whole_with_strchives_thresholds(monkeypatch) -> None:
    """The seed upserts each row by locus_id, both thresholds and the metadata from the row, so
    BEAN1's missing intermediate threshold is written as NULL."""
    session = await seed_a_start(monkeypatch)
    upserted = {params["locus_id"]: (sql, params) for sql, params in upserts(session)}

    for locus in THRESHOLD_LOCI:
        sql, params = upserted[locus.gene_trid]
        assert "ON CONFLICT (locus_id) DO UPDATE" in sql
        for column in ("warning_min", "pathogenic_min", "metadata"):
            assert f"{column} = EXCLUDED.{column}" in sql
        strchive = upserted[locus.strchive_trid][1]
        assert (params["warning_min"], params["pathogenic_min"]) == locus.thresholds
        assert (strchive["warning_min"], strchive["pathogenic_min"]) == locus.thresholds
    assert upserted["BEAN1"][1]["warning_min"] is None
    assert session.committed
