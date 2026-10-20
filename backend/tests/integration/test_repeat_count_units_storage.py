"""Integration test: QA's record of a repeat locus's TRGT counting units, on Postgres.

TRGT's count need not be the clinical repeat number (at FMR1 it leaves out the AGG
interruptions), so until QA validates a catalogue locus, the family table and the genome track
mark a count within 3 repeats of a boundary there (owner's decision, 2026-10-14). The record
lives in ``repeat_locus_count_units``, beside the catalogue the seed rewrites at every start,
and an admin sets it with a reason, each change a hash-chained clinical audit event on chain
``system:repeat-loci``.

On a family of its own, with a woman whose FMR1 alleles are 30 and 53 (the second a grey-zone
count a premutation of 55 can read as): the mark before the record, none after it, the record
kept by the next start, the event chained and verifiable, the mark back after a withdrawal;
and the table refuses a blank reason and a locus the catalogue lacks. FXS_FMR1's record is put
back as it was. Only Postgres is needed. Skipped unless ``RUN_INTEGRATION=1`` (see
conftest.py); the CI ``smoke`` job sets it. All samples and values are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

_LOCUS = "FXS_FMR1"

_VCF = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
    "chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID=FXS_FMR1;END=147912111;MOTIFS=CGG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:90,159:30,53\n"
)


def test_the_record_marks_and_unmarks_a_count_near_a_boundary_survives_a_start_and_is_audited(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.access_control import CurrentUser
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
    from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
    from backend.app.services.repeat_count_units import (
        REPEAT_COUNT_UNITS_AUDIT_CHAIN,
        list_repeat_locus_count_units,
        set_repeat_locus_count_units,
    )
    from backend.app.services.repeat_expansion_pg import (
        get_family_repeat_expansion_table_response,
        get_sample_repeat_expansion_track_response,
        ingest_trgt_text,
        seed_builtin_repeat_catalog,
    )

    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    label = f"itest-count-units-{uuid4()}"
    sample_id = f"{label}-WOMAN"

    async def _read(session, context):
        table = await get_family_repeat_expansion_table_response(session, context=context)
        [locus] = [row for row in table.loci if row.locus_id == _LOCUS]
        alleles = locus.calls[sample_id].alleles
        track = await get_sample_repeat_expansion_track_response(
            session, context=context, sample_name=sample_id, chromosomes=["X"]
        )
        [item] = [row for row in track.items if row.locus_id == _LOCUS]
        return {
            "table_validated": locus.count_units_validated,
            "table_marks": [
                None
                if allele.count_unit_warning is None
                else [(b.repeat_count, b.status) for b in allele.count_unit_warning.boundaries]
                for allele in alleles
            ],
            "table_statuses": [allele.status for allele in alleles],
            "track_validated": item.count_units_validated,
            "track_marks": [
                (warning.repeat_count, [(b.repeat_count, b.status) for b in warning.boundaries])
                for warning in item.count_unit_warnings
            ],
            "track_status": item.status,
        }

    async def _run() -> dict:
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        out: dict = {}
        prior = None
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                prior = (
                    await session.execute(
                        text(
                            "SELECT locus_id, validated, reason, updated_by, updated_by_name, updated_at "
                            "FROM repeat_locus_count_units WHERE locus_id = :l"
                        ),
                        {"l": _LOCUS},
                    )
                ).mappings().first()
                prior = dict(prior) if prior is not None else None
                await session.execute(text("DELETE FROM repeat_locus_count_units WHERE locus_id = :l"), {"l": _LOCUS})
                username = f"{label}-qa"
                user_id = (
                    await session.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:u, 'x', 'admin', :e) RETURNING id::text"
                        ),
                        {"u": username, "e": f"{username}@example.org"},
                    )
                ).scalar_one()
                user = CurrentUser(
                    id=user_id,
                    username=username,
                    email=f"{username}@example.org",
                    role="admin",
                    created_at=datetime.now(timezone.utc),
                )
                family_uuid = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
                    )
                ).scalar_one()
                sample_uuid = (
                    await session.execute(
                        text(
                            "INSERT INTO samples (sample_id, family_id, sex) "
                            "VALUES (:s, CAST(:f AS uuid), 'female') RETURNING id::text"
                        ),
                        {"s": sample_id, "f": family_uuid},
                    )
                ).scalar_one()
                await session.commit()
                await ingest_trgt_text(
                    session,
                    sample_context=SampleMetadataContext(
                        sample_uuid=str(sample_uuid),
                        sample_id=sample_id,
                        family_uuid=str(family_uuid),
                        family_id=label,
                        sex="female",
                        project_ids=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    ),
                    text_value=_VCF.format(column=sample_id),
                    metadata={"source": "trgt", "filename": "woman.vcf"},
                )
                context = FamilyMetadataContext(
                    family_uuid=str(family_uuid),
                    family_id=label,
                    project_ids=[],
                    sample_rows=[
                        {"sample_uuid": str(sample_uuid), "sample_id": sample_id, "role": "mother", "affected": False, "sex": "female"}
                    ],
                    sample_uuid_to_name={str(sample_uuid): sample_id},
                    sample_name_to_uuid={sample_id: str(sample_uuid)},
                    affected_sample_names=[],
                    assembly_id=None,
                    assembly_name="GRCh38",
                )

                out["before"] = await _read(session, context)
                out["set"] = await set_repeat_locus_count_units(
                    session, locus_id=_LOCUS, validated=True, reason="  Validation report 13  ", user=user
                )
                await seed_builtin_repeat_catalog(session)  # the next start
                out["after_start"] = await _read(session, context)
                out["record"] = dict(
                    (
                        await session.execute(
                            text(
                                "SELECT validated, reason, updated_by::text AS updated_by, updated_by_name "
                                "FROM repeat_locus_count_units WHERE locus_id = :l"
                            ),
                            {"l": _LOCUS},
                        )
                    ).mappings().one()
                )
                listed = await list_repeat_locus_count_units(session)
                [out["listed"]] = [locus for locus in listed["loci"] if locus["locus_id"] == _LOCUS]
                out["listed_change"] = next(
                    change for change in listed["changes"] if change["changed_by"] == username
                )

                await set_repeat_locus_count_units(
                    session, locus_id=_LOCUS, validated=False, reason="New TRGT catalogue", user=user
                )
                out["withdrawn"] = await _read(session, context)
                out["events"] = [
                    dict(row)
                    for row in (
                        await session.execute(
                            text(
                                "SELECT action, actor, actor_id::text AS actor_id, family_id, variant_id, "
                                "before, after, summary FROM clinical_audit_events "
                                "WHERE family_identifier = :chain AND actor = :actor "
                                "ORDER BY created_at ASC, id ASC"
                            ),
                            {"chain": REPEAT_COUNT_UNITS_AUDIT_CHAIN, "actor": username},
                        )
                    ).mappings().all()
                ]
                verification = await verify_clinical_audit_chain(session, REPEAT_COUNT_UNITS_AUDIT_CHAIN)
                out["chain"] = (verification.verified, verification.rows_checked)

                # The table itself refuses a blank reason and a locus the catalogue lacks.
                refused = []
                for locus_id, reason in ((_LOCUS, "   "), (f"NO_SUCH_{label}", "checked")):
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                text(
                                    "INSERT INTO repeat_locus_count_units (locus_id, validated, reason) "
                                    "VALUES (:l, true, :r) "
                                    "ON CONFLICT (locus_id) DO UPDATE SET reason = EXCLUDED.reason"
                                ),
                                {"l": locus_id, "r": reason},
                            )
                    except IntegrityError as exc:
                        refused.append(type(exc.orig).__name__ if exc.orig is not None else "IntegrityError")
                out["refused"] = len(refused)
                await session.rollback()
                return out
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM repeat_locus_count_units WHERE locus_id = :l"), {"l": _LOCUS})
                if prior is not None:
                    await session.execute(
                        text(
                            "INSERT INTO repeat_locus_count_units "
                            "(locus_id, validated, reason, updated_by, updated_by_name, updated_at) "
                            "VALUES (:locus_id, :validated, :reason, :updated_by, :updated_by_name, :updated_at)"
                        ),
                        prior,
                    )
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    out = asyncio.run(_run())

    # Not validated: 53 (grey zone) is marked with the premutation at 55, 30 is not; the
    # statuses are the classification's, on the table and on the track.
    assert out["before"] == {
        "table_validated": False,
        "table_marks": [None, [(55, "premutation")]],
        "table_statuses": ["normal", "intermediate"],
        "track_validated": False,
        "track_marks": [(53, [(55, "premutation")])],
        "track_status": "intermediate",
    }
    # Validated, and kept by the next start: nothing is marked, the statuses are the same.
    assert out["after_start"] == {
        "table_validated": True,
        "table_marks": [None, None],
        "table_statuses": ["normal", "intermediate"],
        "track_validated": True,
        "track_marks": [],
        "track_status": "intermediate",
    }
    assert out["set"]["validated"] is True and out["set"]["reason"] == "Validation report 13"
    assert out["record"]["validated"] is True
    assert out["record"]["reason"] == "Validation report 13"
    assert out["record"]["updated_by"] is not None
    assert out["record"]["updated_by_name"].endswith("-qa")
    assert (out["listed"]["validated"], out["listed"]["source"]) == (True, "STRchive")
    # This family's call at least (another test's family may hold FXS_FMR1 calls too).
    assert out["listed"]["call_count"] >= 1
    assert "AGG" in out["listed"]["known_difference"]
    assert (out["listed_change"]["locus_id"], out["listed_change"]["validated"]) == (_LOCUS, True)
    assert out["listed_change"]["reason"] == "Validation report 13"
    # Withdrawn: marked again.
    assert out["withdrawn"]["table_marks"] == [None, [(55, "premutation")]]
    assert out["withdrawn"]["track_marks"] == [(53, [(55, "premutation")])]
    # Two events on the record's own chain, in order, with both sides and the reasons; no family.
    events = out["events"]
    assert [event["action"] for event in events] == [
        "repeat_count_units_validated",
        "repeat_count_units_not_validated",
    ]
    assert {(event["family_id"], event["variant_id"]) for event in events} == {(None, None)}
    assert all(event["actor_id"] == out["record"]["updated_by"] for event in events)
    assert events[0]["before"] == {"locus_id": _LOCUS, "gene": "FMR1", "validated": False, "reason": None}
    assert events[0]["after"] == {"locus_id": _LOCUS, "gene": "FMR1", "validated": True, "reason": "Validation report 13"}
    assert events[1]["after"]["reason"] == "New TRGT catalogue"
    assert events[1]["summary"] == "TRGT's counting units at FXS_FMR1 (FMR1): validated → not validated"
    verified, rows_checked = out["chain"]
    assert verified is True and rows_checked >= 2
    assert out["refused"] == 2
