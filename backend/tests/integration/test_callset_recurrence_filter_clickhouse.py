"""The callset recurrence filter on real ClickHouse: the family search leaves out a variant that
more than ``max_callset_families`` other families carry in the same callset, unless the ClinVar
P/LP override keeps it (the owner's decisions of 2026-11-28).

The families are stored on an assembly of the test's own, so no other module's families count;
its tables are dropped afterwards. The searched family's calls are all Clair3's; the other
families' calls are Clair3's or GATK HaplotypeCaller's:

- ``RECURRENT``: two other families carry it in Clair3;
- ``OTHER_CALLSET``: two other families carry it, in HaplotypeCaller only;
- ``REFERENCE_ONLY``: two other families have a Clair3 row of it, a reference call (a RefCall);
- ``TWO_PROJECTS``: one other family carries it, stored under two projects;
- ``PATHOGENIC``: two other families carry it in Clair3; CoGA's ClinVar release holds it
  pathogenic;
- ``PRIVATE``: no other family has it.

The other families sit in projects the searched family is not in: every project counts.

Synthetic values only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke``
job sets it.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration

_RELEASE = date(2026, 11, 22)
_PROBAND = "PROBAND"

RECURRENT = "1-1000-A-G"
OTHER_CALLSET = "1-2000-C-T"
REFERENCE_ONLY = "1-3000-G-A"
TWO_PROJECTS = "1-4000-T-C"
PATHOGENIC = "1-5000-A-T"
PRIVATE = "1-6000-G-C"
ALL = frozenset({RECURRENT, OTHER_CALLSET, REFERENCE_ONLY, TWO_PROJECTS, PATHOGENIC, PRIVATE})

# Each other family: its sample, the projects it is stored under, and its (variant, callset,
# genotype) rows.
_OTHER_FAMILIES: list[tuple[str, int, list[tuple[str, str, str]]]] = [
    (
        "OTHER1",
        2,
        [
            (RECURRENT, "clair3", "0/1"),
            (OTHER_CALLSET, "haplotypecaller", "0/1"),
            (REFERENCE_ONLY, "clair3", "0/0"),
            (TWO_PROJECTS, "clair3", "0/1"),
            (PATHOGENIC, "clair3", "1/1"),
        ],
    ),
    (
        "OTHER2",
        1,
        [
            (RECURRENT, "clair3", "1/1"),
            (OTHER_CALLSET, "haplotypecaller", "1/1"),
            (REFERENCE_ONLY, "clair3", "0/0"),
            (PATHOGENIC, "clair3", "0/1"),
        ],
    ),
]


def _record(variant_id: str, source: str, sample: str, gt: str) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    ad = [30, 0] if gt == "0/0" else [15, 15] if gt == "0/1" else [0, 30]
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source=source,
        rsid=None,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[SmallVariantCall(sample=sample, gt=gt, gq=50, dp=30, af=[ad[1] / 30], ad=ad, ps=None)],
        qual=50.0,
    )


def _context(assembly: str, family_uuid: str, project: str) -> Any:
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id="RECURRENCE",
        project_ids=[project],
        sample_rows=[{"sample_id": _PROBAND, "role": "proband", "sex": "female", "clinical_status": "affected"}],
        sample_uuid_to_name={"uuid-proband": _PROBAND},
        sample_name_to_uuid={_PROBAND: "uuid-proband"},
        affected_sample_names=[_PROBAND],
        assembly_id=None,
        assembly_name=assembly,
    )


def test_the_search_leaves_out_what_other_families_carry_in_the_callset() -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    import backend.app.services.clickhouse_family_variants as family_variants
    from backend.app.services.clickhouse_variant_ids import _require_clickhouse_identifier
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        get_assembly_small_variant_data_version,
        insert_small_variant_records,
    )
    from backend.app.services.clinvar_release_store import ensure_clinvar_table, insert_clinvar_rows
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters
    from backend.tests.integration._clinvar_source import release_rows

    assembly = f"recurrence-{uuid4().hex[:8]}"
    family_uuid, project = str(uuid4()), str(uuid4())
    context = _context(assembly, family_uuid, project)

    async def kept(**values: Any) -> tuple[frozenset[str], int]:
        filters = SmallVariantQueryFilters(page=1, page_size=100, **values)
        rows = await family_variants._fetch_small_variant_rows(context, filters)
        count, _capped = await family_variants._count_small_variant_rows_bounded(context, filters)
        return frozenset(record.variant_id for record in rows), count

    async def _run() -> dict[str, Any]:
        dataset = _require_clickhouse_identifier(assembly)
        try:
            await ensure_clickhouse_variant_tables(assembly)
            await insert_small_variant_records(
                assembly, family_uuid, [project], [_record(variant, "clair3", _PROBAND, "0/1") for variant in ALL]
            )
            for sample, projects, rows in _OTHER_FAMILIES:
                await insert_small_variant_records(
                    assembly,
                    str(uuid4()),
                    [str(uuid4()) for _ in range(projects)],
                    [_record(variant, source, sample, gt) for variant, source, gt in rows],
                )
            await ensure_clinvar_table(assembly)
            await insert_clinvar_rows(assembly, _RELEASE, release_rows(assembly, {PATHOGENIC: "Pathogenic"}))
            before = await get_assembly_small_variant_data_version(assembly)
            answers = {
                "none": await kept(),
                "at most 2": await kept(max_callset_families=2),
                "at most 1": await kept(max_callset_families=1, clinvar_coga_release=_RELEASE),
                "at most 0": await kept(max_callset_families=0),
                "override": await kept(
                    max_callset_families=1, clinvar_pathogenic_override=True, clinvar_coga_release=_RELEASE
                ),
                "override without a release": await kept(max_callset_families=1, clinvar_pathogenic_override=True),
                "with a rare filter": await kept(max_callset_families=1, max_gnomad_af=0.01),
            }
            # Another family's import moves the version every family's ranking reads.
            await insert_small_variant_records(assembly, str(uuid4()), [str(uuid4())], [_record(PRIVATE, "clair3", "OTHER3", "0/1")])
            answers["version moved"] = before != await get_assembly_small_variant_data_version(assembly)
            answers["private once carried"] = await kept(max_callset_families=0)
            return answers
        finally:
            tables = await execute_clickhouse(
                "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
                {"database": settings.clickhouse_database, "prefix": f"{dataset}/"},
            )
            for (name,) in tables or []:
                await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}`")

    answers = run_closing_stores(_run)
    expected = {
        "none": ALL,
        "at most 2": ALL,
        # A reference call, or a call in another callset, carries nothing.
        "at most 1": ALL - {RECURRENT, PATHOGENIC},
        # A family under two projects counts once.
        "at most 0": ALL - {RECURRENT, PATHOGENIC, TWO_PROJECTS},
        "override": ALL - {RECURRENT},
        "override without a release": ALL - {RECURRENT, PATHOGENIC},
        "with a rare filter": ALL - {RECURRENT, PATHOGENIC},
        "private once carried": ALL - {RECURRENT, PATHOGENIC, TWO_PROJECTS, PRIVATE},
    }
    assert {name: answers[name][0] for name in expected} == expected
    # The count reads the same rows.
    assert {name: answers[name][1] for name in expected} == {name: len(ids) for name, ids in expected.items()}
    assert answers["version moved"]
