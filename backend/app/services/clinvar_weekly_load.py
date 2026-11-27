"""The daily check for a new ClinVar release, and its load (the owner's decisions B3 and B3b).

With ``CLINVAR_WEEKLY_LOAD`` on, a loop like the ClickHouse integrity monitor's
(``clickhouse_integrity_monitor.py``) looks once a day, for each assembly CoGA holds that ClinVar
publishes a weekly VCF for and that has a current release, whether NCBI holds a newer one. An
assembly's first release is a start's (``REFERENCE_BOOTSTRAP_CLINVAR``), an assembly import's or
an administrator's, never the check's. It loads a newer one as the requester ``clinvar-weekly``,
through the Reference page's own load (``clinvar_load_jobs``), which releases it only when its
checks pass: NCBI's MD5, the release and the assembly its header names, every record stored, and
no more than 5 % fewer records than the current release. A new release so comes in within a day
of its publication.

A load whose checks refuse the file ends ``refused``: the current release stays current, the
Reference page shows the load with its reason, and every active administrator is told by mail
(``SMTP_HOST``; without it the log says so). The check does not load that release again: an
administrator loads it, once the reason is known, or the next week's release comes in. A mail
that does not go out (no mail server, no active administrator, a mail server or a database that
fails) is tried again by the next check, until one has (``clinvar_load_jobs.mailed_at``), so the
administrators are told once.

A load that could not be completed ends ``failed`` (NCBI did not answer or answered with an
error, the download broke off, the load ran past ``CLINVAR_LOAD_TIMEOUT_SECONDS``, a restart
stopped it, another error): nothing of it is released, the Reference page shows it with its
reason, no mail goes out, and the next check loads that release again.

On by default (the owner's decision); ``CLINVAR_WEEKLY_LOAD=false`` leaves a newer release to an
administrator. Whether a weekly release may become current within a procedure validated once,
without each release being a major change, is QA's question V1 of the design.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class WeeklyRefusal:
    """A weekly load of an assembly whose checks refused its release, and whether the
    administrators have been told of it."""

    job_id: str
    assembly_name: str
    release: date | None
    error: str | None
    mailed: bool


async def _held_assemblies(session: AsyncSession) -> list[str]:
    """The assemblies CoGA holds that ClinVar publishes a weekly VCF for."""
    names = set((await session.execute(text("SELECT assembly_name FROM assemblies"))).scalars())
    return [name for name in CLINVAR_ASSEMBLY_FOLDERS if name in names]


async def _weekly_refusals(session: AsyncSession, assembly: str) -> list[WeeklyRefusal]:
    """The weekly loads of the assembly whose checks refused their release, oldest first. A
    weekly load that failed otherwise is not one: the next check loads its release again."""
    rows = await session.execute(
        text(
            "SELECT id::text AS id, release, error, mailed_at IS NOT NULL AS mailed "
            "FROM clinvar_load_jobs "
            "WHERE assembly_name = :assembly AND requested_by = :weekly AND status = 'refused' "
            "ORDER BY requested_at"
        ),
        {"assembly": assembly, "weekly": WEEKLY_REQUESTER},
    )
    return [
        WeeklyRefusal(
            job_id=row["id"], assembly_name=assembly, release=row["release"], error=row["error"], mailed=row["mailed"]
        )
        for row in rows.mappings()
    ]


async def _mark_mailed(session: AsyncSession, job_id: str) -> None:
    await session.execute(
        text("UPDATE clinvar_load_jobs SET mailed_at = now() WHERE id = CAST(:job AS uuid)"),
        {"job": job_id},
    )
    await session.commit()


def refusal_message(refusal: WeeklyRefusal, current: date | None, recipients: list[str]) -> EmailMessage:
    """The mail that tells the administrators a weekly load's checks refused its release."""
    url = app_url(REFERENCE_PAGE_PATH)
    return new_message(
        SUBJECT,
        recipients,
        [
            f"CoGA's weekly ClinVar load for {refusal.assembly_name} did not release ClinVar "
            f"{refusal.release.isoformat() if refusal.release else '(its release could not be read)'}.",
            "",
            f"Why: {refusal.error or 'no reason was recorded'}",
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


async def _tell_administrators(assembly: str) -> None:
    """Tell every active administrator of each release of the assembly a weekly load's checks
    refused that no mail has gone out about yet, and mark it mailed. Best-effort: a mail that
    cannot go out, or a database that fails, is logged, never raised; the next check tries
    again."""
    try:
        async with get_postgres_sessionmaker()() as session:
            untold = [refusal for refusal in await _weekly_refusals(session, assembly) if not refusal.mailed]
            if not untold:
                return
            if not mail_enabled():
                logger.warning("SMTP_HOST not set: no mail about the ClinVar release that was not released")
                return
            recipients = await administrator_addresses(session)
            if not recipients:
                logger.warning("no active administrator to tell about the ClinVar release that was not released")
                return
            current = await current_clinvar_release(session, assembly)
            for refusal in untold:
                await asyncio.to_thread(
                    send_mail, refusal_message(refusal, current.release if current else None, recipients)
                )
                await _mark_mailed(session, refusal.job_id)
    except MailNotSent as exc:
        logger.error(
            "the mail about the ClinVar release that was not released was not sent: %s; the next check "
            "tries again",
            error_text(exc),
        )
    except Exception as exc:  # noqa: BLE001 - one assembly's mail never stops the check
        logger.error(
            "the administrators could not be told of the ClinVar release that was not released (%s); "
            "the next check tries again",
            describe_error(exc),
        )


async def _load_newer_release(assembly: str) -> ClinvarLoadJobOut | None:
    """The load of NCBI's newest release of the assembly, run to its end, when the assembly has a
    current release, NCBI's newest is newer, and no weekly load's checks refused it; None when
    there is none to run, or another load is active."""
    async with get_postgres_sessionmaker()() as session:
        current = await current_clinvar_release(session, assembly)
        refused = {refusal.release for refusal in await _weekly_refusals(session, assembly)}
    if current is None:
        # The first release is a start's, an assembly import's or an administrator's.
        return None
    newest = await newest_clinvar_release(assembly)
    if newest <= current.release or newest in refused:
        return None
    job = await run_clinvar_load(assembly, requested_by=WEEKLY_REQUESTER)
    if job is None:
        logger.info("The weekly ClinVar load of %s waits: another ClinVar load is active", assembly)
    return job


async def check_for_new_clinvar_releases() -> list[ClinvarLoadJobOut]:
    """One check: each held assembly's newer NCBI release loaded (``_load_newer_release``), each
    load run to its end, and the administrators told of each release a weekly load refused that
    they were not told of yet. One assembly's check that fails is logged and leaves the
    other's. The loads, as ended."""
    async with get_postgres_sessionmaker()() as session:
        assemblies = await _held_assemblies(session)
    ended: list[ClinvarLoadJobOut] = []
    for assembly in assemblies:
        try:
            job = await _load_newer_release(assembly)
        except Exception as exc:  # noqa: BLE001 - one assembly's check never stops another's
            logger.error("The weekly ClinVar check of %s could not run (%s)", assembly, describe_error(exc))
        else:
            if job is not None:
                ended.append(job)
        await _tell_administrators(assembly)
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
    status read, and its partition dropped by the next load; the next check loads its release
    again."""
    global _worker_task
    task, _worker_task = _worker_task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
