"""The other cfDNA samples that carry a de novo candidate, counted on real ClickHouse (E2E).

The monogenic NIPT de novo view leaves a candidate out as recurrent when another
family's cfDNA sample of the same assay carries it (the R NIPT-M triage's rule). A
per-sample cfDNA callset stores every low-level Mutect2 call, so a read or two that crossed
over from another sample of the run would make a true de novo candidate "recurrent". A
sample therefore counts only with a call of at least 5 alt reads and 1% of the reads, the
quality filter's floors; a call without allele depths counts on its genotype. The index
family's own calls never count.

The rows are synthetic, in the tables of an assembly no other test uses, dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "E2ENIPTCARRIERS"
_INDEX_FAMILY = "e2e-nipt-index"


async def _exercise() -> dict[str, int]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_family_variants import count_cfdna_carriers
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
    database = settings.clickhouse_database
    entries = _small_table_name(_ASSEMBLY, "entries")
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
        for family, variant, sample, gt, ad in rows:
            await execute_clickhouse(
                f"INSERT INTO {entries} (family_guid, variantId, sign, `calls.sampleId`, `calls.gt`, `calls.ad`) "
                "VALUES (%(family)s, %(variant)s, 1, %(samples)s, %(gts)s, %(ads)s)",
                {"family": family, "variant": variant, "samples": [sample], "gts": [gt], "ads": [ad]},
            )
        return await count_cfdna_carriers(
            _ASSEMBLY,
            ["7-1000-A-G", "7-2000-C-T"],
            carrier_samples={name: name for name in ("CFDNA1", "CFDNA2", "CFDNA3", "CFDNA4", "CFDNA5")},
            exclude_family_uuid=_INDEX_FAMILY,
        )
    finally:
        for (name,) in await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(db)s AND name LIKE %(prefix)s",
            {"db": database, "prefix": f"{_ASSEMBLY}/%"},
        ):
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        cvs._ensured_variant_table_assemblies.discard(_ASSEMBLY)


@pytest.fixture(scope="module")
def counts() -> dict[str, int]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


def test_only_real_calls_of_other_families_count(counts) -> None:
    # CFDNA2's call and CFDNA5's genotype; not the crossed-over reads, the 0.5% call, or
    # the index family's own call.
    assert counts.get("7-1000-A-G") == 2


def test_a_variant_only_crossed_over_reads_carry_is_not_listed(counts) -> None:
    assert "7-2000-C-T" not in counts
