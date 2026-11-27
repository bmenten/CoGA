"""A review's note is stored with who wrote it and when, over real Postgres.

Each review table holds ``note_updated_by`` and ``note_updated_at`` beside the note, set exactly
when the note is (``*_note_author_check``): a row with a note and no author, or an author and
no note, is refused. The real writer records the saver for a note it writes or changes and keeps
the author of a note it leaves as it is, while the review's own ``updated_by`` is its last
saver's (review_pg_utils._note_authorship).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

REVIEW_TABLES = ("small_variant_reviews", "structural_variant_reviews", "repeat_expansion_reviews", "paraphase_reviews")


async def _family(session) -> str:
    return (
        await session.execute(
            text("INSERT INTO families (family_id, metadata) VALUES (:f, CAST(:m AS jsonb)) RETURNING id::text"),
            {"f": f"note-author-{uuid4()}", "m": json.dumps({})},
        )
    ).scalar_one()


def test_every_review_table_refuses_a_note_without_its_author() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                checks = (
                    await s.execute(
                        text("SELECT conname FROM pg_constraint WHERE conname = ANY(CAST(:names AS text[]))"),
                        {"names": [f"{table}_note_author_check" for table in REVIEW_TABLES]},
                    )
                ).scalars().all()
            assert sorted(checks) == sorted(f"{table}_note_author_check" for table in REVIEW_TABLES)

            for note, author, at in (("unsigned", None, None), (None, "analyst", None), ("half", "analyst", None)):
                async with sm() as s:
                    family_uuid = await _family(s)
                    with pytest.raises(IntegrityError, match="small_variant_reviews_note_author_check"):
                        await s.execute(
                            text(
                                "INSERT INTO small_variant_reviews "
                                "(family_id, analysis, variant_id, note, note_updated_by, note_updated_at, updated_by) "
                                "VALUES (CAST(:f AS uuid), '', '1-100-A-G', :note, :by, "
                                "CAST(:at AS timestamptz), 'analyst')"
                            ),
                            {"f": family_uuid, "note": note, "by": author, "at": at},
                        )
                    await s.rollback()
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_the_note_keeps_its_writer_through_another_reviewer_s_save() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.schemas import StructuralVariantReviewUpdate
    from backend.app.services import structural_variant_review_pg as strv
    from backend.app.services.analysis_scope import AnalysisScope

    sv = "1-100000-250000-DEL---"

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                family_uuid = await _family(s)
                await s.commit()
            context = SimpleNamespace(
                family_uuid=family_uuid,
                family_id="FAM",
                project_ids=[],
                assembly_name=None,
                scope=AnalysisScope(),
            )

            async def _save(username: str, payload: StructuralVariantReviewUpdate):
                async with sm() as s:
                    return await strv.upsert_structural_variant_review(
                        s,
                        context=context,  # type: ignore[arg-type]
                        variant_id=sv,
                        payload=payload,
                        user=SimpleNamespace(id=None, username=username, email=f"{username}@x.org"),  # type: ignore[arg-type]
                    )

            written = await _save("analyst", StructuralVariantReviewUpdate(note="Breakpoints checked."))
            assert (written.note_updated_by, written.note_updated_at) == ("analyst", written.updated_at)

            classified = await _save(
                "supervisor",
                StructuralVariantReviewUpdate(note="Breakpoints checked.", classification="Pathogenic - class 5"),
            )
            assert (classified.updated_by, classified.note_updated_by, classified.note_updated_at) == (
                "supervisor",
                "analyst",
                written.note_updated_at,
            )

            changed = await _save(
                "supervisor",
                StructuralVariantReviewUpdate(note="Breakpoints confirmed.", classification="Pathogenic - class 5"),
            )
            assert (changed.note_updated_by, changed.note_updated_at) == ("supervisor", changed.updated_at)

            cleared = await _save("supervisor", StructuralVariantReviewUpdate(classification="Pathogenic - class 5"))
            assert (cleared.note, cleared.note_updated_by, cleared.note_updated_at) == (None, None, None)
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
