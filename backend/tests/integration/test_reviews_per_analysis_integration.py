"""A review is one analysis's, over real Postgres (analysis_reviews.py).

A family holding a short-read and a long-read analysis of the same DNA keeps a review per analysis
of a variant (the owner's decision of 2026-11-20): the unique index is per family, analysis and
variant; the real writer stores a save in the analysis it names; each analysis reads its own
review, else the one made while the family held no analysis; a save over that shared review
writes the analysis's own and leaves the shared one as it is; a cleared review is kept empty over
it. The Variant Explorer's condition, evaluated by Postgres, keeps every review in effect in one
of the family's analyses. Only the variant's ClickHouse record is stubbed.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

SR, LR = "short_read_wgs", "long_read_wgs"
A, B, C, D = "1-1000-A-G", "1-2000-C-T", "1-3000-G-A", "1-4000-T-C"


async def _family(session, label: str, analyses: list[str]) -> str:
    return (
        await session.execute(
            text("INSERT INTO families (family_id, metadata) VALUES (:f, CAST(:m AS jsonb)) RETURNING id::text"),
            {"f": label, "m": json.dumps({"analysis_types": analyses})},
        )
    ).scalar_one()


async def _shared(session, family_uuid: str, variant_id: str, tags: list[str]) -> None:
    """A review made while the family held no analysis (stored under none), with who added each
    tag, as every save records it."""
    metadata = {tag: {"updated_by": "analyst-b", "updated_at": "2026-11-01T09:00:00+00:00"} for tag in tags}
    await session.execute(
        text(
            "INSERT INTO small_variant_reviews (family_id, analysis, variant_id, tags, tag_metadata, updated_by) "
            "VALUES (CAST(:f AS uuid), '', :v, CAST(:t AS jsonb), CAST(:m AS jsonb), 'analyst-b')"
        ),
        {"f": family_uuid, "v": variant_id, "t": json.dumps(tags), "m": json.dumps(metadata)},
    )


async def _stored(session, family_uuid: str) -> dict[tuple[str, str], list[str]]:
    rows = (
        await session.execute(
            text("SELECT analysis, variant_id, tags FROM small_variant_reviews WHERE family_id = CAST(:f AS uuid)"),
            {"f": family_uuid},
        )
    ).mappings().all()
    return {(row["analysis"], row["variant_id"]): list(row["tags"]) for row in rows}


def test_each_analysis_keeps_and_reads_its_own_review(monkeypatch) -> None:
    from sqlalchemy.exc import IntegrityError

    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.schemas import SmallVariantReviewUpdate
    from backend.app.services import small_variant_review_pg as svr
    from backend.app.services.analysis_scope import AnalysisScope

    held = frozenset({SR, LR})
    scopes = {analysis: AnalysisScope(analysis=analysis, held=held) for analysis in (SR, LR)}

    async def _variant_record(*, assembly_name, family_guid, variant_id, left_out_sources=()):
        return SimpleNamespace(variant_key=int(variant_id.split("-")[1]), gene_symbols=[])

    async def _tags(*_args, **_kwargs):
        return [SimpleNamespace(key=key) for key in ("report", "review")]

    monkeypatch.setattr(svr, "get_small_variant_family_record", _variant_record)
    monkeypatch.setattr(svr, "list_small_variant_tag_definitions", _tags)
    user = SimpleNamespace(id=None, username="reviewer", email="reviewer@x.org")

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"reviews-per-analysis-{uuid4()}"
            async with sm() as s:
                family_uuid = await _family(s, label, [LR, SR])
                await _shared(s, family_uuid, C, ["report"])
                await _shared(s, family_uuid, D, ["report"])
                await s.commit()

            def _context(analysis: str) -> SimpleNamespace:
                return SimpleNamespace(
                    family_uuid=family_uuid,
                    family_id=label,
                    project_ids=[],
                    assembly_name="GRCh38",
                    assembly_id=None,
                    affected_sample_names=[],
                    scope=scopes[analysis],
                )

            async def _save(analysis: str, variant_id: str, **payload) -> None:
                async with sm() as s:
                    await svr.upsert_small_variant_review(
                        s,
                        context=_context(analysis),  # type: ignore[arg-type]
                        variant_id=variant_id,
                        payload=SmallVariantReviewUpdate(**payload),
                        user=user,  # type: ignore[arg-type]
                    )

            # A reviewed in the long-read analysis only; B in both, each its own classification;
            # C's shared review re-saved in the long-read analysis; D's cleared in the short-read one.
            await _save(LR, A, tags=["report"])
            await _save(LR, B, tags=["report"], classification="Likely pathogenic - class 4")
            await _save(SR, B, tags=["review"], classification="VUS - class 3")
            await _save(LR, C, tags=["report", "review"], note="seen again")
            await _save(SR, D, tags=[])

            async with sm() as s:
                stored = await _stored(s, family_uuid)
                maps = {
                    analysis: await svr.get_small_variant_review_map(
                        s, family_uuid=family_uuid, variant_ids=[A, B, C, D], scope=scope
                    )
                    for analysis, scope in scopes.items()
                }
                reported = {
                    analysis: await svr.list_matching_small_variant_review_ids(
                        s, family_uuid=family_uuid, scope=scope, tags=["report"]
                    )
                    for analysis, scope in scopes.items()
                }
                summaries = {
                    analysis: await svr.get_small_variant_review_summary(s, family_uuid=family_uuid, scope=scope)
                    for analysis, scope in scopes.items()
                }
                # One review per family, analysis and variant.
                with pytest.raises(IntegrityError):
                    await s.execute(
                        text(
                            "INSERT INTO small_variant_reviews (family_id, analysis, variant_id, updated_by) "
                            "VALUES (CAST(:f AS uuid), :a, :v, 'x')"
                        ),
                        {"f": family_uuid, "a": LR, "v": B},
                    )
                await s.rollback()

            assert stored == {
                (LR, A): ["report"],
                (LR, B): ["report"],
                (SR, B): ["review"],
                # The shared reviews stay as they were.
                ("", C): ["report"],
                ("", D): ["report"],
                (LR, C): ["report", "review"],
                # Cleared in the short-read analysis: kept empty over the shared review.
                (SR, D): [],
            }
            assert {variant: review.tags for variant, review in maps[LR].items()} == {
                A: ["report"],
                B: ["report"],
                C: ["report", "review"],
                D: ["report"],
            }
            assert {variant: review.tags for variant, review in maps[SR].items()} == {
                B: ["review"],
                C: ["report"],
            }
            assert (maps[LR][B].classification, maps[SR][B].classification) == (
                "Likely pathogenic - class 4",
                "VUS - class 3",
            )
            assert maps[LR][C].note == "seen again" and maps[SR][C].note is None
            assert reported == {LR: {A, B, C, D}, SR: {C}}
            assert (summaries[LR].reviewed_variant_count, summaries[SR].reviewed_variant_count) == (4, 2)
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_the_explorer_reads_every_review_in_effect_in_an_analysis_of_its_family() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.analysis_reviews import review_in_effect_sql

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                family_uuid = await _family(s, f"explorer-reviews-{uuid4()}", [LR, SR])
                none_held = await _family(s, f"explorer-reviews-{uuid4()}", [])
                for analysis, variant_id in (
                    # A's shared review: no analysis has its own, both read it.
                    ("", A),
                    # B's shared review: each analysis replaced it with its own.
                    ("", B),
                    (SR, B),
                    (LR, B),
                    # C's shared review: the long-read analysis replaced it, the short-read one reads it.
                    ("", C),
                    (LR, C),
                    # D: one analysis's own.
                    (SR, D),
                ):
                    await s.execute(
                        text(
                            "INSERT INTO small_variant_reviews (family_id, analysis, variant_id, updated_by) "
                            "VALUES (CAST(:f AS uuid), :a, :v, 'x')"
                        ),
                        {"f": family_uuid, "a": analysis, "v": variant_id},
                    )
                await s.execute(
                    text(
                        "INSERT INTO small_variant_reviews (family_id, analysis, variant_id, updated_by) "
                        "VALUES (CAST(:f AS uuid), '', :v, 'x')"
                    ),
                    {"f": none_held, "v": A},
                )
                rows = (
                    await s.execute(
                        text(
                            "SELECT r.family_id::text AS family_id, r.analysis, r.variant_id "
                            "FROM small_variant_reviews r "
                            "WHERE r.family_id IN (CAST(:f AS uuid), CAST(:g AS uuid)) "
                            f"AND {review_in_effect_sql('r', 'small_variant_reviews')}"
                        ),
                        {"f": family_uuid, "g": none_held},
                    )
                ).mappings().all()
                await s.rollback()
            in_effect = {(row["family_id"], row["analysis"], row["variant_id"]) for row in rows}
            assert in_effect == {
                (family_uuid, "", A),
                (family_uuid, SR, B),
                (family_uuid, LR, B),
                (family_uuid, "", C),
                (family_uuid, LR, C),
                (family_uuid, SR, D),
                # A family that holds no analysis: its reviews, each stored under none.
                (none_held, "", A),
            }
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_an_sv_review_is_saved_and_read_per_analysis() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.schemas import StructuralVariantReviewUpdate
    from backend.app.services import structural_variant_review_pg as strv
    from backend.app.services.analysis_scope import AnalysisScope

    held = frozenset({SR, LR})
    sv = "1-100000-250000-DEL---"

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"sv-reviews-per-analysis-{uuid4()}"
            async with sm() as s:
                family_uuid = await _family(s, label, [LR, SR])
                await s.commit()
            user = SimpleNamespace(id=None, username="reviewer", email="reviewer@x.org")
            for analysis, classification in ((LR, "Pathogenic - class 5"), (SR, "VUS - class 3")):
                context = SimpleNamespace(
                    family_uuid=family_uuid,
                    family_id=label,
                    project_ids=[],
                    assembly_name=None,
                    scope=AnalysisScope(analysis=analysis, held=held),
                )
                async with sm() as s:
                    await strv.upsert_structural_variant_review(
                        s,
                        context=context,  # type: ignore[arg-type]
                        variant_id=sv,
                        payload=StructuralVariantReviewUpdate(classification=classification),
                        user=user,  # type: ignore[arg-type]
                    )
            async with sm() as s:
                read = {
                    analysis: (
                        await strv.get_structural_variant_review_map(
                            s,
                            family_uuid=family_uuid,
                            variant_ids=[sv],
                            scope=AnalysisScope(analysis=analysis, held=held),
                        )
                    )[sv].classification
                    for analysis in (SR, LR)
                }
                stored = (
                    await s.execute(
                        text(
                            "SELECT analysis, classification FROM structural_variant_reviews "
                            "WHERE family_id = CAST(:f AS uuid) ORDER BY analysis"
                        ),
                        {"f": family_uuid},
                    )
                ).all()
            assert read == {SR: "VUS - class 3", LR: "Pathogenic - class 5"}
            assert [tuple(row) for row in stored] == [(LR, "Pathogenic - class 5"), (SR, "VUS - class 3")]
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
