"""The daily check for a new ClinVar release, and its load (the owner's decisions B3 and B3b).

With ``CLINVAR_WEEKLY_LOAD`` on, a loop like the ClickHouse integrity monitor's
(``clickhouse_integrity_monitor.py``) looks once a day whether NCBI holds a newer weekly ClinVar
VCF, for each assembly CoGA holds that ClinVar publishes one for, than the assembly's current
release. It loads a newer one as the requester ``clinvar-weekly``, through the Reference page's
own load (``clinvar_load_jobs``), which releases it only when its checks pass: NCBI's MD5, the
release and the assembly its header names, every record stored, and no more than 5 % fewer
records than the current release. A new release so comes in within a day of its publication.

When a weekly load does not release its file, the current release stays current, the Reference
page shows the failed load with its reason, and every active administrator is told by mail
(``SMTP_HOST``; without it the log says so). The check does not load that release again: an
administrator loads it, once the reason is known, or the next week's release comes in.

On by default (the owner's decision); ``CLINVAR_WEEKLY_LOAD=false`` leaves a newer release to an
administrator. Whether a weekly release may become current within a procedure validated once,
without each release being a major change, is QA's question V1 of the design.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import date, timedelta
from email.message import EmailMessage

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error, error_text
from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import ClinvarLoadJobOut
from .account_notifications import administrator_addresses
from .clinvar_load_jobs import WEEKLY_REQUESTER, newest_clinvar_release, run_clinvar_load
from .clinvar_releases import CLINVAR_ASSEMBLY_FOLDERS, current_clinvar_release
from .outgoing_mail import MailNotSent, app_url, mail_enabled, new_message, send_mail

logger = logging.getLogger(__name__)

# The first check a while after the start, so a restart does not wait a day for one; then daily.
FIRST_CHECK_DELAY = timedelta(minutes=10)
CHECK_INTERVAL = timedelta(days=1)
# Where an administrator sees the loads (frontend/src/index.tsx).
REFERENCE_PAGE_PATH = "/reference-data"
SUBJECT = "CoGA: a ClinVar release was not released"

_worker_task: asyncio.Task[None] | None = None


async def _held_assemblies(session: AsyncSession) -> list[str]:
    """The assemblies CoGA holds that ClinVar publishes a weekly VCF for."""
    names = set((await session.execute(text("SELECT assembly_name FROM assemblies"))).scalars())
    return [name for name in CLINVAR_ASSEMBLY_FOLDERS if name in names]


async def _refused_weekly(session: AsyncSession, assembly: str) -> set[date]:
    """The releases of the assembly a weekly load did not release."""
    rows = await session.execute(
        text(
            "SELECT DISTINCT release FROM clinvar_load_jobs "
            "WHERE assembly_name = :assembly AND requested_by = :weekly AND status = 'failed' "
            "AND release IS NOT NULL"
        ),
        {"assembly": assembly, "weekly": WEEKLY_REQUESTER},
    )
    return set(rows.scalars())


def refusal_message(job: ClinvarLoadJobOut, current: date | None, recipients: list[str]) -> EmailMessage:
    """The mail that tells the administrators a weekly load did not release its file."""
    url = app_url(REFERENCE_PAGE_PATH)
    return new_message(
        SUBJECT,
        recipients,
        [
            f"CoGA's weekly ClinVar load for {job.assembly_name} did not release ClinVar "
            f"{job.release.isoformat() if job.release else '(its release could not be read)'}.",
            "",
            f"Why: {job.error or 'no reason was recorded'}",
            "",
            (
                f"The current release stays ClinVar {current.isoformat()}."
                if current
                else "The assembly has no ClinVar release yet."
            ),
            "The weekly check does not load this release again. Load it on the Reference page once "
            "the reason is known, or wait for the next week's release:",
            url or "in CoGA, under Reference data.",
            "",
            "You receive this mail as an active CoGA administrator.",
        ],
    )


async def _tell_administrators(job: ClinvarLoadJobOut) -> None:
    """Tell every active administrator that a weekly load did not release its file. Best-effort:
    a mail that cannot go out is logged, never raised."""
    if not mail_enabled():
        logger.warning("SMTP_HOST not set: no mail about the ClinVar release that was not released")
        return
    async with get_postgres_sessionmaker()() as session:
        recipients = await administrator_addresses(session)
        current = await current_clinvar_release(session, job.assembly_name)
    if not recipients:
        logger.warning("no active administrator to tell about the ClinVar release that was not released")
        return
    try:
        await asyncio.to_thread(
            send_mail, refusal_message(job, current.release if current else None, recipients)
        )
    except MailNotSent as exc:
        logger.error("the mail about the ClinVar release that was not released was not sent: %s", error_text(exc))


async def check_for_new_clinvar_releases() -> list[ClinvarLoadJobOut]:
    """One check: each held assembly's newest NCBI release loaded, when it is newer than the
    current one and no weekly load refused it; each load run to its end. The loads, as ended."""
    async with get_postgres_sessionmaker()() as session:
        assemblies = await _held_assemblies(session)
    ended: list[ClinvarLoadJobOut] = []
    for assembly in assemblies:
        try:
            newest = await newest_clinvar_release(assembly)
            async with get_postgres_sessionmaker()() as session:
                current = await current_clinvar_release(session, assembly)
                refused = await _refused_weekly(session, assembly)
            if (current is not None and newest <= current.release) or newest in refused:
                continue
            job = await run_clinvar_load(assembly, requested_by=WEEKLY_REQUESTER)
        except Exception as exc:  # noqa: BLE001 - one assembly's check never stops another's
            logger.error("The weekly ClinVar check of %s could not run (%s)", assembly, describe_error(exc))
            continue
        if job is None:
            logger.info("The weekly ClinVar load of %s waits: another ClinVar load is active", assembly)
            continue
        if job.status == "failed":
            await _tell_administrators(job)
        ended.append(job)
    return ended


async def _worker() -> None:
    await asyncio.sleep(FIRST_CHECK_DELAY.total_seconds())
    while True:
        try:
            await check_for_new_clinvar_releases()
        except Exception as exc:  # noqa: BLE001 - logged; the next day's check runs all the same
            logger.error("The weekly ClinVar check failed (%s)", describe_error(exc))
        await asyncio.sleep(CHECK_INTERVAL.total_seconds())


async def start_clinvar_weekly_load() -> None:
    """Start the daily check, when ``CLINVAR_WEEKLY_LOAD`` is on."""
    global _worker_task
    if settings.clinvar_weekly_load and _worker_task is None:
        _worker_task = asyncio.create_task(_worker())


async def stop_clinvar_weekly_load() -> None:
    """Stop the daily check. A load it runs stops with it: its job is closed as stale by a later
    status read, and its partition dropped by the next load."""
    global _worker_task
    task, _worker_task = _worker_task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
