"""The ClickHouse genotype conditions classify exactly like Python (#511) — real ClickHouse.

Filters, counts and presence checks classify genotypes in SQL with a set lookup plus an
allele-by-allele fallback for strings longer than three characters. This runs every short
genotype string, and a set of long and malformed ones, through the real server and
checks that each lands in exactly the class ``classify_genotype`` gives it.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration

_LONG_GENOTYPES = [
    "10/12", "12/12", "0/10", "10|0", "./10", "10/.", "0/0/0", "1/1/1", "1/1/2",
    "HET", "HOMOZYGOUS", "1/a", "01/1", "00/00", "1//1", "a",
]


def test_sql_genotype_classes_match_python() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client, execute_clickhouse
    from backend.app.services.genotypes import (
        GENOTYPE_CLASSES,
        classify_genotype,
        clickhouse_genotype_condition,
        genotype_vocabulary,
    )

    genotypes = sorted(set(genotype_vocabulary(*GENOTYPE_CLASSES)) | set(_LONG_GENOTYPES))
    params: dict = {"genotypes": genotypes}
    columns = [
        f"{clickhouse_genotype_condition('gt', {cls}, param=f'gt_{cls}', params=params)} AS is_{cls}"
        for cls in GENOTYPE_CLASSES
    ]
    query = f"SELECT gt, {', '.join(columns)} FROM (SELECT arrayJoin(%(genotypes)s) AS gt)"

    async def _run():
        try:
            return await execute_clickhouse(query, params)
        finally:
            await close_clickhouse_client()

    rows = asyncio.run(_run())
    assert len(rows) == len(genotypes)
    mismatches = []
    for gt, *flags in rows:
        sql_classes = [cls for cls, flag in zip(GENOTYPE_CLASSES, flags) if flag]
        if sql_classes != [classify_genotype(gt)]:
            mismatches.append((gt, sql_classes, classify_genotype(gt)))
    assert not mismatches, f"SQL and Python disagree: {mismatches}"
