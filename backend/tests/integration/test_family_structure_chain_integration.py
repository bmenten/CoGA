"""A family's pedigree changes are chained structure versions and clinical audit events (real Postgres).

SEC-7: each change of a family's pedigree is stored as its next structure version, on a hash
chain per family that the append-only trigger protects, and recorded on the family's
clinical audit trail as a ``pedigree`` event with what changed, before and after. A
compound-het pair review is a clinical audit event too. Here the real writers run against
Postgres: a family made by hand, a member edit that renames the proband and makes it
affected, a member removed in the editor and a sample deleted with its data. Then:

- the family holds four structure versions on an intact chain, and four ``pedigree`` events
  on its intact clinical audit chain, each naming its change;
- a family delete that fails part-way commits nothing; the family's deletion is the last
  event of its chain (``family_deleted``: who, its samples, what went with it), and both chains
  stay (``family_id`` nulled), still verifiable by the family's identifier, also through the
  admin's verify endpoint;
- a signed anchor records the structure chain's head;
- an UPDATE or DELETE of a structure version is refused, and a version edited behind the
  trigger's back breaks the chain at that version;
- compound-het pair events round-trip through Postgres on an intact chain.

The values are synthetic, under labels of the run's own; the append-only rows stay.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.core.config import settings

pytestmark = pytest.mark.integration


def _signing_seed() -> str:
    """A fresh Ed25519 seed for the anchor, made when the test runs."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    raw = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(raw).decode()


async def _user(session: Any, run: str) -> Any:
    from backend.app.services.access_control import CurrentUser

    name = f"sec7-{run}"
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


async def _pedigree_events(session: Any, label: str) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT summary, actor, before, after, metadata, family_id::text AS family_id "
            "FROM clinical_audit_events WHERE family_identifier = :f AND action = 'pedigree' "
            "ORDER BY created_at, id"
        ),
        {"f": label},
    )
    return [dict(row) for row in rows.mappings().all()]


async def _family_deleted_events(session: Any, label: str) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT summary, actor, before, after, metadata, family_id::text AS family_id "
            "FROM clinical_audit_events WHERE family_identifier = :f AND action = 'family_deleted' "
            "ORDER BY created_at, id"
        ),
        {"f": label},
    )
    return [dict(row) for row in rows.mappings().all()]


async def _versions(session: Any, label: str) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT id::text AS id, version, family_id::text AS family_id, row_hash, prev_hash "
            "FROM family_structure_versions WHERE family_identifier = :f ORDER BY created_at, id"
        ),
        {"f": label},
    )
    return [dict(row) for row in rows.mappings().all()]


async def _refused(sm: Any, sql: str, params: dict[str, Any]) -> str:
    with pytest.raises(DBAPIError) as refusal:
        async with sm() as session:
            await session.execute(text(sql), params)
            await session.commit()
    return str(refusal.value)


def test_pedigree_changes_are_chained_versions_and_clinical_audit_events(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.routers.admin import verify_integrity_chain
    from backend.app.schemas import FamilyMemberUpdate, ManualPedFamilyCreate, ManualPedMemberCreate
    from backend.app.services import admin_service
    from backend.app.services.admin_service import delete_family_with_data, delete_sample_with_data
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
    from backend.app.services.family_member_management_service import (
        delete_family_member_for_admin,
        update_family_member_for_admin,
    )
    from backend.app.services.family_structure_audit import verify_family_structure_chain
    from backend.app.services.integrity_anchor_service import (
        create_integrity_anchor,
        verify_against_latest_anchor,
    )
    from backend.app.services.ped_service import create_manual_family_data

    monkeypatch.setattr(settings, "integrity_anchor_signing_key", _signing_seed())
    run = uuid4().hex[:10]
    label = f"sec7-{run}"
    father, mother, child = (f"{label}-{name}" for name in ("F", "M", "P"))

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                user = await _user(s, run)

            # The family made by hand, then three changes of its pedigree.
            async with sm() as s:
                await create_manual_family_data(
                    s,
                    ManualPedFamilyCreate(
                        family_id=label,
                        members=[
                            ManualPedMemberCreate(sample_id=father, sex="male", clinical_status="unaffected"),
                            ManualPedMemberCreate(sample_id=mother, sex="female", clinical_status="unaffected"),
                            ManualPedMemberCreate(
                                sample_id=child, father_id=father, mother_id=mother, sex="female", is_proband=True
                            ),
                        ],
                    ),
                    overwrite=False,
                    user=user,
                )
            async with sm() as s:
                await update_family_member_for_admin(
                    s,
                    family_id=label,
                    sample_id=child,
                    update=FamilyMemberUpdate(sample_id=f"{child}2", clinical_status="affected"),
                    user=user,
                )
            async with sm() as s:
                await delete_family_member_for_admin(s, family_id=label, sample_id=mother, confirmed=True, user=user)
            async with sm() as s:
                await delete_sample_with_data(s, father, True, user=user)

            async with sm() as s:
                events = await _pedigree_events(s, label)
                versions = await _versions(s, label)
                structure_chain = await verify_family_structure_chain(s, label)
                audit_chain = await verify_clinical_audit_chain(s, label)

            assert [event["summary"] for event in events] == [
                f"Pedigree recorded with 3 members: {father} (father, male, unaffected); "
                f"{mother} (mother, female, unaffected); {child} (proband, female, affected status unknown)",
                f"Pedigree changed: {child} renamed {child}2: affected status unknown → affected",
                f"Pedigree changed: {mother} made inactive; {child}2: mother {mother} → none; "
                f"couple {father} + {mother} removed",
                f"Pedigree changed by deleting a sample with its data: {child}2: father {father} → none; "
                f"{father} deleted",
            ]
            assert {event["actor"] for event in events} == {user.username}
            rename = events[1]
            assert rename["before"]["members"][f"{child}2"]["sample_id"] == child
            assert rename["before"]["members"][f"{child}2"]["clinical_status"] == "unknown"
            assert rename["after"]["members"][f"{child}2"]["clinical_status"] == "affected"
            assert [event["metadata"]["source"] for event in events] == [
                "family_created",
                "structure_edit",
                "structure_edit",
                "sample_deleted",
            ]
            assert [version["version"] for version in versions] == [1, 2, 3, 4]
            assert [event["metadata"]["structure_version"] for event in events] == [1, 2, 3, 4]
            assert versions[0]["prev_hash"] is None
            assert [version["prev_hash"] for version in versions[1:]] == [version["row_hash"] for version in versions[:-1]]
            assert (structure_chain.verified, structure_chain.rows_checked) == (True, 4), structure_chain
            assert audit_chain.verified, audit_chain

            # A signed anchor records the structure chain's head, and finds it unchanged.
            async with sm() as s:
                anchor = await create_integrity_anchor(s)
            heads = [
                head
                for head in anchor["signed_core"]["heads"]
                if head["family_identifier"] == label and head["table"] == "family_structure_versions"
            ]
            assert heads == [
                {
                    "family_identifier": label,
                    "table": "family_structure_versions",
                    "height": 4,
                    "head_row_hash": versions[-1]["row_hash"],
                }
            ]
            async with sm() as s:
                verdict = await verify_against_latest_anchor(s)
            assert not [entry for entry in verdict.diverged if entry["family_identifier"] == label], verdict

            # A delete that fails part-way commits nothing: no event, the family as it was.
            async def fails(*_args: Any, **_kwargs: Any) -> int:
                raise RuntimeError("purge failed")

            monkeypatch.setattr(admin_service, "purge_family_managed_files", fails)
            with pytest.raises(RuntimeError, match="purge failed"):
                async with sm() as s:
                    await delete_family_with_data(s, label, True, user=user)
            monkeypatch.undo()
            async with sm() as s:
                still_there = (
                    await s.execute(text("SELECT count(*) FROM families WHERE family_id = :f"), {"f": label})
                ).scalar_one()
                deletions = await _family_deleted_events(s, label)
            assert (still_there, deletions) == (1, [])

            # The family deleted: its deletion is the last event of its chain, and both chains
            # stay, unlinked from it and still verifiable.
            async with sm() as s:
                await delete_family_with_data(s, label, True, user=user)
            async with sm() as s:
                kept = await _versions(s, label)
                events_kept = await _pedigree_events(s, label)
                deletions = await _family_deleted_events(s, label)
                last_action = (
                    await s.execute(
                        text(
                            "SELECT action FROM clinical_audit_events WHERE family_identifier = :f "
                            "ORDER BY created_at DESC, id DESC LIMIT 1"
                        ),
                        {"f": label},
                    )
                ).scalar_one()
                endpoint = await verify_integrity_chain(
                    table="family_structure_versions", family_id=label, session=s, user=user
                )
                audit_after = await verify_clinical_audit_chain(s, label)
            assert [(row["id"], row["family_id"]) for row in kept] == [(row["id"], None) for row in versions]
            assert len(events_kept) == 4 and {event["family_id"] for event in events_kept} == {None}
            [deletion] = deletions
            assert last_action == "family_deleted"
            assert (deletion["actor"], deletion["family_id"], deletion["after"]) == (user.username, None, None)
            assert deletion["summary"] == (
                f"Family deleted with its data: 2 samples ({mother}, {child}2), 0 small variants, "
                "0 structural variants, 0 variant reviews"
            )
            assert deletion["before"]["samples"] == [mother, f"{child}2"]
            assert deletion["before"]["structure_version"] == 4
            assert deletion["metadata"] == {"source": "admin_delete"}
            assert (endpoint.verified, endpoint.rows_checked, endpoint.family_id) == (True, 4, label), endpoint
            assert audit_after.verified and audit_after.rows_checked == 5, audit_after

            # Append-only: no UPDATE and no DELETE of a version.
            second = versions[1]["id"]
            assert "family_structure_versions is append-only; UPDATE is not permitted" in await _refused(
                sm,
                "UPDATE family_structure_versions SET structure_hash = 'edited' WHERE id = CAST(:id AS uuid)",
                {"id": second},
            )
            assert "family_structure_versions is append-only; DELETE is not permitted" in await _refused(
                sm, "DELETE FROM family_structure_versions WHERE id = CAST(:id AS uuid)", {"id": second}
            )

            # Edited behind the trigger's back (as the table's owner can): the chain breaks there.
            async with sm() as s:
                await s.execute(text("ALTER TABLE family_structure_versions DISABLE TRIGGER USER"))
                await s.execute(
                    text(
                        "UPDATE family_structure_versions SET snapshot = jsonb_set(snapshot, '{members,0,sex}', "
                        "to_jsonb(CAST('female' AS text))) WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": second},
                )
                await s.execute(text("ALTER TABLE family_structure_versions ENABLE TRIGGER USER"))
                await s.commit()
            async with sm() as s:
                tampered = await verify_family_structure_chain(s, label)
            assert (tampered.verified, tampered.first_bad_row) == (False, second), tampered
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_compound_het_pair_events_round_trip_on_an_intact_chain() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.clinical_audit_service import (
        CompoundHetPairChange,
        compound_het_pair_state,
        record_compound_het_pair_changes,
        verify_clinical_audit_chain,
    )

    run = uuid4().hex[:10]
    label = f"sec7-pair-{run}"
    user = type("User", (), {"id": None, "username": f"sec7-{run}", "email": f"sec7-{run}@example.org"})()

    def pair(partner: str, **fields: Any) -> dict[str, Any] | None:
        return compound_het_pair_state(
            {
                "compound_het_group_id": "group-1",
                "compound_het_partner_variant_ids": [partner],
                "compound_het_gene": "GENE1",
                "compound_het_classification": "Likely pathogenic",
                "compound_het_tags": ["report"],
                "compound_het_note": "in trans",
                "compound_het_phase_status": "unknown",
                **fields,
            }
        )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                family_uuid = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
                    )
                ).scalar_one()
                await record_compound_het_pair_changes(
                    s,
                    family_uuid=family_uuid,
                    family_identifier=label,
                    user=user,
                    changes=[
                        CompoundHetPairChange("1-100-A-G", None, pair("1-200-C-T"), "group-1"),
                        CompoundHetPairChange(
                            "1-100-A-G", pair("1-200-C-T"), pair("1-200-C-T", compound_het_classification="Pathogenic"), "group-1"
                        ),
                        CompoundHetPairChange("1-100-A-G", pair("1-200-C-T"), pair("1-200-C-T"), "group-1"),
                        CompoundHetPairChange("1-100-A-G", pair("1-200-C-T"), None, "group-1"),
                    ],
                )
                await s.commit()
            async with sm() as s:
                rows = (
                    await s.execute(
                        text(
                            "SELECT variant_id, summary, before, after, metadata FROM clinical_audit_events "
                            "WHERE family_identifier = :f ORDER BY created_at, id"
                        ),
                        {"f": label},
                    )
                ).mappings().all()
                chain = await verify_clinical_audit_chain(s, label)
            assert [row["summary"] for row in rows] == [
                "Compound-het pair with 1-200-C-T recorded: gene GENE1; classification Likely pathogenic; "
                "tags report; note added",
                "Compound-het pair with 1-200-C-T: classification Likely pathogenic → Pathogenic",
                "Compound-het pair with 1-200-C-T removed",
            ]
            assert {row["variant_id"] for row in rows} == {"1-100-A-G"}
            assert rows[0]["after"]["partner_variant_ids"] == ["1-200-C-T"]
            assert rows[2]["after"] is None and rows[2]["metadata"] == {"compound_het_group_id": "group-1"}
            assert (chain.verified, chain.rows_checked) == (True, 3), chain
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
