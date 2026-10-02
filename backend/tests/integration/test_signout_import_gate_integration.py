"""Sign-out of a family whose import left it incomplete (real Postgres).

A family-package import that partly fails leaves the family in place and stamps
``families.metadata.import_incomplete`` (``_flag_family_import_incomplete``); a later
import that completes having imported the failed datasets again clears it
(``_clear_family_import_incomplete``). Sign-out reads
the flag from the same row. This drives the REAL flag writers, the sign-out writer and
the sign-out readers against the real schema:

- a flagged family is refused sign-out (409, naming the failed datasets and the import
  job that holds their errors) and nothing is written: no ``report_signouts`` row, no
  clinical audit event;
- an acknowledgement without a reason is refused (422); with one, the flag's details and
  the reason are frozen in the content-hashed snapshot, extracted by the list/detail
  reads, and recorded in the audit event;
- a sign-out stored before the gate existed (a snapshot without the gate's keys, as the
  old writer wrote it) keeps verifying on read and in the chain beside new ones, and
  reads as "no acknowledgement recorded", not as ``False``;
- once the flag is cleared, sign-out needs no acknowledgement, and the page check sees
  that the import state changed since the acknowledged sign-out.

The ClickHouse-backed parts of the snapshot (manifest, drift, Sample QC) and the QC
cut-off lookup are stubbed; everything this gate adds is real SQL. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.app.services import hash_chain

pytestmark = pytest.mark.integration

_REASON = "SV calls are not part of this referral; the SNV re-import is booked."


def test_sign_out_refuses_an_incomplete_import_unless_acknowledged(monkeypatch) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services import report_signout_service as rss
    from backend.app.services.family_package_registration import (
        _clear_family_import_incomplete,
        _flag_family_import_incomplete,
    )
    from backend.app.services.sample_integrity_qc import SampleIntegrityReport

    label = f"import-gate-{uuid4()}"
    # The job whose record holds each dataset's error; the flag only names it.
    job_id = str(uuid4())
    holder: dict = {}

    def _context() -> SimpleNamespace:
        return SimpleNamespace(
            family_uuid=holder["uuid"], family_id=label, assembly_name="GRCh38", sample_rows=[]
        )

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return _context()

    async def _manifest(session, *, family_id, user, project_id=None):
        return {"assembly": "GRCh38", "modules": [{"key": "clinvar", "version": "2026-05"}]}

    async def _drift(session, *, family_id, user, project_id=None):
        return {
            "checked": 0,
            "drifted_count": 0,
            "drifted": [],
            "structural": {"checked": 0, "drifted_count": 0, "drifted": []},
        }

    async def _qc(session, *, family_id, user, project_id=None):
        return SampleIntegrityReport(overall_status="pass")

    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)
    monkeypatch.setattr(rss, "evaluate_classification_drift", _drift)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)

    user = SimpleNamespace(username="signer", email="signer@example.org", id=None)
    signed_at = datetime(2026, 9, 1, 8, 30, 0, 250000, tzinfo=timezone.utc)
    # The snapshot the writer stored before the gate existed: none of its keys.
    pre_gate_snapshot = {
        "family_id": label,
        "assembly": "GRCh38",
        "modules": [{"key": "clinvar", "version": "2026-05"}],
        "software": {"version": "1.4.0", "git_sha": "0123abc"},
        "drift": {"checked": 0, "drifted_count": 0, "drifted": []},
        "sample_qc": {"overall_status": "pass"},
        "sequencing_qc": {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}},
        "reported_variants": [],
        "reported_structural_variants": [],
        "version": 1,
        "generated_at": signed_at.isoformat(),
        "signed_out_by": "auditor",
        "acknowledged_drift": False,
        "drift_acknowledgement_reason": None,
        "acknowledged_qc": False,
        "qc_acknowledgement_reason": None,
    }

    counts = {
        "report_signouts": text(
            "SELECT count(*) FROM report_signouts WHERE family_identifier = :f"
        ),
        "clinical_audit_events": text(
            "SELECT count(*) FROM clinical_audit_events WHERE family_identifier = :f"
        ),
    }

    async def _count(session, table: str) -> int:
        return (await session.execute(counts[table], {"f": label})).scalar_one()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()

            # A family with one sign-out already on record, made before the gate.
            async with sm() as s:
                holder["uuid"] = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                content_hash = rss._canonical_hash(pre_gate_snapshot)
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
                        "VALUES (CAST(:f AS uuid), :fl, 1, 'auditor', :ts, :ch, "
                        "CAST(:snap AS jsonb), :rh, NULL)"
                    ),
                    {
                        "f": holder["uuid"],
                        "fl": label,
                        "ts": signed_at,
                        "ch": content_hash,
                        "snap": json.dumps(pre_gate_snapshot),
                        "rh": row_hash,
                    },
                )
                await s.commit()

            # A re-import partly fails: the real import helper flags the family.
            async with sm() as s:
                await _flag_family_import_incomplete(
                    s,
                    _context(),
                    failed_datasets=["sv", "snv"],
                    imported_datasets=["coverage"],
                    job_id=job_id,
                )

            # (a) Refused without an acknowledgement; nothing written or audited.
            async with sm() as s:
                with pytest.raises(HTTPException) as refused:
                    await rss.sign_out_report(s, family_id=label, user=user)
                await s.rollback()
            assert refused.value.status_code == 409
            assert refused.value.detail["gate"] == "import_incomplete"
            flag = refused.value.detail["import_incomplete"]
            assert flag["failed_datasets"] == ["snv", "sv"]
            assert flag["imported_datasets"] == ["coverage"]
            assert flag["at"]
            assert flag["job_id"] == job_id
            assert f"import job {job_id}" in refused.value.detail["message"]
            async with sm() as s:
                assert await _count(s, "report_signouts") == 1
                assert await _count(s, "clinical_audit_events") == 0

            # (b) An acknowledgement needs a reason.
            async with sm() as s:
                with pytest.raises(HTTPException) as no_reason:
                    await rss.sign_out_report(
                        s,
                        family_id=label,
                        user=user,
                        acknowledge_import_incomplete=True,
                        import_incomplete_acknowledgement_reason="  ",
                    )
                await s.rollback()
            assert no_reason.value.status_code == 422

            # (c) Acknowledged with a reason: signed out as version 2, on the same chain.
            async with sm() as s:
                signed = await rss.sign_out_report(
                    s,
                    family_id=label,
                    user=user,
                    acknowledge_import_incomplete=True,
                    import_incomplete_acknowledgement_reason=_REASON,
                )
            assert signed["version"] == 2

            async with sm() as s:
                stored = (
                    await s.execute(
                        text(
                            "SELECT snapshot FROM report_signouts "
                            "WHERE family_identifier = :f AND version = 2"
                        ),
                        {"f": label},
                    )
                ).scalar_one()
                stored = json.loads(stored) if isinstance(stored, str) else stored
                assert stored["import_incomplete"] == flag
                assert stored["acknowledged_import_incomplete"] is True
                assert stored["import_incomplete_acknowledgement_reason"] == _REASON

                listed = await rss.list_report_signouts(s, family_id=label, user=user)
                by_version = {row["version"]: row for row in listed["signouts"]}
                assert by_version[2]["import_incomplete_acknowledged"] is True
                assert by_version[2]["import_incomplete_acknowledgement_reason"] == _REASON
                assert by_version[2]["import_incomplete_failed_datasets"] == ["snv", "sv"]
                assert by_version[2]["import_incomplete_job_id"] == job_id
                # The pre-gate record claims no acknowledgement either way.
                assert by_version[1]["import_incomplete_acknowledged"] is None
                assert by_version[1]["import_incomplete_acknowledgement_reason"] is None
                assert by_version[1]["import_incomplete_failed_datasets"] is None
                assert by_version[1]["import_incomplete_job_id"] is None

                old = await rss.get_report_signout(s, family_id=label, version=1, user=user)
                new = await rss.get_report_signout(s, family_id=label, version=2, user=user)
                assert old["verified"] is True, "a pre-gate record must not read as tampered"
                assert old["import_incomplete_acknowledged"] is None
                assert new["verified"] is True
                assert new["import_incomplete_failed_datasets"] == ["snv", "sv"]
                assert new["import_incomplete_job_id"] == job_id

                events = (
                    await s.execute(
                        text(
                            "SELECT summary, after FROM clinical_audit_events "
                            "WHERE family_identifier = :f AND action = 'sign_out'"
                        ),
                        {"f": label},
                    )
                ).all()
                assert len(events) == 1
                summary, after = events[0]
                after = json.loads(after) if isinstance(after, str) else after
                assert ", incomplete import acknowledged" in summary
                assert after["import_incomplete"] == flag
                assert after["acknowledged_import_incomplete"] is True
                assert after["import_incomplete_acknowledgement_reason"] == _REASON

            # (d) A re-import that imports the failed datasets again clears the flag (the
            # real helper); one that completes without them leaves it. The page check sees
            # the import state differ from the acknowledged record, through the JSONB
            # round-trip; sign-out then needs no acknowledgement.
            async with sm() as s:
                left = await _clear_family_import_incomplete(
                    s, _context(), imported={"coverage": None}
                )
                assert left is not None and set(left.failures.failed) == {"snv", "sv"}
            async with sm() as s:
                await _clear_family_import_incomplete(s, _context(), imported={"snv": None, "sv": None})
            async with sm() as s:
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert check["version"] == 2
                assert check["changed_sections"] == ["import_incomplete"]
            async with sm() as s:
                complete = await rss.sign_out_report(s, family_id=label, user=user)
            assert complete["version"] == 3
            assert complete["snapshot"]["import_incomplete"] is None
            assert complete["snapshot"]["acknowledged_import_incomplete"] is False

            # The whole history — pre-gate, acknowledged, complete — is one verified chain.
            async with sm() as s:
                chain = await rss.verify_report_signout_chain(s, label)
                assert chain.verified and chain.rows_checked == 3, chain
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert check["version"] == 3 and check["matches"] is True, check
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
