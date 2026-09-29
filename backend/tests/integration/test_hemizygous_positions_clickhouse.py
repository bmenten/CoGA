"""The ClickHouse hemizygous-position condition agrees with Python (#545) — real ClickHouse.

The de novo pre-filter admits a male's haploid ``1`` or ``1/1`` call only on chrX and chrY
outside the pseudo-autosomal regions. This runs sites on both sides of every PAR
boundary, under each chromosome name, through the real server, and checks that each lands
where :func:`hemizygous_chromosome` puts it.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration

_SITES = [
    ("chrX", 10_000), ("chrX", 10_001), ("chrX", 2_781_479), ("chrX", 2_781_480),
    ("X", 31_500_000), ("23", 31_500_000), ("chr23", 31_500_000),
    ("chrX", 155_701_382), ("chrX", 155_701_383), ("chrX", 156_030_895), ("chrX", 156_030_896),
    ("chrY", 10_001), ("chrY", 2_787_000), ("Y", 2_787_000), ("24", 2_787_000),
    ("chrY", 56_887_902), ("chrY", 56_887_903), ("chrY", 57_217_415), ("chrY", 57_217_416),
    ("chr1", 31_500_000), ("1", 2_787_000), ("chrM", 3_243), ("MT", 3_243),
]


@pytest.mark.parametrize("assembly", ["GRCh38", "GRCh37"])
def test_sql_hemizygous_positions_match_python(assembly: str) -> None:
    from backend.app.core.clickhouse import close_clickhouse_client, execute_clickhouse
    from backend.app.services.clickhouse_variant_queries import _small_hemizygous_position_condition
    from backend.app.services.sex_chromosomes import hemizygous_chromosome

    params: dict = {
        "chroms": [chrom for chrom, _ in _SITES],
        "positions": [pos for _, pos in _SITES],
    }
    condition = _small_hemizygous_position_condition(assembly, prefix="hemi", params=params)
    assert condition is not None
    query = (
        f"SELECT e.chrom, e.pos, {condition} AS hemizygous FROM ("
        "SELECT tupleElement(site, 1) AS chrom, toUInt32(tupleElement(site, 2)) AS pos "
        "FROM (SELECT arrayJoin(arrayZip(%(chroms)s, %(positions)s)) AS site)"
        ") AS e"
    )

    async def _run():
        try:
            return await execute_clickhouse(query, params)
        finally:
            await close_clickhouse_client()

    rows = asyncio.run(_run())
    assert len(rows) == len(_SITES)
    mismatches = [
        (chrom, pos, bool(flag))
        for chrom, pos, flag in rows
        if bool(flag) != (hemizygous_chromosome(assembly, chrom, int(pos)) is not None)
    ]
    assert not mismatches, f"SQL and Python disagree: {mismatches}"
