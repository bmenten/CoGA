"""An import's ClinVar concordance on real ClickHouse and Postgres: the allele keys it stores,
the release's alleles among them, and the import's transaction after a comparison that failed.

Records written through the import's write path (``insert_small_variant_records``) carry, on
their ``entries`` row, the allele key of each ALT, a multi-allelic record one key per ALT. A
ClinVar release stored from the synthetic ClinVar file (``fixtures/clinvar``) is read for the
load's own keys (``clinvar_alleles_of_callset``), and the import's comparison names the alleles
that agree and each difference with its reason: a pipeline's untrimmed, multi-allelic record
matches ClinVar's anchored record of the same allele. A load of one sample's file into a callset
of several (a monogenic NIPT's plasma and father) is compared on that sample's alleles only. A
comparison whose read of Postgres fails leaves the import's transaction usable, with what the
import wrote in it.

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


def _record(
    variant_id: str, annotations: list[dict[str, Any]], *, samples: tuple[str, ...] = ("PROBAND",), source: str = _SOURCE
) -> Any:
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
        source=source,
        rsid=None,
        filters=[],
        gene_symbols=[],
        annotations=annotations,
        calls=[
            SmallVariantCall(sample=sample, gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None) for sample in samples
        ],
    )


_MODULES = {"clinvar": {"version": "20261122"}}


def _kept() -> list[Any]:
    from backend.app.services.clinvar_releases import ClinvarRelease

    return [
        ClinvarRelease(
            release=_RELEASE,
            records=8,
            source_url=None,
            source_version="2026-11-22",
            loaded_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
            loaded_by=None,
        )
    ]


async def _store_release(assembly: str) -> None:
    from backend.app.services.clinvar_release_store import ensure_clinvar_table, insert_clinvar_rows
    from backend.app.services.clinvar_vcf import ClinvarFileReader

    lines = [
        f"##reference={assembly}" if line.startswith("##reference=") else line
        for line in _FIXTURE.read_text().splitlines()
    ]
    reader = ClinvarFileReader(assembly_name=assembly, release=_RELEASE, file_name=_FIXTURE.name)
    await ensure_clinvar_table(assembly)
    await insert_clinvar_rows(assembly, _RELEASE, list(reader.rows(lines)))


async def _drop_tables(assembly: str) -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services.clickhouse_variant_ids import _require_clickhouse_identifier

    tables = await execute_clickhouse(
        "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
        {"database": settings.clickhouse_database, "prefix": f"{_require_clickhouse_identifier(assembly)}/"},
    )
    for (name,) in tables or []:
        await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}`")


def _clinvar(clinvar: str, status: str, vid: str, **more: Any) -> dict[str, Any]:
    return {"clinvar": clinvar, "clinvar_review_status": status, "clinvar_variation_id": vid, **more}


def test_the_callsets_alleles_are_compared_with_the_release_by_their_keys() -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name, small_allele_key
    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records
    from backend.app.services.clinvar_concordance import ClinvarConcordance

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
        try:
            concordance = ClinvarConcordance(assembly)
            concordance.observe(records)
            await insert_small_variant_records(assembly, family, ["project-1"], records, annotation_version="vcf_info")
            await _store_release(assembly)

            stored = await execute_clickhouse(
                f"SELECT alt, allele_keys FROM {_small_table_name(assembly, 'entries')} "
                "WHERE family_guid = %(family)s AND pos = 1000200",
                {"family": family},
            )
            assert stored == [("AG,AGGT", [small_allele_key(assembly, "1-1000200-AG-A"), small_allele_key(assembly, "1-1000202-G-GT")])]

            result = await concordance.compare(
                modules=_MODULES, kept=_kept(), family_uuid=family, source=_SOURCE, samples=["PROBAND"]
            )
            assert (result.compared, result.agree, result.differ) == (True, 3, 3)
            assert [(d.allele, d.reason) for d in result.differences] == [
                ("M-3243-A-G", "not in the annotation"),
                ("1-1000300-A-AT", "classification"),
                ("5-5000000-G-A", "not in CoGA's release"),
            ]
            # Another family's callset is not read.
            other = await concordance.compare(
                modules=_MODULES, kept=_kept(), family_uuid=str(uuid4()), source=_SOURCE, samples=["PROBAND"]
            )
            assert other.agree == 0 and {d.reason for d in other.differences} == {"not in CoGA's release"}
        finally:
            await _drop_tables(assembly)

    run_closing_stores(_run)


def test_a_load_of_one_samples_file_is_compared_on_that_samples_alleles() -> None:
    """A monogenic NIPT's callset holds the plasma's and the father's calls, each loaded from the
    sample's own file in turn: the father's comparison reads his alleles only, never the
    plasma's that CoGA classifies and his file does not hold, and the plasma's reads its own."""
    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records
    from backend.app.services.clinvar_concordance import ClinvarConcordance

    assembly = f"concordance-{uuid4().hex[:8]}"
    family = str(uuid4())
    plasma_file = [
        _record("chr1-1000100-G-A", [_clinvar("Pathogenic", "criteria provided, multiple submitters, no conflicts", "900001")], samples=("CFDNA1",), source="nipt"),
        _record("chr2-2000100-C-T", [_clinvar("Benign/Likely_benign", "criteria provided, single submitter", "900004")], samples=("CFDNA1",), source="nipt"),
        _record("chrX-3000100-G-C", [{"gene": "SYNGX"}], samples=("CFDNA1",), source="nipt"),
    ]
    father_file = [
        _record("chr1-1000100-G-A", [_clinvar("Pathogenic", "criteria provided, multiple submitters, no conflicts", "900001")], samples=("FATHER1",), source="nipt"),
        _record("chr1-1000300-A-AT", [_clinvar("Pathogenic", "criteria provided, single submitter", "900003")], samples=("FATHER1",), source="nipt"),
    ]
    # The callset once both files are loaded: one row per variant, with each sample's call.
    callset = [
        _record("chr1-1000100-G-A", [], samples=("CFDNA1", "FATHER1"), source="nipt"),
        _record("chr2-2000100-C-T", [], samples=("CFDNA1",), source="nipt"),
        _record("chrX-3000100-G-C", [], samples=("CFDNA1",), source="nipt"),
        _record("chr1-1000300-A-AT", [], samples=("FATHER1",), source="nipt"),
    ]

    async def compared(file: list[Any], sample: str) -> Any:
        concordance = ClinvarConcordance(assembly)
        concordance.observe(file)
        return await concordance.compare(
            modules=_MODULES, kept=_kept(), family_uuid=family, source="nipt", samples=[sample]
        )

    async def _run() -> None:
        try:
            await insert_small_variant_records(assembly, family, ["project-1"], callset, annotation_version="vcf_info")
            await _store_release(assembly)

            father = await compared(father_file, "FATHER1")
            assert (father.samples, father.agree, father.differ) == (["FATHER1"], 1, 1)
            assert [(d.allele, d.reason) for d in father.differences] == [("1-1000300-A-AT", "classification")]

            plasma = await compared(plasma_file, "CFDNA1")
            assert (plasma.samples, plasma.agree, plasma.differ) == (["CFDNA1"], 2, 1)
            assert [(d.allele, d.reason) for d in plasma.differences] == [("X-3000100-G-C", "not in the annotation")]
        finally:
            await _drop_tables(assembly)

    run_closing_stores(_run)


def test_a_comparison_that_fails_leaves_the_imports_transaction_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison runs on the import's session, after the import's own statements: a read of
    Postgres that fails is rolled back to its savepoint, so the import's next statement runs in
    its transaction, with what it wrote there."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import clinvar_concordance as cc

    async def refused_read(session: Any, _assembly: str) -> list[Any]:
        # A statement Postgres refuses: outside a savepoint it aborts the whole transaction.
        await session.execute(text("SELECT 1 / 0"))
        return []

    monkeypatch.setattr(cc, "recorded_clinvar_releases", refused_read)

    async def _run() -> None:
        async with get_postgres_sessionmaker()() as session:
            await session.execute(text("CREATE TEMPORARY TABLE import_step (n integer) ON COMMIT DROP"))
            await session.execute(text("INSERT INTO import_step VALUES (1)"))

            reported = await cc.ClinvarConcordance("GRCh38").report(
                session, modules=_MODULES, family_uuid=str(uuid4()), source=_SOURCE, samples=["PROBAND"]
            )

            assert reported["compared"] is False
            assert reported["reason"].startswith("Not compared: the comparison failed (")
            assert (await session.execute(text("SELECT count(*) FROM import_step"))).scalar_one() == 1
            await session.rollback()

    run_closing_stores(_run, init_schema=True)
