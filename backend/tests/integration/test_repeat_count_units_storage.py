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
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.tests.integration._datastores import (
    TRGT_VCF_HEADER,
    admin_account,
    repeat_family_context,
    trgt_family,
)

pytestmark = pytest.mark.integration

_LOCUS = "FXS_FMR1"

_VCF = TRGT_VCF_HEADER + (
    "chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID=FXS_FMR1;END=147912111;MOTIFS=CGG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:90,159:30,53\n"
)


def test_the_record_marks_and_unmarks_a_count_near_a_boundary_survives_a_start_and_is_audited(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
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
                            "SELECT locus_id, validated, count_offset, reason, updated_by, updated_by_name, updated_at "
                            "FROM repeat_locus_count_units WHERE locus_id = :l"
                        ),
                        {"l": _LOCUS},
                    )
                ).mappings().first()
                prior = dict(prior) if prior is not None else None
                await session.execute(text("DELETE FROM repeat_locus_count_units WHERE locus_id = :l"), {"l": _LOCUS})
                username = f"{label}-qa"
                user = await admin_account(session, username)
                family_uuid, contexts = await trgt_family(session, label, {"WOMAN": "female"})
                await ingest_trgt_text(
                    session,
                    sample_context=contexts["WOMAN"],
                    text_value=_VCF.format(column=sample_id),
                    metadata={"source": "trgt", "filename": "woman.vcf"},
                )
                context = repeat_family_context(family_uuid, label, [(contexts["WOMAN"], "mother", False)])

                out["before"] = await _read(session, context)
                out["set"] = await set_repeat_locus_count_units(
                    session, locus_id=_LOCUS, validated=True, count_offset=0, reason="  Validation report 13  ", user=user
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
                    session, locus_id=_LOCUS, validated=False, count_offset=0, reason="New TRGT catalogue", user=user
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
                            "(locus_id, validated, count_offset, reason, updated_by, updated_by_name, updated_at) "
                            "VALUES (:locus_id, :validated, :count_offset, :reason, :updated_by, :updated_by_name, :updated_at)"
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
    assert events[0]["before"] == {
        "locus_id": _LOCUS,
        "gene": "FMR1",
        "validated": False,
        "count_offset": 0,
        "reason": None,
    }
    assert events[0]["after"] == {
        "locus_id": _LOCUS,
        "gene": "FMR1",
        "validated": True,
        "count_offset": 0,
        "reason": "Validation report 13",
    }
    assert events[1]["after"]["reason"] == "New TRGT catalogue"
    assert events[1]["summary"] == "TRGT's counting units at FXS_FMR1 (FMR1): validated → not validated"
    verified, rows_checked = out["chain"]
    assert verified is True and rows_checked >= 2
    assert out["refused"] == 2


# A man's HTT call of 18 and 36 (CLIN-57): TRGT reads HTT one above the usual count, so with QA's
# correction of -1 the 36 (pathogenic) is 35 (grey zone).
_HTT_LOCUS = "HD_HTT"
_HTT_VCF = TRGT_VCF_HEADER + (
    "chr4\t3074876\t.\tCAG\t<TR>\t.\t.\tTRID=HD_HTT;END=3074933;MOTIFS=CAG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:54,108:18,36\n"
)


def test_a_count_correction_reclassifies_the_table_the_track_and_the_import_and_is_audited(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
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

    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    label = f"itest-count-offset-{uuid4()}"
    sample_id = f"{label}-MAN"

    async def _read(session, context):
        table = await get_family_repeat_expansion_table_response(session, context=context)
        [locus] = [row for row in table.loci if row.locus_id == _HTT_LOCUS]
        alleles = locus.calls[sample_id].alleles
        track = await get_sample_repeat_expansion_track_response(
            session, context=context, sample_name=sample_id, chromosomes=["4"]
        )
        [item] = [row for row in track.items if row.locus_id == _HTT_LOCUS]
        return {
            "offset": (locus.count_offset, item.count_offset),
            "counts": [(allele.repeat_count, allele.trgt_repeat_count) for allele in alleles],
            "statuses": [allele.status for allele in alleles],
            "marked": [allele.count_unit_warning is not None for allele in alleles],
            "track": (
                item.allele_repeat_counts,
                item.allele_trgt_repeat_counts,
                item.status,
                len(item.count_unit_warnings),
            ),
            "reason": locus.count_units_reason,
        }

    async def _stored(session, sample_uuid):
        row = (
            await session.execute(
                text(
                    "SELECT status, alleles FROM repeat_expansions "
                    "WHERE sample_id = CAST(:s AS uuid) AND locus_id = :l"
                ),
                {"s": sample_uuid, "l": _HTT_LOCUS},
            )
        ).mappings().one()
        return row["status"], [allele["repeat_count"] for allele in row["alleles"]]

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
                            "SELECT locus_id, validated, count_offset, reason, updated_by, updated_by_name, "
                            "updated_at FROM repeat_locus_count_units WHERE locus_id = :l"
                        ),
                        {"l": _HTT_LOCUS},
                    )
                ).mappings().first()
                prior = dict(prior) if prior is not None else None
                await session.execute(
                    text("DELETE FROM repeat_locus_count_units WHERE locus_id = :l"), {"l": _HTT_LOCUS}
                )
                username = f"{label}-qa"
                user = await admin_account(session, username)
                family_uuid, contexts = await trgt_family(session, label, {"MAN": "male"})
                vcf = _HTT_VCF.format(column=sample_id)
                metadata = {"source": "trgt", "filename": "man.vcf"}
                await ingest_trgt_text(session, sample_context=contexts["MAN"], text_value=vcf, metadata=metadata)
                context = repeat_family_context(family_uuid, label, [(contexts["MAN"], "proband", True)])

                out["before"] = await _read(session, context)
                out["stored_before"] = await _stored(session, contexts["MAN"].sample_uuid)
                await set_repeat_locus_count_units(
                    session,
                    locus_id=_HTT_LOCUS,
                    validated=False,
                    count_offset=-1,
                    reason="Offset from validation report VAL-7",
                    user=user,
                )
                await seed_builtin_repeat_catalog(session)  # the next start keeps it
                out["corrected"] = await _read(session, context)
                # The call imported again with the correction recorded: TRGT's counts stored, the
                # status read from the corrected ones.
                await ingest_trgt_text(
                    session, sample_context=contexts["MAN"], text_value=vcf, metadata=metadata, replace=True
                )
                out["stored_after"] = await _stored(session, contexts["MAN"].sample_uuid)
                await set_repeat_locus_count_units(
                    session,
                    locus_id=_HTT_LOCUS,
                    validated=True,
                    count_offset=-1,
                    reason="Validation report VAL-8",
                    user=user,
                )
                out["validated"] = await _read(session, context)
                listed = await list_repeat_locus_count_units(session)
                [out["listed"]] = [locus for locus in listed["loci"] if locus["locus_id"] == _HTT_LOCUS]
                out["events"] = [
                    dict(row)
                    for row in (
                        await session.execute(
                            text(
                                "SELECT action, before, after, summary FROM clinical_audit_events "
                                "WHERE family_identifier = :chain AND actor = :actor "
                                "ORDER BY created_at ASC, id ASC"
                            ),
                            {"chain": REPEAT_COUNT_UNITS_AUDIT_CHAIN, "actor": username},
                        )
                    ).mappings().all()
                ]
                verification = await verify_clinical_audit_chain(session, REPEAT_COUNT_UNITS_AUDIT_CHAIN)
                out["chain"] = verification.verified

                # The table's check refuses a correction beyond 20 either way, and keeps the record.
                refused = 0
                for offset in (21, -21):
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                text("UPDATE repeat_locus_count_units SET count_offset = :o WHERE locus_id = :l"),
                                {"o": offset, "l": _HTT_LOCUS},
                            )
                    except IntegrityError:
                        refused += 1
                out["refused"] = refused
                out["kept"] = (
                    await session.execute(
                        text("SELECT count_offset FROM repeat_locus_count_units WHERE locus_id = :l"),
                        {"l": _HTT_LOCUS},
                    )
                ).scalar_one()
                await session.rollback()
                return out
        finally:
            async with sessionmaker() as session:
                await session.execute(
                    text("DELETE FROM repeat_locus_count_units WHERE locus_id = :l"), {"l": _HTT_LOCUS}
                )
                if prior is not None:
                    await session.execute(
                        text(
                            "INSERT INTO repeat_locus_count_units "
                            "(locus_id, validated, count_offset, reason, updated_by, updated_by_name, updated_at) "
                            "VALUES (:locus_id, :validated, :count_offset, :reason, :updated_by, "
                            ":updated_by_name, :updated_at)"
                        ),
                        prior,
                    )
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    out = asyncio.run(_run())

    # No record: TRGT's counts, the 36 pathogenic and marked (35 reads grey zone).
    assert out["before"] == {
        "offset": (0, 0),
        "counts": [(18, 18), (36, 36)],
        "statuses": ["normal", "pathogenic"],
        "marked": [False, True],
        "track": ([18, 36], [18, 36], "pathogenic", 1),
        "reason": None,
    }
    assert out["stored_before"] == ("pathogenic", [18, 36])
    # Corrected by -1, not validated: 35 is grey zone, still marked (36 is within the margin),
    # TRGT's own count beside it, on the table and the track alike; kept by the next start.
    assert out["corrected"] == {
        "offset": (-1, -1),
        "counts": [(17, 18), (35, 36)],
        "statuses": ["normal", "intermediate"],
        "marked": [False, True],
        "track": ([17, 35], [18, 36], "intermediate", 1),
        "reason": "Offset from validation report VAL-7",
    }
    # The import stores TRGT's counts, and reads its status from the corrected ones.
    assert out["stored_after"] == ("intermediate", [18, 36])
    # Validated with the correction: the same statuses, no mark.
    assert out["validated"]["statuses"] == ["normal", "intermediate"]
    assert out["validated"]["marked"] == [False, False]
    assert out["validated"]["track"] == ([17, 35], [18, 36], "intermediate", 0)
    assert (out["listed"]["count_offset"], out["listed"]["validated"], out["listed"]["offset_note"]) == (-1, True, None)
    # Two events with the correction before and after, chained and verifiable.
    events = out["events"]
    assert [event["action"] for event in events] == [
        "repeat_count_units_not_validated",
        "repeat_count_units_validated",
    ]
    assert [(event["before"]["count_offset"], event["after"]["count_offset"]) for event in events] == [
        (0, -1),
        (-1, -1),
    ]
    assert events[0]["summary"] == (
        "TRGT's counting units at HD_HTT (HTT): correction no correction → TRGT's count −1"
    )
    assert events[1]["summary"] == "TRGT's counting units at HD_HTT (HTT): not validated → validated"
    assert out["chain"] is True
    assert (out["refused"], out["kept"]) == (2, -1)
