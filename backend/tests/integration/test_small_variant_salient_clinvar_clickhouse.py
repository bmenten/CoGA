"""The small-variant track's salient variants read ClinVar in CoGA's release as the browser does —
real ClickHouse.

Zoomed out, the track draws only the variants ``_small_salient_condition`` keeps: an allele the
release holds (likely) pathogenic (a ClinVar diamond), or a transcript of HIGH or MODERATE impact
unless the release holds every allele (likely) benign. The browser reads each classification
with ``clinvarClass`` (``frontend/src/lib/clinvar.ts``): a diamond where a (likely) pathogenic
classification is listed, (likely) benign only where every classification listed is; ClinVar's
aggregate conflict is neither. Each case stores its alleles' classifications in a release of an
assembly of its own (the terms ``clinvar_vcf`` stores, ``_status_terms``) and runs the condition
on a row with those alleles' keys and a transcript of the given impact, as ClickHouse evaluates
it; an allele the release does not hold has no ClinVar, and without a release the impact alone
counts.

Synthetic values only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration

_RELEASE = date(2026, 11, 22)

# (each ALT's classification in the release, None where it does not hold the ALT; the
# transcript's impact; salient)
_CASES: list[tuple[list[str | None], str, bool]] = [
    (["Pathogenic"], "low", True),
    (["Pathogenic/Likely_pathogenic"], "modifier", True),
    # A multi-allelic row: a (likely) pathogenic ALT beside another classification.
    (["Uncertain_significance", "Likely_pathogenic"], "low", True),
    (["Benign", "Pathogenic"], "modifier", True),
    (["Conflicting_classifications_of_pathogenicity"], "low", False),
    (["Conflicting_classifications_of_pathogenicity"], "high", True),
    (["Benign/Likely_benign"], "high", False),
    (["Benign|risk_factor"], "moderate", False),
    # Benign only where every ALT is.
    (["Benign", "Likely_benign"], "high", False),
    (["Benign", "Uncertain_significance"], "high", True),
    (["Likely_benign", None], "moderate", True),
    (["Uncertain_significance"], "moderate", True),
    (["Uncertain_significance"], "low", False),
    # Not in the release: no ClinVar, the impact alone.
    ([None], "high", True),
    ([None], "low", False),
]


def test_the_salient_condition_reads_clinvar_in_the_release_as_the_track_marks_it() -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services.clickhouse_variant_ids import _require_clickhouse_identifier
    from backend.app.services.clickhouse_variant_queries import _small_salient_condition
    from backend.app.services.clickhouse_variant_records import _status_terms
    from backend.app.services.clinvar_release_store import ensure_clinvar_table, insert_clinvar_rows
    from backend.app.services.clinvar_vcf import ClinvarRow
    from backend.app.services.family_metadata_context import FamilyMetadataContext
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    assembly = f"salient-{uuid4().hex[:8]}"
    context = FamilyMetadataContext(
        family_uuid=str(uuid4()),
        family_id="F1",
        project_ids=["project-1"],
        sample_rows=[],
        sample_uuid_to_name={},
        sample_name_to_uuid={},
        affected_sample_names=[],
        assembly_id=None,
        assembly_name=assembly,
    )
    # Each case's ALTs: allele keys of their own, the held ones stored with their classification.
    keys = [[1_000 * case + alt for alt, _classification in enumerate(alts)] for case, (alts, _i, _s) in enumerate(_CASES)]
    rows = [
        ClinvarRow(
            allele_key=key,
            variant_id=f"1_{key}_G_A",
            variation_id=key,
            allele_id=key,
            classification=classification,
            classification_terms=tuple(sorted(_status_terms(classification))),
            conflicting="",
            review_status="criteria provided, single submitter",
            conditions=(),
            genes=(),
            included_only=False,
        )
        for (alts, _impact, _salient), case_keys in zip(_CASES, keys)
        for key, classification in zip(case_keys, alts)
        if classification is not None
    ]

    async def salient(release: date | None) -> list[bool]:
        params: dict[str, Any] = {}
        filters = SmallVariantQueryFilters(page=1, page_size=100, salient_only=True, clinvar_coga_release=release)
        condition = _small_salient_condition(context, filters, params=params)
        query = (
            f"SELECT {condition} FROM (SELECT CAST(%(keys)s AS Array(UInt64)) AS allele_keys,"
            " CAST([tuple(%(impact)s)] AS Array(Tuple(impact String))) AS transcripts) AS e"
        )
        return [
            bool((await execute_clickhouse(query, {**params, "keys": case_keys, "impact": impact}))[0][0])
            for (_alts, impact, _salient), case_keys in zip(_CASES, keys)
        ]

    async def _run() -> tuple[list[bool], list[bool]]:
        dataset = _require_clickhouse_identifier(assembly)
        try:
            await ensure_clinvar_table(assembly)
            await insert_clinvar_rows(assembly, _RELEASE, rows)
            return await salient(_RELEASE), await salient(None)
        finally:
            tables = await execute_clickhouse(
                "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
                {"database": settings.clickhouse_database, "prefix": f"{dataset}/"},
            )
            for (name,) in tables or []:
                await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}`")

    with_release, without = run_closing_stores(_run)
    assert with_release == [expected for _alts, _impact, expected in _CASES]
    # Without a release no allele has ClinVar: HIGH and MODERATE impact alone.
    assert without == [impact in {"high", "moderate"} for _alts, impact, _expected in _CASES]
