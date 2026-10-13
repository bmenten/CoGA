"""The explorer's panel-region join keeps what the family page's region condition keeps (CLIN-48).

The family page keeps a variant in one of a panel's regions with ``arrayExists`` over every
region, per row (``_small_region_filter_condition``). Across every family of the cohort the
Variant Explorer reads the same regions as a join on 64-kb windows instead
(``variant_explorer_service._region_key_condition``): a region is listed under each window it
touches, a variant under each window its REF touches, and a pair that shares a window is kept
when the two overlap by the family page's rule.

Both conditions run here, in ClickHouse, over the same synthetic variants: every chromosome
spelling the stores hold ("1", "chr1", "X", "M", "MT", "chrM", an unplaced contig), positions
on each side of window boundaries, and REFs of one base to more than two windows; and regions
spelled with and without "chr", reversed, repeated, of one base, ending or starting on a
window boundary, spanning several windows, or without a chromosome. Each keeps exactly the
same variants. The variants are a subquery in place of the annotation index, so no table is
written.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from itertools import product
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_WINDOW = 1 << 16
_CHROMOSOMES = ["1", "chr1", "X", "chrX", "M", "MT", "chrM", "GL000192.1"]


def _positions() -> list[int]:
    """Positions on each side of three window boundaries, and a few in between."""
    positions: set[int] = {1, 2, 1000, 30_000}
    for boundary in (_WINDOW, 2 * _WINDOW, 2092 * _WINDOW):
        positions.update(boundary + offset for offset in range(-6, 7))
    return sorted(positions)


def _variants() -> list[tuple[int, str, int, int]]:
    """(key, chromosome as stored, position, REF length): every chromosome and position
    with REFs of 1 to 50 bases, and a few REFs longer than one and than two windows."""
    shapes = list(product(_CHROMOSOMES, _positions(), [1, 2, 5, 50]))
    shapes += product(["1", "GL000192.1"], [1, _WINDOW - 3, _WINDOW + 1, 2 * _WINDOW - 2], [_WINDOW + 1, 2 * _WINDOW + 7])
    return [(key, chromosome, position, length) for key, (chromosome, position, length) in enumerate(shapes, start=1)]


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
    ]


def _rows_subquery(params: dict[str, Any]) -> str:
    """The synthetic variants, typed as the annotation index types them."""
    variants = _variants()
    params["rows_keys"] = [key for key, *_rest in variants]
    params["rows_chromosomes"] = [chromosome for _key, chromosome, _position, _length in variants]
    params["rows_positions"] = [position for _key, _chromosome, position, _length in variants]
    params["rows_ref_lengths"] = [length for *_rest, length in variants]
    return (
        "(SELECT toUInt64(row.1) AS key, toLowCardinality(row.2) AS chrom, toUInt32(row.3) AS pos,"
        " repeat('A', toUInt64(row.4)) AS ref"
        " FROM (SELECT arrayJoin(arrayZip(%(rows_keys)s, %(rows_chromosomes)s,"
        " %(rows_positions)s, %(rows_ref_lengths)s)) AS row))"
    )


def test_the_region_join_keeps_exactly_what_the_family_page_keeps() -> None:
    from backend.app.core.clickhouse import (
        close_clickhouse_client,
        execute_clickhouse,
        init_clickhouse_schema,
    )
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.clickhouse_variant_queries import _small_region_filter_condition
    from backend.app.services.variant_explorer_service import _region_key_condition

    regions = _regions()
    family_params: dict[str, Any] = {}
    family = _small_region_filter_condition(regions, prefix="panel_region", params=family_params)
    explorer_params: dict[str, Any] = {}
    explorer = _region_key_condition("GRCh38", regions, prefix="panel_region", params=explorer_params)
    assert family is not None and explorer is not None
    assert explorer_params == family_params

    params: dict[str, Any] = dict(family_params)
    rows = _rows_subquery(params)
    index = _small_table_name("GRCh38", "variants/annotation_index")
    assert f"FROM {index} AS ai" in explorer
    explorer_on_rows = explorer.replace(f"FROM {index} AS ai", f"FROM {rows} AS ai")

    async def _run() -> tuple[set[int], set[int], int]:
        try:
            await init_clickhouse_schema()
            family_keys = await execute_clickhouse(
                f"SELECT e.key FROM {rows} AS e WHERE {family}", params
            )
            explorer_keys = await execute_clickhouse(
                f"SELECT e.key FROM {rows} AS e WHERE {explorer_on_rows}", params
            )
            total = await execute_clickhouse(f"SELECT count() FROM {rows} AS e", params)
            return (
                {int(row[0]) for row in family_keys},
                {int(row[0]) for row in explorer_keys},
                int(total[0][0]),
            )
        finally:
            await close_clickhouse_client()

    family_keys, explorer_keys, total = asyncio.run(_run())
    assert total == len(_variants())
    # Not a vacuous comparison: some variants are kept, by every chromosome spelling of a
    # region, and some are not.
    assert 0 < len(family_keys) < total
    kept_chromosomes = {chromosome for key, chromosome, *_rest in _variants() if key in family_keys}
    assert kept_chromosomes == set(_CHROMOSOMES)
    assert explorer_keys == family_keys
