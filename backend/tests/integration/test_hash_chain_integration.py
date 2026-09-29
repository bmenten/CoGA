"""End-to-end tamper-evidence hash-chain integrity (real Postgres, smoke job).

Proves the per-family chains hold against real Postgres:
- the REAL writers (``record_clinical_event``; ``sign_out_report``; the structural-variant
  / CNV review save ``upsert_structural_variant_review``) produce a chain that
  ``verify_*_chain`` accepts (genuine positive control, not the helper verifying itself);
- a privileged mutation that BYPASSES the append-only trigger and does NOT recompute the
  hashes (``DISABLE TRIGGER`` — careless owner / any non-owner role) is DETECTED & localised;
- the chain is partitioned on the immutable ``family_identifier``, so the legitimate
  ``ON DELETE SET NULL`` cascades on account deletion (actor_id→NULL) AND family deletion
  (family_id→NULL) leave the chain intact and verifiable;
- large integral floats survive the JSONB round-trip (the ``canonical_json`` normalisation),
  and ``get_report_signout`` re-verifies the stored content hash on read.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
The pure chain math is unit-tested in ``backend/tests/test_hash_chain.py``. (NOTE on scope:
the trigger-bypass tampering here models the careless owner / any non-owner role — NOT a
determined owner who re-chains an interior edit, which an in-DB chain cannot detect; see
the ``hash_chain`` module docstring.)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.app.services import hash_chain

pytestmark = pytest.mark.integration


async def _fresh_family(session, label: str) -> str:
    """Create an isolated family (only family_id is required) and return its UUID."""
    return (
        await session.execute(
            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
            {"f": label},
        )
    ).scalar_one()


async def _fresh_user(session) -> str:
    return (
        await session.execute(
            text(
                "INSERT INTO users (username, hashed_password, role, email) "
                "VALUES (:u, 'x', 'viewer', :e) RETURNING id::text"
            ),
            {"u": f"p1-4-{uuid4()}", "e": f"p1-4-{uuid4()}@x.org"},
        )
    ).scalar_one()


async def _clinical_event_ids(session, family_identifier: str) -> list[str]:
    return [
        row[0]
        for row in (
            await session.execute(
                text(
                    "SELECT id::text FROM clinical_audit_events "
                    "WHERE family_identifier = :f ORDER BY created_at ASC, id ASC"
                ),
                {"f": family_identifier},
            )
        ).all()
    ]


async def _bypass_trigger_mutate(session, table: str, sql: str, params: dict) -> None:
    """Mutate a row with the append-only trigger DISABLED — i.e. privileged tampering
    that does NOT recompute the chain hashes (careless owner / any non-owner role)."""
    await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
    await session.execute(text(sql), params)
    await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
    await session.commit()


def test_clinical_audit_hash_chain_holds_and_detects_tampering() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clinical_audit_service import (
        record_clinical_event,
        verify_clinical_audit_chain,
    )

    async def _append(session, family_uuid, family_label, n, actor_id=None):
        for i in range(n):
            await record_clinical_event(
                session,
                family_uuid=family_uuid,
                family_identifier=family_label,
                variant_id=f"1-{i}-A-G",
                actor="alice",
                actor_id=actor_id,
                action="classification",
                summary=f"event {i}",
                before={"acmg_class": "acmg_class_3", "n": i},
                after={"acmg_class": "acmg_class_4", "n": i + 1},
                metadata={"source": "test"},
            )
        await session.commit()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()

            # (a) A clean chain written by the real writer verifies.
            label_a = f"p1-4-cae-a-{uuid4()}"
            async with sm() as s:
                fam_a = await _fresh_family(s, label_a)
                await _append(s, fam_a, label_a, 3)
                ok = await verify_clinical_audit_chain(s, label_a)
                assert ok.verified and ok.rows_checked == 3, ok

            # (b) Editing a row's content (trigger bypassed) is detected & localised.
            async with sm() as s:
                ids_a = await _clinical_event_ids(s, label_a)
            async with sm() as s:
                await _bypass_trigger_mutate(
                    s,
                    "clinical_audit_events",
                    "UPDATE clinical_audit_events SET summary = 'TAMPERED' "
                    "WHERE id = CAST(:id AS uuid)",
                    {"id": ids_a[1]},
                )
                bad = await verify_clinical_audit_chain(s, label_a)
                assert not bad.verified and bad.first_bad_row == ids_a[1], bad
                assert "row_hash mismatch" in (bad.reason or "")

            # (c) Deleting a middle row (trigger bypassed) is detected as a broken link.
            label_b = f"p1-4-cae-b-{uuid4()}"
            async with sm() as s:
                fam_b = await _fresh_family(s, label_b)
                await _append(s, fam_b, label_b, 3)
            async with sm() as s:
                ids_b = await _clinical_event_ids(s, label_b)
                await _bypass_trigger_mutate(
                    s,
                    "clinical_audit_events",
                    "DELETE FROM clinical_audit_events WHERE id = CAST(:id AS uuid)",
                    {"id": ids_b[1]},
                )
                gap = await verify_clinical_audit_chain(s, label_b)
                assert not gap.verified and "prev_hash" in (gap.reason or ""), gap
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_clinical_audit_chain_survives_account_and_family_deletion() -> None:
    """The chain is keyed on the immutable family_identifier, so the legitimate FK
    SET-NULL cascades (account deletion → actor_id NULL; family deletion → family_id
    NULL) leave a multi-row chain intact AND verifiable — the exact case the design
    claims to survive (and which a family_id-keyed partition would false-positive)."""
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clinical_audit_service import (
        record_clinical_event,
        verify_clinical_audit_chain,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label_a = f"p1-4-del-a-{uuid4()}"
            label_b = f"p1-4-del-b-{uuid4()}"
            async with sm() as s:
                fam_a = await _fresh_family(s, label_a)
                fam_b = await _fresh_family(s, label_b)
                uid = await _fresh_user(s)
                for i in range(3):  # family A: every event authored by the user we delete
                    await record_clinical_event(
                        s, family_uuid=fam_a, family_identifier=label_a,
                        variant_id=f"1-{i}-A-G", actor="alice", actor_id=uid,
                        action="classification", summary=f"a{i}", metadata={},
                    )
                for i in range(2):  # family B: a second, independent chain
                    await record_clinical_event(
                        s, family_uuid=fam_b, family_identifier=label_b,
                        variant_id=f"2-{i}-A-G", actor="bob", actor_id=None,
                        action="tags", summary=f"b{i}", metadata={},
                    )
                await s.commit()
                assert (await verify_clinical_audit_chain(s, label_a)).verified
                assert (await verify_clinical_audit_chain(s, label_b)).verified

            # Account deletion: nulls actor_id on every (mid-)chain row of family A
            # (permitted carve-out, no trigger bypass). actor_id is excluded from the
            # payload, so the chain stays intact.
            async with sm() as s:
                await s.execute(
                    text("DELETE FROM users WHERE id = CAST(:u AS uuid)"), {"u": uid}
                )
                await s.commit()
                acct = await verify_clinical_audit_chain(s, label_a)
                assert acct.verified and acct.rows_checked == 3, acct

            # Family deletion: nulls family_id on every row of family A. Because the chain
            # is keyed on the retained family_identifier, A's chain is still found & intact,
            # and family B (a different identifier) is wholly unaffected.
            async with sm() as s:
                await s.execute(
                    text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": fam_a}
                )
                await s.commit()
                famdel = await verify_clinical_audit_chain(s, label_a)
                assert famdel.verified and famdel.rows_checked == 3, famdel
                assert (await verify_clinical_audit_chain(s, label_b)).verified
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_structural_variant_review_saves_are_chained_and_verify() -> None:
    """SV / CNV review saves are on the family's clinical chain: the REAL
    ``upsert_structural_variant_review`` against real Postgres records a ClinGen
    classification, tag and note edits, a rescoring whose point total rounds to -0.0, a
    cleared CNV scoring and the deletion of the review — interleaved with a small-variant
    event on the same family — and the chain still verifies after the JSONB round-trip
    (Postgres reads a stored -0.0 back as 0, so an unnormalised one would not)."""
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import (
        CnvAcmgClassificationPayload,
        CnvAcmgCriterion,
        SmallVariantReviewUpdate,
    )
    from backend.app.services.clinical_audit_service import (
        record_review_changes,
        verify_clinical_audit_chain,
    )
    from backend.app.services.structural_variant_review_pg import (
        upsert_structural_variant_review,
    )

    def _loss(*criteria: tuple[str, float]) -> CnvAcmgClassificationPayload:
        return CnvAcmgClassificationPayload(
            kind="loss",
            criteria=[CnvAcmgCriterion(code=c, points=p, accepted=True) for c, p in criteria],
        )

    sv_id = "1-100000-250000-DEL---"
    reported = {"tags": ["report"], "note": "reported CNV"}
    saves = [
        SmallVariantReviewUpdate(classification="Pathogenic - class 5", cnv_acmg=_loss(("2A", 1.0))),
        SmallVariantReviewUpdate(
            classification="Pathogenic - class 5", cnv_acmg=_loss(("2A", 1.0)), **reported
        ),
        # 0.30 + 0.15 - 0.45 rounds to a -0.0 point total (VUS).
        SmallVariantReviewUpdate(
            classification="VUS - class 3",
            cnv_acmg=_loss(("4C", 0.30), ("2H", 0.15), ("5D", -0.45)),
            **reported,
        ),
        SmallVariantReviewUpdate(classification="VUS - class 3", cnv_acmg=_loss(), **reported),
        SmallVariantReviewUpdate(),  # everything cleared: the review is deleted
    ]

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"sv-audit-{uuid4()}"
            async with sm() as s:
                fam = await _fresh_family(s, label)
                await s.commit()
            context = SimpleNamespace(family_uuid=fam, family_id=label, project_ids=[])
            user = SimpleNamespace(id=None, username="sv-reviewer", email="svr@x.org")

            for index, payload in enumerate(saves):
                async with sm() as s:
                    await upsert_structural_variant_review(
                        s, context=context, variant_id=sv_id, payload=payload, user=user
                    )
                if index == 1:  # a small-variant save on the same family, same chain
                    async with sm() as s:
                        await record_review_changes(
                            s,
                            family_uuid=fam,
                            family_identifier=label,
                            variant_id="1-2000-C-T",
                            user=user,
                            existing=None,
                            new_state={
                                "acmg_class": "acmg_class_4",
                                "acmg": {"criteria": [{"code": "PM2", "accepted": True}]},
                                "tags": [],
                                "note": None,
                            },
                        )
                        await s.commit()

            async with sm() as s:
                chain = await verify_clinical_audit_chain(s, label)
                rows = (
                    await s.execute(
                        text(
                            "SELECT variant_id, action, summary, after, metadata, row_hash "
                            "FROM clinical_audit_events WHERE family_identifier = :f "
                            "ORDER BY created_at ASC, id ASC"
                        ),
                        {"f": label},
                    )
                ).mappings().all()
                review_left = (
                    await s.execute(
                        text(
                            "SELECT count(*) FROM structural_variant_reviews "
                            "WHERE family_id = CAST(:f AS uuid)"
                        ),
                        {"f": fam},
                    )
                ).scalar_one()

            assert chain.verified and chain.rows_checked == len(rows) == 9, (chain, rows)
            assert all(row["row_hash"] for row in rows)
            sv_rows = [row for row in rows if row["variant_id"] == sv_id]
            assert [row["action"] for row in sv_rows] == [
                "classification",  # classified
                "tags", "note",  # reported
                "classification",  # rescored
                "classification",  # CNV scoring cleared
                "classification", "tags", "note",  # review deleted
            ]
            assert all(row["metadata"] == {"modality": "sv"} for row in sv_rows)
            [small] = [row for row in rows if row["variant_id"] == "1-2000-C-T"]
            assert small["metadata"] == {}
            rescored = sv_rows[3]["after"]
            assert rescored["cnv_class"] == "cnv_class_3" and rescored["cnv_point_total"] == 0.0
            assert rescored["cnv_criteria"] == [
                {"code": "2H", "points": 0.15},
                {"code": "4C", "points": 0.3},
                {"code": "5D", "points": -0.45},
            ]
            assert sv_rows[4]["summary"] == "CNV classification VUS - class 3 → unclassified"
            assert sv_rows[5]["summary"] == "Classification VUS - class 3 → unclassified"
            assert review_left == 0
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_report_signout_verifier_detects_tampering_and_survives_family_deletion() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services import report_signout_service as rss

    signed_at = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)

    async def _insert_signout(session, fam_uuid, fam_label, version, snapshot, prev_hash):
        content_hash = rss._canonical_hash(snapshot)
        row_hash = hash_chain.chain_row_hash(
            prev_hash,
            rss._signout_chain_payload(
                {
                    "version": version,
                    "content_hash": content_hash,
                    "signed_out_at": signed_at,
                    "signed_out_by": "auditor",
                    "family_identifier": fam_label,
                }
            ),
        )
        await session.execute(
            text(
                "INSERT INTO report_signouts (family_id, family_identifier, version, "
                "signed_out_by, signed_out_at, content_hash, snapshot, row_hash, prev_hash) "
                "VALUES (CAST(:f AS uuid), :fl, :v, 'auditor', :ts, :ch, "
                "CAST(:snap AS jsonb), :rh, :ph)"
            ),
            {
                "f": fam_uuid, "fl": fam_label, "v": version, "ts": signed_at,
                "ch": content_hash, "snap": json.dumps(snapshot, default=str),
                "rh": row_hash, "ph": prev_hash,
            },
        )
        return row_hash

    # Realistic snapshot with nested structures + floats INCLUDING a large integral float
    # (>= 2**53), to prove the stored content_hash survives the JSONB round-trip.
    def _snap(v):
        return {
            "version": v,
            "reported": [{"variant": "1-1-A-G", "acmg": "acmg_class_4"}],
            "metrics": {"call_rate": 0.9876, "het_ratio": 1.5, "epoch_ns": 1.75e18},
            "note": "signed",
        }

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()

            # (a) Clean two-version chain verifies (chain + content_hash re-check, incl. big float).
            label = f"p1-4-rs-{uuid4()}"
            async with sm() as s:
                fam = await _fresh_family(s, label)
                h1 = await _insert_signout(s, fam, label, 1, _snap(1), None)
                await _insert_signout(s, fam, label, 2, _snap(2), h1)
                await s.commit()
                ok = await rss.verify_report_signout_chain(s, label)
                assert ok.verified and ok.rows_checked == 2, ok

            # (b) Editing the snapshot (content_hash NOT updated) is caught by the content re-check.
            async with sm() as s:
                await _bypass_trigger_mutate(
                    s, "report_signouts",
                    "UPDATE report_signouts SET snapshot = "
                    "jsonb_set(snapshot, '{note}', '\"ALTERED\"') "
                    "WHERE family_identifier = :fl AND version = 2",
                    {"fl": label},
                )
                bad = await rss.verify_report_signout_chain(s, label)
                assert not bad.verified and "content_hash" in (bad.reason or ""), bad

            # (c) Family deletion (family_id→NULL) leaves the signed history verifiable by identifier.
            label2 = f"p1-4-rs2-{uuid4()}"
            async with sm() as s:
                fam2 = await _fresh_family(s, label2)
                hh = await _insert_signout(s, fam2, label2, 1, _snap(1), None)
                await _insert_signout(s, fam2, label2, 2, _snap(2), hh)
                await s.commit()
            async with sm() as s:
                await s.execute(
                    text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": fam2}
                )
                await s.commit()
                survived = await rss.verify_report_signout_chain(s, label2)
                assert survived.verified and survived.rows_checked == 2, survived

            # (d) Editing a chained identity field is caught by the chain itself.
            label3 = f"p1-4-rs3-{uuid4()}"
            async with sm() as s:
                fam3 = await _fresh_family(s, label3)
                await _insert_signout(s, fam3, label3, 1, _snap(1), None)
                await s.commit()
                await _bypass_trigger_mutate(
                    s, "report_signouts",
                    "UPDATE report_signouts SET signed_out_by = 'impostor' "
                    "WHERE family_identifier = :fl",
                    {"fl": label3},
                )
                tampered = await rss.verify_report_signout_chain(s, label3)
                assert not tampered.verified and "row_hash mismatch" in (tampered.reason or "")
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_report_signout_real_writer_chain_and_read_verification(monkeypatch) -> None:
    """Genuine positive control for the report-signout chain: drive the REAL
    sign_out_report (advisory-lock prev_hash read + the row_hash/prev_hash INSERT column
    mapping) against real Postgres, then verify the chain AND the on-read content
    re-verification (get_report_signout's ``verified`` flag) — the production paths the
    helper-built test cannot cover."""
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services import report_signout_service as rss

    holder: dict = {}

    async def _stub_ctx(session, *, family_identifier, user, project_id=None):
        return SimpleNamespace(
            family_uuid=holder["uuid"], family_id=holder["label"], assembly_name="GRCh38"
        )

    async def _stub_snapshot(session, *, family_id, user, project_id=None):
        # Minimal snapshot_body with the keys sign_out_report reads — incl. a large
        # integral float to exercise the JSONB-normalisation fix through the real writer.
        return {
            "drift": {"drifted_count": 0},
            "sample_qc": {"overall_status": "pass"},
            "software": {"version": "0.0.0-test", "git_sha": "deadbeef"},
            "reported_variants": [],
            "reported_structural_variants": [],
            "metrics": {"epoch_ns": 1.75e18},
        }

    monkeypatch.setattr(rss, "build_family_metadata_context", _stub_ctx)
    monkeypatch.setattr(rss, "build_report_snapshot", _stub_snapshot)

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"p1-4-rsw-{uuid4()}"
            async with sm() as s:
                holder["uuid"] = await _fresh_family(s, label)
                holder["label"] = label
                await s.commit()

            user = SimpleNamespace(username="signer", email="s@x.org", id=None)
            # Two real sign-outs → a two-version chain persisted by the production INSERT.
            async with sm() as s:
                await rss.sign_out_report(s, family_id=label, user=user)
            async with sm() as s:
                await rss.sign_out_report(s, family_id=label, user=user)

            async with sm() as s:
                chain = await rss.verify_report_signout_chain(s, label)
                assert chain.verified and chain.rows_checked == 2, chain
                # On-read re-verification: a clean row reads back verified=True.
                detail = await rss.get_report_signout(s, family_id=label, version=1, user=user)
                assert detail["verified"] is True, detail

            # Tamper the snapshot (content_hash unchanged) → on-read verified=False, but the
            # record is still returned (must not raise) so an auditor can inspect it.
            async with sm() as s:
                await _bypass_trigger_mutate(
                    s, "report_signouts",
                    "UPDATE report_signouts SET snapshot = "
                    "jsonb_set(snapshot, '{note}', '\"ALTERED\"') "
                    "WHERE family_identifier = :fl AND version = 1",
                    {"fl": label},
                )
                detail2 = await rss.get_report_signout(s, family_id=label, version=1, user=user)
                assert detail2["verified"] is False, detail2
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
