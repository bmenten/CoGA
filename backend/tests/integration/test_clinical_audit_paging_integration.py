"""A family's clinical audit trail, read a page at a time, is the whole trail (real Postgres).

The report listed the 200 newest events of a family's trail as the whole trail (AUDIT-1): no
paging, no total, while a review save writes up to three events. ``list_clinical_audit`` serves
the trail a page at a time, newest first in the hash chain's order reversed, with the number of
events the trail holds and whether older ones remain; ``before``, the id of the last event
shown, reads the next page. Against real Postgres, with events written by the real writer
(``record_clinical_event``):

* a trail of 205 events: the default page holds the newest 200, counts 205 and says older ones
  remain; the page after it holds the other five and says none remain;
* read in pages of seven, one after another, the pages hold every event once, newest first,
  in the chain's order;
* an event written between two page reads is not on the older page, which neither repeats nor
  skips an event; the first page read again leads with it and counts it;
* a ``before`` that names another family's event, an unknown id or no UUID is answered 404;
  an empty trail is listed as empty; the chain still verifies after the reads.

The statement itself (the order, the count from the page's snapshot, the bounded page size) is
unit-tested in ``backend/tests/test_clinical_audit.py``.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _reader():
    """An admin: the family's projects do not limit what the trail shows them."""
    from backend.app.services.access_control import CurrentUser

    username = f"audit-reader-{uuid4()}"
    return CurrentUser(
        id=str(uuid4()),
        username=username,
        email=f"{username}@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


async def _fresh_family(session, label: str) -> str:
    return (
        await session.execute(
            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
            {"f": label},
        )
    ).scalar_one()


async def _append(session, family_uuid: str, label: str, numbers) -> None:
    """Classification events ``numbers`` on the family's chain, through the real writer."""
    from backend.app.services.clinical_audit_service import record_clinical_event

    for n in numbers:
        await record_clinical_event(
            session,
            family_uuid=family_uuid,
            family_identifier=label,
            variant_id=f"1-{n}-A-G",
            actor="reviewer",
            actor_id=None,
            action="classification",
            summary=f"event {n}",
            before={"n": n - 1},
            after={"n": n},
        )
    await session.commit()


async def _chain_newest_first(session, label: str) -> list[str]:
    """The family's event ids in the hash chain's order, reversed."""
    rows = await session.execute(
        text(
            "SELECT id::text FROM clinical_audit_events "
            "WHERE family_identifier = :f ORDER BY created_at ASC, id ASC"
        ),
        {"f": label},
    )
    return [row[0] for row in rows.all()][::-1]


def _ids(page: dict) -> list[str]:
    return [event["id"] for event in page["events"]]


def test_a_trail_longer_than_a_page_is_read_whole_page_by_page() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clinical_audit_service import (
        CLINICAL_AUDIT_PAGE_SIZE,
        list_clinical_audit,
        verify_clinical_audit_chain,
    )

    reader = _reader()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"audit-pages-{uuid4()}"
            async with sm() as s:
                family_uuid = await _fresh_family(s, label)
                await s.commit()
                await _append(s, family_uuid, label, range(1, CLINICAL_AUDIT_PAGE_SIZE + 6))
            async with sm() as s:
                chain = await _chain_newest_first(s, label)
            assert len(chain) == 205

            # The default page: the newest 200 of 205, and older ones remain.
            async with sm() as s:
                first = await list_clinical_audit(s, family_id=label, user=reader)
            assert first["family_id"] == label
            assert (first["total"], first["has_more"], len(first["events"])) == (205, True, 200)
            assert _ids(first) == chain[:200]
            assert first["events"][0]["summary"] == "event 205"
            assert first["events"][0]["after"] == {"n": 205}

            # The page after its last event: the other five, and none remain.
            async with sm() as s:
                rest = await list_clinical_audit(
                    s, family_id=label, user=reader, before=first["events"][-1]["id"]
                )
            assert (rest["total"], rest["has_more"]) == (205, False)
            assert _ids(rest) == chain[200:]
            assert [event["summary"] for event in rest["events"]] == [
                f"event {n}" for n in (5, 4, 3, 2, 1)
            ]

            # Pages of seven, read one after another as the report reads them: every event
            # once, in the chain's order reversed.
            pages = []
            before = None
            async with sm() as s:
                while True:
                    page = await list_clinical_audit(
                        s, family_id=label, user=reader, limit=7, before=before
                    )
                    pages.append(page)
                    if not page["has_more"]:
                        break
                    before = page["events"][-1]["id"]
            assert len(pages) == math.ceil(205 / 7)
            assert all(page["total"] == 205 for page in pages)
            assert [event_id for page in pages for event_id in _ids(page)] == chain

            # Reading changes nothing: the chain still verifies.
            async with sm() as s:
                verified = await verify_clinical_audit_chain(s, label)
            assert verified.verified and verified.rows_checked == 205, verified
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_an_event_written_between_two_page_reads_shifts_no_page() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clinical_audit_service import list_clinical_audit

    reader = _reader()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label = f"audit-shift-{uuid4()}"
            async with sm() as s:
                family_uuid = await _fresh_family(s, label)
                await s.commit()
                await _append(s, family_uuid, label, range(1, 6))
            async with sm() as s:
                chain = await _chain_newest_first(s, label)

            async with sm() as s:
                first = await list_clinical_audit(s, family_id=label, user=reader, limit=2)
            assert (first["total"], first["has_more"], _ids(first)) == (5, True, chain[:2])

            # A review save in the meantime writes the trail's newest event.
            async with sm() as s:
                await _append(s, family_uuid, label, [6])

            # The next page holds the events recorded before the last one shown: not the new
            # event, and not again the last one shown. It counts the trail as it is now.
            async with sm() as s:
                second = await list_clinical_audit(
                    s, family_id=label, user=reader, limit=2, before=first["events"][-1]["id"]
                )
                third = await list_clinical_audit(
                    s, family_id=label, user=reader, limit=2, before=second["events"][-1]["id"]
                )
            assert (second["total"], second["has_more"], _ids(second)) == (6, True, chain[2:4])
            assert (third["total"], third["has_more"], _ids(third)) == (6, False, chain[4:])

            # Read again, the first page leads with the new event and counts it.
            async with sm() as s:
                again = await list_clinical_audit(s, family_id=label, user=reader, limit=2)
                now = await _chain_newest_first(s, label)
            assert again["events"][0]["summary"] == "event 6"
            assert (again["total"], _ids(again)) == (6, now[:2])
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_a_page_after_an_event_outside_the_familys_trail_is_answered_404() -> None:
    from fastapi import HTTPException

    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clinical_audit_service import list_clinical_audit

    reader = _reader()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label_a = f"audit-own-{uuid4()}"
            label_b = f"audit-other-{uuid4()}"
            label_empty = f"audit-empty-{uuid4()}"
            async with sm() as s:
                family_a = await _fresh_family(s, label_a)
                family_b = await _fresh_family(s, label_b)
                await _fresh_family(s, label_empty)
                await s.commit()
                await _append(s, family_a, label_a, range(1, 4))
                await _append(s, family_b, label_b, range(1, 3))
            async with sm() as s:
                [other_event, *_] = await _chain_newest_first(s, label_b)

            for before in (other_event, str(uuid4()), "not-an-event"):
                async with sm() as s:
                    with pytest.raises(HTTPException) as refused:
                        await list_clinical_audit(s, family_id=label_a, user=reader, before=before)
                assert (refused.value.status_code, refused.value.detail) == (
                    404,
                    "Audit event not found",
                ), before

            # A family with no events: an empty trail, nothing more to read.
            async with sm() as s:
                empty = await list_clinical_audit(s, family_id=label_empty, user=reader)
            assert (empty["total"], empty["has_more"], empty["events"]) == (0, False, [])
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
