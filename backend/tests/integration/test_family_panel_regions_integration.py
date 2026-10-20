"""The family page's panel-region join keeps what testing each row against every region keeps (ENG-15).

The family page kept a variant in one of a panel's regions by testing each of its rows against
every region (``_small_region_filter_condition``, ``arrayExists`` over the regions): 2.1 s for
a panel of 1,000 regions on a family of about 190,000 rows. It now joins the family's own rows
to the regions on 64-kb windows (``_small_family_region_condition``), as the Variant Explorer
joins its annotation index (CLIN-48), and keeps a row whose chromosome, position and REF length
are those of a row the join keeps.

Both conditions run here, in ClickHouse, on the same synthetic entry rows, in the search's own
WHERE (``_small_variant_where_clauses``): every chromosome spelling a store may hold ("1",
"chr1", "X", "M", "MT", "chrM", an unplaced contig), positions on each side of window
boundaries, and REFs of one base to more than two windows; and #857's regions (spelled with and
without "chr", reversed, repeated, of one base, ending or starting on a window boundary,
spanning several windows, or without a chromosome), with regions that share bases, nested or
in a chain across a window boundary, as a gene's transcript loci do. Those are merged into one
before they are bound (``_small_region_bounds``), so that a panel as large as the Mendeliome,
some 42,000 loci, binds about one region per gene; a third query tests each region on its own,
unmerged. The family's rows lie in two projects, beside the same rows of another family, of a
project the search does not read, and collapsed (``sign = -1``). Without a locus and within
each of several loci, overlapping or not, the three keep exactly the same rows. The rows are a
subquery in place of the entries table, so no table is written.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke``
job sets it.
"""

from __future__ import annotations

import asyncio
from itertools import product
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_WINDOW = 1 << 16
_CHROMOSOMES = ["1", "chr1", "X", "chrX", "M", "MT", "chrM", "GL000192.1"]
_FAMILY = "eng15-family"
_PROJECTS = ["eng15-project-a", "eng15-project-b"]


def _positions() -> list[int]:
    """Positions on each side of three window boundaries, and a few in between."""
    positions: set[int] = {1, 2, 1000, 30_000}
    for boundary in (_WINDOW, 2 * _WINDOW, 2092 * _WINDOW):
        positions.update(boundary + offset for offset in range(-6, 7))
    return sorted(positions)


def _shapes() -> list[tuple[str, int, int]]:
    """(chromosome as stored, position, REF length): every chromosome and position with REFs
    of 1 to 50 bases, and a few REFs longer than one and than two windows."""
    shapes = list(product(_CHROMOSOMES, _positions(), [1, 2, 5, 50]))
    shapes += product(["1", "GL000192.1"], [1, _WINDOW - 3, _WINDOW + 1, 2 * _WINDOW - 2], [_WINDOW + 1, 2 * _WINDOW + 7])
    return shapes


def _rows() -> list[tuple[int, str, int, int, str, str, int, int]]:
    """(key, chromosome, position, REF length, family, project, sign, xpos): each shape once
    for the family, in one of its two projects in turn, and once as another family's row, as
    a row of a project the search does not read, and as a collapsed row of the family."""
    from backend.app.services.clickhouse_variant_queries import _xpos

    rows = []
    placements = [
        *((_FAMILY, project, 1) for project in _PROJECTS),
        ("eng15-other-family", _PROJECTS[0], 1),
        (_FAMILY, "eng15-other-project", 1),
        (_FAMILY, _PROJECTS[0], -1),
    ]
    for index, (chromosome, position, length) in enumerate(_shapes()):
        family_rows = [placements[index % 2], *placements[2:]]
        for family, project, sign in family_rows:
            rows.append((len(rows) + 1, chromosome, position, length, family, project, sign, _xpos(chromosome, position)))
    return rows


def _regions() -> list[Any]:
    from backend.app.services.clickhouse_variant_records import Region

    window_2092 = 2092 * _WINDOW
    return [
        Region(chr="chr1", start=_WINDOW, end=_WINDOW + 2),  # starts on a boundary
        Region(chr="1", start=2 * _WINDOW - 3, end=2 * _WINDOW - 1),  # ends before one
        Region(chr="1", start=2 * _WINDOW - 1, end=2 * _WINDOW - 1),  # one base
        Region(chr="1", start=_WINDOW + 2, end=_WINDOW),  # reversed
        Region(chr="chr1", start=_WINDOW, end=_WINDOW + 2),  # repeated
        Region(chr="X", start=window_2092 - 4, end=window_2092 + 1),  # across a boundary
        Region(chr="chrM", start=900, end=1_100),  # M, read as MT
        Region(chr="MT", start=_WINDOW - 1, end=_WINDOW - 1),
        Region(chr="GL000192.1", start=1, end=3 * _WINDOW + 5),  # several windows
        Region(chr="", start=1, end=10),  # no chromosome: left out
        # Regions that share bases, as a gene's transcript loci do (a gene reference of one
        # row per transcript): merged into one before they are bound.
        Region(chr="1", start=900, end=1_200),
        Region(chr="chr1", start=950, end=1_000),  # nested
        Region(chr="1", start=1_150, end=1_100),  # nested, reversed
        Region(chr="X", start=2 * _WINDOW - 6, end=2 * _WINDOW - 3),  # a chain across a boundary
        Region(chr="x", start=2 * _WINDOW - 3, end=2 * _WINDOW + 1),
        Region(chr="chrX", start=2 * _WINDOW + 1, end=2 * _WINDOW + 4),
        Region(chr="MT", start=29_990, end=29_999),  # touching only: not merged
        Region(chr="chrM", start=30_000, end=30_001),
    ]


def _each_region_bounds(regions: Any) -> tuple[list[str], list[int], list[int]]:
    """The regions each read on their own (match key, start and end in order, each once),
    unmerged: the reference the merged regions must answer alike."""
    from backend.app.services.clickhouse_variant_queries import _chromosome_match_key

    kept = dict.fromkeys(
        (_chromosome_match_key(region.chr), min(region.start, region.end), max(region.start, region.end))
        for region in regions
        if _chromosome_match_key(region.chr)
    )
    return [c for c, _s, _e in kept], [s for _c, s, _e in kept], [e for _c, _s, e in kept]


def _rows_subquery(params: dict[str, Any]) -> str:
    """The synthetic rows, typed as the entries table types them."""
    rows = _rows()
    for name, column in zip(
        ("keys", "chromosomes", "positions", "ref_lengths", "families", "projects", "signs", "xpos"),
        zip(*rows),
    ):
        params[f"rows_{name}"] = list(column)
    return (
        "(SELECT toUInt64(row.1) AS key, toLowCardinality(row.2) AS chrom, toUInt32(row.3) AS pos,"
        " repeat('A', toUInt64(row.4)) AS ref, row.5 AS family_guid,"
        " toLowCardinality(row.6) AS project_guid, toInt8(row.7) AS sign, toUInt64(row.8) AS xpos"
        " FROM (SELECT arrayJoin(arrayZip(%(rows_keys)s, %(rows_chromosomes)s, %(rows_positions)s,"
        " %(rows_ref_lengths)s, %(rows_families)s, %(rows_projects)s, %(rows_signs)s,"
        " %(rows_xpos)s)) AS row))"
    )


# The searches: none narrowed to a locus, and loci that cut through regions and rows, with and
# without the overlap rule a locus can apply.
_SEARCHES: dict[str, dict[str, Any]] = {
    "no locus": {},
    "chromosome 1, overlapping a window boundary": {
        "chromosome": "1", "start": _WINDOW - 3, "end": _WINDOW + 4, "overlap": True,
    },
    "chr1, contained in a locus": {"chromosome": "chr1", "start": _WINDOW - 3, "end": 2 * _WINDOW},
    "X, from a position on": {"chromosome": "X", "start": 2092 * _WINDOW - 2},
    "MT, up to a position": {"chromosome": "MT", "end": _WINDOW},
    "the unplaced contig, overlapping": {
        "chromosome": "GL000192.1", "start": 2 * _WINDOW, "end": 2 * _WINDOW + 3, "overlap": True,
    },
}


def test_the_family_region_join_keeps_exactly_what_the_per_row_test_keeps() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client, execute_clickhouse, init_clickhouse_schema
    from backend.app.services import clickhouse_variant_queries as queries
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.clickhouse_variant_queries import (
        _small_family_region_condition,
        _small_region_filter_condition,
        _small_variant_where_clauses,
    )
    from backend.app.services.family_metadata_context import FamilyMetadataContext
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    context = FamilyMetadataContext(
        family_uuid=_FAMILY,
        family_id="ENG15",
        project_ids=list(_PROJECTS),
        sample_rows=[{"sample_id": "PROBAND"}],
        sample_uuid_to_name={},
        sample_name_to_uuid={"PROBAND": "PROBAND"},
        affected_sample_names=["PROBAND"],
        assembly_id="eng15-assembly",
        assembly_name="GRCh38",
    )
    entries = _small_table_name("GRCh38", "entries")
    rows_params: dict[str, Any] = {}
    rows = _rows_subquery(rows_params)
    searches: dict[str, tuple[str, str, str, dict[str, Any]]] = {}
    for name, locus in _SEARCHES.items():
        filters = SmallVariantQueryFilters(page=1, page_size=100, **locus)
        clauses, params = _small_variant_where_clauses(context, filters)
        # The calls are what a search reads besides the rows' place; these rows have none.
        clauses = [clause for clause in clauses if not clause.startswith("hasAny(e.calls.sampleId")]
        per_row = _small_region_filter_condition(_regions(), prefix="panel_region", params=params)
        region_params = dict(params)
        joined = _small_family_region_condition(context, filters)(
            _regions(), prefix="panel_region", params=params
        )
        # The reference: each region tested on its own, not merged with those it shares bases with.
        with pytest.MonkeyPatch.context() as each:
            each.setattr(queries, "_small_region_bounds", _each_region_bounds)
            each_region = _small_region_filter_condition(_regions(), prefix="each_region", params=params)
        assert per_row is not None and joined is not None and each_region is not None
        assert len(params["panel_region_starts"]) < len(params["each_region_starts"])
        # The same regions, read the same way; the join adds the family and its projects.
        assert {key: params[key] for key in region_params} == region_params
        assert f"FROM {entries} AS fe" in joined
        joined = joined.replace(f"FROM {entries} AS fe", f"FROM {rows} AS fe")
        where = " AND ".join(clauses)
        searches[name] = (
            f"SELECT e.key FROM {rows} AS e WHERE {where} AND {per_row}",
            f"SELECT e.key FROM {rows} AS e WHERE {where} AND {joined}",
            f"SELECT e.key FROM {rows} AS e WHERE {where} AND {each_region}",
            {**params, **rows_params},
        )

    async def _run() -> dict[str, tuple[set[int], set[int], set[int]]]:
        try:
            await init_clickhouse_schema()
            kept: dict[str, tuple[set[int], set[int], set[int]]] = {}
            for name, (per_row_query, joined_query, each_region_query, params) in searches.items():
                per_row_keys = await execute_clickhouse(per_row_query, params)
                joined_keys = await execute_clickhouse(joined_query, params)
                each_region_keys = await execute_clickhouse(each_region_query, params)
                kept[name] = (
                    {int(row[0]) for row in per_row_keys},
                    {int(row[0]) for row in joined_keys},
                    {int(row[0]) for row in each_region_keys},
                )
            return kept
        finally:
            await close_clickhouse_client()

    kept = asyncio.run(_run())
    by_key = {row[0]: row for row in _rows()}
    for name, (per_row_keys, joined_keys, each_region_keys) in kept.items():
        assert joined_keys == per_row_keys, name
        # Merging the regions that share bases keeps what each of them kept.
        assert per_row_keys == each_region_keys, name
        # Only the family's own rows, in its projects, are read.
        assert all(by_key[key][4:7] in {(_FAMILY, project, 1) for project in _PROJECTS} for key in per_row_keys), name
    # Not a vacuous comparison: without a locus some of the family's rows are kept, by every
    # chromosome spelling of a region and in both projects, and some are not; within each
    # locus some are kept.
    everything, _joined, _each_region = kept["no locus"]
    family_rows = [row for row in _rows() if row[4] == _FAMILY and row[5] in _PROJECTS and row[6] == 1]
    assert 0 < len(everything) < len(family_rows)
    assert {by_key[key][1] for key in everything} == set(_CHROMOSOMES)
    assert {by_key[key][5] for key in everything} == set(_PROJECTS)
    # The merged regions keep rows: chromosome 1 at 1,000, in the nested regions, and X on
    # each side of the boundary the chain crosses.
    assert {by_key[key][2] for key in everything if by_key[key][1] in ("1", "chr1")} >= {1000}
    assert {by_key[key][2] for key in everything if by_key[key][1] in ("X", "chrX")} >= {
        2 * _WINDOW - 6, 2 * _WINDOW, 2 * _WINDOW + 4,
    }
    for name, (per_row_keys, _joined, _each_region) in kept.items():
        assert per_row_keys, name
