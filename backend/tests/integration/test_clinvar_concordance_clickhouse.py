"""An import's ClinVar concordance on real ClickHouse: the allele keys it stores, and the
release's alleles among them.

Records written through the import's write path (``insert_small_variant_records``) carry, on
their ``entries`` row, the allele key of each ALT, a multi-allelic record one key per ALT. A
ClinVar release stored from the synthetic ClinVar file (``fixtures/clinvar``) is read for the
callset's own keys (``clinvar_alleles_of_callset``), and the import's comparison names the
alleles that agree and each difference with its reason: a pipeline's untrimmed, multi-allelic
record matches ClinVar's anchored record of the same allele.

On an assembly of its own, whose tables the test drops. Synthetic data only. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "clinvar" / "clinvar_20261122.vcf"
_RELEASE = date(2026, 11, 22)
_SOURCE = "haplotypecaller"


def _record(variant_id: str, annotations: list[dict[str, Any]]) -> Any:
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
        source=_SOURCE,
        rsid=None,
        filters=[],
        gene_symbols=[],
        annotations=annotations,
        calls=[SmallVariantCall(sample="PROBAND", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
    )


def _clinvar(clinvar: str, status: str, vid: str, **more: Any) -> dict[str, Any]:
    return {"clinvar": clinvar, "clinvar_review_status": status, "clinvar_variation_id": vid, **more}


def test_the_callsets_alleles_are_compared_with_the_release_by_their_keys() -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services.clickhouse_variant_ids import _require_clickhouse_identifier, _small_table_name, small_allele_key
    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records
    from backend.app.services.clinvar_concordance import ClinvarConcordance
    from backend.app.services.clinvar_release_store import ensure_clinvar_table, insert_clinvar_rows
    from backend.app.services.clinvar_releases import ClinvarRelease
    from backend.app.services.clinvar_vcf import ClinvarFileReader

    assembly = f"concordance-{uuid4().hex[:8]}"
    family = str(uuid4())
    records = [
        _record("chr1-1000100-G-A", [_clinvar("Pathogenic", "criteria provided, multiple submitters, no conflicts", "900001")]),
        # ClinVar's 1 1000200 AG A, as the pipeline writes it: untrimmed, beside another ALT.
        _record(
            "chr1-1000200-AGG-AG,AGGT",
            [_clinvar("Likely_pathogenic", "reviewed by expert panel", "900002", alt_allele="AG"), {"alt_allele": "AGGT"}],
        ),
        _record("chr1-1000300-A-AT", [_clinvar("Pathogenic", "criteria provided, single submitter", "900003")]),
        _record("chr2-2000100-C-T", [_clinvar("Benign/Likely_benign", "criteria provided, single submitter", "900004")]),
        _record("chrM-3243-A-G", [{"gene": "MT-SYN1"}]),
        _record("chr5-5000000-G-A", [_clinvar("Pathogenic", "criteria provided, single submitter", "1")]),
    ]

    async def _run() -> None:
        dataset = _require_clickhouse_identifier(assembly)
        try:
            concordance = ClinvarConcordance(assembly)
            concordance.observe(records)
            await insert_small_variant_records(assembly, family, ["project-1"], records, annotation_version="vcf_info")
            lines = [
                f"##reference={assembly}" if line.startswith("##reference=") else line
                for line in _FIXTURE.read_text().splitlines()
            ]
            reader = ClinvarFileReader(assembly_name=assembly, release=_RELEASE, file_name=_FIXTURE.name)
            await ensure_clinvar_table(assembly)
            await insert_clinvar_rows(assembly, _RELEASE, list(reader.rows(lines)))

            stored = await execute_clickhouse(
                f"SELECT alt, allele_keys FROM {_small_table_name(assembly, 'entries')} "
                "WHERE family_guid = %(family)s AND pos = 1000200",
                {"family": family},
            )
            assert stored == [("AG,AGGT", [small_allele_key(assembly, "1-1000200-AG-A"), small_allele_key(assembly, "1-1000202-G-GT")])]

            kept = [
                ClinvarRelease(
                    release=_RELEASE,
                    records=8,
                    source_url=None,
                    source_version="2026-11-22",
                    loaded_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
                    loaded_by=None,
                )
            ]
            result = await concordance.compare(
                modules={"clinvar": {"version": "20261122"}}, kept=kept, family_uuid=family, source=_SOURCE
            )
            assert (result.compared, result.agree, result.differ) == (True, 3, 3)
            assert [(d.allele, d.reason) for d in result.differences] == [
                ("M-3243-A-G", "not in the annotation"),
                ("1-1000300-A-AT", "classification"),
                ("5-5000000-G-A", "not in CoGA's release"),
            ]
            # Another family's callset is not read.
            other = await concordance.compare(
                modules={"clinvar": {"version": "20261122"}}, kept=kept, family_uuid=str(uuid4()), source=_SOURCE
            )
            assert other.agree == 0 and {d.reason for d in other.differences} == {"not in CoGA's release"}
        finally:
            tables = await execute_clickhouse(
                "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
                {"database": settings.clickhouse_database, "prefix": f"{dataset}/"},
            )
            for (name,) in tables or []:
                await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}`")

    run_closing_stores(_run)
