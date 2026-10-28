"""A family made again under its ID keeps every chain of the ID verifiable (TRACE-13, real Postgres).

A family ID's sign-outs are one hash chain, ordered by version, whichever family row signed
them. They were numbered per family row, so a family deleted and made again under its ID
signed version 1 again, and the chain failed its verification as if it had been tampered
with. Here the real writers run against Postgres: a family made by hand and signed out twice;
replaced by a family made by hand with overwrite (the Family Builder's replacement, as a PED
upload's: the old family deleted and a new one made under its ID, in one transaction) and
signed out twice; then deleted by an admin, made again and signed out once. Then:

- the five sign-outs are versions 1 to 5 of one chain that verifies, also through
  ``/admin/integrity/verify``, each linked to the one before it across both deletions;
- the family ID's clinical audit chain (both ``family_deleted`` events and the five
  ``sign_out`` events among its events) and its structure-version chain verify across the
  three families;
- each family's pedigree is its structure version 1, numbered per family row; the trail names
  them apart (ENG-19): each family made again names how many versions the deleted families
  stored before it, in its first event's metadata and summary and in its deletion;
- the five report codes differ; the lookup finds the current family's record and answers the
  deleted families' codes like an unknown code; the family's pages hold its own record only;
- a signed anchor records the chain's head at height 5 and finds it unchanged;
- the database refuses a second sign-out under a version the family ID holds, also under one
  a deleted family signed (``uq_report_signouts_family_identifier_version``);
- a deleted family's record changed behind the trigger's back breaks the chain at that record.

The sign-out's family context and snapshot body are stubbed: they read ClickHouse and the
annotation stack, which this test does not load. What numbers, chains, hashes and records a
sign-out, and finds it by its code, is the production code. The values are synthetic, under a
label of the run's own; the append-only rows stay. Skipped unless ``RUN_INTEGRATION=1`` (see
conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.app.core.config import settings
from backend.tests._signout_dialogs import no_finding_reviews

pytestmark = pytest.mark.integration


def _signing_seed() -> str:
    """A fresh Ed25519 seed for the anchor, made when the test runs."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    raw = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(raw).decode()


async def _admin(session: Any, run: str) -> Any:
    from datetime import datetime, timezone

    from backend.app.services.access_control import CurrentUser

    name = f"trace13-{run}"
    user_id = (
        await session.execute(
            text(
                "INSERT INTO users (username, hashed_password, role, email) "
                "VALUES (:u, :h, 'admin', :e) RETURNING id::text"
            ),
            {"u": name, "h": "unusable", "e": f"{name}@example.org"},
        )
    ).scalar_one()
    await session.commit()
    return CurrentUser(
        id=user_id, username=name, email=f"{name}@example.org", role="admin", created_at=datetime.now(timezone.utc)
    )


async def _rows(session: Any, sql: str, label: str) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(text(sql), {"f": label})).mappings().all()]


def test_a_family_made_again_under_its_id_keeps_its_chains_verifiable(monkeypatch) -> None:
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.routers.admin import verify_integrity_chain
    from backend.app.schemas import ManualPedFamilyCreate, ManualPedMemberCreate
    from backend.app.services import report_signout_service as rss
    from backend.app.services.admin_service import delete_family_with_data
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
    from backend.app.services.family_structure_audit import verify_family_structure_chain
    from backend.app.services.integrity_anchor_service import (
        create_integrity_anchor,
        verify_against_latest_anchor,
    )
    from backend.app.services.ped_service import create_manual_family_data
    from backend.app.services.report_code import report_code

    monkeypatch.setattr(settings, "integrity_anchor_signing_key", _signing_seed())
    run = uuid4().hex[:10]
    label = f"trace13-{run}"

    async def _context(session: Any, *, family_identifier: str, user: Any, project_id: Any = None) -> Any:
        # The family row that holds the ID now.
        family_uuid = (
            await session.execute(
                text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_identifier}
            )
        ).scalar_one_or_none()
        if family_uuid is None:
            raise HTTPException(status_code=404, detail="Family not found")
        return SimpleNamespace(family_uuid=family_uuid, family_id=family_identifier, assembly_name="GRCh38")

    async def _snapshot_body(session: Any, *, family_id: str, user: Any, project_id: Any = None) -> dict[str, Any]:
        # The keys sign_out_report reads, the drift lists its drift gate's refusal lists
        # among them (TRACE-19).
        return {
            "drift": {"drifted_count": 0, "drifted": []},
            "structural_drift": {"drifted_count": 0, "drifted": []},
            # No repeat or Paraphase review and no finding without one (finding_review_pg).
            "repeat_expansion_reviews": [],
            "paraphase_reviews": [],
            "finding_review_drift": {"checked": 0, "drifted_count": 0, "drifted": []},
            "unreviewed_findings": [],
            "sample_qc": {"overall_status": "pass"},
            "software": {"version": "0.0.0-test", "git_sha": "trace13"},
            "reported_variants": [],
            "reported_structural_variants": [],
        }

    monkeypatch.setattr(rss, "build_family_metadata_context", _context)
    # No repeat or Paraphase call, and no review of one (finding_review_pg).
    monkeypatch.setattr(rss, "finding_reviews_for_record", no_finding_reviews)
    monkeypatch.setattr(rss, "build_report_snapshot", _snapshot_body)

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                user = await _admin(s, run)

            async def _make_family(life: str, *, overwrite: bool) -> None:
                async with sm() as s:
                    await create_manual_family_data(
                        s,
                        ManualPedFamilyCreate(
                            family_id=label,
                            members=[
                                ManualPedMemberCreate(sample_id=f"{label}-{life}-P", sex="female", is_proband=True)
                            ],
                        ),
                        overwrite=overwrite,
                        user=user,
                    )

            async def _sign_out() -> dict[str, Any]:
                async with sm() as s:
                    return await rss.sign_out_report(s, family_id=label, user=user)

            # Three families under one ID: made by hand, replaced with overwrite, deleted by
            # an admin and made again; signed out twice, twice and once.
            await _make_family("first", overwrite=False)
            signed = [await _sign_out(), await _sign_out()]
            await _make_family("second", overwrite=True)
            signed += [await _sign_out(), await _sign_out()]
            async with sm() as s:
                await delete_family_with_data(s, label, True, user=user, reason="integration test")
            await _make_family("third", overwrite=False)
            signed.append(await _sign_out())

            async with sm() as s:
                current = (
                    await s.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": label})
                ).scalar_one()
                rows = await _rows(
                    s,
                    "SELECT version, family_id::text AS family_id, content_hash, row_hash, prev_hash "
                    "FROM report_signouts WHERE family_identifier = :f ORDER BY signed_out_at, version",
                    label,
                )
                chain = await rss.verify_report_signout_chain(s, label)
                endpoint = await verify_integrity_chain(
                    table="report_signouts", family_id=label, session=s, user=user
                )
                audit_chain = await verify_clinical_audit_chain(s, label)
                actions = [
                    row["action"]
                    for row in await _rows(
                        s,
                        "SELECT action FROM clinical_audit_events WHERE family_identifier = :f "
                        "ORDER BY created_at, id",
                        label,
                    )
                ]
                structure_chain = await verify_family_structure_chain(s, label)
                structure = await _rows(
                    s,
                    "SELECT family_id::text AS family_id, version, row_hash, prev_hash FROM family_structure_versions "
                    "WHERE family_identifier = :f ORDER BY created_at, id",
                    label,
                )
                pedigree_trail = await _rows(
                    s,
                    "SELECT action, summary, before, metadata FROM clinical_audit_events "
                    "WHERE family_identifier = :f AND action IN ('pedigree', 'family_deleted') "
                    "ORDER BY created_at, id",
                    label,
                )

            # Numbered per family ID, on from the deleted families' versions.
            assert [entry["version"] for entry in signed] == [1, 2, 3, 4, 5]
            assert [(row["version"], row["family_id"]) for row in rows] == [
                (1, None),
                (2, None),
                (3, None),
                (4, None),
                (5, current),
            ]
            # One chain, each record linked to the one before it, across both deletions.
            assert rows[0]["prev_hash"] is None
            assert [row["prev_hash"] for row in rows[1:]] == [row["row_hash"] for row in rows[:-1]]
            assert (chain.verified, chain.rows_checked) == (True, 5), chain
            assert (endpoint.verified, endpoint.rows_checked, endpoint.family_id) == (True, 5, label), endpoint

            # The clinical audit chain and the structure-version chain run on across the three
            # families too: each deletion is the last event of its family, the next family's
            # pedigree follows it.
            assert audit_chain.verified, audit_chain
            assert [action for action in actions if action in {"pedigree", "sign_out", "family_deleted"}] == [
                "pedigree",
                "sign_out",
                "sign_out",
                "family_deleted",
                "pedigree",
                "sign_out",
                "sign_out",
                "family_deleted",
                "pedigree",
                "sign_out",
            ]
            assert (structure_chain.verified, structure_chain.rows_checked) == (True, 3), structure_chain
            assert [row["family_id"] for row in structure] == [None, None, current]
            assert structure[0]["prev_hash"] is None
            assert [row["prev_hash"] for row in structure[1:]] == [row["row_hash"] for row in structure[:-1]]

            # Structure versions are numbered per family row: each family's pedigree is its
            # version 1. The trail names them apart (ENG-19): each family made again names how
            # many versions the families deleted under the ID stored before it, its first event
            # says so, and so does its deletion. The first family's are recorded as before.
            assert [row["version"] for row in structure] == [1, 1, 1]
            pedigree = [event for event in pedigree_trail if event["action"] == "pedigree"]
            assert [
                (event["metadata"].get("deleted_family_versions", 0), event["metadata"]["structure_version"])
                for event in pedigree
            ] == [(0, 1), (1, 1), (2, 1)]
            assert set(pedigree[0]["metadata"]) == {"source", "structure_version", "structure_hash"}
            assert "Made again" not in pedigree[0]["summary"]
            assert pedigree[1]["summary"].endswith(
                ". Made again under this family ID, after 1 structure version of a deleted family: "
                "its own start again at 1."
            )
            assert pedigree[2]["summary"].endswith(
                ". Made again under this family ID, after 2 structure versions of 2 deleted families: "
                "its own start again at 1."
            )
            deletions = [event["before"] for event in pedigree_trail if event["action"] == "family_deleted"]
            assert [
                (entry.get("deleted_family_versions", 0), entry["structure_version"]) for entry in deletions
            ] == [(0, 1), (1, 1)]

            # A version names one record of the family ID, and so does its report code.
            codes = [entry["report_code"] for entry in signed]
            assert codes == [report_code(label, row["version"], row["content_hash"]) for row in rows]
            assert len(set(codes)) == 5
            async with sm() as s:
                found = await rss.find_report_signout_by_code(s, code=codes[-1], user=user)
                assert (found["family_id"], found["version"]) == (label, 5)
                for code in codes[:-1]:
                    with pytest.raises(HTTPException) as unknown:
                        await rss.find_report_signout_by_code(s, code=code, user=user)
                    assert (unknown.value.status_code, unknown.value.detail) == (
                        404,
                        "No signed record has this report code.",
                    )
                # The family's pages hold its own record only.
                listed = await rss.list_report_signouts(s, family_id=label, user=user)
                assert [entry["version"] for entry in listed["signouts"]] == [5]
                detail = await rss.get_report_signout(s, family_id=label, version=5, user=user)
                assert detail["verified"] is True
                with pytest.raises(HTTPException) as earlier:
                    await rss.get_report_signout(s, family_id=label, version=1, user=user)
                assert earlier.value.status_code == 404

            # A signed anchor records the chain's head, and finds it unchanged.
            async with sm() as s:
                anchor = await create_integrity_anchor(s)
            heads = [
                head
                for head in anchor["signed_core"]["heads"]
                if head["family_identifier"] == label and head["table"] == "report_signouts"
            ]
            assert heads == [
                {
                    "family_identifier": label,
                    "table": "report_signouts",
                    "height": 5,
                    "head_row_hash": rows[-1]["row_hash"],
                }
            ]
            async with sm() as s:
                verdict = await verify_against_latest_anchor(s)
            assert not [entry for entry in verdict.diverged if entry["family_identifier"] == label], verdict

            # The database refuses a version the family ID holds, one a deleted family signed too.
            with pytest.raises(IntegrityError) as taken:
                async with sm() as s:
                    await s.execute(
                        text(
                            "INSERT INTO report_signouts (family_identifier, version, signed_out_by, "
                            "content_hash, snapshot) VALUES (:f, 3, 'trace13', 'not-a-record', '{}'::jsonb)"
                        ),
                        {"f": label},
                    )
                    await s.commit()
            assert "uq_report_signouts_family_identifier_version" in str(taken.value)

            # A deleted family's record changed behind the trigger's back (as the table's owner
            # can) breaks the chain at that record.
            async with sm() as s:
                await s.execute(text("ALTER TABLE report_signouts DISABLE TRIGGER USER"))
                await s.execute(
                    text(
                        "UPDATE report_signouts SET signed_out_by = 'impostor' "
                        "WHERE family_identifier = :f AND version = 2"
                    ),
                    {"f": label},
                )
                await s.execute(text("ALTER TABLE report_signouts ENABLE TRIGGER USER"))
                await s.commit()
            async with sm() as s:
                tampered = await rss.verify_report_signout_chain(s, label)
            assert (tampered.verified, tampered.first_bad_row) == (False, "2"), tampered
            assert tampered.reason == "row_hash mismatch (row content tampered)"
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
