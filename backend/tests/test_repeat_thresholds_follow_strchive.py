"""The built-in repeat loci carry STRchive's thresholds, so a call reads the same whatever its TRID.

A TRGT call's TRID chooses the catalogue row that classifies it: a gene name (as in the demo
data) finds the built-in row (``repeat_expansion_catalog.py``), a STRchive ID such as
``SCA1_ATXN1`` the STRchive row (``data/ref-data/STRchive-loci.json``). At fifteen loci the two
rows started the intermediate or the pathogenic range at a different repeat count, so the same
allele was called differently depending on the pipeline's TRIDs (CLIN-22). The owner decided
on 2026-10-09 that the built-in rows take STRchive's thresholds wherever STRchive has the
locus. A threshold is the first count of its range (the classifier tests ``>=``), as
STRchive's inclusive ranges read: ``warning_min`` starts the intermediate range,
``pathogenic_min`` the pathogenic one.

- The guard: every built-in row has exactly one STRchive row, with the same two thresholds.
- Each side of every threshold that changed, STRchive's and the built-in row's old one, run
  through the catalogue a start seeds and the import's TRID lookup, under the gene name and
  under the STRchive ID.
- Every start writes the thresholds over those an earlier start stored, BEAN1's missing
  intermediate threshold as NULL.
- The family table classifies a call stored under an old row again against the catalogue row's
  thresholds, NULL included. (The genome tracks show the status stored at import until the
  calls are imported again; integration/test_repeat_status_storage.py runs this on Postgres.)
All samples and values are synthetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.repeat_expansion_catalog import BUILTIN_REPEAT_LOCI

N, I, P = "normal", "intermediate", "pathogenic"

# Built-in loci that STRchive does not list keep thresholds of their own, which the owner sets.
# None today: every built-in locus is in STRchive.
BUILT_IN_LOCI_STRCHIVE_LACKS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ChangedLocus:
    """A locus whose built-in thresholds changed to STRchive's (CLIN-22)."""

    gene_trid: str  # the built-in row's locus_id, the TRID of the demo data
    strchive_trid: str  # STRchive's locus ID
    now: tuple[int | None, int]  # (warning_min, pathogenic_min): STRchive's, and the built-in row's now
    before: tuple[int, int]  # the built-in row's before
    # The status of an allele of each count, on each side of every threshold in ``now`` and
    # ``before``, under either row.
    statuses: dict[int, str]
    # A count whose status changed, and the status the old built-in row gave it.
    flip: tuple[int, str]


CHANGED_LOCI = [
    ChangedLocus("ATXN1", "SCA1_ATXN1", (36, 39), (39, 45), {35: N, 36: I, 38: I, 39: P, 44: P, 45: P}, (39, I)),
    ChangedLocus("ATXN2", "SCA2_ATXN2", (29, 35), (32, 34), {28: N, 29: I, 31: I, 32: I, 33: I, 34: I, 35: P}, (34, P)),
    ChangedLocus("CACNA1A", "SCA6_CACNA1A", (19, 21), (19, 20), {18: N, 19: I, 20: I, 21: P}, (20, P)),
    ChangedLocus("ATXN7", "SCA7_ATXN7", (28, 37), (20, 36), {19: N, 20: N, 27: N, 28: I, 35: I, 36: I, 37: P}, (36, P)),
    ChangedLocus("TBP", "SCA17_TBP", (41, 49), (42, 49), {40: N, 41: I, 42: I, 48: I, 49: P}, (41, N)),
    ChangedLocus("ATXN8OS", "SCA8_ATXN8OS", (50, 71), (50, 80), {49: N, 50: I, 70: I, 71: P, 79: P, 80: P}, (71, I)),
    ChangedLocus("CNBP", "DM2_CNBP", (27, 75), (55, 75), {26: N, 27: I, 54: I, 55: I, 74: I, 75: P}, (27, N)),
    ChangedLocus("FXN", "FRDA_FXN", (34, 56), (34, 66), {33: N, 34: I, 55: I, 56: P, 65: P, 66: P}, (56, I)),
    ChangedLocus("C9orf72", "FTDALS1_C9orf72", (24, 31), (24, 30), {23: N, 24: I, 29: I, 30: I, 31: P}, (30, P)),
    ChangedLocus("ATXN10", "SCA10_ATXN10", (33, 800), (280, 800), {32: N, 33: I, 279: I, 280: I, 799: I, 800: P}, (33, N)),
    # STRchive gives SCA31 no intermediate range: below 110 is normal.
    ChangedLocus("BEAN1", "SCA31_BEAN1", (None, 110), (300, 500), {109: N, 110: P, 299: P, 300: P, 499: P, 500: P}, (300, I)),
    ChangedLocus("PPP2R2B", "SCA12_PPP2R2B", (40, 51), (44, 51), {39: N, 40: I, 43: I, 44: I, 50: I, 51: P}, (40, N)),
    ChangedLocus("NOP56", "SCA36_NOP56", (15, 650), (31, 650), {14: N, 15: I, 30: I, 31: I, 649: I, 650: P}, (15, N)),
    ChangedLocus("JPH3", "HDL2_JPH3", (29, 40), (36, 41), {28: N, 29: I, 35: I, 36: I, 39: I, 40: P, 41: P}, (40, I)),
    ChangedLocus("PABPN1", "OPMD_PABPN1", (11, 12), (11, 13), {10: N, 11: I, 12: P, 13: P}, (12, I)),
]
LOCUS_IDS = [locus.gene_trid for locus in CHANGED_LOCI]


def _shipped_strchive_rows() -> list[dict]:
    return repeat_expansion_pg.load_strchive_repeat_loci(repeat_expansion_pg.REPO_STRCHIVE_LOCI_PATH)


def _strchive_rows_of(builtin: dict, strchive_rows: list[dict]) -> list[dict]:
    """The STRchive rows of a built-in locus: those whose gene is its gene or one of its aliases."""
    names = {str(name).lower() for name in (builtin["gene"], *builtin.get("aliases", []))}
    return [row for row in strchive_rows if str(row["gene"]).lower() in names]


# ---- The guard -------------------------------------------------------------------------------


def test_every_built_in_row_has_one_strchive_row_with_the_same_thresholds() -> None:
    """Fails whenever a built-in row's thresholds drift from its STRchive row's: change the
    built-in row and STRchive-loci.json together."""
    strchive_rows = _shipped_strchive_rows()
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


def test_the_cases_below_are_the_built_in_rows_whose_thresholds_changed() -> None:
    """Each case pairs a built-in row with its STRchive row and STRchive's thresholds; the six
    built-in rows without a case already agreed with STRchive (FMR1 since #799)."""
    strchive_by_id = {row["locus_id"]: row for row in _shipped_strchive_rows()}
    builtin_by_id = {locus["locus_id"]: locus for locus in BUILTIN_REPEAT_LOCI}
    for locus in CHANGED_LOCI:
        strchive = strchive_by_id[locus.strchive_trid]
        assert strchive["gene"].lower() == builtin_by_id[locus.gene_trid]["gene"].lower()
        assert (strchive["warning_min"], strchive["pathogenic_min"]) == locus.now != locus.before
    unchanged = sorted(set(builtin_by_id) - set(LOCUS_IDS))
    assert unchanged == ["AR", "ATN1", "ATXN3", "DMPK", "FMR1", "HTT"]


# ---- Through the catalogue a start seeds and the import's TRID lookup ---------------------------


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))

    async def commit(self) -> None:
        self.committed = True


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _CatalogueSession:
    """Answers the import's catalogue query with the given ``repeat_loci`` rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def execute(self, statement, params=None):
        return _Result(self.rows)


async def _seed_a_start(monkeypatch) -> _RecordingSession:
    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    session = _RecordingSession()
    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)
    return session


async def _catalogue_after_a_start(monkeypatch) -> dict[str, dict]:
    """The ``repeat_loci`` rows a start leaves, by locus_id, as the import's catalogue query
    returns them. The seed upserts by ``locus_id``, so the last write of a locus is its row."""
    session = await _seed_a_start(monkeypatch)
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
    return {locus_id: rows[locus_id] for locus_id in sorted(rows)}


async def _import_lookup(monkeypatch) -> dict[str, dict]:
    rows = await _catalogue_after_a_start(monkeypatch)
    return await repeat_expansion_pg._build_repeat_locus_lookup(_CatalogueSession(list(rows.values())))


def _call(lookup: dict[str, dict], *, trid: str, strchive: dict, count: int) -> dict:
    """A woman's TRGT call at the locus: a normal allele of 5 repeats and one of ``count``.

    The record is the same under either TRID: STRchive's coordinates and motif."""
    motif = str(strchive["motif"])
    start = int(strchive["metadata"]["start_hg38"])
    return repeat_expansion_pg._build_trgt_insert_params(
        sample_context=SampleMetadataContext(
            sample_uuid="sample-uuid",
            sample_id="sample",
            family_uuid="family-uuid",
            family_id="FAM1",
            sex="female",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        locus_lookup=lookup,
        chrom=str(strchive["metadata"]["chrom"]),
        pos=str(start),
        ref=motif,
        info_field=f"TRID={trid};END={start + len(motif) * count};MOTIFS={motif}",
        format_field="GT:AL:MC",
        sample_field=f"1/2:{5 * len(motif)},{count * len(motif)}:5,{count}",
        header_sample="sample",
        metadata={},
    )


@pytest.mark.parametrize("trid_kind", ["gene name", "STRchive ID"])
@pytest.mark.parametrize("locus", CHANGED_LOCI, ids=LOCUS_IDS)
@pytest.mark.asyncio
async def test_each_side_of_every_changed_threshold_reads_the_same_under_either_trid(
    monkeypatch, locus: ChangedLocus, trid_kind: str
) -> None:
    lookup = await _import_lookup(monkeypatch)
    strchive = lookup[locus.strchive_trid.lower()]
    trid = locus.gene_trid if trid_kind == "gene name" else locus.strchive_trid

    statuses: dict[int, str] = {}
    stored_thresholds: set[tuple[int | None, int | None]] = set()
    for count in locus.statuses:
        params = _call(lookup, trid=trid, strchive=strchive, count=count)
        assert params["locus_id"] == trid  # the row the TRID resolved to
        assert [allele["status"] for allele in json.loads(params["alleles_json"])][0] == N
        statuses[count] = str(params["status"])
        stored_thresholds.add((params["warning_min"], params["pathogenic_min"]))

    assert statuses == locus.statuses
    # The call is stored with the thresholds that classified it.
    assert stored_thresholds == {locus.now}


@pytest.mark.asyncio
async def test_each_start_writes_strchives_thresholds_over_those_an_earlier_start_stored(monkeypatch) -> None:
    """A database seeded with the old built-in rows gets STRchive's thresholds on its next start:
    the upsert by locus_id sets both columns from the row, so BEAN1's NULL replaces a stored 300."""
    session = await _seed_a_start(monkeypatch)
    upserts = {params["locus_id"]: (" ".join(sql.split()), params) for sql, params in session.statements}

    for locus in CHANGED_LOCI:
        sql, params = upserts[locus.gene_trid]
        assert "ON CONFLICT (locus_id) DO UPDATE" in sql
        assert "warning_min = EXCLUDED.warning_min" in sql
        assert "pathogenic_min = EXCLUDED.pathogenic_min" in sql
        strchive = upserts[locus.strchive_trid][1]
        assert (params["warning_min"], params["pathogenic_min"]) == locus.now
        assert (strchive["warning_min"], strchive["pathogenic_min"]) == locus.now
    assert upserts["BEAN1"][1]["warning_min"] is None
    assert session.committed


# ---- The family table ---------------------------------------------------------------------------


def _context() -> FamilyMetadataContext:
    sample_uuid = "00000000-0000-4000-8000-000000000003"
    return FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f3",
        family_id="FAM1",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_uuid": sample_uuid, "sample_id": "WOMAN1", "role": "proband", "affected": False, "sex": "female"}],
        sample_uuid_to_name={sample_uuid: "WOMAN1"},
        sample_name_to_uuid={"WOMAN1": sample_uuid},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.mark.parametrize("locus", CHANGED_LOCI, ids=LOCUS_IDS)
@pytest.mark.asyncio
async def test_the_family_table_reads_a_call_stored_under_the_old_row_with_strchives_thresholds(
    monkeypatch, locus: ChangedLocus
) -> None:
    """A call imported under the old built-in row keeps its stored status (the genome tracks show
    it until the calls are imported again); the family table classifies it again against the row
    a start leaves."""
    builtin = (await _catalogue_after_a_start(monkeypatch))[locus.gene_trid]
    count, status_before = locus.flip

    class _TableSession:
        async def execute(self, statement, params=None):
            # The query takes the thresholds from the catalogue row it matched.
            return _Result(
                [
                    {
                        "sample_uuid": "00000000-0000-4000-8000-000000000003",
                        "locus_id": locus.gene_trid,
                        "gene": builtin["gene"],
                        "display_name": builtin["display_name"],
                        "disease": builtin["disease"],
                        "inheritance": builtin["inheritance"],
                        "chr": "1",
                        "start": 1000,
                        "end": 1100,
                        "motif": builtin["motif"],
                        "genotype": "1/2",
                        "allele_count": 2,
                        "alleles": [
                            {"repeat_count": 5, "status": N},
                            {"repeat_count": count, "status": status_before},
                        ],
                        "warning_min": builtin["warning_min"],
                        "pathogenic_min": builtin["pathogenic_min"],
                        "benign_min": builtin["metadata"].get("benign_min"),
                        "benign_max": builtin["metadata"].get("benign_max"),
                        "pathogenic_max": builtin["metadata"].get("pathogenic_max"),
                        "premutation_min": builtin["metadata"].get("premutation_min"),
                        "status": status_before,
                    }
                ]
            )

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(), context=_context())

    [row] = table.loci
    assert (row.warning_min, row.pathogenic_min) == locus.now
    call = row.calls["WOMAN1"]
    assert [allele.status for allele in call.alleles] == [N, locus.statuses[count]]
    assert call.status == row.status == locus.statuses[count] != status_before


@pytest.mark.asyncio
async def test_the_family_table_takes_both_thresholds_from_the_catalogue_row_it_matched() -> None:
    """A call keeps the thresholds it was imported with, but the table reads the matched
    catalogue row's, NULL included: BEAN1's intermediate threshold is gone, and a call stored
    with the old 300 must not bring it back. Only a call no catalogue row matches falls back to
    its stored thresholds. (integration/test_repeat_status_storage.py runs the query.)"""
    captured: list[str] = []

    class _Session:
        async def execute(self, statement, params=None):
            captured.append(" ".join(str(statement).split()))
            return _Result([])

    await repeat_expansion_pg.get_family_repeat_expansion_table_response(_Session(), context=_context())

    [sql] = captured
    for column in ("warning_min", "pathogenic_min"):
        assert (
            f"CASE WHEN catalog.locus_id IS NULL THEN repeat_expansions.{column} "
            f"ELSE catalog.{column} END AS {column}"
        ) in sql
        assert f"COALESCE(catalog.{column}" not in sql
