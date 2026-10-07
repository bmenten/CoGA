"""The other cfDNA samples that carry a de novo candidate, counted on real ClickHouse (E2E).

The monogenic NIPT de novo view leaves a candidate out as recurrent when another
family's cfDNA sample of the same assay carries it (the R NIPT-M triage's rule). A
per-sample cfDNA callset stores every low-level Mutect2 call, so a read or two that crossed
over from another sample of the run would make a true de novo candidate "recurrent". A
sample therefore counts only with a call of at least 5 alt reads and 1% of the reads, the
quality filter's floors; a call without allele depths counts on its genotype. The index
family's own calls never count.

The rows are synthetic, written by the importer's own writer as a monogenic NIPT import
stores them: one row per variant and family in the ``nipt`` callset, each with the key,
position and project an import gives it. ``SNV_INDEL/entries`` is a CollapsingMergeTree,
and when ClickHouse merges its parts in the background it keeps one row per sort key. The
rows used to leave the sort-key columns at their defaults, so a family's two rows shared
one key, and a merge that ran before the count lost one of them. The counts are read as
stored and again after a forced part merge (``OPTIMIZE … FINAL``), and must be the same.
The tables are those of an assembly no other test uses, dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "E2ENIPTCARRIERS"
_PROJECT = "e2e-nipt-project"
_INDEX_FAMILY = "e2e-nipt-index"


def _record(variant_id: str, sample: str, gt: str, ad: list[int]):
    from backend.app.services.clickhouse_variant_queries import NIPT_SMALL_VARIANT_SOURCE
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source=NIPT_SMALL_VARIANT_SOURCE,
        rsid=None,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[],
        calls=[SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=ad, ps=None)],
    )


async def _count() -> dict[str, int]:
    from backend.app.services.clickhouse_family_variants import count_cfdna_carriers

    return await count_cfdna_carriers(
        _ASSEMBLY,
        ["7-1000-A-G", "7-2000-C-T"],
        carrier_samples={name: name for name in ("CFDNA1", "CFDNA2", "CFDNA3", "CFDNA4", "CFDNA5")},
        exclude_family_uuid=_INDEX_FAMILY,
    )


async def _exercise() -> dict[str, dict[str, int]]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
    database = settings.clickhouse_database
    rows = [
        # A real call in another family's plasma: 10% of 1000 reads.
        ("e2e-nipt-other-1", "7-1000-A-G", "CFDNA2", "0/1", [900, 100]),
        # Crossed-over reads: 2 alt reads.
        ("e2e-nipt-other-2", "7-1000-A-G", "CFDNA3", "0/1", [998, 2]),
        # 10 alt reads, but 0.5% of the reads.
        ("e2e-nipt-other-3", "7-1000-A-G", "CFDNA4", "0/1", [1990, 10]),
        # No allele depths: the genotype tells.
        ("e2e-nipt-other-4", "7-1000-A-G", "CFDNA5", "0/1", []),
        # The index family's own call.
        (_INDEX_FAMILY, "7-1000-A-G", "CFDNA1", "0/1", [900, 100]),
        # Another variant, carried only by crossed-over reads.
        ("e2e-nipt-other-1", "7-2000-C-T", "CFDNA2", "0/1", [997, 3]),
    ]
    try:
        records: dict[str, list] = {}
        for family, variant, sample, gt, ad in rows:
            records.setdefault(family, []).append(_record(variant, sample, gt, ad))
        # One write per family, as each family's import writes its callset.
        for family, family_records in records.items():
            await cvs.insert_small_variant_records(_ASSEMBLY, family, [_PROJECT], family_records)
        stored = await _count()
        await execute_clickhouse(f"OPTIMIZE TABLE {_small_table_name(_ASSEMBLY, 'entries')} FINAL")
        return {"stored": stored, "merged": await _count()}
    finally:
        for (name,) in await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(db)s AND name LIKE %(prefix)s",
            {"db": database, "prefix": f"{_ASSEMBLY}/%"},
        ):
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        cvs._ensured_variant_table_assemblies.discard(_ASSEMBLY)


@pytest.fixture(scope="module")
def counts() -> dict[str, dict[str, int]]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


def test_only_real_calls_of_other_families_count(counts) -> None:
    # CFDNA2's call and CFDNA5's genotype; not the crossed-over reads, the 0.5% call, or
    # the index family's own call. Before the rows had their own sort keys, the merge kept
    # only one of family other-1's rows, and CFDNA2's call here was lost.
    assert counts["stored"].get("7-1000-A-G") == 2
    assert counts["merged"].get("7-1000-A-G") == 2


def test_a_variant_only_crossed_over_reads_carry_is_not_listed(counts) -> None:
    assert "7-2000-C-T" not in counts["stored"]
    assert "7-2000-C-T" not in counts["merged"]
