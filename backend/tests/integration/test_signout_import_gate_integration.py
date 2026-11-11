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
- once the flag is cleared, sign-out needs no acknowledgement, and the page check sees
  that the import state changed since the acknowledged sign-out.

The ClickHouse-backed parts of the snapshot (manifest, drift, Sample QC) and the QC
cut-off lookup are stubbed; everything this gate adds is real SQL. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.tests._signout_dialogs import no_finding_reviews

pytestmark = pytest.mark.integration

_REASON = "SV calls are not part of this referral; the SNV re-import is booked."
# What an overwrite of each failed dataset replaces (dataset_scopes), as the flag records it.
_SCOPES = {"snv": {"source": "clair3", "samples": None}, "sv": {"source": None, "samples": None}}


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

    async def _qc(session, *, family_id, user, project_id=None, shared_nipt=None):
        return SampleIntegrityReport(overall_status="pass")

    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    # No repeat or Paraphase call, and no review of one (finding_review_pg).
    monkeypatch.setattr(rss, "finding_reviews_for_record", no_finding_reviews)
    monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)
    monkeypatch.setattr(rss, "evaluate_classification_drift", _drift)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)

    user = SimpleNamespace(username="signer", email="signer@example.org", id=None, role="supervisor")
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

            # The family.
            async with sm() as s:
                holder["uuid"] = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                await s.commit()

            # A re-import partly fails: the real import helper flags the family.
            async with sm() as s:
                await _flag_family_import_incomplete(
                    s,
                    _context(),
                    failed_datasets=["sv", "snv"],
                    imported_datasets=["coverage"],
                    job_id=job_id,
                    scopes=_SCOPES,
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
                assert await _count(s, "report_signouts") == 0
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

            # (c) Acknowledged with a reason and the fingerprint of the flag the refusal listed,
            # as its dialog sends it: signed out as version 1.
            # Read back through JSONB, the flag still has the fingerprint the refusal gave.
            shown: Any = refused.value.detail
            async with sm() as s:
                signed = await rss.sign_out_report(
                    s,
                    family_id=label,
                    user=user,
                    acknowledge_import_incomplete=True,
                    import_incomplete_acknowledgement_reason=_REASON,
                    import_incomplete_concerns_fingerprint=shown["concerns_fingerprint"],
                )
            assert signed["version"] == 1

            async with sm() as s:
                stored = (
                    await s.execute(
                        text(
                            "SELECT snapshot FROM report_signouts "
                            "WHERE family_identifier = :f AND version = 1"
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
                assert by_version[1]["import_incomplete_acknowledged"] is True
                assert by_version[1]["import_incomplete_acknowledgement_reason"] == _REASON
                assert by_version[1]["import_incomplete_failed_datasets"] == ["snv", "sv"]
                assert by_version[1]["import_incomplete_job_id"] == job_id

                new = await rss.get_report_signout(s, family_id=label, version=1, user=user)
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
                assert after["import_incomplete_concerns_fingerprint"] == shown["concerns_fingerprint"]

            # (d) A re-import that imports the failed datasets again clears the flag (the
            # real helper); one that completes without them leaves it. The page check sees
            # the import state differ from the acknowledged record, through the JSONB
            # round-trip; sign-out then needs no acknowledgement.
            async with sm() as s:
                left = await _clear_family_import_incomplete(
                    s, _context(), imported={"coverage": {"source": None, "samples": None}}
                )
                assert left is not None and set(left.failures.failed) == {"snv", "sv"}
            async with sm() as s:
                await _clear_family_import_incomplete(s, _context(), imported=_SCOPES)
            async with sm() as s:
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert check["version"] == 1
                assert check["changed_sections"] == ["import_incomplete"]
            async with sm() as s:
                complete = await rss.sign_out_report(s, family_id=label, user=user)
            assert complete["version"] == 2
            assert complete["snapshot"]["import_incomplete"] is None
            assert complete["snapshot"]["acknowledged_import_incomplete"] is False

            # The whole history — acknowledged, complete — is one verified chain.
            async with sm() as s:
                chain = await rss.verify_report_signout_chain(s, label)
                assert chain.verified and chain.rows_checked == 2, chain
                check = await rss.compare_report_with_latest_signout(s, family_id=label, user=user)
                assert check["version"] == 2 and check["matches"] is True, check
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
