"""Integration test: a QC cut-off edit is recorded with the value it replaced (real Postgres).

Verifying evidence for RTM REQ-QC-007. A QC acceptance limit decides whether CoGA reports
a run as inadequate, so every edit must be reconstructable later: who changed which limit,
from what, to what, and why. The request-audit log records the request but has no prior
value; ``set_qc_threshold`` therefore appends both sides of the edit to the append-only
``qc_threshold_changes`` table. The unit tests in ``test_qc_threshold_service.py`` pin the
statements it sends; this test runs them against the real schema.

It works on a profile of its own, created here with a unique key, so the limits it sets
gate no family and cannot affect other tests on the same database. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

pytestmark = pytest.mark.integration

_METRIC = "depth.mean_depth"  # lower is worse: the warning bound sits at or above the error bound


def test_each_cut_off_edit_leaves_one_history_row_with_the_replaced_value() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.qc_threshold_service import (
        create_qc_threshold_profile,
        set_qc_threshold,
    )

    async def _history(sm, profile_key: str) -> list[dict]:
        async with sm() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT previous_warn_value, previous_error_value, warn_value, "
                        "error_value, changed_by_email, reason FROM qc_threshold_changes "
                        "WHERE profile_key = :key ORDER BY changed_at, id"
                    ),
                    {"key": profile_key},
                )
            ).mappings()
            return [dict(row) for row in rows]

    async def _limit(sm, profile_key: str) -> tuple | None:
        async with sm() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT t.warn_value, t.error_value FROM qc_thresholds t "
                        "JOIN qc_threshold_profiles p ON p.id = t.profile_id "
                        "WHERE p.key = :key AND t.metric_key = :metric"
                    ),
                    {"key": profile_key, "metric": _METRIC},
                )
            ).first()
            return tuple(row) if row else None

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"Recording test {uuid.uuid4().hex[:8]}"
            async with sm() as session:
                profile = await create_qc_threshold_profile(session, label=label)
            key = profile["key"]

            async def edit(warn, error, reason) -> None:
                async with sm() as session:
                    await set_qc_threshold(
                        session,
                        profile_key=key,
                        metric_key=_METRIC,
                        warn_value=warn,
                        error_value=error,
                        user_id=None,
                        user_email="qa@example.org",
                        reason=reason,
                    )

            await edit(20.0, 10.0, "First limit, per the validation report")
            await edit(25.0, 12.0, "  Raised after the depth study  ")
            assert await _limit(sm, key) == (25.0, 12.0)
            await edit(None, None, "Withdrawn pending revalidation")
            assert await _limit(sm, key) is None

            # A change without a reason is refused and leaves no trace.
            with pytest.raises(HTTPException) as excinfo:
                await edit(30.0, 15.0, "   ")
            assert excinfo.value.status_code == 400
            assert await _limit(sm, key) is None

            history = await _history(sm, key)
            assert [
                (
                    row["previous_warn_value"],
                    row["previous_error_value"],
                    row["warn_value"],
                    row["error_value"],
                    row["reason"],
                )
                for row in history
            ] == [
                (None, None, 20.0, 10.0, "First limit, per the validation report"),
                (20.0, 10.0, 25.0, 12.0, "Raised after the depth study"),
                (25.0, 12.0, None, None, "Withdrawn pending revalidation"),
            ]
            assert {row["changed_by_email"] for row in history} == {"qa@example.org"}
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
