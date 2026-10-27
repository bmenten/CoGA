"""A gene with several STRchive loci: its name picks the tract by the call's position (CLIN-31).

STRchive lists two repeat tracts in ARX (EIEE1_ARX, pathogenic from 17 GCN repeats; PRTS_ARX,
from 20) and three in HOXA13 (HFG_HOXA13-I and -III from 22, -II from 18). A TRGT call whose
TRID is the gene's name resolved to the first of them by locus ID, so a call at PRTS_ARX was
read against EIEE1_ARX's thresholds: 17 repeats read pathogenic where PRTS_ARX does not call
them so. The owner decided on 2026-10-14 that such a TRID is resolved by position: the STRchive
locus the call's region overlaps, on the family's assembly. A region that overlaps none of
them, or several, or an assembly STRchive gives no coordinates on, classifies the call by no
locus: it reads *not assessed*, and the call carries the reason.

- The catalogue a start seeds names ARX's and HOXA13's tracts under the gene's name, and the
  name maps to no row by itself; a STRchive ID or STRipy ID still names its locus.
- Each tract's own thresholds, at the counts where the tracts disagree, through the
  catalogue a start seeds and the import's TRID lookup, on GRCh38 and T2T-CHM13; the whole
  per-sample ingest too.
- None, several, or no coordinates: not assessed, with the reason stored with the call.
- The family table shows the reason, and its query lets a name that reaches several STRchive
  loci choose none of them. (integration/test_repeat_multi_locus_storage.py runs it on
  Postgres.)
All samples and values are synthetic.
"""

from __future__ import annotations

import functools
import json
from typing import Any

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext

N, P, NOT_ASSESSED = "normal", "pathogenic", "not_assessed"
# Between a tract's benign range and its pathogenic threshold, where STRchive states no grey
# zone of its own: the grey zone (the owner's decision of 2026-10-27, CLIN-14).
I = "intermediate"

# The STRchive tracts of each gene, and each tract's thresholds in the shipped release.
TRACT_THRESHOLDS = {
    "EIEE1_ARX": (None, 17),
    "PRTS_ARX": (None, 20),
    "HFG_HOXA13-I": (None, 22),
    "HFG_HOXA13-II": (None, 18),
    "HFG_HOXA13-III": (None, 22),
}
GENE_TRACTS = {"ARX": ["EIEE1_ARX", "PRTS_ARX"], "HOXA13": ["HFG_HOXA13-I", "HFG_HOXA13-II", "HFG_HOXA13-III"]}
# Why a call under TRID ARX between ARX and the next gene is not assessed.
ARX_BETWEEN_THE_TRACTS = (
    "TRID ARX names 2 STRchive loci (EIEE1_ARX, PRTS_ARX), and the call's region, "
    "chrX:25020000-25020060, overlaps none of them, so none of their thresholds applies."
)


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement: Any, params: dict | None = None) -> None:
        self.statements.append((str(statement), params or {}))

    async def commit(self) -> None:
        pass


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict]:
        return self._rows


class _CatalogueSession:
    """Answers the import's catalogue query with the given ``repeat_loci`` rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def execute(self, statement: Any, params: dict | None = None) -> _Result:
        return _Result(self.rows)


async def _catalogue_after_a_start(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The ``repeat_loci`` rows a start leaves, in the catalogue query's order (locus_id)."""
    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    session = _RecordingSession()
    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)
    rows: dict[str, dict] = {}
    for _sql, params in session.statements:
        if "locus_id" not in params:  # the removal of other releases' rows
            continue
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
    return [rows[locus_id] for locus_id in sorted(rows)]


async def _import_lookup(monkeypatch: pytest.MonkeyPatch) -> repeat_expansion_pg.RepeatLocusLookup:
    return await repeat_expansion_pg._build_repeat_locus_lookup(
        _CatalogueSession(await _catalogue_after_a_start(monkeypatch))
    )


def _sample_context(*, assembly_name: str | None = "GRCh38") -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid="sample-uuid",
        sample_id="sample",
        family_uuid="family-uuid",
        family_id="FAM1",
        sex="female",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name=assembly_name,
    )


def _call(
    lookup: dict[str, dict],
    *,
    trid: str,
    chrom: str,
    start: int,
    end: int,
    count: int,
    assembly_name: str | None = "GRCh38",
) -> dict:
    """A woman's TRGT call over ``start``-``end``: a normal allele of 8 GCN repeats and one of
    ``count``."""
    return repeat_expansion_pg._build_trgt_insert_params(
        sample_context=_sample_context(assembly_name=assembly_name),
        locus_lookup=lookup,
        chrom=chrom,
        pos=str(start),
        ref="GCG",
        info_field=f"TRID={trid};END={end};MOTIFS=GCN",
        format_field="GT:AL:MC",
        sample_field=f"1/2:24,{count * 3}:8,{count}",
        header_sample="sample",
        metadata={},
    )


@functools.cache
def _shipped_strchive_entries() -> tuple[dict, ...]:
    return tuple(json.loads(repeat_expansion_pg.REPO_STRCHIVE_LOCI_PATH.read_bytes()))


def _tract_region(locus_id: str, suffix: str = "hg38") -> tuple[str, int, int]:
    """The tract's region as the shipped STRchive release places it, on GRCh38 (``hg38``) or
    T2T-CHM13 (``t2t``)."""
    [entry] = [entry for entry in _shipped_strchive_entries() if entry["id"] == locus_id]
    return str(entry["chrom"]), int(entry[f"start_{suffix}"]), int(entry[f"stop_{suffix}"])


def _statuses(params: dict) -> list[str]:
    return [allele["status"] for allele in json.loads(params["alleles_json"])]


def _stored_reason(params: dict) -> str | None:
    return json.loads(params["metadata_json"]).get("locus_not_resolved")


# ---- The catalogue ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_gene_name_of_a_locus_with_several_tracts_names_each_tract_and_no_row(monkeypatch) -> None:
    lookup = await _import_lookup(monkeypatch)

    for gene, tracts in GENE_TRACTS.items():
        assert gene.lower() not in lookup  # the first tract by locus ID before
        assert [row["locus_id"] for row in lookup.tracts[gene.lower()]] == tracts
        for row in lookup.tracts[gene.lower()]:
            assert (row["warning_min"], row["pathogenic_min"]) == TRACT_THRESHOLDS[row["locus_id"]]
    # The tracts' thresholds differ, so the tract decides how a count reads.
    assert len({TRACT_THRESHOLDS[locus] for locus in GENE_TRACTS["ARX"]}) == 2
    assert len({TRACT_THRESHOLDS[locus] for locus in GENE_TRACTS["HOXA13"]}) == 2
    # Every other gene of the shipped release has one STRchive locus.
    gene_names = {key for key, rows in lookup.tracts.items() if {str(row["gene"]).lower() for row in rows} == {key}}
    assert gene_names == {gene.lower() for gene in GENE_TRACTS}


@pytest.mark.parametrize(("trid", "locus_id"), [("PRTS_ARX", "PRTS_ARX"), ("ARX_2", "PRTS_ARX"), ("HFG_HOXA13-II", "HFG_HOXA13-II"), ("HOXA13_2", "HFG_HOXA13-II")])
@pytest.mark.asyncio
async def test_a_strchive_or_stripy_id_still_names_its_locus_wherever_the_call_lies(monkeypatch, trid: str, locus_id: str) -> None:
    lookup = await _import_lookup(monkeypatch)

    params = _call(lookup, trid=trid, chrom="chr1", start=1_000_000, end=1_000_060, count=10)

    assert params["locus_id"] == locus_id
    assert (params["warning_min"], params["pathogenic_min"]) == TRACT_THRESHOLDS[locus_id]
    assert _stored_reason(params) is None


# ---- The gene's name picks the tract the call's region overlaps --------------------------------------

# (tract, count, status under the tract's own thresholds), at the counts where the tracts of the
# gene disagree; the first tract by locus ID gave the other status before.
OWN_TRACT_CASES = [
    ("EIEE1_ARX", 17, P),
    ("PRTS_ARX", 17, I),  # pathogenic under EIEE1_ARX's 17 before
    ("PRTS_ARX", 19, I),
    ("PRTS_ARX", 20, P),
    ("HFG_HOXA13-I", 18, I),
    ("HFG_HOXA13-II", 18, P),  # normal under HFG_HOXA13-I's 22 before
    ("HFG_HOXA13-II", 17, I),
    ("HFG_HOXA13-III", 21, I),
    ("HFG_HOXA13-III", 22, P),
]


def _gene_of(locus_id: str) -> str:
    return next(gene for gene, tracts in GENE_TRACTS.items() if locus_id in tracts)


@pytest.mark.parametrize(("locus_id", "count", "status"), OWN_TRACT_CASES)
@pytest.mark.asyncio
async def test_a_gene_name_trid_takes_the_thresholds_of_the_tract_its_region_overlaps(
    monkeypatch, locus_id: str, count: int, status: str
) -> None:
    lookup = await _import_lookup(monkeypatch)
    chrom, start, stop = _tract_region(locus_id)

    params = _call(lookup, trid=_gene_of(locus_id), chrom=chrom, start=start, end=stop, count=count)

    assert params["locus_id"] == locus_id
    assert (params["warning_min"], params["pathogenic_min"]) == TRACT_THRESHOLDS[locus_id]
    assert _statuses(params) == [N, status]
    assert params["status"] == status
    assert _stored_reason(params) is None


@pytest.mark.parametrize(("locus_id", "count", "status"), [("PRTS_ARX", 17, I), ("HFG_HOXA13-II", 18, P)])
# NCBI's name, the UCSC import's (UCSC's description), the ClickHouse dataset key and UCSC's own.
@pytest.mark.parametrize("assembly_name", ["T2T-CHM13v2.0", "T2T CHM13v2.0", "T2T_CHM13v2.0", "hs1"])
@pytest.mark.asyncio
async def test_the_tract_is_found_on_t2t_by_its_t2t_coordinates(
    monkeypatch, locus_id: str, count: int, status: str, assembly_name: str
) -> None:
    lookup = await _import_lookup(monkeypatch)
    chrom, start, stop = _tract_region(locus_id, "t2t")
    assert (start, stop) != _tract_region(locus_id)[1:]

    params = _call(lookup, trid=_gene_of(locus_id), chrom=chrom, start=start, end=stop, count=count, assembly_name=assembly_name)

    assert params["locus_id"] == locus_id
    assert params["status"] == status


@pytest.mark.asyncio
async def test_a_tag_several_loci_share_is_resolved_by_position_too(monkeypatch) -> None:
    """STRchive's tags are aliases as well; one names loci of several genes, and the first of
    them by locus ID (CANVAS_RFC1) classified the call before."""
    lookup = await _import_lookup(monkeypatch)
    chrom, start, stop = _tract_region("PRTS_ARX")

    params = _call(lookup, trid="phenotypic_spectrum", chrom=chrom, start=start, end=stop, count=17)

    assert params["locus_id"] == "PRTS_ARX"
    assert params["status"] == I


# ---- None, several, or no coordinates: not assessed, with the reason --------------------------------


@pytest.mark.asyncio
async def test_a_region_that_overlaps_no_tract_is_not_assessed_with_the_reason(monkeypatch) -> None:
    lookup = await _import_lookup(monkeypatch)

    params = _call(lookup, trid="ARX", chrom="chrX", start=25_020_000, end=25_020_060, count=40)

    # EIEE1_ARX's thresholds before: 40 repeats read pathogenic.
    assert _statuses(params) == [NOT_ASSESSED, NOT_ASSESSED]
    assert params["status"] == NOT_ASSESSED
    assert (params["locus_id"], params["warning_min"], params["pathogenic_min"]) == ("ARX", None, None)
    assert _stored_reason(params) == ARX_BETWEEN_THE_TRACTS


@pytest.mark.asyncio
async def test_a_region_over_several_tracts_is_not_assessed_with_the_reason(monkeypatch) -> None:
    lookup = await _import_lookup(monkeypatch)
    chrom, start, _stop = _tract_region("HFG_HOXA13-III")
    _chrom, _start, stop = _tract_region("HFG_HOXA13-II")

    params = _call(lookup, trid="HOXA13", chrom=chrom, start=start, end=stop, count=20)

    assert params["status"] == NOT_ASSESSED
    assert (params["warning_min"], params["pathogenic_min"]) == (None, None)
    reason = _stored_reason(params)
    assert reason is not None
    assert reason.startswith("TRID HOXA13 names 3 STRchive loci (HFG_HOXA13-I, HFG_HOXA13-II, HFG_HOXA13-III)")
    assert "overlaps 2 of them (HFG_HOXA13-II, HFG_HOXA13-III)" in reason


@pytest.mark.parametrize("assembly_name", [None, "CanFam3.1"])
@pytest.mark.asyncio
async def test_an_assembly_without_strchive_coordinates_is_not_assessed_with_the_reason(
    monkeypatch, assembly_name: str | None
) -> None:
    lookup = await _import_lookup(monkeypatch)
    chrom, start, stop = _tract_region("PRTS_ARX")

    params = _call(lookup, trid="ARX", chrom=chrom, start=start, end=stop, count=17, assembly_name=assembly_name)

    assert params["status"] == NOT_ASSESSED
    reason = _stored_reason(params)
    assert reason is not None and "STRchive gives no coordinates" in reason


@pytest.mark.asyncio
async def test_a_no_call_there_stays_unknown(monkeypatch) -> None:
    lookup = await _import_lookup(monkeypatch)

    params = repeat_expansion_pg._build_trgt_insert_params(
        sample_context=_sample_context(),
        locus_lookup=lookup,
        chrom="chrX",
        pos="25020000",
        ref="GCG",
        info_field="TRID=ARX;END=25020060;MOTIFS=GCN",
        format_field="GT:AL:MC",
        sample_field="./.:.:.",
        header_sample="sample",
        metadata={},
    )

    assert params["status"] == "unknown"


@pytest.mark.asyncio
async def test_the_ingest_stores_each_call_against_its_own_tract(monkeypatch) -> None:
    """The whole per-sample ingest: TRID ARX at PRTS_ARX, at EIEE1_ARX and between genes."""
    rows = await _catalogue_after_a_start(monkeypatch)
    prts = _tract_region("PRTS_ARX")
    eiee1 = _tract_region("EIEE1_ARX")

    class _IngestSession:
        def __init__(self) -> None:
            self.inserted: list[dict] = []

        async def execute(self, statement: Any, params: Any = None) -> _Result:
            if "INSERT INTO repeat_expansions" in str(statement):
                self.inserted.extend(params)
            if "FROM repeat_loci" in str(statement):
                return _Result(rows)
            return _Result([])

        async def commit(self) -> None:
            pass

    async def _known_samples(*_args: Any, **_kwargs: Any) -> set[str]:
        return {"sample"}

    async def _no_provenance(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(repeat_expansion_pg, "known_vcf_sample_ids", _known_samples)
    monkeypatch.setattr("backend.app.services.annotation_manifest_service.merge_vcf_header_provenance", _no_provenance)
    session = _IngestSession()
    records = "".join(
        f"{chrom}\t{start}\t.\tGCG\t<TR>\t.\tPASS\tTRID=ARX;END={stop};MOTIFS=GCN\tGT:AL:MC\t1/2:24,51:8,17\n"
        for chrom, start, stop in (prts, eiee1, ("chrX", 25_020_000, 25_020_060))
    )

    await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=_sample_context(),
        text_value=(
            "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n" + records
        ),
        metadata={"filename": "sample.trgt.vcf", "source": "trgt"},
    )

    stored = [(record["locus_id"], record["pathogenic_min"], record["status"]) for record in session.inserted]
    assert stored == [("PRTS_ARX", 20, I), ("EIEE1_ARX", 17, P), ("ARX", None, NOT_ASSESSED)]


# ---- The family table ----------------------------------------------------------------------------------

SAMPLE_UUID = "00000000-0000-4000-8000-000000000031"


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f3",
        family_id="FAM1",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_uuid": SAMPLE_UUID, "sample_id": "WOMAN1", "role": "proband", "affected": True, "sex": "female"}],
        sample_uuid_to_name={SAMPLE_UUID: "WOMAN1"},
        sample_name_to_uuid={"WOMAN1": SAMPLE_UUID},
        affected_sample_names=["WOMAN1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.mark.asyncio
async def test_the_family_table_shows_why_a_call_is_not_assessed() -> None:
    """The call as the import stored it between ARX's tracts, read by the table's query."""
    reason = ARX_BETWEEN_THE_TRACTS

    class _TableSession:
        async def execute(self, statement: Any, params: Any = None) -> _Result:
            # No catalogue row matched the call: its stored (missing) thresholds, and the reason.
            return _Result(
                [
                    {
                        "sample_uuid": SAMPLE_UUID,
                        "locus_id": "ARX",
                        "gene": "ARX",
                        "display_name": "ARX",
                        "disease": "ARX",
                        "inheritance": None,
                        "chr": "X",
                        "start": 25_020_000,
                        "end": 25_020_060,
                        "motif": "GCN",
                        "genotype": "1/2",
                        "allele_count": 2,
                        "alleles": [{"repeat_count": 8, "status": NOT_ASSESSED}, {"repeat_count": 40, "status": NOT_ASSESSED}],
                        "warning_min": None,
                        "pathogenic_min": None,
                        "benign_min": None,
                        "benign_max": None,
                        "pathogenic_max": None,
                        "premutation_min": None,
                        "status": NOT_ASSESSED,
                        "locus_not_resolved": reason,
                    }
                ]
            )

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(), context=_context())

    [locus] = table.loci
    call = locus.calls["WOMAN1"]
    assert call.status == locus.status == NOT_ASSESSED
    assert call.note == reason
    assert (locus.warning_min, locus.pathogenic_min) == (None, None)


@pytest.mark.asyncio
async def test_the_family_table_lets_a_name_of_several_strchive_loci_choose_none_of_them() -> None:
    """The query matched a stored call to the first catalogue row its gene names, so a call
    stored as not assessed under TRID ARX was read against EIEE1_ARX's thresholds again. Where
    the call's names reach several STRchive loci, only the locus it was stored under matches;
    the stored reason is read while no row matches. (integration/test_repeat_multi_locus_storage.py
    runs the query.)"""
    captured: list[str] = []

    class _Session:
        async def execute(self, statement: Any, params: Any = None) -> _Result:
            captured.append(" ".join(str(statement).split()))
            return _Result([])

    await repeat_expansion_pg.get_family_repeat_expansion_table_response(_Session(), context=_context())

    [sql] = captured
    assert "SELECT count(*) AS loci FROM repeat_loci AS named WHERE named.metadata ->> 'source' = 'STRchive'" in sql
    assert (
        "AND ( lower(repeat_loci.locus_id) = lower(repeat_expansions.locus_id) OR strchive_named.loci <= 1 )"
    ) in sql
    assert (
        "CASE WHEN catalog.locus_id IS NULL THEN repeat_expansions.metadata ->> 'locus_not_resolved' END "
        "AS locus_not_resolved"
    ) in sql
