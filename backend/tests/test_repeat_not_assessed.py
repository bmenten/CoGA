"""A repeat call at a locus without thresholds reads *not assessed*, not *normal* (CLIN-26).

A TRGT call whose TRID matches no catalogue row got no thresholds, so every allele read
``normal`` ("No reference cutoff" on the repeat page) and the aberrant-only view hid it: an
expansion at a locus the catalogue lacks, or under a TRID spelled differently, could not be
seen. The owner decided on 2026-10-13 that such a call gets a status of its own,
``not_assessed``, distinct from ``normal``, from ``unknown`` (no call) and from ``review`` (a
count the catalogue cannot classify); it ranks above ``normal``, so the aberrant-only view
keeps it, and the page counts such calls.

A locus without thresholds has no intermediate, premutation or pathogenic threshold: no
catalogue row matches the TRID, or the row it matches states none (STRchive's ADTKD_MUC1 and
CPEO_POLG). A row with one threshold keeps today's reading: normal below it.

Where such a row states a benign range, a count inside it reads ``normal`` (the owner's
decision of 2026-10-14, CLIN-36): STRchive's CPEO_POLG states exactly 10 repeats and no
threshold, so a POLG allele of 10 read not assessed from CLIN-26 on, and every family whose
TRGT catalogue holds POLG showed it under *Aberrant only*. A count outside the range, above it
or below it, stays not assessed (a benign range is not a threshold), as does every count at
ADTKD_MUC1, which states nothing. The count is the one TRGT reports for the catalogue motif.

- The classification, the ranking, and the known loci unchanged.
- The import: the status stored with a call under an unknown TRID, a TRID spelled differently,
  and a catalogue row without thresholds, through the catalogue a start seeds; at POLG, normal
  inside its benign range only.
- The family table: a call stored as ``normal`` before reads not assessed at once, and a POLG
  call stored as ``not_assessed`` inside its benign range (CLIN-26 to CLIN-36) reads normal.
- The genome tracks serve the stored status, ``not_assessed`` included.
- The API models, the stored status's check constraint and the ranking list the same statuses.
(integration/test_repeat_status_storage.py stores the status on Postgres.)
All samples and values are synthetic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.repeat_expansion_pg import (
    MALE_X_DISTINCT_ALLELES_NOTE,
    REPEAT_STATUS_RANK,
    classify_repeat_count,
    summarize_repeat_status,
)

NOT_ASSESSED = "not_assessed"

# STRchive's ranges (data/ref-data/STRchive-loci.json), with the premutation threshold the
# catalogue adds to FMR1.
HTT = {"warning_min": 27, "pathogenic_min": 36, "benign_min": 6, "benign_max": 26, "pathogenic_max": 250}
FMR1 = {
    "warning_min": 45,
    "pathogenic_min": 201,
    "benign_min": 5,
    "benign_max": 44,
    "pathogenic_max": 2000,
    "premutation_min": 55,
}
VWA1 = {"warning_min": None, "pathogenic_min": 1, "benign_min": 2, "benign_max": 2, "pathogenic_max": 3}
# A pathogenic threshold and no grey zone, below it a benign range: ABCD3.
ABCD3 = {"warning_min": None, "pathogenic_min": 118, "benign_min": 3, "benign_max": 44, "pathogenic_max": 694}
# Rows that state no threshold: ADTKD_MUC1 states nothing, CPEO_POLG a benign count of 10.
NO_RANGES = {"warning_min": None, "pathogenic_min": None, "benign_min": None, "benign_max": None, "pathogenic_max": None}
POLG = {"warning_min": None, "pathogenic_min": None, "benign_min": 10, "benign_max": 10, "pathogenic_max": None}


def classify(count: int | None, locus: dict) -> str:
    warning_min, pathogenic_min = locus["warning_min"], locus["pathogenic_min"]
    ranges = {key: value for key, value in locus.items() if key not in ("warning_min", "pathogenic_min")}
    return classify_repeat_count(count, warning_min, pathogenic_min, **ranges)


# ---- The classification ----------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 5, 10, 12, 300, 5000])
def test_a_called_allele_at_a_locus_without_thresholds_is_not_assessed(count: int) -> None:
    # "normal" before: a 5000-repeat expansion read as healthy as a 5-repeat allele.
    assert classify(count, NO_RANGES) == NOT_ASSESSED


def test_a_no_call_is_unknown_whether_or_not_the_locus_has_thresholds() -> None:
    assert classify(None, NO_RANGES) == "unknown"
    assert classify(None, POLG) == "unknown"
    assert classify(None, HTT) == "unknown"


# ---- A count inside a stated benign range (CLIN-36) -------------------------------------------


def test_a_count_inside_the_benign_range_of_a_locus_without_thresholds_is_normal() -> None:
    # STRchive's CPEO_POLG: benign exactly 10, no threshold. Not assessed from CLIN-26 on, so
    # every family whose TRGT catalogue holds POLG showed it under Aberrant only.
    assert classify(10, POLG) == "normal"


@pytest.mark.parametrize("count", [0, 5, 9, 11, 12, 14, 300, 5000])
def test_a_count_outside_the_benign_range_of_a_locus_without_thresholds_stays_not_assessed(count: int) -> None:
    # Above the range or below it: a benign range is not a threshold, so nothing says whether
    # such a count is harmful.
    assert classify(count, POLG) == NOT_ASSESSED


@pytest.mark.parametrize(
    ("count", "status"), [(5, NOT_ASSESSED), (6, "normal"), (11, "normal"), (16, "normal"), (17, NOT_ASSESSED)]
)
def test_a_benign_range_holds_both_its_ends(count: int, status: str) -> None:
    # A wider range than POLG's, as another STRchive release may state one: inclusive at both
    # ends, as STRchive's ranges read.
    assert classify(count, {**NO_RANGES, "benign_min": 6, "benign_max": 16}) == status


@pytest.mark.parametrize(
    "ends",
    [{"benign_min": None, "benign_max": 10}, {"benign_min": 10, "benign_max": None}],
    ids=["no lower end", "no upper end"],
)
@pytest.mark.parametrize("count", [0, 5, 10, 300])
def test_a_benign_range_with_an_end_missing_is_not_stated(ends: dict, count: int) -> None:
    # Half a range says nothing about the counts on its open side: not assessed, as at a row
    # that states nothing.
    assert classify(count, {**NO_RANGES, **ends}) == NOT_ASSESSED


@pytest.mark.parametrize(
    ("locus", "count", "status"),
    [
        (ABCD3, 2, "normal"),
        (ABCD3, 44, "normal"),
        # Between the benign range and the threshold, normal as before (CLIN-38 asks about it).
        (ABCD3, 117, "normal"),
        (ABCD3, 118, "pathogenic"),
        ({**POLG, "warning_min": 20}, 5, "normal"),
        ({**POLG, "warning_min": 20}, 15, "normal"),
        ({**POLG, "warning_min": 20}, 20, "intermediate"),
    ],
)
def test_a_benign_range_changes_nothing_at_a_locus_with_a_threshold(locus: dict, count: int, status: str) -> None:
    # The thresholds decide there, a count outside the benign range included: only a locus
    # without one reads its benign range.
    assert classify(count, locus) == status


@pytest.mark.parametrize(("count", "status"), [(39, "normal"), (40, "pathogenic"), (4000, "pathogenic")])
def test_a_row_with_only_a_pathogenic_threshold_keeps_it(count: int, status: str) -> None:
    # Most STRchive rows (ABCD3, CSTB, DMD, ...): normal below the threshold, as before.
    assert classify_repeat_count(count, None, 40) == status


@pytest.mark.parametrize(("count", "status"), [(19, "normal"), (20, "intermediate"), (900, "intermediate")])
def test_a_row_with_only_an_intermediate_threshold_keeps_it(count: int, status: str) -> None:
    assert classify_repeat_count(count, 20, None) == status


@pytest.mark.parametrize(
    ("locus", "count", "status"),
    [
        (HTT, 20, "normal"),
        (HTT, 30, "intermediate"),
        (HTT, 40, "pathogenic"),
        (FMR1, 50, "intermediate"),
        (FMR1, 60, "premutation"),
        (FMR1, 201, "pathogenic"),
        (VWA1, 2, "normal"),
        (VWA1, 3, "pathogenic"),
        (VWA1, 5, "review"),
        (HTT, None, "unknown"),
    ],
)
def test_the_known_loci_read_as_before(locus: dict, count: int | None, status: str) -> None:
    assert classify(count, locus) == status


def test_not_assessed_outranks_normal_and_review_and_yields_to_a_finding() -> None:
    rank = REPEAT_STATUS_RANK
    assert rank["unknown"] < rank["normal"] < rank["review"] < rank[NOT_ASSESSED] < rank["intermediate"]
    assert summarize_repeat_status(["normal", NOT_ASSESSED]) == NOT_ASSESSED
    assert summarize_repeat_status(["unknown", NOT_ASSESSED]) == NOT_ASSESSED
    assert summarize_repeat_status(["review", NOT_ASSESSED]) == NOT_ASSESSED
    assert summarize_repeat_status([NOT_ASSESSED, "intermediate"]) == "intermediate"
    assert summarize_repeat_status([NOT_ASSESSED, "pathogenic"]) == "pathogenic"


def test_the_api_the_stored_status_and_the_ranking_list_the_same_statuses() -> None:
    """A status the classifier gives must be one the API serves and the database stores: the
    check constraint refused `review` and `premutation` once, failing whole repeat datasets."""
    from backend.app.schemas.tracks import RepeatStatus

    schema = Path(__file__).resolve().parents[1] / "db" / "schema" / "postgres" / "03_assay.sql"
    match = re.search(
        r"repeat_expansions_status_check CHECK \(\(status = ANY \(ARRAY\[(?P<values>[^\]]*)\]",
        schema.read_text(encoding="utf-8"),
    )
    assert match is not None
    stored = set(re.findall(r"'([a-z_]+)'::text", match.group("values")))

    assert set(REPEAT_STATUS_RANK) == set(get_args(RepeatStatus)) == stored
    assert NOT_ASSESSED in stored


# ---- The import, through the catalogue a start seeds ----------------------------------------------


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))

    async def commit(self) -> None:
        pass


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _CatalogueSession:
    """Answers the import's catalogue query with the given ``repeat_loci`` rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def execute(self, statement, params=None):
        return _Result(self.rows)


async def _catalogue_after_a_start(monkeypatch) -> dict[str, dict]:
    """The ``repeat_loci`` rows a start leaves, by locus_id, as the import's catalogue query
    returns them. The seed upserts by ``locus_id``, so the last write of a locus is its row."""
    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    session = _RecordingSession()
    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)
    rows: dict[str, dict] = {}
    for _sql, params in session.statements:
        rows[params["locus_id"]] = {
            **{
                column: params[column]
                for column in (
                    "locus_id",
                    "gene",
                    "display_name",
                    "disease",
                    "inheritance",
                    "motif",
                    "motif_index",
                    "warning_min",
                    "pathogenic_min",
                    "notes",
                )
            },
            "aliases": json.loads(params["aliases_json"]),
            "metadata": json.loads(params["metadata_json"]),
        }
    return rows


async def _import_lookup(monkeypatch) -> dict[str, dict]:
    rows = await _catalogue_after_a_start(monkeypatch)
    return await repeat_expansion_pg._build_repeat_locus_lookup(_CatalogueSession(list(rows.values())))


def _sample_context(*, sex: str = "female") -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid="sample-uuid",
        sample_id="sample",
        family_uuid="family-uuid",
        family_id="FAM1",
        sex=sex,
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _call(
    lookup: dict[str, dict],
    *,
    trid: str,
    counts: tuple[int, int] = (12, 300),
    chrom: str = "chr2",
    pos: int = 1_000_000,
    motif: str = "CAG",
    motifs: str | None = None,
    sex: str = "female",
    sample_field: str | None = None,
) -> dict:
    """A TRGT call of two alleles of ``counts`` repeats, or of ``sample_field`` as given;
    ``motifs`` is the record's MOTIFS when it lists more than ``motif``."""
    if sample_field is None:
        lengths = ",".join(str(count * len(motif)) for count in counts)
        sample_field = f"1/2:{lengths}:{counts[0]},{counts[1]}"
    return repeat_expansion_pg._build_trgt_insert_params(
        sample_context=_sample_context(sex=sex),
        locus_lookup=lookup,
        chrom=chrom,
        pos=str(pos),
        ref=motif,
        info_field=f"TRID={trid};END={pos + 30};MOTIFS={motifs or motif}",
        format_field="GT:AL:MC",
        sample_field=sample_field,
        header_sample="sample",
        metadata={},
    )


def _allele_statuses(params: dict) -> list[str]:
    return [allele["status"] for allele in json.loads(params["alleles_json"])]


@pytest.mark.parametrize(
    "trid",
    [
        # A locus the catalogue lacks, named as a TRGT catalogue names its loci.
        "chr2_1000000_1000030_CAG",
        # Huntington's locus under a TRID spelled differently from STRchive's HD_HTT.
        "HD-HTT",
    ],
)
@pytest.mark.asyncio
async def test_a_call_whose_trid_no_catalogue_row_names_is_stored_not_assessed(monkeypatch, trid: str) -> None:
    lookup = await _import_lookup(monkeypatch)
    assert trid.lower() not in lookup

    params = _call(lookup, trid=trid, counts=(12, 300))

    # Both alleles, the 300-repeat expansion included, read "normal" before.
    assert _allele_statuses(params) == [NOT_ASSESSED, NOT_ASSESSED]
    assert params["status"] == NOT_ASSESSED
    assert (params["locus_id"], params["warning_min"], params["pathogenic_min"]) == (trid, None, None)


@pytest.mark.parametrize(
    ("trid", "counts"),
    [
        # MUC1 states nothing: every count, ten included.
        ("ADTKD_MUC1", (25, 60)),
        ("ADTKD_MUC1", (10, 10)),
        # POLG states a benign count of 10: both alleles outside it, above and below.
        ("CPEO_POLG", (11, 30)),
        ("CPEO_POLG", (5, 9)),
        ("POLG", (12, 14)),
    ],
)
@pytest.mark.asyncio
async def test_a_call_at_a_catalogue_row_without_thresholds_is_stored_not_assessed(
    monkeypatch, trid: str, counts: tuple[int, int]
) -> None:
    lookup = await _import_lookup(monkeypatch)
    row = lookup[trid.lower()]
    assert (row["warning_min"], row["pathogenic_min"], row["metadata"].get("premutation_min")) == (None, None, None)

    params = _call(lookup, trid=trid, counts=counts, motif=str(row["motif"]))

    assert params["locus_id"] == row["locus_id"]
    assert _allele_statuses(params) == [NOT_ASSESSED, NOT_ASSESSED]
    assert params["status"] == NOT_ASSESSED


@pytest.mark.asyncio
async def test_the_rows_a_start_seeds_without_thresholds_are_muc1_and_polg(monkeypatch) -> None:
    """The loci CLIN-36's rule reaches: the seeded rows with no threshold. ADTKD_MUC1 states no
    benign range, so every count there stays not assessed; CPEO_POLG states 10, the one count
    that reads normal. No built-in row lacks a threshold, nor does any other STRchive row: a
    release that adds such a row fails here, for QA to look at the range it states."""
    rows = await _catalogue_after_a_start(monkeypatch)

    def ranges(row: dict) -> dict:
        metadata = row["metadata"]
        return {key: metadata.get(key) for key in ("benign_min", "benign_max", "pathogenic_max", "premutation_min")}

    without_thresholds = {
        locus_id: row
        for locus_id, row in rows.items()
        if row["warning_min"] is None and row["pathogenic_min"] is None and row["metadata"].get("premutation_min") is None
    }
    assert {locus_id: ranges(row) for locus_id, row in without_thresholds.items()} == {
        "ADTKD_MUC1": {"benign_min": None, "benign_max": None, "pathogenic_max": None, "premutation_min": None},
        "CPEO_POLG": {"benign_min": 10, "benign_max": 10, "pathogenic_max": None, "premutation_min": None},
    }
    counts = range(0, 501)
    read = {
        locus_id: {count: classify_repeat_count(count, None, None, **ranges(row)) for count in counts}
        for locus_id, row in without_thresholds.items()
    }
    assert {count for count, status in read["ADTKD_MUC1"].items() if status != NOT_ASSESSED} == set()
    assert {count for count, status in read["CPEO_POLG"].items() if status != NOT_ASSESSED} == {10}
    assert read["CPEO_POLG"][10] == "normal"


@pytest.mark.parametrize("trid", ["CPEO_POLG", "POLG"])
@pytest.mark.parametrize(
    ("counts", "alleles", "status"),
    [
        ((10, 10), ["normal", "normal"], "normal"),
        # The call reads its most severe allele: one outside the range keeps it not assessed.
        ((10, 30), ["normal", NOT_ASSESSED], NOT_ASSESSED),
        ((9, 10), [NOT_ASSESSED, "normal"], NOT_ASSESSED),
    ],
)
@pytest.mark.asyncio
async def test_a_polg_count_inside_its_benign_range_is_stored_normal(
    monkeypatch, trid: str, counts: tuple[int, int], alleles: list[str], status: str
) -> None:
    """STRchive's locus ID and the gene name both find CPEO_POLG, benign exactly 10 (CLIN-36)."""
    lookup = await _import_lookup(monkeypatch)
    row = lookup[trid.lower()]
    assert row["locus_id"] == "CPEO_POLG"
    assert (row["metadata"]["benign_min"], row["metadata"]["benign_max"]) == (10, 10)

    params = _call(lookup, trid=trid, counts=counts, chrom="chr15", pos=89_333_579, motif=str(row["motif"]))

    assert _allele_statuses(params) == alleles
    assert params["status"] == status
    assert (params["locus_id"], params["warning_min"], params["pathogenic_min"]) == ("CPEO_POLG", None, None)


@pytest.mark.asyncio
async def test_a_polg_allele_is_read_by_the_count_trgt_reports_for_the_catalogue_motif(monkeypatch) -> None:
    """TRGT counts the catalogue motif over the whole region its catalogue gives the locus.
    The TRGT catalogue in use gives CPEO_POLG 50 bp (MOTIFS=GCT,GTT): two pairs of GCT and a
    GTT ahead of the tract STRchive's benign count of 10 measures, so the common ten-repeat
    allele (50 bp) reads 14 GCT copies and stays not assessed; an allele TRGT counts as 10 GCT
    copies in all reads normal."""
    lookup = await _import_lookup(monkeypatch)

    params = _call(
        lookup,
        trid="CPEO_POLG",
        chrom="chr15",
        pos=89_333_579,
        motif="GCT",
        motifs="GCT,GTT",
        sample_field="0/1:50,38:14_1,10_1",
    )

    alleles = json.loads(params["alleles_json"])
    assert [(allele["repeat_count"], allele["status"]) for allele in alleles] == [(14, NOT_ASSESSED), (10, "normal")]
    assert params["status"] == NOT_ASSESSED


@pytest.mark.parametrize(("counts", "status"), [((18, 20), "normal"), ((18, 30), "intermediate"), ((18, 40), "pathogenic")])
@pytest.mark.parametrize("trid", ["HD_HTT", "HTT"])
@pytest.mark.asyncio
async def test_a_call_at_a_catalogued_locus_reads_as_before(
    monkeypatch, trid: str, counts: tuple[int, int], status: str
) -> None:
    lookup = await _import_lookup(monkeypatch)

    params = _call(lookup, trid=trid, counts=counts, chrom="chr4", pos=3_074_876)

    assert params["status"] == status
    assert (params["warning_min"], params["pathogenic_min"]) == (27, 36)


@pytest.mark.asyncio
async def test_a_no_call_at_an_unknown_locus_is_unknown(monkeypatch) -> None:
    lookup = await _import_lookup(monkeypatch)

    params = _call(lookup, trid="chr2_1000000_1000030_CAG", sample_field="./.:.:.")

    assert _allele_statuses(params) == ["unknown"]
    assert params["status"] == "unknown"


@pytest.mark.asyncio
async def test_a_males_two_chrx_alleles_at_an_unknown_locus_stay_not_assessed(monkeypatch) -> None:
    """A note raises a call to at least review; not assessed already ranks above it, so the
    call keeps saying why, and the family table shows the note with it."""
    lookup = await _import_lookup(monkeypatch)

    params = _call(lookup, trid="chrX_70000000_70000030_GCC", chrom="chrX", pos=70_000_000, motif="GCC", sex="male", counts=(12, 40))

    assert params["allele_count"] == 2
    assert params["status"] == NOT_ASSESSED


class _IngestSession:
    """Answers the import's catalogue query with ``catalogue`` (none by default), every other
    query with no rows, and records the rows the ingest inserts."""

    def __init__(self, catalogue: list[dict] | None = None) -> None:
        self.catalogue = catalogue or []
        self.inserted: list[dict] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "INSERT INTO repeat_expansions" in sql:
            self.inserted.extend(params)
        return _Result(self.catalogue if "FROM repeat_loci" in sql else [])

    async def commit(self) -> None:
        pass


_TRGT_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{columns}\n"


def _polg_record(*sample_fields: str) -> str:
    return (
        "chr15\t89333579\t.\tGCT\t<TR>\t.\tPASS\tTRID=CPEO_POLG;END=89333629;MOTIFS=GCT\tGT:AL:MC\t"
        + "\t".join(sample_fields)
        + "\n"
    )


def _muc1_record(*sample_fields: str) -> str:
    return (
        "chr1\t155188505\t.\tGCC\t<TR>\t.\tPASS\tTRID=ADTKD_MUC1;END=155192239;MOTIFS=GCC\tGT:AL:MC\t"
        + "\t".join(sample_fields)
        + "\n"
    )


@pytest.mark.asyncio
async def test_the_ingest_stores_not_assessed_for_an_uncatalogued_trid() -> None:
    """The whole per-sample ingest, with an empty catalogue: the row it inserts."""
    session = _IngestSession()

    await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=_sample_context(),
        text_value=(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n"
            "chr2\t1000000\t.\tCAG\t<TR>\t.\tPASS\tTRID=chr2_1000000_1000030_CAG;END=1000030;MOTIFS=CAG"
            "\tGT:AL:MC\t1/2:36,900:12,300\n"
        ),
        metadata={"filename": "sample.trgt.vcf", "source": "trgt"},
    )

    [record] = session.inserted
    assert record["status"] == NOT_ASSESSED
    assert [allele["status"] for allele in json.loads(record["alleles_json"])] == [NOT_ASSESSED, NOT_ASSESSED]


@pytest.mark.asyncio
async def test_the_ingest_stores_a_polg_call_inside_its_benign_range_as_normal(monkeypatch) -> None:
    """The whole per-sample ingest, through the catalogue a start seeds: POLG 10/10 is stored
    normal (CLIN-36); MUC1, which states nothing, stays not assessed."""
    rows = await _catalogue_after_a_start(monkeypatch)
    session = _IngestSession(list(rows.values()))

    await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=_sample_context(),
        text_value=(
            _TRGT_HEADER.format(columns="sample")
            + _polg_record("0/0:30,30:10,10")
            + _muc1_record("0/0:30,30:10,10")
        ),
        metadata={"filename": "sample.trgt.vcf", "source": "trgt"},
    )

    stored = {
        record["locus_id"]: (record["status"], _allele_statuses(record), record["warning_min"], record["pathogenic_min"])
        for record in session.inserted
    }
    assert stored == {
        "CPEO_POLG": ("normal", ["normal", "normal"], None, None),
        "ADTKD_MUC1": (NOT_ASSESSED, [NOT_ASSESSED, NOT_ASSESSED], None, None),
    }


@pytest.mark.asyncio
async def test_the_family_ingest_stores_each_members_polg_call_by_its_counts(monkeypatch) -> None:
    """A family TRGT file: the mother's 10/10 is stored normal, the father's 10/13 not assessed."""
    rows = await _catalogue_after_a_start(monkeypatch)
    session = _IngestSession(list(rows.values()))
    contexts = {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM1",
            sex=sex,
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name, sex in (("MOTHER", "female"), ("FATHER", "male"))
    }

    await repeat_expansion_pg.ingest_family_trgt_text(
        session,
        sample_contexts=contexts,
        text_value=_TRGT_HEADER.format(columns="MOTHER\tFATHER") + _polg_record("0/0:30,30:10,10", "0/1:30,39:10,13"),
        metadata={"filename": "family.trgt.vcf", "source": "trgt"},
    )

    stored = {record["sample_id"]: (record["status"], _allele_statuses(record)) for record in session.inserted}
    assert stored == {
        "mother-uuid": ("normal", ["normal", "normal"]),
        "father-uuid": (NOT_ASSESSED, ["normal", NOT_ASSESSED]),
    }


# ---- The family table ------------------------------------------------------------------------------

SON_UUID = "00000000-0000-4000-8000-000000000001"
MOTHER_UUID = "00000000-0000-4000-8000-000000000002"


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id="FAM1",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": SON_UUID, "sample_id": "SON1", "role": "proband", "affected": True, "sex": "male"},
            {"sample_uuid": MOTHER_UUID, "sample_id": "MOTHER1", "role": "mother", "affected": False, "sex": "female"},
        ],
        sample_uuid_to_name={SON_UUID: "SON1", MOTHER_UUID: "MOTHER1"},
        sample_name_to_uuid={"SON1": SON_UUID, "MOTHER1": MOTHER_UUID},
        affected_sample_names=["SON1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _table_row(sample_uuid: str, *, locus_id: str, chrom: str, start: int, alleles: list[dict], status: str, **thresholds) -> dict:
    """A row of the table query: the thresholds the query took from the matched catalogue row,
    or the stored ones when no row matched (both NULL for an uncatalogued TRID)."""
    return {
        "sample_uuid": sample_uuid,
        "locus_id": locus_id,
        "gene": locus_id,
        "display_name": locus_id,
        "disease": locus_id,
        "inheritance": None,
        "chr": chrom,
        "start": start,
        "end": start + 30,
        "motif": "CAG",
        "genotype": "1/2",
        "allele_count": len(alleles),
        "alleles": alleles,
        "warning_min": thresholds.get("warning_min"),
        "pathogenic_min": thresholds.get("pathogenic_min"),
        "benign_min": thresholds.get("benign_min"),
        "benign_max": thresholds.get("benign_max"),
        "pathogenic_max": thresholds.get("pathogenic_max"),
        "premutation_min": thresholds.get("premutation_min"),
        "status": status,
    }


class _TableSession:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def execute(self, statement, params=None):
        return _Result(self.rows)


@pytest.mark.asyncio
async def test_the_family_table_reads_a_call_stored_as_normal_at_an_uncatalogued_locus_as_not_assessed() -> None:
    """Imported before CLIN-26: stored as normal, with no thresholds. The table classifies it
    again at once; a member without a call there stays unknown, and a catalogued locus is as
    before."""
    uncatalogued = "chr2_1000000_1000030_CAG"
    rows = [
        _table_row(
            SON_UUID,
            locus_id=uncatalogued,
            chrom="2",
            start=1_000_000,
            alleles=[{"repeat_count": 12, "status": "normal"}, {"repeat_count": 300, "status": "normal"}],
            status="normal",
        ),
        _table_row(
            MOTHER_UUID,
            locus_id=uncatalogued,
            chrom="2",
            start=1_000_000,
            alleles=[{"status": "unknown"}],
            status="unknown",
        ),
        _table_row(
            SON_UUID,
            locus_id="HD_HTT",
            chrom="4",
            start=3_074_876,
            alleles=[{"repeat_count": 18, "status": "normal"}, {"repeat_count": 20, "status": "normal"}],
            status="normal",
            **HTT,
        ),
    ]

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(rows), context=_context())

    loci = {row.locus_id: row for row in table.loci}
    locus = loci[uncatalogued]
    assert (locus.warning_min, locus.pathogenic_min) == (None, None)
    son = locus.calls["SON1"]
    assert [allele.status for allele in son.alleles] == [NOT_ASSESSED, NOT_ASSESSED]
    assert son.status == NOT_ASSESSED
    assert locus.calls["MOTHER1"].status == "unknown"
    # The locus reads its most severe call: not assessed, which the aberrant-only view keeps.
    assert locus.status == NOT_ASSESSED
    htt = loci["HD_HTT"]
    assert htt.status == htt.calls["SON1"].status == "normal"


@pytest.mark.asyncio
async def test_the_family_table_reads_a_catalogue_row_without_thresholds_as_not_assessed() -> None:
    # The query took the matched rows' thresholds: none. MUC1 states no benign range either;
    # POLG states 10, so only its allele of 30 is not assessed (CLIN-36).
    rows = [
        _table_row(
            MOTHER_UUID,
            locus_id="ADTKD_MUC1",
            chrom="1",
            start=155_188_505,
            alleles=[{"repeat_count": 10, "status": "normal"}, {"repeat_count": 60, "status": "normal"}],
            status="normal",
        ),
        _table_row(
            MOTHER_UUID,
            locus_id="CPEO_POLG",
            chrom="15",
            start=89_333_579,
            alleles=[{"repeat_count": 10, "status": "normal"}, {"repeat_count": 30, "status": "normal"}],
            status="normal",
            benign_min=10,
            benign_max=10,
        ),
    ]

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(rows), context=_context())

    loci = {row.locus_id: row for row in table.loci}
    muc1, polg = loci["ADTKD_MUC1"], loci["CPEO_POLG"]
    assert [allele.status for allele in muc1.calls["MOTHER1"].alleles] == [NOT_ASSESSED, NOT_ASSESSED]
    assert muc1.status == muc1.calls["MOTHER1"].status == NOT_ASSESSED
    assert [allele.status for allele in polg.calls["MOTHER1"].alleles] == ["normal", NOT_ASSESSED]
    assert polg.status == polg.calls["MOTHER1"].status == NOT_ASSESSED


@pytest.mark.asyncio
async def test_the_family_table_reads_a_polg_call_stored_not_assessed_inside_its_benign_range_as_normal() -> None:
    """Imported from CLIN-26 to CLIN-36: POLG 10/10 stored as not assessed. The table classifies
    it again at once, so the call is normal, and so is the locus when every call there is.
    (The genome track shows the stored status until the call is imported again: CLIN-14.)"""
    stored = [{"repeat_count": 10, "status": NOT_ASSESSED}, {"repeat_count": 10, "status": NOT_ASSESSED}]
    polg = {"locus_id": "CPEO_POLG", "chrom": "15", "start": 89_333_579, "benign_min": 10, "benign_max": 10}
    rows = [
        _table_row(SON_UUID, alleles=stored, status=NOT_ASSESSED, **polg),
        _table_row(MOTHER_UUID, alleles=stored, status=NOT_ASSESSED, **polg),
    ]

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(rows), context=_context())

    [locus] = table.loci
    for sample in ("SON1", "MOTHER1"):
        call = locus.calls[sample]
        assert [allele.status for allele in call.alleles] == ["normal", "normal"]
        assert call.status == "normal"
    assert locus.status == "normal"
    assert (locus.warning_min, locus.pathogenic_min, locus.benign_min, locus.benign_max) == (None, None, 10, 10)


@pytest.mark.asyncio
async def test_the_family_table_keeps_a_males_chrx_note_on_a_call_not_assessed() -> None:
    rows = [
        _table_row(
            SON_UUID,
            locus_id="chrX_70000000_70000030_GCC",
            chrom="X",
            start=70_000_000,
            alleles=[{"repeat_count": 12, "status": "normal"}, {"repeat_count": 40, "status": "normal"}],
            status="normal",
        )
    ]

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(rows), context=_context())

    call = table.loci[0].calls["SON1"]
    assert call.status == NOT_ASSESSED
    assert call.note == MALE_X_DISTINCT_ALLELES_NOTE


# ---- The genome tracks -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_genome_track_serves_a_stored_not_assessed_status() -> None:
    """The tracks show the status stored at import (CLIN-14): the API model must accept it."""
    stored = {
        "locus_id": "chr2_1000000_1000030_CAG",
        "gene": "chr2_1000000_1000030_CAG",
        "display_name": "chr2_1000000_1000030_CAG",
        "disease": "chr2_1000000_1000030_CAG",
        "chr": "2",
        "start": 1_000_000,
        "end": 1_000_030,
        "motif": "CAG",
        "warning_min": None,
        "pathogenic_min": None,
        "status": NOT_ASSESSED,
        "alleles": [{"repeat_count": 12, "status": NOT_ASSESSED}, {"repeat_count": 300, "status": NOT_ASSESSED}],
    }

    response = await repeat_expansion_pg.get_sample_repeat_expansion_track_response(
        _TableSession([stored]), context=_context(), sample_name="SON1", chromosomes=["2"]
    )

    [item] = response.items
    assert item.status == NOT_ASSESSED
    assert item.allele_repeat_counts == [12, 300]
