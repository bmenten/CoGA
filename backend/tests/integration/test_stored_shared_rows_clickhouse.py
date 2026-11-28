"""A small-variant write leaves out the shared rows that are stored already, against real
ClickHouse.

The shared tables (``variants/details``, ``annotations``, ``annotation_index``,
``gene_index``) hold a variant's rows per annotation version and annotation set, whatever
family wrote them. A write looks its variants up in ``annotation_index`` and inserts the
shared rows of those it does not find there, with their annotation set and rsID; the family's
``entries`` rows it always inserts. These tests write the same families twice, once as the
writer does and once with every shared row written again, and check on the real server that
both store the same distinct rows, the first with fewer duplicates; that every family row
finds its shared rows; and that a write that stopped before ``annotation_index`` leaves its
variants to be written again by the next one.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import run_closing_stores, small_variant, vep_annotations

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_CSQ = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature"
_SHARED_TABLES = ("variants/details", "variants/annotations", "variants/annotation_index", "variants/gene_index")


def _families() -> list[list[Any]]:
    """Three families' writes: the first stores two variants; the second carries the first
    of them as stored, the second with another rsID, and a variant of its own; the third the
    first variant with another annotation set."""
    first = small_variant("1-1000-A-G", vep_annotations(_CSQ, ["G|missense_variant|MODERATE|GENE1|ENSG1|Transcript|ENST1"]))
    second = replace(
        small_variant("1-2000-C-T", vep_annotations(_CSQ, ["T|intron_variant|MODIFIER|GENE2|ENSG2|Transcript|ENST2"])),
        rsid="rs2",
    )
    own = small_variant("2-3000-G-A", vep_annotations(_CSQ, ["A|stop_gained|HIGH|GENE3|ENSG3|Transcript|ENST3"]))
    reannotated = small_variant(
        "1-1000-A-G",
        vep_annotations(
            _CSQ,
            [
                "G|missense_variant|MODERATE|GENE1|ENSG1|Transcript|ENST1",
                "G|intron_variant|MODIFIER|GENE1|ENSG1|Transcript|ENST4",
            ],
        ),
    )
    return [[first, second], [first, replace(second, rsid="rs2-other"), own], [reannotated]]


async def _rows(query: str, params: dict[str, Any]) -> list[Any]:
    from backend.app.core.clickhouse import execute_clickhouse

    return list(await execute_clickhouse(query, params) or [])


@asynccontextmanager
async def _merges_stopped() -> AsyncIterator[None]:
    """The shared tables unmerged meanwhile: a merge would fold their duplicates, which the
    tests count."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    names = [_small_table_name(_ASSEMBLY, table) for table in _SHARED_TABLES]
    for name in names:
        await execute_clickhouse(f"SYSTEM STOP MERGES {name}")
    try:
        yield
    finally:
        for name in names:
            await execute_clickhouse(f"SYSTEM START MERGES {name}")


async def _shared_rows(table: str, version: str) -> tuple[int, list[str]]:
    """``table``'s rows of ``version``: how many are stored, and the distinct ones (without
    the version and the write time)."""
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    name = _small_table_name(_ASSEMBLY, table)
    params = {"version": version}
    ((count,),) = await _rows(f"SELECT count() FROM {name} WHERE annotation_version = %(version)s", params)
    distinct = await _rows(
        f"SELECT DISTINCT * EXCEPT (annotation_version, updatedAt) FROM {name} WHERE annotation_version = %(version)s",
        params,
    )
    return int(count), sorted(repr(row) for row in distinct)


async def _family_rows_without_shared_rows(families: list[str], version: str) -> int:
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    entries = _small_table_name(_ASSEMBLY, "entries")
    missing = 0
    for table in _SHARED_TABLES[:3]:
        ((count,),) = await _rows(
            f"""
            SELECT count() FROM {entries}
            WHERE family_guid IN %(families)s AND annotation_version = %(version)s
              AND (key, annotationSetHash) NOT IN (
                SELECT key, annotationSetHash FROM {_small_table_name(_ASSEMBLY, table)}
                WHERE annotation_version = %(version)s
              )
            """,
            {"families": tuple(families), "version": version},
        )
        missing += int(count)
    return missing


def test_a_write_stores_the_distinct_shared_rows_of_writing_them_all_with_fewer_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services import clickhouse_variant_storage as cvs

    project = str(uuid4())
    skipping, writing_all = f"stored-rows-{uuid4().hex}", f"all-rows-{uuid4().hex}"
    families = {skipping: [str(uuid4()) for _ in range(3)], writing_all: [str(uuid4()) for _ in range(3)]}

    async def nothing_stored(*_args: Any) -> set[Any]:
        return set()

    async def write(version: str) -> None:
        for family, records in zip(families[version], _families(), strict=True):
            await cvs.insert_small_variant_records(_ASSEMBLY, family, [project], records, annotation_version=version)

    async def _run() -> None:
        await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
        async with _merges_stopped():
            await write(skipping)
            with monkeypatch.context() as patch:
                patch.setattr(cvs, "_stored_shared_rows", nothing_stored)
                await write(writing_all)

            for table in _SHARED_TABLES:
                stored, distinct = await _shared_rows(table, skipping)
                stored_all, distinct_all = await _shared_rows(table, writing_all)
                assert distinct == distinct_all, table
                # The second family's first variant is the one write left out.
                assert stored < stored_all, table
            # One annotation_index row per identity written: two, three less the stored one, one.
            assert (await _shared_rows("variants/annotation_index", skipping))[0] == 5
        assert await _family_rows_without_shared_rows(families[skipping], skipping) == 0

    run_closing_stores(_run)


def test_a_write_that_stopped_before_the_annotation_index_leaves_its_variants_to_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services import clickhouse_variant_storage as cvs

    project, version = str(uuid4()), f"stopped-{uuid4().hex}"
    stopped, next_family = str(uuid4()), str(uuid4())
    records = _families()[0]
    write_rows = cvs._execute_insert_chunks

    async def stop_at_the_annotation_index(query: str, rows: Any, *, chunk_size: int) -> None:
        if "variants/annotation_index" in query:
            raise RuntimeError("the write stopped")
        await write_rows(query, rows, chunk_size=chunk_size)

    async def _run() -> None:
        await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
        async with _merges_stopped():
            with monkeypatch.context() as patch:
                patch.setattr(cvs, "_execute_insert_chunks", stop_at_the_annotation_index)
                with pytest.raises(RuntimeError, match="the write stopped"):
                    await cvs.insert_small_variant_records(
                        _ASSEMBLY, stopped, [project], records, annotation_version=version
                    )
            assert (await _shared_rows("variants/details", version))[0] == 2
            assert (await _shared_rows("variants/annotation_index", version))[0] == 0

            await cvs.insert_small_variant_records(_ASSEMBLY, next_family, [project], records, annotation_version=version)

            # Not found stored, so written again whole: twice over in the tables the stopped
            # write reached, once in annotation_index.
            for table in ("variants/details", "variants/annotations", "variants/gene_index"):
                stored, distinct = await _shared_rows(table, version)
                assert stored == 2 * len(distinct), table
            assert (await _shared_rows("variants/annotation_index", version))[0] == 2
        assert await _family_rows_without_shared_rows([next_family], version) == 0

    run_closing_stores(_run)
