"""The frequencies and in-silico scores a small-variant ACMG classification freezes, and their
drift, on real Postgres + ClickHouse (CLIN-7; TF-09a REQ-TRACE-002, REQ-TRACE-003).

- The variants go into ClickHouse through the import's write path
  (``insert_small_variant_records``), annotated as the import parses VEP's CSQ: a MANE Select
  missense transcript and a more severe transcript that is not. The real review save
  (``upsert_small_variant_review``) reads the variant as the drift check does and freezes, in
  the real ``acmg_evidence_snapshot`` column, the population frequencies and in-silico scores
  of its primary transcript: the values the variant list serves the ACMG dialog
  (``get_family_small_variants_page``).
- Read back from JSONB, unchanged evidence is current, and so is a snapshot stored as the
  writer stored it before CLIN-7 (the annotation version and set hash, ClinVar and the time).
- A re-import with a new annotation (another REVEL score, another gnomAD genome frequency)
  reads as drift naming each value that moved, with the value as frozen and as it is now,
  while the snapshot stored the old way stays current: its annotation set did not change.
  Sign-out is refused until the drift is acknowledged with a reason; the signed record holds
  the frozen values of the reported classification and the drift naming what moved, and the
  sign-out check, computing both again, finds the live report still matching it. Saving the
  classification again freezes the values as they are now, and the drift clears.
- The same for the consequence, impact and LOFTEE call of the primary transcript and the
  gene's gnomAD missense Z, which the save reads from the gene's profile in Postgres through
  the variant list's own gene lookup: a re-annotation that adds a consequence term and turns
  LOFTEE's call from LC to HC, and a gene profile refresh that moves the missense Z, read as
  drift naming each, while a snapshot stored before these values were frozen stays current.

The family context, the Sample QC and the QC cut-off lookup are stubbed, and the variant
list's hydration (reviews, gene metrics, cohort) is left out; its gene lookup is called
directly. The review table, the gene profiles, the drift query, the ClickHouse writes and
reads and the sign-out writer are real. Skipped unless ``RUN_INTEGRATION=1`` (see
conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_SAMPLE = "PROBAND"
# Positions no other test writes: the variant details are shared across families.
_CLASSIFIED = "12-40100101-C-T"
_OLD_SNAPSHOT = "12-40100201-G-A"
_MANE_TRANSCRIPT = "ENST00000000101"
_REASON = "REVEL rose with the new dbNSFP release; PP3 re-reviewed at the case meeting."
_OLD_FORMAT_KEYS = ("annotation_version", "annotation_set_hash", "clinvar", "captured_at")

_CSQ_HEADER = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP.'
    " Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|MANE_SELECT"
    "|SIFT|PolyPhen|REVEL|CADD_PHRED|am_class|am_pathogenicity|SpliceAI_pred_DS_AG"
    "|SpliceAI_pred_DS_AL|SpliceAI_pred_DS_DG|SpliceAI_pred_DS_DL|gnomADe_AF|gnomADg_AF|MAX_AF"
    '|gnomAD_AC|gnomAD_nhomalt|gnomAD_Hemi|TOPMed_AF|CLIN_SIG">'
)


def _csq(transcript: str, *, mane: bool, consequence: str, impact: str, revel: str, gnomad_genomes: str) -> str:
    # One transcript's CSQ entry, as VEP writes it (an empty field is a missing value).
    return "|".join(
        [
            "T", consequence, impact, "GENE7", "ENSG00000000107", "Transcript", transcript,
            "YES" if mane else "", "NM_000107.1" if mane else "",
            "deleterious(0.01)", "probably_damaging(0.981)", revel, "24.3",
            "likely_pathogenic", "0.871", "0.01", "0.02", "0.00", "0.00",
            "1.2e-05", gnomad_genomes, "0.0002", "4", "0", "", "2e-05", "uncertain_significance",
        ]
    )


def _annotations(*, revel: str = "0.412", gnomad_genomes: str = "3.1e-05") -> list[dict[str, Any]]:
    from backend.app.services.variant_annotation_parser import (
        AnnotationHeaderState,
        extract_small_variant_annotations,
        update_annotation_header_state,
    )

    state = AnnotationHeaderState()
    update_annotation_header_state(state, _CSQ_HEADER)
    entries = [
        # A more severe transcript first: the primary one is still the MANE Select one.
        _csq("ENST00000000102", mane=False, consequence="stop_gained", impact="HIGH",
             revel="0.990", gnomad_genomes=gnomad_genomes),
        _csq(_MANE_TRANSCRIPT, mane=True, consequence="missense_variant", impact="MODERATE",
             revel=revel, gnomad_genomes=gnomad_genomes),
    ]
    return extract_small_variant_annotations({"CSQ": ",".join(entries)}, state)


def _record(variant_id: str, annotations: list[dict[str, Any]]):
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos),
        ref=ref, alt=alt, source="clair3", rsid=None, filters=["PASS"], gene_symbols=[],
        annotations=annotations,
        calls=[SmallVariantCall(sample=_SAMPLE, gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
        qual=100.0,
    )


def _context(family_uuid: str, family_id: str, project: str):
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=family_id,
        project_ids=[project],
        sample_rows=[{"sample_id": _SAMPLE, "role": "proband", "sex": "female", "affected": True}],
        sample_uuid_to_name={_SAMPLE: _SAMPLE},
        sample_name_to_uuid={_SAMPLE: _SAMPLE},
        affected_sample_names=[_SAMPLE],
        assembly_id=None,
        assembly_name=_ASSEMBLY,
    )


def test_a_classification_freezes_its_frequencies_and_scores_and_drifts_when_they_move(monkeypatch) -> None:
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import (
        AcmgClassificationPayload,
        AcmgCriterionSelection,
        SmallVariantReviewUpdate,
    )
    from backend.app.services import annotation_manifest_service as ams
    from backend.app.services import classification_drift_service as cds
    from backend.app.services import clickhouse_family_variants as family_variants
    from backend.app.services import report_signout_service as rss
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_small_variants,
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )
    from backend.app.services.sample_integrity_qc import SampleIntegrityReport
    from backend.app.services.small_variant_review_pg import upsert_small_variant_review

    label = f"snv-evidence-{uuid4()}"
    project = str(uuid4())
    holder: dict[str, str] = {}

    def _ctx_now():
        return _context(holder["uuid"], label, project)

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return _ctx_now()

    async def _qc(session, *, family_id, user, project_id=None):
        return SampleIntegrityReport(overall_status="pass")

    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    async def _no_hydration(session, *, context, variants):
        return None

    # One authorised view of the family, wherever a service builds it.
    for module in (rss, cds, ams):
        monkeypatch.setattr(module, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)
    monkeypatch.setattr(family_variants, "_hydrate_small_variant_outs", _no_hydration)

    user = SimpleNamespace(username="analyst", email="analyst@example.org", id=None)
    # PS3 strong (4) + PM2 supporting (1) + PP3 supporting (1) = 6: Likely Pathogenic.
    classification = SmallVariantReviewUpdate(
        classification="Likely Pathogenic - class 4",
        tags=["acmg_class_4", "report"],
        acmg=AcmgClassificationPayload(
            criteria=[
                AcmgCriterionSelection(code="PS3", strength="strong", accepted=True, evidence="RNA assay"),
                AcmgCriterionSelection(code="PM2", strength="supporting", accepted=True, auto_suggested=True),
                AcmgCriterionSelection(code="PP3", strength="supporting", accepted=True, auto_suggested=True),
            ]
        ),
    )
    old_way = SmallVariantReviewUpdate(
        classification="VUS - class 3",
        tags=["acmg_class_3"],
        acmg=AcmgClassificationPayload(
            criteria=[AcmgCriterionSelection(code="PM2", strength="supporting", accepted=True)]
        ),
    )

    async def _frozen(session, variant_id: str) -> dict[str, Any]:
        stored = (
            await session.execute(
                text(
                    "SELECT acmg_evidence_snapshot FROM small_variant_reviews "
                    "WHERE family_id = CAST(:f AS uuid) AND variant_id = :v"
                ),
                {"f": holder["uuid"], "v": variant_id},
            )
        ).scalar_one()
        return json.loads(stored) if isinstance(stored, str) else stored

    async def _stored_signout(session, version: int) -> dict[str, Any]:
        stored = (
            await session.execute(
                text("SELECT snapshot FROM report_signouts WHERE family_identifier = :f AND version = :v"),
                {"f": label, "v": version},
            )
        ).scalar_one()
        return json.loads(stored) if isinstance(stored, str) else stored

    async def _import(*, revel: str, gnomad_genomes: str) -> None:
        # The family's small variants as an import writes them, replacing what was there:
        # the classified variant with the given annotation, the other one unchanged.
        await delete_family_small_variants(_ASSEMBLY, holder["uuid"], source="clair3")
        await insert_small_variant_records(
            _ASSEMBLY,
            holder["uuid"],
            [project],
            [
                _record(_CLASSIFIED, _annotations(revel=revel, gnomad_genomes=gnomad_genomes)),
                _record(_OLD_SNAPSHOT, _annotations()),
            ],
        )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            sm = get_postgres_sessionmaker()
            # A family whose SNV callset declared its annotation versions in its VCF header.
            async with sm() as s:
                holder["uuid"] = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                await s.execute(
                    text(
                        "INSERT INTO family_annotation_manifest (family_id, modules, source, recorded_by) "
                        "VALUES (CAST(:f AS uuid), CAST(:m AS jsonb), 'vcf_header', 'import (vcf_header)')"
                    ),
                    {
                        "f": holder["uuid"],
                        "m": json.dumps(
                            {
                                "vep": {"version": "112", "by_modality": {"snv": "112"}},
                                "dbnsfp": {"version": "5.2", "by_modality": {"snv": "5.2"}},
                                "gnomad": {"version": "4.1", "by_modality": {"snv": "4.1"}},
                            }
                        ),
                    },
                )
                await s.commit()
            await _import(revel="0.412", gnomad_genomes="3.1e-05")

            # (a) The save freezes the primary transcript's frequencies and in-silico scores,
            # the values the variant list serves the ACMG dialog.
            async with sm() as s:
                await upsert_small_variant_review(
                    s, context=_ctx_now(), variant_id=_CLASSIFIED, payload=classification, user=user
                )
            async with sm() as s:
                frozen = await _frozen(s, _CLASSIFIED)
                page = await family_variants.get_family_small_variants_page(
                    s, context=_ctx_now(), page=1, page_size=10
                )
            served = {variant.id: variant for variant in page.variants}[_CLASSIFIED]
            assert frozen["transcript_id"] == served.transcript_id == _MANE_TRANSCRIPT
            assert frozen["in_silico"] == {
                "cadd_phred": served.cadd_phred,
                "revel": served.revel,
                "spliceai_max": served.spliceai_max,
                "sift": served.sift,
                "polyphen": served.polyphen,
                "alpha_missense_class": served.alpha_missense_class,
                "alpha_missense_pathogenicity": served.alpha_missense_pathogenicity,
            }
            assert frozen["in_silico"]["revel"] == 0.412
            frequencies = frozen["frequencies"]
            assert (frequencies["gnomad_af"], frequencies["gnomad_hom_count"]) == (
                served.gnomad_af,
                served.gnomad_hom_count,
            )
            for field in ("gnomad_exomes_af", "gnomad_genomes_af", "gnomad_popmax_af", "topmed_af"):
                assert frequencies[field] == served.population_frequencies.get(field), field
            assert frequencies["gnomad_ac"] == served.annotation_extra.get("gnomad_ac") == 4
            assert frequencies["gnomad_hemi_count"] is None
            assert frozen["clinvar"] == served.clinvar == "uncertain_significance"

            # A classification whose snapshot was stored as the writer before CLIN-7 stored
            # it: the annotation version and set hash, ClinVar and the time.
            async with sm() as s:
                await upsert_small_variant_review(
                    s, context=_ctx_now(), variant_id=_OLD_SNAPSHOT, payload=old_way, user=user
                )
            async with sm() as s:
                stored_old = await _frozen(s, _OLD_SNAPSHOT)
                old_snapshot = {key: stored_old[key] for key in _OLD_FORMAT_KEYS}
                await s.execute(
                    text(
                        "UPDATE small_variant_reviews SET acmg_evidence_snapshot = CAST(:snap AS jsonb) "
                        "WHERE family_id = CAST(:f AS uuid) AND variant_id = :v"
                    ),
                    {"snap": json.dumps(old_snapshot), "f": holder["uuid"], "v": _OLD_SNAPSHOT},
                )
                await s.commit()

            # (b) Read back from JSONB, unchanged evidence is current, the old snapshot too.
            async with sm() as s:
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
            assert (drift["checked"], drift["drifted_count"], drift["drifted"]) == (2, 0, [])

            # (c) A new annotation release: REVEL and the gnomAD genome frequency move. The
            # drift names each value that moved; the old snapshot's annotation is unchanged.
            await _import(revel="0.951", gnomad_genomes="0.012")
            async with sm() as s:
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
            assert (drift["checked"], drift["drifted_count"]) == (2, 1)
            [entry] = drift["drifted"]
            assert (entry["variant_id"], entry["status"]) == (_CLASSIFIED, "drifted")
            assert entry["changed"] == ["gnomad_af", "gnomad_genomes_af", "revel", "annotations"]
            assert (entry["evidence_from"]["revel"], entry["evidence_to"]["revel"]) == (0.412, 0.951)
            assert (entry["evidence_from"]["gnomad_genomes_af"], entry["evidence_to"]["gnomad_genomes_af"]) == (
                3.1e-05,
                0.012,
            )
            # The ClinVar significance did not move, and is not named.
            assert entry["clinvar_from"] == entry["clinvar_to"] == "uncertain_significance"

            # (d) Sign-out is refused until the drift is acknowledged with a reason; the
            # signed record holds the frozen values and what moved.
            async with sm() as s:
                with pytest.raises(HTTPException) as refused:
                    await rss.sign_out_report(s, family_id=label, user=user)
                await s.rollback()
            assert refused.value.status_code == 409
            assert "1 classification(s) have evidence" in refused.value.detail
            async with sm() as s:
                v1 = await rss.sign_out_report(
                    s, family_id=label, user=user, acknowledge_drift=True, drift_acknowledgement_reason=_REASON
                )
            assert v1["version"] == 1 and v1["drift_acknowledged"] is True
            async with sm() as s:
                signed = await _stored_signout(s, 1)
                assert (await rss.get_report_signout(s, family_id=label, version=1, user=user))["verified"] is True
                # The values read back from the signed record compare equal to the drift
                # computed again: the live report still matches the version just signed.
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert check["version"] == 1 and check["matches"] is True, check
            [reported] = signed["reported_variants"]
            assert reported["variant_id"] == _CLASSIFIED
            assert reported["evidence_snapshot"]["in_silico"]["revel"] == 0.412
            assert reported["evidence_snapshot"]["frequencies"]["gnomad_genomes_af"] == 3.1e-05
            [signed_drift] = signed["drift"]["drifted"]
            assert signed_drift["changed"] == ["gnomad_af", "gnomad_genomes_af", "revel", "annotations"]
            assert signed_drift["evidence_to"]["revel"] == 0.951
            assert signed["drift_acknowledgement_reason"] == _REASON

            # (e) Re-reviewed: saving the classification again freezes the values as they are
            # now, and the drift clears.
            async with sm() as s:
                await upsert_small_variant_review(
                    s, context=_ctx_now(), variant_id=_CLASSIFIED, payload=classification, user=user
                )
            async with sm() as s:
                assert (await _frozen(s, _CLASSIFIED))["in_silico"]["revel"] == 0.951
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
            assert (drift["checked"], drift["drifted_count"]) == (2, 0)
            async with sm() as s:
                v2 = await rss.sign_out_report(s, family_id=label, user=user)
            assert v2["version"] == 2 and v2["drift_acknowledged"] is False
        finally:
            await close_postgres_engine()
            await close_clickhouse_client()

    asyncio.run(_run())


# --- The consequence, the LoF call and the gene's missense Z --------------------------------------

_LOF_CLASSIFIED = "12-40100301-C-T"
_LOF_EARLIER = "12-40100401-G-A"
_LOF_TRANSCRIPT = "ENST00000000108"
_LOF_GENE = "GENE8"
_LOF_GENE_ID = "ENSG00000000108"
_LOF_REASON = "LOFTEE now calls the stop gained high-confidence; PVS1 and PP2 re-reviewed."
_LOF_CSQ_HEADER = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP.'
    " Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|MANE_SELECT"
    '|REVEL|gnomADe_AF|gnomADg_AF|LoF|CLIN_SIG">'
)


def _lof_annotations(*, consequence: str = "stop_gained", lof: str = "LC") -> list[dict[str, Any]]:
    from backend.app.services.variant_annotation_parser import (
        AnnotationHeaderState,
        extract_small_variant_annotations,
        update_annotation_header_state,
    )

    state = AnnotationHeaderState()
    update_annotation_header_state(state, _LOF_CSQ_HEADER)

    def _entry(transcript: str, *, mane: bool, consequence: str, lof: str) -> str:
        return "|".join(
            [
                "T", consequence, "HIGH", _LOF_GENE, _LOF_GENE_ID, "Transcript", transcript,
                "YES" if mane else "", "NM_000108.1" if mane else "",
                "", "1.2e-05", "3.1e-05", lof, "uncertain_significance",
            ]
        )

    entries = [
        # Another stop-gained transcript, which LOFTEE calls high-confidence: the primary
        # transcript is the MANE Select one, and so is the call frozen.
        _entry("ENST00000000109", mane=False, consequence="stop_gained", lof="HC"),
        _entry(_LOF_TRANSCRIPT, mane=True, consequence=consequence, lof=lof),
    ]
    return extract_small_variant_annotations({"CSQ": ",".join(entries)}, state)


def test_a_classification_freezes_its_consequence_lof_call_and_missense_z_and_drifts_when_they_move(
    monkeypatch,
) -> None:
    """The consequence, impact and LoF call of the primary transcript and the gene's missense
    Z: the real save freezes the values the variant list serves the ACMG dialog, the missense
    Z from the gene's profile in Postgres; a re-annotation and a gene profile refresh read as
    drift naming each, while a snapshot frozen before these values were stays current;
    sign-out, the signed record and its check; saving again clears the drift."""
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import (
        AcmgClassificationPayload,
        AcmgCriterionSelection,
        SmallVariantReviewUpdate,
    )
    from backend.app.services import annotation_manifest_service as ams
    from backend.app.services import classification_drift_service as cds
    from backend.app.services import clickhouse_family_variants as family_variants
    from backend.app.services import report_signout_service as rss
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_small_variants,
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )
    from backend.app.services.sample_integrity_qc import SampleIntegrityReport
    from backend.app.services.small_variant_review_pg import upsert_small_variant_review

    tag = uuid4().hex[:12]
    label = f"snv-consequence-{tag}"
    project = str(uuid4())
    holder: dict[str, str] = {}

    def _ctx_now():
        return _context(holder["uuid"], label, project)

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return _ctx_now()

    async def _qc(session, *, family_id, user, project_id=None):
        return SampleIntegrityReport(overall_status="pass")

    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    async def _no_hydration(session, *, context, variants):
        return None

    for module in (rss, cds, ams):
        monkeypatch.setattr(module, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)
    monkeypatch.setattr(family_variants, "_hydrate_small_variant_outs", _no_hydration)

    user = SimpleNamespace(username="analyst", email="analyst@example.org", id=None)
    # PVS1 strong (4) + PM2 supporting (1) + PP4 supporting (1) = 6: Likely Pathogenic.
    classification = SmallVariantReviewUpdate(
        classification="Likely Pathogenic - class 4",
        tags=["acmg_class_4", "report"],
        acmg=AcmgClassificationPayload(
            criteria=[
                AcmgCriterionSelection(code="PVS1", strength="strong", accepted=True, auto_suggested=True),
                AcmgCriterionSelection(code="PM2", strength="supporting", accepted=True, auto_suggested=True),
                AcmgCriterionSelection(code="PP4", strength="supporting", accepted=True, evidence="phenotype"),
            ]
        ),
    )
    earlier = SmallVariantReviewUpdate(
        classification="VUS - class 3",
        tags=["acmg_class_3"],
        acmg=AcmgClassificationPayload(
            criteria=[AcmgCriterionSelection(code="PM2", strength="supporting", accepted=True)]
        ),
    )

    async def _frozen(session, variant_id: str) -> dict[str, Any]:
        stored = (
            await session.execute(
                text(
                    "SELECT acmg_evidence_snapshot FROM small_variant_reviews "
                    "WHERE family_id = CAST(:f AS uuid) AND variant_id = :v"
                ),
                {"f": holder["uuid"], "v": variant_id},
            )
        ).scalar_one()
        return json.loads(stored) if isinstance(stored, str) else stored

    async def _import(*, consequence: str, lof: str) -> None:
        await delete_family_small_variants(_ASSEMBLY, holder["uuid"], source="clair3")
        await insert_small_variant_records(
            _ASSEMBLY,
            holder["uuid"],
            [project],
            [
                _record(_LOF_CLASSIFIED, _lof_annotations(consequence=consequence, lof=lof)),
                _record(_LOF_EARLIER, _lof_annotations()),
            ],
        )

    async def _set_missense_z(session, value: float) -> None:
        # A gene profile refresh, as the gene reference job writes it: the newest row is read.
        await session.execute(
            text(
                "UPDATE gene_info SET extra = CAST(:e AS jsonb), updated_at = now() "
                "WHERE assembly_id = CAST(:a AS uuid) AND hgnc_symbol = :s"
            ),
            {"e": json.dumps({"mis_z": value}), "a": holder["assembly"], "s": _LOF_GENE},
        )
        await session.commit()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                holder["uuid"] = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                await s.execute(
                    text(
                        "INSERT INTO family_annotation_manifest (family_id, modules, source, recorded_by) "
                        "VALUES (CAST(:f AS uuid), CAST(:m AS jsonb), 'vcf_header', 'import (vcf_header)')"
                    ),
                    {
                        "f": holder["uuid"],
                        "m": json.dumps({"vep": {"version": "112", "by_modality": {"snv": "112"}}}),
                    },
                )
                # The gene's profile, with the gnomAD missense Z the PP2 suggestion reads.
                species = (
                    await s.execute(
                        text(
                            "INSERT INTO species (name, common_name, tax_id) "
                            "VALUES (:n, 'test', :t) RETURNING id::text"
                        ),
                        {"n": f"snv-consequence {tag}", "t": uuid4().int % 2_000_000_000},
                    )
                ).scalar_one()
                holder["assembly"] = (
                    await s.execute(
                        text(
                            "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                            "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
                        ),
                        {"s": species, "a": f"SNVCONSEQUENCE{tag}"},
                    )
                ).scalar_one()
                await s.execute(
                    text(
                        "INSERT INTO gene_info (assembly_id, hgnc_symbol, gene_id, extra) "
                        "VALUES (CAST(:a AS uuid), :s, :g, CAST(:e AS jsonb))"
                    ),
                    {
                        "a": holder["assembly"],
                        "s": _LOF_GENE,
                        "g": _LOF_GENE_ID,
                        "e": json.dumps({"mis_z": 2.95}),
                    },
                )
                await s.commit()
            await _import(consequence="stop_gained", lof="LC")

            # (a) The save freezes the primary transcript's consequence, impact and LoF call,
            # as the variant list serves them, and the gene's missense Z as the list's gene
            # lookup reads it from the gene's profile.
            async with sm() as s:
                await upsert_small_variant_review(
                    s, context=_ctx_now(), variant_id=_LOF_CLASSIFIED, payload=classification, user=user
                )
            async with sm() as s:
                frozen = await _frozen(s, _LOF_CLASSIFIED)
                page = await family_variants.get_family_small_variants_page(
                    s, context=_ctx_now(), page=1, page_size=10
                )
                served = {variant.id: variant for variant in page.variants}[_LOF_CLASSIFIED]
                metrics = await family_variants._fetch_gene_constraint_metric_map(s, [served])
            assert frozen["transcript_id"] == served.transcript_id == _LOF_TRANSCRIPT
            assert frozen["consequence"] == {"effect": served.effect, "impact": served.impact, "lof": served.lof}
            assert frozen["consequence"] == {"effect": "stop_gained", "impact": "HIGH", "lof": "LC"}
            assert frozen["gene_constraint"] == {"gene_missense_z": metrics[served.id]["gene_missense_z"]}
            assert frozen["gene_constraint"] == {"gene_missense_z": 2.95}

            # A classification whose snapshot was stored before the consequence and the
            # missense Z were frozen: the transcript, frequencies and in-silico scores.
            async with sm() as s:
                await upsert_small_variant_review(
                    s, context=_ctx_now(), variant_id=_LOF_EARLIER, payload=earlier, user=user
                )
            async with sm() as s:
                stored_earlier = await _frozen(s, _LOF_EARLIER)
                del stored_earlier["consequence"], stored_earlier["gene_constraint"]
                await s.execute(
                    text(
                        "UPDATE small_variant_reviews SET acmg_evidence_snapshot = CAST(:snap AS jsonb) "
                        "WHERE family_id = CAST(:f AS uuid) AND variant_id = :v"
                    ),
                    {"snap": json.dumps(stored_earlier), "f": holder["uuid"], "v": _LOF_EARLIER},
                )
                await s.commit()

            # (b) Read back from JSONB, unchanged evidence is current, the earlier snapshot too.
            async with sm() as s:
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
            assert (drift["checked"], drift["drifted_count"], drift["drifted"]) == (2, 0, [])

            # (c) A new VEP and LOFTEE release: the MANE Select transcript's consequence gains
            # a term and LOFTEE calls it high-confidence; the gene profiles are refreshed and
            # the gene's missense Z moves. The drift names each; the earlier snapshot holds
            # none of them, and its own annotation did not change: it stays current.
            await _import(consequence="stop_gained&splice_region_variant", lof="HC")
            async with sm() as s:
                await _set_missense_z(s, 3.52)
            async with sm() as s:
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
            assert (drift["checked"], drift["drifted_count"]) == (2, 1)
            [entry] = drift["drifted"]
            assert (entry["variant_id"], entry["status"]) == (_LOF_CLASSIFIED, "drifted")
            assert entry["changed"] == ["effect", "lof", "gene_missense_z", "annotations"]
            assert (entry["evidence_from"]["effect"], entry["evidence_to"]["effect"]) == (
                "stop_gained",
                "stop_gained&splice_region_variant",
            )
            assert (entry["evidence_from"]["lof"], entry["evidence_to"]["lof"]) == ("LC", "HC")
            assert (entry["evidence_from"]["gene_missense_z"], entry["evidence_to"]["gene_missense_z"]) == (
                2.95,
                3.52,
            )

            # (d) Sign-out is refused until the drift is acknowledged; the signed record holds
            # the frozen values and what moved, and the sign-out check still matches it.
            async with sm() as s:
                with pytest.raises(HTTPException) as refused:
                    await rss.sign_out_report(s, family_id=label, user=user)
                await s.rollback()
            assert refused.value.status_code == 409
            async with sm() as s:
                v1 = await rss.sign_out_report(
                    s, family_id=label, user=user, acknowledge_drift=True, drift_acknowledgement_reason=_LOF_REASON
                )
            assert v1["version"] == 1 and v1["drift_acknowledged"] is True
            async with sm() as s:
                signed = (
                    await s.execute(
                        text("SELECT snapshot FROM report_signouts WHERE family_identifier = :f AND version = 1"),
                        {"f": label},
                    )
                ).scalar_one()
                signed = json.loads(signed) if isinstance(signed, str) else signed
                assert (await rss.get_report_signout(s, family_id=label, version=1, user=user))["verified"] is True
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert check["version"] == 1 and check["matches"] is True, check
            [reported] = signed["reported_variants"]
            assert reported["variant_id"] == _LOF_CLASSIFIED
            assert reported["evidence_snapshot"]["consequence"] == {
                "effect": "stop_gained",
                "impact": "HIGH",
                "lof": "LC",
            }
            assert reported["evidence_snapshot"]["gene_constraint"] == {"gene_missense_z": 2.95}
            [signed_drift] = signed["drift"]["drifted"]
            assert signed_drift["changed"] == ["effect", "lof", "gene_missense_z", "annotations"]
            assert signed_drift["evidence_to"]["gene_missense_z"] == 3.52

            # (e) Re-reviewed: saving again freezes the values as they are now; the drift clears.
            async with sm() as s:
                await upsert_small_variant_review(
                    s, context=_ctx_now(), variant_id=_LOF_CLASSIFIED, payload=classification, user=user
                )
            async with sm() as s:
                refrozen = await _frozen(s, _LOF_CLASSIFIED)
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
            assert refrozen["consequence"]["lof"] == "HC"
            assert refrozen["gene_constraint"] == {"gene_missense_z": 3.52}
            assert (drift["checked"], drift["drifted_count"]) == (2, 0)
        finally:
            await close_postgres_engine()
            await close_clickhouse_client()

    asyncio.run(_run())


def test_the_snapshot_survives_the_jsonb_round_trip_of_its_numbers() -> None:
    # The JSONB column keeps a number's digits: what the drift check reads back must compare
    # equal to what it froze, for every value the snapshot holds.
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import classification_drift_service as cds
    from backend.app.services.small_variant_review_acmg import build_evidence_snapshot

    record = SimpleNamespace(annotation_version="v1", annotation_set_hash="1", annotations=_annotations())
    snapshot = build_evidence_snapshot(record, datetime(2026, 10, 14, tzinfo=timezone.utc), gene_missense_z=3.0)
    # A few values whose text forms differ between Python and Postgres.
    snapshot["frequencies"].update(gnomad_af=1e-05, gnomad_popmax_af=0.1 + 0.2, topmed_af=0.0)
    snapshot["in_silico"].update(cadd_phred=25.0, revel=0.30000000000000004)
    snapshot["gene_constraint"].update(gene_missense_z=3.0900000000000003)

    async def _run() -> dict[str, Any]:
        try:
            await init_postgres_schema()
            async with get_postgres_sessionmaker()() as s:
                stored = (
                    await s.execute(text("SELECT CAST(:snap AS jsonb)"), {"snap": json.dumps(snapshot)})
                ).scalar_one()
            return json.loads(stored) if isinstance(stored, str) else stored
        finally:
            await close_postgres_engine()

    read_back = asyncio.run(_run())
    diff = cds._diff(read_back, snapshot)
    assert (diff["status"], diff["changed"]) == ("current", [])
