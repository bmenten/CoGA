"""The small-variant track's salient variants read ClinVar as the browser does — real ClickHouse.

Zoomed out, the track draws only the variants ``_small_salient_condition`` keeps: a ClinVar
diamond, or HIGH or MODERATE impact and not ClinVar (likely) benign. The browser reads ClinVar
with ``clinvarClass`` (``frontend/src/lib/clinvar.ts``): a red diamond where every
classification listed is (likely) pathogenic, an orange one where a (likely) pathogenic
classification stands beside one that disagrees, (likely) benign only where every
classification listed is. Each case runs the condition on the terms the import stores for a
ClinVar value (``_status_terms``) and a transcript's impact, as ClickHouse evaluates it.

Synthetic values only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration

# (ClinVar as annotated, the transcript's impact, salient)
_CASES = [
    ("Pathogenic", "low", True),
    ("Pathogenic/Likely_pathogenic", "modifier", True),
    # VEP's cache lists the classifications of every ClinVar record at the position.
    ("uncertain_significance&likely_pathogenic", "low", True),
    ("benign&pathogenic", "modifier", True),
    ("Conflicting_classifications_of_pathogenicity", "low", False),
    ("Conflicting_classifications_of_pathogenicity", "high", True),
    ("Benign/Likely_benign", "high", False),
    ("Benign|risk_factor", "moderate", False),
    ("benign&uncertain_significance", "high", True),
    ("likely_benign&conflicting_interpretations_of_pathogenicity", "moderate", True),
    ("uncertain_significance", "moderate", True),
    ("uncertain_significance", "low", False),
    ("", "high", True),
]


def test_the_salient_condition_reads_clinvar_as_the_track_marks_it() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client, execute_clickhouse
    from backend.app.services.clickhouse_variant_queries import _small_salient_condition
    from backend.app.services.clickhouse_variant_records import _status_terms
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    params: dict = {}
    filters = SmallVariantQueryFilters(page=1, page_size=100, salient_only=True)
    condition = _small_salient_condition(filters, params=params)
    query = (
        f"SELECT {condition} FROM (SELECT CAST(%(terms)s AS Array(String)) AS clinvar_terms,"
        " %(impact)s AS impact) AS a"
    )

    async def _run() -> list[bool]:
        try:
            return [
                bool((await execute_clickhouse(
                    query, {**params, "terms": sorted(_status_terms(clinvar)), "impact": impact}
                ))[0][0])
                for clinvar, impact, _salient in _CASES
            ]
        finally:
            await close_clickhouse_client()

    kept = asyncio.run(_run())
    assert kept == [salient for _clinvar, _impact, salient in _CASES]
