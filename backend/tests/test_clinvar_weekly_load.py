"""The daily check for a new ClinVar release (the owner's decisions B3 and B3b), behind
``CLINVAR_WEEKLY_LOAD``, on by default (the owner's decision).

Each day the check loads, for each assembly CoGA holds that ClinVar publishes a VCF for and that
has a current release, NCBI's newest weekly release when it is newer than the current one, as
the requester ``clinvar-weekly``, through the Reference page's own load, whose checks decide
whether it is released. An assembly without a release is left to a start or an administrator.
A load whose checks refuse its release leaves the current release current and tells every
active administrator by mail, once: a mail that does not go out is tried again by the next
check, and the check does not load that release again. A load that could not be completed is
not mailed, and the next check loads its release again. One assembly's failure, its mail's
included, never stops another's check.

NCBI, the releases, the loads and the mail server are stood in for;
``integration/test_clinvar_load_clickhouse.py`` runs the check against real Postgres and
ClickHouse. Synthetic values only.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import pytest

from backend.app.core.config import Settings
from backend.app.schemas import ClinvarLoadJobOut
from backend.app.services import clinvar_weekly_load as weekly
from backend.app.services.clinvar_load_jobs import WEEKLY_REQUESTER

_CURRENT = date(2026, 11, 22)
_NEWER = date(2026, 11, 29)


def _job(assembly: str, status: str, *, error: str | None = None) -> ClinvarLoadJobOut:
    return ClinvarLoadJobOut(
        id="00000000-0000-0000-0000-000000000001",
        assembly_id="00000000-0000-0000-0000-000000000002",
        assembly_name=assembly,
        release=_NEWER,
        status=status,  # type: ignore[arg-type]
        requested_by=WEEKLY_REQUESTER,
        requested_at=datetime(2026, 11, 30, tzinfo=timezone.utc),
        error=error,
    )


class _Ncbi:
    """What the check reads and starts, per assembly: NCBI's newest release, the current one,
    the weekly loads it ran (a refused one kept as the job table keeps it), and the mails."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, held: list[str]) -> None:
        self.newest: dict[str, date | Exception] = {}
        self.current: dict[str, date] = {}
        self.refusals: dict[str, list[weekly.WeeklyRefusal]] = {}
        self.outcome: dict[str, ClinvarLoadJobOut | None] = {}
        self.loaded: list[tuple[str, str]] = []
        self.asked_ncbi: list[str] = []
        self.mails: list[EmailMessage] = []

        async def held_assemblies(_session: Any) -> list[str]:
            return held

        async def newest(assembly: str) -> date:
            self.asked_ncbi.append(assembly)
            value = self.newest[assembly]
            if isinstance(value, Exception):
                raise value
            return value

        async def current(_session: Any, assembly: str) -> Any:
            release = self.current.get(assembly)
            return type("Release", (), {"release": release})() if release else None

        async def refusals(_session: Any, assembly: str) -> list[weekly.WeeklyRefusal]:
            return list(self.refusals.get(assembly, []))

        async def mark_mailed(_session: Any, job_id: str) -> None:
            for assembly, kept in self.refusals.items():
                self.refusals[assembly] = [
                    replace(refusal, mailed=True) if refusal.job_id == job_id else refusal for refusal in kept
                ]

        async def run(assembly: str, *, requested_by: str) -> ClinvarLoadJobOut | None:
            self.loaded.append((assembly, requested_by))
            job = self.outcome[assembly]
            if job is not None and job.status == "refused":
                self.refusals.setdefault(assembly, []).append(
                    weekly.WeeklyRefusal(
                        job_id=f"job-{len(self.loaded)}",
                        assembly_name=assembly,
                        release=job.release,
                        error=job.error,
                        mailed=False,
                    )
                )
            return job

        async def admins(_session: Any) -> list[str]:
            return ["admin@example.org"]

        class _Session:
            async def __aenter__(self) -> "_Session":
                return self

            async def __aexit__(self, *_args: Any) -> None:
                return None

        monkeypatch.setattr(weekly, "get_postgres_sessionmaker", lambda: _Session)
        monkeypatch.setattr(weekly, "_held_assemblies", held_assemblies)
        monkeypatch.setattr(weekly, "newest_clinvar_release", newest)
        monkeypatch.setattr(weekly, "current_clinvar_release", current)
        monkeypatch.setattr(weekly, "_weekly_refusals", refusals)
        monkeypatch.setattr(weekly, "_mark_mailed", mark_mailed)
        monkeypatch.setattr(weekly, "run_clinvar_load", run)
        monkeypatch.setattr(weekly, "administrator_addresses", admins)
        monkeypatch.setattr(weekly, "mail_enabled", lambda: True)
        monkeypatch.setattr(weekly, "send_mail", self.mails.append)


def _check() -> list[ClinvarLoadJobOut]:
    return asyncio.run(weekly.check_for_new_clinvar_releases())


def test_the_weekly_load_is_on_by_default() -> None:
    assert Settings.model_fields["clinvar_weekly_load"].default is True


def test_a_newer_release_is_loaded_as_the_weekly_requester(monkeypatch: pytest.MonkeyPatch) -> None:
    ncbi = _Ncbi(monkeypatch, held=["GRCh38", "GRCh37"])
    ncbi.newest = {"GRCh38": _NEWER, "GRCh37": _CURRENT}
    ncbi.current = {"GRCh38": _CURRENT, "GRCh37": _CURRENT}
    ncbi.outcome = {"GRCh38": _job("GRCh38", "completed")}

    ended = _check()

    # GRCh37's newest release is its current one: not loaded.
    assert ncbi.loaded == [("GRCh38", WEEKLY_REQUESTER)]
    assert [job.status for job in ended] == ["completed"]
    assert ncbi.mails == []


def test_an_assembly_without_a_release_is_left_to_a_start_or_an_administrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first release is a start's (REFERENCE_BOOTSTRAP_CLINVAR), an assembly import's or an
    administrator's: with the bootstrap off, as for the browser end-to-end run, the check loads
    none and does not ask NCBI."""
    ncbi = _Ncbi(monkeypatch, held=["GRCh38"])
    ncbi.newest = {"GRCh38": _NEWER}
    ncbi.outcome = {"GRCh38": _job("GRCh38", "completed")}

    assert _check() == []
    assert ncbi.loaded == [] and ncbi.asked_ncbi == []


def test_a_refused_release_is_mailed_to_the_administrators_once_and_not_loaded_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "ClinVar 2026-11-29 has 2,000 records, more than 5% fewer than the current release."
    ncbi = _Ncbi(monkeypatch, held=["GRCh38"])
    ncbi.newest = {"GRCh38": _NEWER}
    ncbi.current = {"GRCh38": _CURRENT}
    ncbi.outcome = {"GRCh38": _job("GRCh38", "refused", error=reason)}

    [refused] = _check()

    assert refused.status == "refused"
    [mail] = ncbi.mails
    body = mail.get_content()
    assert (mail["To"], mail["Subject"]) == ("admin@example.org", weekly.SUBJECT)
    assert "CoGA's weekly ClinVar load for GRCh38 did not release ClinVar 2026-11-29." in body
    assert f"Why: {reason}" in body
    assert "The current release stays ClinVar 2026-11-22." in body

    # The next day: the refused release is not loaded again, and no second mail goes out.
    assert _check() == []
    assert len(ncbi.loaded) == 1 and len(ncbi.mails) == 1


@pytest.mark.parametrize(
    "error",
    [
        "The ClinVar file could not be downloaded (HTTPStatusError).",
        "The ClinVar load was stopped after 7200 seconds (CLINVAR_LOAD_TIMEOUT_SECONDS).",
        "Interrupted: no sign of its load for 10 minutes; the server running it was probably restarted.",
    ],
    ids=["ncbi-error", "timeout", "interrupted"],
)
def test_a_load_that_could_not_be_completed_is_not_mailed_and_loaded_again_the_next_day(
    monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    ncbi = _Ncbi(monkeypatch, held=["GRCh38"])
    ncbi.newest = {"GRCh38": _NEWER}
    ncbi.current = {"GRCh38": _CURRENT}
    ncbi.outcome = {"GRCh38": _job("GRCh38", "failed", error=error)}

    assert [job.status for job in _check()] == ["failed"]
    assert ncbi.mails == []

    ncbi.outcome = {"GRCh38": _job("GRCh38", "completed")}
    assert [job.status for job in _check()] == ["completed"]
    assert ncbi.loaded == [("GRCh38", WEEKLY_REQUESTER)] * 2 and ncbi.mails == []


def test_a_mail_that_does_not_go_out_is_sent_by_a_later_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """No mail server, no active administrator, a mail server that refuses: each leaves the
    check as it is, and the next check tells the administrators once it can."""
    from backend.app.services.outgoing_mail import MailNotSent

    ncbi = _Ncbi(monkeypatch, held=["GRCh38"])
    ncbi.newest = {"GRCh38": _NEWER}
    ncbi.current = {"GRCh38": _CURRENT}
    ncbi.outcome = {"GRCh38": _job("GRCh38", "refused", error="refused")}

    monkeypatch.setattr(weekly, "mail_enabled", lambda: False)
    assert [job.status for job in _check()] == ["refused"]

    async def nobody(_session: Any) -> list[str]:
        return []

    monkeypatch.setattr(weekly, "mail_enabled", lambda: True)
    monkeypatch.setattr(weekly, "administrator_addresses", nobody)
    assert _check() == []

    def refuse(_message: EmailMessage) -> None:
        raise MailNotSent("The mail server refused the mail (SMTPRecipientsRefused).")

    async def admins(_session: Any) -> list[str]:
        return ["admin@example.org"]

    monkeypatch.setattr(weekly, "administrator_addresses", admins)
    monkeypatch.setattr(weekly, "send_mail", refuse)
    assert _check() == []
    assert ncbi.mails == [] and not ncbi.refusals["GRCh38"][0].mailed

    monkeypatch.setattr(weekly, "send_mail", ncbi.mails.append)
    assert _check() == [] and _check() == []
    assert len(ncbi.mails) == 1 and ncbi.loaded == [("GRCh38", WEEKLY_REQUESTER)]


def test_a_database_that_fails_while_telling_the_administrators_leaves_the_other_assembly_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ncbi = _Ncbi(monkeypatch, held=["GRCh38", "GRCh37"])
    ncbi.newest = {"GRCh38": _NEWER, "GRCh37": _NEWER}
    ncbi.current = {"GRCh38": _CURRENT, "GRCh37": _CURRENT}
    ncbi.outcome = {"GRCh38": _job("GRCh38", "refused", error="refused"), "GRCh37": _job("GRCh37", "completed")}

    async def lost(_session: Any) -> list[str]:
        raise ConnectionError("the database went away")

    monkeypatch.setattr(weekly, "administrator_addresses", lost)
    assert [(job.assembly_name, job.status) for job in _check()] == [("GRCh38", "refused"), ("GRCh37", "completed")]
    assert ncbi.mails == []

    async def admins(_session: Any) -> list[str]:
        return ["admin@example.org"]

    monkeypatch.setattr(weekly, "administrator_addresses", admins)
    ncbi.current["GRCh37"] = _NEWER
    assert _check() == []
    [mail] = ncbi.mails
    assert "for GRCh38 did not release ClinVar 2026-11-29" in mail.get_content()


def test_one_assemblys_failure_does_not_stop_anothers_check(monkeypatch: pytest.MonkeyPatch) -> None:
    ncbi = _Ncbi(monkeypatch, held=["GRCh38", "GRCh37"])
    ncbi.newest = {"GRCh38": OSError("NCBI did not answer"), "GRCh37": _NEWER}
    ncbi.current = {"GRCh38": _CURRENT, "GRCh37": _CURRENT}
    ncbi.outcome = {"GRCh37": _job("GRCh37", "completed")}

    ended = _check()

    assert [job.assembly_name for job in ended] == ["GRCh37"]


def test_a_check_while_another_load_is_active_waits_for_the_next_day(monkeypatch: pytest.MonkeyPatch) -> None:
    ncbi = _Ncbi(monkeypatch, held=["GRCh38"])
    ncbi.newest = {"GRCh38": _NEWER}
    ncbi.current = {"GRCh38": _CURRENT}
    ncbi.outcome = {"GRCh38": None}

    assert _check() == []
    assert ncbi.mails == []


def test_the_check_runs_only_with_the_setting_on_and_stops_with_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    checks: list[int] = []

    async def check() -> list[ClinvarLoadJobOut]:
        checks.append(1)
        return []

    monkeypatch.setattr(weekly, "check_for_new_clinvar_releases", check)
    monkeypatch.setattr(weekly, "FIRST_CHECK_DELAY", timedelta(seconds=0))

    async def run(enabled: bool) -> None:
        monkeypatch.setattr(weekly.settings, "clinvar_weekly_load", enabled)
        await weekly.start_clinvar_weekly_load()
        await asyncio.sleep(0.05)
        await weekly.stop_clinvar_weekly_load()

    asyncio.run(run(False))
    assert checks == []
    asyncio.run(run(True))
    assert checks == [1]
