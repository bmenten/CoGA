"""A queued import does not run as an account deactivated since it was queued (found while fixing SEC-32), nor as one no longer an administrator (SEC-35).

The worker runs a queued import job as the account that requested it, found by the job's
``requested_by_id`` (SEC-32), and took that account whatever its state: an account an
administrator deactivated between the request and the run had its import run as it, though
deactivating an account ends its access at once (docs/security-posture.md). Such a job is now
ended failed, saying why, before anything of the family is written, as a job whose requester
no longer exists already was; the log names neither the account nor its address. An import
already running when its requester is deactivated runs to its end: the account is checked
once, when the import starts.

Only a superuser or an administrator queues an import (the import routes require one), and the import
checked the requester's access to the job's project only: an administrator demoted to viewer
since, an analyst of that project, still had the import run (SEC-35). Such a job now ends
failed the same way, as the owner decided on 2026-10-28, and so does a job claimed again
after its worker stopped while it was still ``validating``; a superuser or an administrator
has the import run, and one demoted while the import runs has it run to its end. Overwriting an
existing family is an admin's (REQ-SEC-002): an overwrite whose requester is no longer an admin
ends failed the same way; a superuser's update or dry run runs.

These refusals are stored as they are written. A job that fails on any other error, one CoGA
did not raise in its own words, ends with a fixed sentence naming the error's kind, never its
text, which can quote a statement's values (SEC-39).

The job's row and the import are stand-ins: what this tests is whether the import runs, as
whom, and how the job ends.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import family_package_import as package_import
from backend.app.services.access_control import CurrentUser

JOB = "00000000-0000-0000-0000-0000000000b1"
WORKER = "worker-1"
REQUESTER_ID = "00000000-0000-0000-0000-0000000000c1"
REQUESTER_EMAIL = "colleague-deactivated@example.org"


def _requester(*, is_active: bool, role: str = "admin") -> CurrentUser:
    return CurrentUser(
        id=REQUESTER_ID,
        username=REQUESTER_EMAIL,
        email=REQUESTER_EMAIL,
        first_name="Given-c1",
        last_name="Surname-c1",
        role=role,
        is_active=is_active,
        # An analyst of the job's project: the project's check alone lets the import run.
        metadata_project_ids=["project-uuid"],
        created_at=datetime.now(timezone.utc),
    )


class _Job:
    """A job this worker has claimed (``validating``), requested by ``requester_id``, whose
    account the lookup finds as ``accounts`` holds it at that moment. The import is a stand-in
    that records the account it runs as; the job's updates are recorded as the runner gives
    them, each matching the job's row."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        requester_id: str | None,
        accounts: dict[str, CurrentUser],
    ) -> None:
        self.accounts = accounts
        self.looked_up: list[str] = []
        self.ran_as: list[CurrentUser] = []
        self.ends: list[dict[str, Any]] = []
        self.during_import: list[Any] = []
        self.row = row = {
            "id": JOB,
            "submitted_path": "/data/families/FAM001",
            "project_id": "project-uuid",
            "dry_run": False,
            "requested_by_id": requester_id,
            "metadata": {"conflict_mode": "cancel"},
            "logs": [],
        }

        class _Session:
            async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
                return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

            async def rollback(self) -> None:
                return None

            async def __aenter__(self) -> "_Session":
                return self

            async def __aexit__(self, *_exc: Any) -> bool:
                return False

        async def lookup(_session: Any, user_id: str) -> CurrentUser | None:
            self.looked_up.append(user_id)
            return self.accounts.get(user_id)

        async def import_package(_session: Any, **kwargs: Any) -> Any:
            self.ran_as.append(kwargs["user"])
            for step in self.during_import:
                step()
            return SimpleNamespace(error=None, family_id=None, validation=None, datasets=[], logs=["Imported."])

        async def update_job(_session: Any, **kwargs: Any) -> bool:
            self.ends.append({key: kwargs[key] for key in ("status", "error", "completed") if key in kwargs})
            return True

        monkeypatch.setattr(package_import, "get_postgres_sessionmaker", lambda: _Session)
        monkeypatch.setattr(package_import, "get_current_user_by_id", lookup)
        monkeypatch.setattr(package_import, "execute_family_package_import", import_package)
        monkeypatch.setattr(package_import, "_update_job_progress", update_job)

    async def run(self) -> str | None:
        """Run the job as the worker does; the error it raises to the worker, if any."""
        try:
            await package_import.run_family_import_job(job_id=JOB, worker_id=WORKER)
        except RuntimeError as exc:
            return str(exc)
        return None


@pytest.mark.asyncio
async def test_the_job_of_a_deactivated_account_ends_failed_and_its_import_does_not_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: _requester(is_active=False)})
    caplog.set_level(logging.DEBUG)

    raised = await job.run()

    # Before, the import ran as the deactivated account, and the job ended completed.
    assert job.ran_as == [], "the import ran as a deactivated account"
    assert job.looked_up == [REQUESTER_ID]
    assert job.ends == [
        {"status": "failed", "error": package_import.REQUESTER_INACTIVE_ERROR, "completed": True}
    ]
    assert raised == package_import.REQUESTER_INACTIVE_ERROR
    # The job's record says why, and that nothing was written.
    assert "deactivated" in package_import.REQUESTER_INACTIVE_ERROR
    assert "Nothing of the family was written" in package_import.REQUESTER_INACTIVE_ERROR
    # The log names neither the account nor its address: the job's record names its requester.
    for personal in (REQUESTER_ID, REQUESTER_EMAIL, "Given-c1", "Surname-c1"):
        assert personal not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requester_id",
    [None, REQUESTER_ID],
    ids=["account-deleted-link-cleared", "no-account-has-the-id"],
)
async def test_the_job_of_an_account_that_no_longer_exists_still_ends_failed(
    monkeypatch: pytest.MonkeyPatch, requester_id: str | None
) -> None:
    # As before (SEC-32): a deleted account's jobs keep no link to it (ON DELETE SET NULL).
    job = _Job(monkeypatch, requester_id=requester_id, accounts={})

    raised = await job.run()

    assert job.ran_as == []
    assert job.ends == [{"status": "failed", "error": "Requesting user no longer exists", "completed": True}]
    assert raised == "Requesting user no longer exists"


@pytest.mark.asyncio
async def test_the_job_of_an_account_no_longer_an_administrator_ends_failed_and_its_import_does_not_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # SEC-35: demoted to viewer since it queued the import, the account is an analyst of the
    # job's project, which is all the import itself checked.
    demoted = _requester(is_active=True, role="viewer")
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: demoted})
    caplog.set_level(logging.DEBUG)

    raised = await job.run()

    # Before, the import ran as the demoted account, and the job ended completed.
    assert job.ran_as == [], "the import ran as an account that is no longer an administrator"
    assert job.looked_up == [REQUESTER_ID]
    assert job.ends == [
        {"status": "failed", "error": package_import.REQUESTER_NOT_ADMIN_ERROR, "completed": True}
    ]
    assert raised == package_import.REQUESTER_NOT_ADMIN_ERROR
    # The job's record says why, and that nothing was written.
    assert "no longer a superuser or an administrator" in package_import.REQUESTER_NOT_ADMIN_ERROR
    assert "Nothing of the family was written" in package_import.REQUESTER_NOT_ADMIN_ERROR
    # The log names neither the account nor its address.
    for personal in (REQUESTER_ID, REQUESTER_EMAIL, "Given-c1", "Surname-c1"):
        assert personal not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("account", "error"),
    [
        (_requester(is_active=False), "REQUESTER_INACTIVE_ERROR"),
        (_requester(is_active=True, role="viewer"), "REQUESTER_NOT_ADMIN_ERROR"),
    ],
    ids=["deactivated", "no-longer-an-administrator"],
)
async def test_a_job_claimed_again_while_validating_checks_its_requester_again(
    monkeypatch: pytest.MonkeyPatch, account: CurrentUser, error: str
) -> None:
    # A job whose worker stopped before its import wrote anything (still ``validating``) is
    # claimed again by another worker and run from the start: its requester is checked again,
    # as it is by then.
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: account})

    with pytest.raises(RuntimeError) as refused:
        await package_import.handle_claimed_family_import_job(
            {"id": JOB, "status": "validating"}, worker_id=WORKER
        )

    assert job.ran_as == []
    expected = getattr(package_import, error)
    assert str(refused.value) == expected
    assert job.ends == [{"status": "failed", "error": expected, "completed": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "superuser"])
async def test_the_job_of_an_active_administrator_runs_as_that_account(
    monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    active = _requester(is_active=True, role=role)
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: active})

    raised = await job.run()

    assert raised is None
    assert job.ran_as == [active]
    assert job.ends == [{"status": "completed", "completed": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [_requester(is_active=False), _requester(is_active=True, role="viewer")],
    ids=["deactivated", "no-longer-an-administrator"],
)
async def test_an_import_already_running_when_its_requester_is_deactivated_or_demoted_runs_to_its_end(
    monkeypatch: pytest.MonkeyPatch, changed: CurrentUser
) -> None:
    # The account is checked once, when the import starts: deactivating or demoting it while
    # the import runs ends the account's own requests at once (or its administration), and
    # lets the import finish rather than stop it part-way, which would leave its family to
    # the import's fail-clean restore.
    active = _requester(is_active=True)
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: active})
    job.during_import.append(lambda: job.accounts.update({REQUESTER_ID: changed}))

    raised = await job.run()

    assert raised is None
    assert job.looked_up == [REQUESTER_ID]
    assert job.ran_as == [active]
    assert job.ends == [{"status": "completed", "completed": True}]


@pytest.mark.asyncio
async def test_a_job_that_fails_on_an_unexpected_error_ends_naming_the_error_by_its_kind(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # SEC-39. A job's error, which the import pages show, was the error's text: for a failed
    # statement, its SQL and the values it was given. It is now a fixed sentence naming the
    # error's kind, and the log names the job; a refusal in CoGA's own words, as those above,
    # is stored as it is written.
    from sqlalchemy.exc import OperationalError

    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: _requester(is_active=True)})
    failure = OperationalError(
        "UPDATE families SET family_id = %(family_id)s", {"family_id": "FAMILY-VALUE"}, ConnectionResetError()
    )

    def fail() -> None:
        raise failure

    job.during_import.append(fail)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OperationalError):
        await job.run()

    assert job.ends == [
        {
            "status": "failed",
            "error": (
                "The import failed on an unexpected error (OperationalError (ConnectionResetError)); "
                "the server log shows where it failed."
            ),
            "completed": True,
        }
    ]
    assert f"Family package import job {JOB} failed" in caplog.messages
    assert "FAMILY-VALUE" not in "".join(caplog.messages)


# --------------------------------------------------------------------------- #
# Overwriting an existing family is an admin's (REQ-SEC-002)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_overwrite_of_an_account_no_longer_an_administrator_ends_failed_and_does_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An admin queued an overwrite and was made a superuser since: a superuser imports and adds
    # missing datasets, but replacing a family's data is an admin's.
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: _requester(is_active=True, role="superuser")})
    job.row["metadata"] = {"conflict_mode": "overwrite"}

    raised = await job.run()

    assert job.ran_as == []
    assert job.ends == [
        {"status": "failed", "error": package_import.REQUESTER_CANNOT_OVERWRITE_ERROR, "completed": True}
    ]
    assert raised == package_import.REQUESTER_CANNOT_OVERWRITE_ERROR
    assert "Nothing of the family was written" in package_import.REQUESTER_CANNOT_OVERWRITE_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "conflict_mode", "dry_run"),
    [("admin", "overwrite", False), ("superuser", "overwrite", True), ("superuser", "update", False)],
    ids=["an-admins-overwrite", "a-superusers-dry-run", "a-superusers-update"],
)
async def test_an_admins_overwrite_and_a_superusers_update_or_dry_run_run(
    monkeypatch: pytest.MonkeyPatch, role: str, conflict_mode: str, dry_run: bool
) -> None:
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: _requester(is_active=True, role=role)})
    job.row["metadata"] = {"conflict_mode": conflict_mode}
    job.row["dry_run"] = dry_run

    assert await job.run() is None
    assert [user.role for user in job.ran_as] == [role]


@pytest.mark.parametrize(
    ("role", "conflict_mode", "dry_run", "queued"),
    [
        ("superuser", "overwrite", False, False),
        ("superuser", "overwrite", True, True),
        ("superuser", "update", False, True),
        ("admin", "overwrite", False, True),
    ],
    ids=["a-superusers-overwrite", "a-superusers-dry-run", "a-superusers-update", "an-admins-overwrite"],
)
def test_the_route_refuses_a_superuser_the_overwrite_before_queuing_it(
    monkeypatch: pytest.MonkeyPatch, role: str, conflict_mode: str, dry_run: bool, queued: bool
) -> None:
    from backend.app.routers import family_imports
    from backend.tests._family_import_fakes import signed_in_client

    calls: list[dict[str, Any]] = []

    async def approve(session: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        raise RuntimeError("queued")

    monkeypatch.setattr(family_imports, "approve_delivery", approve)
    body = {"revision": 0, "dry_run": dry_run, "conflict_mode": conflict_mode}
    with signed_in_client(role, raise_server_exceptions=False) as client:
        response = client.post("/api/family-imports/deliveries/00000000-0000-4000-8000-0000000000d1/approve", json=body)

    if queued:
        assert [call["conflict_mode"] for call in calls] == [conflict_mode]
        return
    assert (response.status_code, response.json()) == (
        403,
        {"detail": family_imports.OVERWRITE_ADMIN_ONLY_DETAIL},
    )
    assert calls == []
