"""A review saved with ACMG criteria comes back through a variant list with them (real Postgres).

Every list of small variants — the tables and cards, the report, the NIPT candidates, the
mtDNA analysis — carries each variant's review from ``get_small_variant_review_map``. Its
query once left out the ACMG record, so every list served a classified variant with
``acmg = None``: the live report showed no criteria, and the ACMG dialog opened from a list
was seeded without the saved ones, so a re-save replaced them.

This saves through the real writer (``upsert_small_variant_review``) and reads through the
real list path against real Postgres, JSONB round-trip included. Only the variant's
ClickHouse record is stubbed: the review lives in Postgres.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_VARIANT_ID = "17-43000000-A-G"


def _criteria(review) -> list[tuple[str, str, bool, str | None, bool]]:
    return [
        (c.code, c.strength, c.accepted, c.evidence, c.auto_suggested) for c in review.acmg.criteria
    ]


def test_a_review_saved_with_acmg_criteria_comes_back_through_the_list(monkeypatch) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import (
        AcmgClassificationPayload,
        AcmgCriterionSelection,
        SmallVariantReviewUpdate,
    )
    from backend.app.services import small_variant_review_pg as svr

    async def _variant_record(*, assembly_name, family_guid, variant_id):
        # What the save reads from ClickHouse: the variant's key, and the annotation it
        # freezes as evidence.
        return SimpleNamespace(
            variant_key=4242,
            annotations=[{"clinvar": "Likely_pathogenic"}],
            annotation_version="v1",
            annotation_set_hash="set-1",
        )

    monkeypatch.setattr(svr, "get_small_variant_family_record", _variant_record)

    # An analyst's own criterion (PS3, with its evidence), an auto-suggested one they
    # accepted, and one they left unaccepted.
    saved = [
        AcmgCriterionSelection(
            code="PS3", strength="strong", accepted=True, evidence="RNA assay shows exon skipping"
        ),
        AcmgCriterionSelection(code="PM2", strength="supporting", accepted=True, auto_suggested=True),
        AcmgCriterionSelection(code="BP4", strength="supporting", accepted=False, auto_suggested=True),
    ]
    expected = [
        ("PS3", "strong", True, "RNA assay shows exon skipping", False),
        ("PM2", "supporting", True, None, True),
        ("BP4", "supporting", False, None, True),
    ]

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"acmg-list-{uuid4()}"
            async with sm() as s:
                family_uuid = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                await s.commit()
            context = SimpleNamespace(
                family_uuid=family_uuid, family_id=label, project_ids=[], assembly_name="GRCh38"
            )
            user = SimpleNamespace(id=None, username="reviewer", email="reviewer@x.org")

            async with sm() as s:
                await svr.upsert_small_variant_review(
                    s,
                    context=context,
                    variant_id=_VARIANT_ID,
                    payload=SmallVariantReviewUpdate(
                        classification="VUS - class 3",
                        tags=["acmg_class_3", "report"],
                        note="kept",
                        acmg=AcmgClassificationPayload(criteria=saved),
                    ),
                    user=user,
                )

            async with sm() as s:
                listed = await svr.get_small_variant_review_map(
                    s, family_uuid=family_uuid, variant_ids=[_VARIANT_ID]
                )
                single = svr._serialize_review(
                    await svr._fetch_review_row(s, family_uuid=family_uuid, variant_id=_VARIANT_ID)
                )

            review = listed[_VARIANT_ID]
            # The criteria come back through the list as saved, with the class and points
            # the server computed from them (PS3 strong 4 + PM2 supporting 1 = 5: a hot VUS).
            assert review.acmg is not None, review
            assert _criteria(review) == expected
            assert (review.acmg.point_total, review.acmg.classification, review.acmg.vus_tier) == (
                5,
                "VUS - class 3",
                "hot",
            )
            assert review.acmg_unreadable is False
            # And the list serves the review exactly as a read of that one review does.
            assert review == single

            # Re-saved unchanged, as the ACMG dialog sends it when seeded from the list: the
            # stored criteria stay as they were.
            async with sm() as s:
                await svr.upsert_small_variant_review(
                    s,
                    context=context,
                    variant_id=_VARIANT_ID,
                    payload=SmallVariantReviewUpdate(
                        expected_updated_at=review.updated_at,
                        classification=review.acmg.classification,
                        tags=review.tags,
                        note=review.note,
                        acmg=AcmgClassificationPayload(criteria=review.acmg.criteria),
                    ),
                    user=user,
                )
            async with sm() as s:
                after = (
                    await svr.get_small_variant_review_map(
                        s, family_uuid=family_uuid, variant_ids=[_VARIANT_ID]
                    )
                )[_VARIANT_ID]
            assert _criteria(after) == expected
            assert after.acmg.point_total == 5
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_a_review_holding_only_an_acmg_record_counts_as_reviewed(monkeypatch) -> None:
    # Saved through the real writer with criteria and nothing else (no tag, class label or
    # note), the review is counted by the family's review summary.
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import (
        AcmgClassificationPayload,
        AcmgCriterionSelection,
        SmallVariantReviewUpdate,
    )
    from backend.app.services import small_variant_review_pg as svr

    async def _variant_record(*, assembly_name, family_guid, variant_id):
        return SimpleNamespace(variant_key=4243, annotations=[], annotation_version="v1", annotation_set_hash="set-1")

    monkeypatch.setattr(svr, "get_small_variant_family_record", _variant_record)

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"acmg-summary-{uuid4()}"
            async with sm() as s:
                family_uuid = (
                    await s.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                await s.commit()
            context = SimpleNamespace(
                family_uuid=family_uuid, family_id=label, project_ids=[], assembly_name="GRCh38"
            )
            async with sm() as s:
                await svr.upsert_small_variant_review(
                    s,
                    context=context,
                    variant_id=_VARIANT_ID,
                    payload=SmallVariantReviewUpdate(
                        acmg=AcmgClassificationPayload(
                            criteria=[AcmgCriterionSelection(code="PM2", strength="supporting", accepted=True)]
                        )
                    ),
                    user=SimpleNamespace(id=None, username="reviewer", email="reviewer@x.org"),
                )
            async with sm() as s:
                summary = await svr.get_small_variant_review_summary(s, family_uuid=family_uuid)
            assert summary.reviewed_variant_count == 1, summary
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
