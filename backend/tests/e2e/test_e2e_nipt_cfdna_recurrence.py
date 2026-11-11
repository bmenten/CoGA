"""How an allele recurs in the other cfDNA samples of an assay, counted on real ClickHouse (E2E).

The monogenic NIPT list shows, for each variant, in how many other families' cfDNA samples
of the same assay its allele is called, and how: at an allele fraction of 0.25 or more (that
mother carries it), lower where that family's father carries it (the fetus's paternal
allele), or lower where no parent explains it (the mark of a recurrent artefact). The de
novo view leaves a candidate out as recurrent when any other sample carries it (the R
NIPT-M triage's rule).

A per-sample cfDNA callset stores every low-level Mutect2 call, so a read or two that
crossed over from another sample of the run would make a true de novo candidate "recurrent".
A sample therefore counts only with a call of at least 5 alt reads and 1% of the reads, the
quality filter's floors; a call without allele depths counts on its genotype. The father
explains a call only with a call of his own of at least 20 reads, 5 alt reads, 20% of the
reads and a quality of 20: a low-level or low-quality paternal call does not. The index
family's own calls never count, and a family stored under two projects counts once.

The rows are synthetic, written by the importer's own writer as a monogenic NIPT import
stores them: one row per variant and family in the ``nipt`` callset, the plasma's and the
father's calls in it, and the family's per-sample summary refreshed after each write, as an
import refreshes it (the other samples of the assay are counted from it). ``SNV_INDEL/entries`` is a CollapsingMergeTree, and when ClickHouse
merges its parts in the background it keeps one row per sort key, so rows that shared one
key would lose one of them in a merge. The counts are read as stored and again after a
forced part merge (``OPTIMIZE … FINAL``), and must be the same. The tables are those of an
assembly no other test uses, dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "E2ENIPTRECURRENCE"
_PROJECT = "e2e-nipt-project"
_OTHER_PROJECT = "e2e-nipt-other-project"
_INDEX_FAMILY = "e2e-nipt-index"
_SITE = "7-1000-A-G"
_CROSSED_OVER_ONLY = "7-2000-C-T"
# The cfDNA samples of the assay; CFDNA10 has no data on the assembly.
_CFDNA = [f"CFDNA{index}" for index in range(1, 11)]

Call = tuple[str, str, list[int], dict[str, float]]

# One row per family and variant: (family, variant, [(sample, gt, ad, metrics), ...]).
_ROWS: list[tuple[str, str, list[Call]]] = [
    # A low fraction (10%) the father carries (50% of 100 reads): his allele, in the fetus.
    ("e2e-nipt-other-1", _SITE, [("CFDNA2", "0/1", [900, 100], {}), ("FATHER2", "0/1", [50, 50], {"TLOD": 80.0})]),
    # Crossed-over reads: 2 alt reads. Not a carrier.
    ("e2e-nipt-other-2", _SITE, [("CFDNA3", "0/1", [998, 2], {})]),
    # 10 alt reads, but 0.5% of the reads. Not a carrier.
    ("e2e-nipt-other-3", _SITE, [("CFDNA4", "0/1", [1990, 10], {})]),
    # No allele depths: a carrier on its genotype, in none of the three groups.
    ("e2e-nipt-other-4", _SITE, [("CFDNA5", "0/1", [], {})]),
    # 2%, no paternal call: unexplained. Stored under two projects, it counts once.
    ("e2e-nipt-other-5", _SITE, [("CFDNA6", "0/1", [980, 20], {})]),
    # 1.5%, and the father has only a low-level call (1%): unexplained.
    ("e2e-nipt-other-6", _SITE, [("CFDNA7", "0/1", [985, 15], {}), ("FATHER7", "0/1", [990, 10], {"TLOD": 30.0})]),
    # 40%: that mother carries it.
    ("e2e-nipt-other-7", _SITE, [("CFDNA8", "0/1", [600, 400], {})]),
    # 3%, and the father's het call has a quality below 20: unexplained.
    ("e2e-nipt-other-8", _SITE, [("CFDNA9", "0/1", [970, 30], {}), ("FATHER9", "0/1", [50, 50], {"TLOD": 5.0})]),
    # The index family's own call.
    (_INDEX_FAMILY, _SITE, [("CFDNA1", "0/1", [980, 20], {})]),
    # Another variant, carried only by crossed-over reads.
    ("e2e-nipt-other-1", _CROSSED_OVER_ONLY, [("CFDNA2", "0/1", [997, 3], {})]),
]


def _record(variant_id: str, calls: list[Call]):
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
        calls=[
            SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=ad, ps=None, metrics=metrics)
            for sample, gt, ad, metrics in calls
        ],
    )


async def _count() -> dict[str, Any]:
    from backend.app.services.clickhouse_family_variants import count_cfdna_recurrence, count_other_cfdna_samples
    from backend.app.services.nipt_analysis import NiptQualityThresholds
    from backend.app.services.nipt_service import recurrence_rules

    return {
        "recurrence": await count_cfdna_recurrence(
            _ASSEMBLY,
            [_SITE, _CROSSED_OVER_ONLY],
            carrier_samples=_CFDNA,
            exclude_family_uuid=_INDEX_FAMILY,
            rules=recurrence_rules(NiptQualityThresholds()),
        ),
        "other_samples": await count_other_cfdna_samples(
            _ASSEMBLY, carrier_samples=_CFDNA, exclude_family_uuid=_INDEX_FAMILY
        ),
    }


async def _exercise() -> dict[str, dict[str, Any]]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
    database = settings.clickhouse_database
    try:
        records: dict[str, list] = {}
        for family, variant, calls in _ROWS:
            records.setdefault(family, []).append(_record(variant, calls))
        # One write per family, as each family's import writes its callset.
        for family, family_records in records.items():
            projects = [_PROJECT, _OTHER_PROJECT] if family == "e2e-nipt-other-5" else [_PROJECT]
            await cvs.insert_small_variant_records(_ASSEMBLY, family, projects, family_records)
            # As an import ends: the per-sample summary the other samples are counted from.
            await cvs.refresh_family_small_variant_summaries(_ASSEMBLY, family)
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
def counts() -> dict[str, dict[str, Any]]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


@pytest.mark.parametrize("state", ["stored", "merged"])
def test_only_real_calls_of_other_families_count(counts, state: str) -> None:
    # CFDNA2, 5, 6, 7, 8 and 9; not the crossed-over reads (CFDNA3), the 0.5% call (CFDNA4)
    # or the index family's own call. Each row has its own sort key, so the merge keeps them.
    assert counts[state]["recurrence"][_SITE].carriers == 6


@pytest.mark.parametrize("state", ["stored", "merged"])
def test_each_call_is_read_as_maternal_paternal_or_unexplained(counts, state: str) -> None:
    recurrence = counts[state]["recurrence"][_SITE]
    # CFDNA8 at 40%; CFDNA2 beside its father's het call; CFDNA6, 7 and 9 (2%, 1.5%, 3%)
    # with no father's call that explains them, at a median of 2%. CFDNA5 has no allele depths.
    assert (recurrence.maternal, recurrence.paternal_low_vaf, recurrence.unexplained_low_vaf) == (1, 1, 3)
    assert recurrence.unexplained_median_vaf == pytest.approx(0.02)


@pytest.mark.parametrize("state", ["stored", "merged"])
def test_a_variant_only_crossed_over_reads_carry_is_not_listed(counts, state: str) -> None:
    assert _CROSSED_OVER_ONLY not in counts[state]["recurrence"]


@pytest.mark.parametrize("state", ["stored", "merged"])
def test_the_other_samples_are_those_of_the_assay_with_a_call_on_the_assembly(counts, state: str) -> None:
    # CFDNA2 to CFDNA9: not the index family's CFDNA1, nor CFDNA10, which has no data.
    assert counts[state]["other_samples"] == 8
