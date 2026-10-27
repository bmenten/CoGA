"""SV/CNV classification evidence and its sign-out drift gate, on real Postgres + ClickHouse
(TF-09a REQ-TRACE-014).

- The evidence is read from ClickHouse exactly as the SV page reads the SV, and an SV the
  family's data does not hold (another family's, one only in samples the view does not
  show, one on an assembly without SV storage) reads as absent. An uploaded Sniffles call
  of the same deletion, under the same id, is a row of its own: each caller's row is read
  with its own evidence, the named caller's alone when one is named, none for a caller
  that does not call it (TRACE-11).
- End to end, through the real writers and readers: an SV imported into ClickHouse is
  classified through the real review save, which freezes its evidence and the SV
  callset's versions (from the real manifest row) into the real
  ``cnv_evidence_snapshot`` column. A sign-out stored before this existed (a snapshot
  without the new section, as the old writer stored it) keeps verifying and is compared
  without the section it predates. With the evidence unchanged, sign-out needs no
  acknowledgement. When a re-import changes the genes the CNV overlaps, sign-out is
  refused (nothing written or audited) until the drift is acknowledged with a reason,
  which is frozen with the drift and recorded in the audit event. Saving the scoring
  again refreezes the evidence and clears the drift. A reported SV whose scoring froze no
  evidence gates too. The whole signed history is one verified chain.
- Two callers of one SV (TRACE-11), in a family whose id gives the upload's row the lower
  storage key (the row the save froze, and the drift check read, before): the real save
  freezes the row of the caller it names, refuses one that names no caller (422) or a
  caller that does not call the SV (404) and writes nothing then, and takes the one row of
  an SV one caller calls; the drift check compares evidence frozen from NeedlR's row with
  NeedlR's row, so it is current while that row is, drifted when its genes move, and no
  longer in the data when NeedlR's call is gone, whatever the upload's row holds.

The family context, the Sample QC and the QC cut-off lookup are stubbed; the review
table, the manifest, the drift query, the ClickHouse reads and the sign-out writer are
real. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
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
_SV_ID = "needlr_DEL_18_55000000"
_NEEDLR_ONLY = "needlr_DEL_18_60000000"
_REASON = "TXNL1 left the gene list after the re-annotation; class 5 rests on TCF4 alone."


def _context(family_uuid: str, family_id: str, project: str, samples=("PROBAND",), assembly=_ASSEMBLY):
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=family_id,
        project_ids=[project],
        sample_rows=[{"sample_id": sample} for sample in samples],
        sample_uuid_to_name={sample: sample for sample in samples},
        sample_name_to_uuid={sample: sample for sample in samples},
        affected_sample_names=list(samples),
        assembly_id=None,
        assembly_name=assembly,
    )


def _sv(genes: list[str], *, variant_id: str = _SV_ID, sample: str = "PROBAND"):
    from backend.app.services.clickhouse_variant_records import (
        StructuralVariantCall,
        StructuralVariantRecord,
    )

    info = {"SVTYPE": "DEL", "Genes": ",".join(genes), "pLI": "0.99", "Inheritance": "de_novo"}
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="18",
        start=55_000_000,
        end=55_400_000,
        sv_type="DEL",
        source="needlr",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-400_000,
        filters=["PASS"],
        gene_symbols=list(genes),
        annotations=[{"source": "needlr", "ref": "N", "alt": "<DEL>", "info": info}],
        calls=[StructuralVariantCall(sample=sample, gt="0/1", qual=60.0, read_support=12, filter="PASS")],
    )


def _sniffles_upload(*, variant_id: str = _SV_ID, sample: str = "PROBAND"):
    """An uploaded Sniffles call of the same deletion: its genes from the gene loci, and
    neither a pLI nor an inheritance."""
    from backend.app.services.clickhouse_variant_records import (
        StructuralVariantCall,
        StructuralVariantRecord,
    )

    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="18",
        start=55_000_000,
        end=55_400_000,
        sv_type="DEL",
        source="sniffles",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-400_000,
        filters=["PASS"],
        gene_symbols=["TCF4"],
        annotations=[{"source": "sniffles", "ref": "N", "alt": "<DEL>", "info": {"SVTYPE": "DEL", "SUPPORT": "14"}}],
        calls=[StructuralVariantCall(sample=sample, gt="0/1", qual=55.0, read_support=14, filter="PASS")],
    )


def test_the_evidence_is_read_as_the_sv_page_reads_the_sv() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.services.clickhouse_family_variants import _fetch_structural_variant_rows
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_structural_variant_records,
    )
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters
    from backend.app.services.structural_variant_evidence import (
        fetch_structural_variant_records,
        structural_evidence,
    )

    family_uuid, project = str(uuid4()), str(uuid4())
    ctx = _context(family_uuid, "FAM-SV-EVIDENCE", project)

    async def _run() -> None:
        try:
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            await insert_structural_variant_records(
                _ASSEMBLY,
                family_uuid,
                [project],
                [_sv(["TCF4", "TXNL1"]), _sv(["DCC"], variant_id=_NEEDLR_ONLY), _sniffles_upload()],
            )
            [fetched] = await fetch_structural_variant_records(ctx, _SV_ID, source="needlr")
            page = {
                (record.variant_id, record.source): record
                for record in await _fetch_structural_variant_rows(
                    ctx, StructuralVariantQueryFilters(page=1, page_size=10)
                )
            }
            # What is frozen is what the page gives the classification dialog, row by row.
            assert structural_evidence(fetched) == structural_evidence(page[(_SV_ID, "needlr")])
            evidence = structural_evidence(fetched)
            assert (evidence["gene_symbols"], evidence["gene_count"]) == (["TCF4", "TXNL1"], 2)
            assert (evidence["pli"], evidence["inheritance"], evidence["sv_type"]) == (0.99, "de_novo", "DEL")
            assert (evidence["chrom"], evidence["start"], evidence["end"]) == ("18", 55_000_000, 55_400_000)

            # The upload's call of the deletion, under the same id, is a row of its own,
            # with the upload's evidence (TRACE-11).
            [upload] = await fetch_structural_variant_records(ctx, _SV_ID, source="sniffles")
            assert structural_evidence(upload) == structural_evidence(page[(_SV_ID, "sniffles")])
            upload_evidence = structural_evidence(upload)
            assert (
                upload_evidence["source"],
                upload_evidence["gene_symbols"],
                upload_evidence["pli"],
                upload_evidence["inheritance"],
            ) == ("sniffles", ["TCF4"], None, None)
            # Named by no caller: each caller's row, in storage-key order; a caller that does
            # not call it: none; an SV one caller calls: that caller's row.
            both = await fetch_structural_variant_records(ctx, _SV_ID)
            assert sorted(record.source for record in both) == ["needlr", "sniffles"]
            assert [record.variant_key for record in both] == sorted(record.variant_key for record in both)
            assert await fetch_structural_variant_records(ctx, _SV_ID, source="spectre") == []
            assert [record.source for record in await fetch_structural_variant_records(ctx, _NEEDLR_ONLY)] == [
                "needlr"
            ]

            # Absent from the family's data.
            assert await fetch_structural_variant_records(ctx, "no-such-sv") == []
            other_family = _context(str(uuid4()), "FAM-OTHER", project)
            assert await fetch_structural_variant_records(other_family, _SV_ID) == []
            hidden = _context(family_uuid, "FAM-SV-EVIDENCE", project, samples=("SOMEONE_ELSE",))
            assert await fetch_structural_variant_records(hidden, _SV_ID) == []
            no_storage = _context(family_uuid, "FAM-SV-EVIDENCE", project, assembly=f"nosv{uuid4().hex[:8]}")
            assert await fetch_structural_variant_records(no_storage, _SV_ID) == []
        finally:
            await close_clickhouse_client()

    asyncio.run(_run())


def test_sign_out_gates_on_the_drift_of_a_reported_cnvs_evidence(monkeypatch) -> None:
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import (
        CnvAcmgClassificationPayload,
        CnvAcmgCriterion,
        StructuralVariantReviewUpdate,
    )
    from backend.app.services import annotation_manifest_service as ams
    from backend.app.services import classification_drift_service as cds
    from backend.app.services import report_signout_service as rss
    from backend.app.services import hash_chain
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_structural_variant_records,
        replace_family_structural_variants,
    )
    from backend.app.services.sample_integrity_qc import SampleIntegrityReport
    from backend.app.services.structural_variant_review_pg import upsert_structural_variant_review

    label = f"sv-evidence-{uuid4()}"
    project = str(uuid4())
    holder: dict = {}

    def _ctx_now():
        return _context(holder["uuid"], label, project)

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return _ctx_now()

    async def _qc(session, *, family_id, user, project_id=None, shared_nipt=None):
        return SampleIntegrityReport(overall_status="pass")

    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    # One authorised view of the family, wherever a service builds it.
    for module in (rss, cds, ams):
        monkeypatch.setattr(module, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)

    user = SimpleNamespace(username="signer", email="signer@example.org", id=None)
    scoring = CnvAcmgClassificationPayload(
        kind="loss", criteria=[CnvAcmgCriterion(code="2A", points=1.0, accepted=True)]
    )
    signed_at = datetime(2026, 9, 1, 8, 30, 0, 250000, tzinfo=timezone.utc)

    counts = {
        "report_signouts": text("SELECT count(*) FROM report_signouts WHERE family_identifier = :f"),
        "clinical_audit_events": text(
            "SELECT count(*) FROM clinical_audit_events WHERE family_identifier = :f"
        ),
    }

    async def _count(session, table: str) -> int:
        return (await session.execute(counts[table], {"f": label})).scalar_one()

    async def _stored_snapshot(session, version: int) -> dict:
        stored = (
            await session.execute(
                text("SELECT snapshot FROM report_signouts WHERE family_identifier = :f AND version = :v"),
                {"f": label, "v": version},
            )
        ).scalar_one()
        return json.loads(stored) if isinstance(stored, str) else stored

    async def _run() -> None:
        try:
            await init_postgres_schema()
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            sm = get_postgres_sessionmaker()

            # A family whose SV callset declared its annotation versions, with one
            # sign-out made before SV evidence was frozen (the old writer's snapshot).
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
                                "gencode": {"version": "49", "by_modality": {"snv": "49", "sv": "45"}},
                                "needlr": {"version": "0.4.2", "by_modality": {"sv": "0.4.2"}},
                                "clinvar": {"version": "2026-05", "by_modality": {"snv": "2026-05"}},
                            }
                        ),
                    },
                )
                old = {
                    "family_id": label,
                    "assembly": _ASSEMBLY,
                    "modules": [],
                    "reference_modules": ["assembly", "gene_loci", "monarch", "hpo"],
                    "software": {"version": "1.5.0", "git_sha": "0633db8"},
                    "drift": {"checked": 0, "drifted_count": 0, "drifted": []},
                    "sample_qc": {"overall_status": "pass"},
                    "sequencing_qc": {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}},
                    "import_incomplete": None,
                    "reported_variants": [],
                    "reported_structural_variants": [
                        {
                            "variant_id": _SV_ID,
                            "variant_key": None,
                            "classification": None,
                            "cnv_class": "cnv_class_5",
                            "cnv_point_total": 1.0,
                            "cnv_acmg": None,
                            "tags": ["report"],
                            "note": None,
                        }
                    ],
                    "version": 1,
                    "generated_at": signed_at.isoformat(),
                    "signed_out_by": "auditor",
                    "acknowledged_drift": False,
                    "drift_acknowledgement_reason": None,
                    "acknowledged_qc": False,
                    "qc_acknowledgement_reason": None,
                    "acknowledged_import_incomplete": False,
                    "import_incomplete_acknowledgement_reason": None,
                }
                content_hash = rss._canonical_hash(old)
                row_hash = hash_chain.chain_row_hash(
                    None,
                    rss._signout_chain_payload(
                        {
                            "version": 1,
                            "content_hash": content_hash,
                            "signed_out_at": signed_at,
                            "signed_out_by": "auditor",
                            "family_identifier": label,
                        }
                    ),
                )
                await s.execute(
                    text(
                        "INSERT INTO report_signouts (family_id, family_identifier, version, "
                        "signed_out_by, signed_out_at, content_hash, snapshot, row_hash, prev_hash) "
                        "VALUES (CAST(:f AS uuid), :fl, 1, 'auditor', :ts, :ch, CAST(:snap AS jsonb), :rh, NULL)"
                    ),
                    {"f": holder["uuid"], "fl": label, "ts": signed_at, "ch": content_hash,
                     "snap": json.dumps(old), "rh": row_hash},
                )
                await s.commit()

            # The CNV is imported, then classified and reported through the real save.
            await insert_structural_variant_records(
                _ASSEMBLY, holder["uuid"], [project], [_sv(["TCF4", "TXNL1"])]
            )
            async with sm() as s:
                await upsert_structural_variant_review(
                    s,
                    context=_ctx_now(),
                    variant_id=_SV_ID,
                    payload=StructuralVariantReviewUpdate(tags=["report"], cnv_acmg=scoring, source="needlr"),
                    user=user,
                )
            async with sm() as s:
                frozen = (
                    await s.execute(
                        text(
                            "SELECT cnv_evidence_snapshot FROM structural_variant_reviews "
                            "WHERE family_id = CAST(:f AS uuid) AND variant_id = :v"
                        ),
                        {"f": holder["uuid"], "v": _SV_ID},
                    )
                ).scalar_one()
            assert frozen["evidence"]["gene_symbols"] == ["TCF4", "TXNL1"]
            assert (frozen["evidence"]["pli"], frozen["evidence"]["inheritance"]) == (0.99, "de_novo")
            # The versions of the SV callset, not of its SNVs.
            assert frozen["versions"] == {"gencode": "45", "needlr": "0.4.2"}

            # (a) The record signed before SV evidence was frozen still verifies. The check
            # does not compare the SV/CNV drift section it lacks, but compares its reported
            # CNV whole: the evidence frozen since reads as a change (#681).
            async with sm() as s:
                v1 = await rss.get_report_signout(s, family_id=label, version=1, user=user)
                assert v1["verified"] is True
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert "structural_drift" in check["not_compared"]
                assert "structural_drift" not in check["changed_sections"]
                assert "reported_structural_variants" in check["changed_sections"]
                assert check["not_captured"] == []

            # (b) Evidence unchanged: sign-out needs no acknowledgement.
            async with sm() as s:
                v2 = await rss.sign_out_report(s, family_id=label, user=user)
            assert v2["version"] == 2 and v2["drift_acknowledged"] is False
            async with sm() as s:
                stored = await _stored_snapshot(s, 2)
            assert stored["structural_drift"] == {"checked": 1, "drifted_count": 0, "drifted": []}
            [reported] = stored["reported_structural_variants"]
            assert reported["evidence_snapshot"]["evidence_hash"] == frozen["evidence_hash"]

            # (c) A re-import changes the genes the CNV overlaps: the drift is reported and
            # sign-out is refused, with nothing written or audited.
            await replace_family_structural_variants(_ASSEMBLY, holder["uuid"], [project], [_sv(["TCF4"])])
            async with sm() as s:
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
            [entry] = drift["structural"]["drifted"]
            assert (entry["variant_id"], entry["status"]) == (_SV_ID, "drifted")
            assert "gene_symbols" in entry["changed"]
            assert entry["evidence_to"]["gene_symbols"] == ["TCF4"]
            async with sm() as s:
                events_before = await _count(s, "clinical_audit_events")
                with pytest.raises(HTTPException) as refused:
                    await rss.sign_out_report(s, family_id=label, user=user)
                await s.rollback()
            assert refused.value.status_code == 409
            refusal: Any = refused.value.detail
            assert refusal["gate"] == "evidence_drift"
            assert "structural-variant or CNV" in refusal["message"]
            async with sm() as s:
                assert await _count(s, "report_signouts") == 2
                assert await _count(s, "clinical_audit_events") == events_before
                with pytest.raises(HTTPException) as no_reason:
                    await rss.sign_out_report(
                        s, family_id=label, user=user, acknowledge_drift=True, drift_acknowledgement_reason=" "
                    )
                await s.rollback()
            assert no_reason.value.status_code == 422

            # (d) Acknowledged with a reason and the fingerprint of the drift the refusal listed,
            # as its dialog sends it (TRACE-19): frozen with the drift, and audited.
            async with sm() as s:
                v3 = await rss.sign_out_report(
                    s,
                    family_id=label,
                    user=user,
                    acknowledge_drift=True,
                    drift_acknowledgement_reason=_REASON,
                    drift_concerns_fingerprint=refusal["concerns_fingerprint"],
                )
            assert v3["version"] == 3 and v3["drift_acknowledged"] is True
            async with sm() as s:
                stored = await _stored_snapshot(s, 3)
                assert stored["structural_drift"]["drifted_count"] == 1
                assert stored["structural_drift"]["drifted"][0]["evidence_to"]["gene_symbols"] == ["TCF4"]
                assert stored["drift_acknowledgement_reason"] == _REASON
                after = (
                    await s.execute(
                        text(
                            "SELECT after FROM clinical_audit_events "
                            "WHERE family_identifier = :f AND action = 'sign_out' ORDER BY created_at DESC LIMIT 1"
                        ),
                        {"f": label},
                    )
                ).scalar_one()
                after = json.loads(after) if isinstance(after, str) else after
                assert (after["drifted_count"], after["structural_drifted_count"]) == (1, 1)
                assert after["drift_acknowledgement_reason"] == _REASON
                assert after["drift_concerns_fingerprint"] == refusal["concerns_fingerprint"]
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert check["version"] == 3 and check["matches"] is True, check

            # (e) Re-reviewed: saving the scoring again freezes the evidence as it is now,
            # and the drift clears.
            async with sm() as s:
                await upsert_structural_variant_review(
                    s,
                    context=_ctx_now(),
                    variant_id=_SV_ID,
                    payload=StructuralVariantReviewUpdate(tags=["report"], cnv_acmg=scoring, source="needlr"),
                    user=user,
                )
            async with sm() as s:
                drift = await cds.evaluate_classification_drift(s, family_id=label, user=user)
                assert drift["structural"] == {"checked": 1, "drifted_count": 0, "drifted": []}
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert set(check["changed_sections"]) == {"reported_structural_variants", "structural_drift"}
            async with sm() as s:
                v4 = await rss.sign_out_report(s, family_id=label, user=user)
            assert v4["version"] == 4 and v4["drift_acknowledged"] is False

            # (f) A reported SV whose scoring froze no evidence (saved where no SV storage
            # could be read) gates too.
            async with sm() as s:
                await upsert_structural_variant_review(
                    s,
                    context=_context(holder["uuid"], label, project, assembly=None),
                    variant_id="needlr_DUP_7_1000000",
                    payload=StructuralVariantReviewUpdate(tags=["report"], cnv_acmg=scoring),
                    user=user,
                )
            async with sm() as s:
                with pytest.raises(HTTPException) as unverified:
                    await rss.sign_out_report(s, family_id=label, user=user)
                await s.rollback()
            assert unverified.value.status_code == 409
            async with sm() as s:
                body = await rss.build_report_snapshot(s, family_id=label, user=user)
            assert {"variant_id": "needlr_DUP_7_1000000", "classification": None,
                    "cnv_class": "cnv_class_5", "status": "no_snapshot"} in body["structural_drift"]["drifted"]

            # The whole signed history, old record included, is one verified chain.
            async with sm() as s:
                chain = await rss.verify_report_signout_chain(s, label)
                assert chain.verified and chain.rows_checked == 4, chain
        finally:
            await close_postgres_engine()
            await close_clickhouse_client()

    asyncio.run(_run())


# --- two callers of one SV (TRACE-11) ------------------------------------------------------------


def _upload_keyed_lower() -> str:
    """A family id under which the upload's row of ``_SV_ID`` has the lower storage key: the
    row the save froze, and the drift check read, whatever row a classification was made on."""
    from backend.app.services.clickhouse_variant_ids import structural_variant_key

    while True:
        family_uuid = str(uuid4())
        keys = {
            caller: structural_variant_key(_ASSEMBLY, family_uuid, _SV_ID, source=caller)
            for caller in ("needlr", "sniffles")
        }
        if keys["sniffles"] < keys["needlr"]:
            return family_uuid


async def _two_caller_family(sessionmaker, family_uuid: str, label: str, project: str) -> dict:
    """The family, with the deletion NeedlR and the upload both call and one NeedlR alone
    calls; returns the shared deletion's rows as the SV page reads them, by caller."""
    from backend.app.services.clickhouse_family_variants import _fetch_structural_variant_rows
    from backend.app.services.clickhouse_variant_storage import insert_structural_variant_records
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

    async with sessionmaker() as s:
        await s.execute(
            text("INSERT INTO families (id, family_id) VALUES (CAST(:id AS uuid), :f)"),
            {"id": family_uuid, "f": label},
        )
        await s.execute(
            text(
                "INSERT INTO family_annotation_manifest (family_id, modules, source, recorded_by) "
                "VALUES (CAST(:f AS uuid), CAST(:m AS jsonb), 'vcf_header', 'import (vcf_header)')"
            ),
            {"f": family_uuid, "m": json.dumps({"needlr": {"version": "0.4.2", "by_modality": {"sv": "0.4.2"}}})},
        )
        await s.commit()
    await insert_structural_variant_records(
        _ASSEMBLY,
        family_uuid,
        [project],
        [_sv(["TCF4", "TXNL1"]), _sniffles_upload(), _sv(["DCC"], variant_id=_NEEDLR_ONLY)],
    )
    rows = {
        record.source: record
        for record in await _fetch_structural_variant_rows(
            _context(family_uuid, label, project), StructuralVariantQueryFilters(page=1, page_size=10)
        )
        if record.variant_id == _SV_ID
    }
    assert rows["sniffles"].variant_key < rows["needlr"].variant_key
    return rows


def test_a_classification_freezes_the_row_of_the_caller_it_was_made_on(monkeypatch) -> None:
    """The real save, on an SV NeedlR and the upload both call, the upload's row keyed lower:
    named for NeedlR's row it freezes NeedlR's evidence, named for the upload's the upload's.
    Naming no caller it is refused (422), and naming a caller that does not call the SV (404),
    neither writing anything; naming none for an SV one caller calls, it takes that row."""
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.schemas import CnvAcmgClassificationPayload, CnvAcmgCriterion, StructuralVariantReviewUpdate
    from backend.app.services import annotation_manifest_service as ams
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.structural_variant_evidence import structural_evidence
    from backend.app.services.structural_variant_review_pg import upsert_structural_variant_review

    family_uuid, project = _upload_keyed_lower(), str(uuid4())
    label = f"sv-own-caller-save-{uuid4()}"
    ctx = _context(family_uuid, label, project)

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return ctx

    monkeypatch.setattr(ams, "build_family_metadata_context", _ctx)
    user = SimpleNamespace(username="reviewer", email="reviewer@example.org", id=None)
    scoring = CnvAcmgClassificationPayload(kind="loss", criteria=[CnvAcmgCriterion(code="2A", points=1.0, accepted=True)])

    async def _run() -> None:
        try:
            await init_postgres_schema()
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            sm = get_postgres_sessionmaker()
            rows = await _two_caller_family(sm, family_uuid, label, project)

            async def _save(variant_id: str, **named) -> None:
                async with sm() as s:
                    await upsert_structural_variant_review(
                        s,
                        context=ctx,
                        variant_id=variant_id,
                        payload=StructuralVariantReviewUpdate(cnv_acmg=scoring, **named),
                        user=user,
                    )

            async def _stored(variant_id: str) -> tuple:
                async with sm() as s:
                    snapshot = (
                        await s.execute(
                            text(
                                "SELECT cnv_evidence_snapshot FROM structural_variant_reviews "
                                "WHERE family_id = CAST(:f AS uuid) AND variant_id = :v"
                            ),
                            {"f": family_uuid, "v": variant_id},
                        )
                    ).scalar_one_or_none()
                    events = (
                        await s.execute(
                            text("SELECT count(*) FROM clinical_audit_events WHERE family_identifier = :f"),
                            {"f": label},
                        )
                    ).scalar_one()
                return snapshot, events

            await _save(_SV_ID, source="needlr")
            needlr_snapshot, _ = await _stored(_SV_ID)
            assert needlr_snapshot["evidence"] == structural_evidence(rows["needlr"])
            assert (needlr_snapshot["evidence"]["pli"], needlr_snapshot["evidence"]["inheritance"]) == (0.99, "de_novo")

            await _save(_SV_ID, source="sniffles")
            upload_snapshot, events = await _stored(_SV_ID)
            assert upload_snapshot["evidence"] == structural_evidence(rows["sniffles"])

            for named, status in (({}, 422), ({"source": "spectre"}, 404)):
                with pytest.raises(HTTPException) as refused:
                    await _save(_SV_ID, **named)
                assert refused.value.status_code == status
            assert await _stored(_SV_ID) == (upload_snapshot, events)

            await _save(_NEEDLR_ONLY)
            one_caller_snapshot, _ = await _stored(_NEEDLR_ONLY)
            assert one_caller_snapshot["evidence"]["source"] == "needlr"
            assert one_caller_snapshot["evidence"]["gene_symbols"] == ["DCC"]
        finally:
            await close_postgres_engine()
            await close_clickhouse_client()

    asyncio.run(_run())


def test_the_drift_check_compares_the_evidence_with_the_row_it_was_frozen_from(monkeypatch) -> None:
    """Evidence frozen from NeedlR's row of an SV the upload also calls, the upload's row keyed
    lower, is compared with NeedlR's row: current while it is unchanged, although the upload's
    row has other genes and no pLI or inheritance; drifted when a re-import moves NeedlR's
    genes; no longer in the data when NeedlR's call is gone, although the upload still calls
    the SV."""
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import classification_drift_service as cds
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_structural_variants,
        ensure_clickhouse_variant_tables,
        replace_family_structural_variants,
    )
    from backend.app.services.structural_variant_evidence import build_structural_evidence_snapshot

    family_uuid, project = _upload_keyed_lower(), str(uuid4())
    label = f"sv-own-caller-drift-{uuid4()}"
    ctx = _context(family_uuid, label, project)

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return ctx

    monkeypatch.setattr(cds, "build_family_metadata_context", _ctx)
    user = SimpleNamespace(username="reviewer", email="reviewer@example.org", id=None)

    async def _run() -> None:
        try:
            await init_postgres_schema()
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            sm = get_postgres_sessionmaker()
            rows = await _two_caller_family(sm, family_uuid, label, project)
            # The evidence a classification made on the NeedlR row freezes.
            frozen = build_structural_evidence_snapshot(rows["needlr"], {}, captured_at=datetime.now(timezone.utc))
            async with sm() as s:
                await s.execute(
                    text(
                        "INSERT INTO structural_variant_reviews "
                        "(family_id, variant_id, cnv_class, cnv_evidence_snapshot, updated_by) "
                        "VALUES (CAST(:f AS uuid), :v, 'cnv_class_5', CAST(:snapshot AS jsonb), 'reviewer')"
                    ),
                    {"f": family_uuid, "v": _SV_ID, "snapshot": json.dumps(frozen)},
                )
                await s.commit()

            async def _structural() -> dict:
                async with sm() as s:
                    return (await cds.evaluate_classification_drift(s, family_id=label, user=user))["structural"]

            assert await _structural() == {"checked": 1, "drifted_count": 0, "drifted": []}

            await replace_family_structural_variants(
                _ASSEMBLY, family_uuid, [project], [_sv(["TCF4"]), _sniffles_upload()]
            )
            # Its genes and the annotation that lists them moved; nothing the upload's row
            # holds is compared.
            [entry] = (await _structural())["drifted"]
            assert (entry["status"], entry["changed"]) == ("drifted", ["gene_symbols", "annotations"])
            assert (entry["evidence_to"]["source"], entry["evidence_to"]["gene_symbols"]) == ("needlr", ["TCF4"])

            await delete_family_structural_variants(_ASSEMBLY, family_uuid, source="needlr")
            [entry] = (await _structural())["drifted"]
            assert (entry["status"], entry["evidence_to"]) == ("variant_missing", None)
            assert entry["evidence_from"] == frozen["evidence"]
        finally:
            await close_postgres_engine()
            await close_clickhouse_client()

    asyncio.run(_run())
