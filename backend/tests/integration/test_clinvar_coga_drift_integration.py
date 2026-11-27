"""ClinVar (CoGA) in the clinical trail, on real Postgres and ClickHouse (the owner's decision
B5).

A ClinVar release is loaded through the real load job (NCBI stood in for, the synthetic file of
``fixtures/clinvar``), for an assembly of the test's own; the family's variant is written
through the import's write path. Then:

- the real review save freezes, in ``acmg_evidence_snapshot``, the variant's ClinVar in the
  current release: its classification, review status and stars, with the release;
- a newer release that gives the same classification another review status reads as
  ``noted``: listed, not counted, so the sign-out needs no acknowledgement; the signed record
  holds the frozen value, the noted entry and the release it was signed with, and the sign-out
  check finds the live report still matching it;
- a newer release that classifies the variant otherwise is drift naming ``clinvar_coga``, and
  the sign-out is refused until it is acknowledged.

The family context, the Sample QC and the sequencing QC are stubbed, and the variant list's
hydration (reviews, gene metrics, cohort) is left out. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.tests._signout_dialogs import no_finding_reviews
from backend.tests.integration._clinvar_source import (
    StandInNcbi,
    clinvar_file,
    load_clinvar_release,
    stand_in_ncbi,
)
from backend.tests.integration._datastores import (
    fresh_assembly,
    fresh_species,
    proband_context,
    run_closing_stores,
    small_variant,
    stored_signout_snapshot,
    vep_annotations,
)

pytestmark = pytest.mark.integration

# The fixture's 1 1000100 G A: Pathogenic, criteria provided, multiple submitters, no conflicts.
_VARIANT = "1-1000100-G-A"
_RECORD_ID = "\t900001\t"
_FIRST, _REVIEWED, _RECLASSIFIED = date(2026, 11, 22), date(2026, 11, 29), date(2026, 12, 6)
_CSQ_FIELDS = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|MANE_SELECT"


def _expert_panel(record: str) -> str:
    """The record, reviewed by an expert panel: the same classification, three stars."""
    if _RECORD_ID not in record:
        return record
    return record.replace("criteria_provided,_multiple_submitters,_no_conflicts", "reviewed_by_expert_panel")


def _likely_pathogenic(record: str) -> str:
    """The record, reviewed by an expert panel and classified likely pathogenic."""
    record = _expert_panel(record)
    return record.replace("CLNSIG=Pathogenic;", "CLNSIG=Likely_pathogenic;") if _RECORD_ID in record else record


@pytest.fixture()
def clinvar(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str, StandInNcbi]:
    return stand_in_ncbi(monkeypatch)


def test_a_changed_classification_is_drift_and_a_changed_review_status_is_noted(
    clinvar: tuple[Any, str, StandInNcbi], monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.schemas import AcmgClassificationPayload, AcmgCriterionSelection, SmallVariantReviewUpdate
    from backend.app.services import annotation_manifest_service as ams
    from backend.app.services import classification_drift_service as cds
    from backend.app.services import clickhouse_family_variants as family_variants
    from backend.app.services import report_signout_service as rss
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )
    from backend.app.services.sample_integrity_qc import SampleIntegrityReport
    from backend.app.services.small_variant_review_pg import upsert_small_variant_review

    jobs, assembly, ncbi = clinvar
    label = f"clinvar-coga-drift-{uuid4()}"
    project = str(uuid4())
    held: dict[str, str] = {}

    def _context() -> Any:
        return proband_context(
            held["family"], label, project, assembly_id=held["assembly"], assembly_name=assembly
        )

    async def _ctx(session, *, family_identifier, user, project_id=None, analysis=None):
        return _context()

    async def _qc(
        session, *, family_id, user, project_id=None, shared_nipt=None, analysis_types=None, genotype_source_held=None,
        analysis=None,
    ):
        return SampleIntegrityReport(overall_status="pass")

    async def _sequencing_qc(session, context, scope=None):
        return {"analyses": {}}

    async def _no_hydration(session, *, context, variants):
        return None

    for module in (rss, cds, ams):
        monkeypatch.setattr(module, "build_family_metadata_context", _ctx)
    # The test's own assembly, signed out as a validated one.
    monkeypatch.setattr(settings, "validated_assemblies", [assembly])
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)
    monkeypatch.setattr(rss, "finding_reviews_for_record", no_finding_reviews)
    monkeypatch.setattr(family_variants, "_hydrate_small_variant_outs", _no_hydration)

    user = SimpleNamespace(username="analyst", email="analyst@example.org", id=None, role="supervisor")
    classification = SmallVariantReviewUpdate(
        classification="Pathogenic - class 5",
        tags=["acmg_class_5", "report"],
        acmg=AcmgClassificationPayload(
            criteria=[
                AcmgCriterionSelection(code="PVS1", strength="very_strong", accepted=True),
                AcmgCriterionSelection(code="PS3", strength="strong", accepted=True, evidence="RNA assay"),
            ]
        ),
    )

    async def _drift(sm: Any) -> dict[str, Any]:
        async with sm() as s:
            return await cds.evaluate_classification_drift(s, family_id=label, user=user)

    async def _load(sm: Any, release: date, edit: Any) -> None:
        ncbi.put(release, clinvar_file(assembly, release, edit=edit))
        job = await load_clinvar_release(jobs, sm, assembly)
        assert (job["status"], job["release"]) == ("completed", release), job["error"]

    async def _run() -> None:
        sm = get_postgres_sessionmaker()
        async with sm() as s:
            species = await fresh_species(s, assembly)
            held["assembly"] = await fresh_assembly(s, species, assembly)
            held["family"] = (
                await s.execute(
                    text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
                )
            ).scalar_one()
            # One active load at a time, across the shared integration database.
            await s.execute(text("DELETE FROM clinvar_load_jobs WHERE status IN ('queued', 'running')"))
            await s.commit()
        try:
            await ensure_clickhouse_variant_tables(assembly)
            annotation = "|".join(
                ["A", "missense_variant", "MODERATE", "SYNG1", "ENSG00000000901", "Transcript",
                 "ENST00000000901", "YES", "NM_000901.1"]
            )
            await insert_small_variant_records(
                assembly, held["family"], [project], [small_variant(_VARIANT, vep_annotations(_CSQ_FIELDS, [annotation]))]
            )
            await _load(sm, _FIRST, lambda record: record)

            # (a) The save freezes the variant's ClinVar in the current release.
            async with sm() as s:
                await upsert_small_variant_review(
                    s, context=_context(), variant_id=_VARIANT, payload=classification, user=user
                )
            frozen = {
                "alt": "A",
                "status": "in_release",
                "release": "2026-11-22",
                "classification": "Pathogenic",
                "review_status": "criteria provided, multiple submitters, no conflicts",
                "stars": 2,
                "conditions": ["Synthetic disorder 1"],
                "variation_id": 900001,
                "included_only": False,
            }
            drift = await _drift(sm)
            assert (drift["checked"], drift["drifted_count"], drift["drifted"]) == (1, 0, [])

            # (b) The same classification, reviewed by an expert panel: noted, not counted.
            await _load(sm, _REVIEWED, _expert_panel)
            drift = await _drift(sm)
            assert (drift["checked"], drift["drifted_count"]) == (1, 0)
            [noted] = drift["drifted"]
            assert (noted["variant_id"], noted["status"], noted["changed"]) == (_VARIANT, "noted", [])
            assert noted["clinvar_coga_from"] == [frozen]
            [now] = noted["clinvar_coga_to"]
            assert (now["release"], now["review_status"], now["stars"]) == ("2026-11-29", "reviewed by expert panel", 3)

            # Signed out without an acknowledgement: the record holds the frozen value, the
            # noted entry and the release it was signed with.
            async with sm() as s:
                signed_out = await rss.sign_out_report(s, family_id=label, user=user)
            assert signed_out["drift_acknowledged"] is False
            async with sm() as s:
                signed = await stored_signout_snapshot(s, label, signed_out["version"])
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
            assert check["matches"] is True, check
            [reported] = signed["reported_variants"]
            assert reported["evidence_snapshot"]["clinvar_coga"] == [frozen]
            assert signed["drift"]["drifted_count"] == 0
            assert [entry["status"] for entry in signed["drift"]["drifted"]] == ["noted"]
            release = signed["reference_data"]["clinvar_coga"]
            assert (release["release"], release["records"]) == ("2026-11-29", 8)
            assert release["source_version"].startswith("2026-11-29 (clinvar_20261129.vcf.gz, SHA-256 ")

            # (c) Classified otherwise: drift, and the sign-out refused until acknowledged.
            await _load(sm, _RECLASSIFIED, _likely_pathogenic)
            drift = await _drift(sm)
            assert drift["drifted_count"] == 1
            [drifted] = drift["drifted"]
            assert (drifted["status"], drifted["changed"]) == ("drifted", ["clinvar_coga"])
            assert drifted["clinvar_coga_to"][0]["classification"] == "Likely_pathogenic"
            async with sm() as s:
                with pytest.raises(HTTPException) as refused:
                    await rss.sign_out_report(s, family_id=label, user=user)
                await s.rollback()
            assert refused.value.status_code == 409
            refusal: Any = refused.value.detail
            assert "1 classification(s) have evidence" in refusal["message"]
            [listed] = refusal["drifted"]
            assert (listed["variant_id"], listed["changed"]) == (_VARIANT, ["clinvar_coga"])
        finally:
            tables = await execute_clickhouse(
                "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
                {"database": settings.clickhouse_database, "prefix": f"{assembly}/"},
            )
            for (name,) in tables or []:
                await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}`")
            async with sm() as s:
                await s.execute(text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": species})
                await s.commit()

    run_closing_stores(_run, init_schema=True)
