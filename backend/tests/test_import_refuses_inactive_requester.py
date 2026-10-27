"""A queued import does not run as an account deactivated since it was queued (found while fixing SEC-32).

The worker runs a queued import job as the account that requested it, found by the job's
``requested_by_id`` (SEC-32), and took that account whatever its state: an account an
administrator deactivated between the request and the run had its import run as it, though
deactivating an account ends its access at once (docs/security-posture.md). Such a job is now
ended failed, saying why, before anything of the family is written, as a job whose requester
no longer exists already was; the log names neither the account nor its address. An import
already running when its requester is deactivated runs to its end: the account is checked
once, when the import starts.

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


def _requester(*, is_active: bool) -> CurrentUser:
    return CurrentUser(
        id=REQUESTER_ID,
        username=REQUESTER_EMAIL,
        email=REQUESTER_EMAIL,
        first_name="Given-c1",
        last_name="Surname-c1",
        role="admin",
        is_active=is_active,
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
        row = {
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
async def test_the_job_of_an_active_account_runs_as_that_account(monkeypatch: pytest.MonkeyPatch) -> None:
    active = _requester(is_active=True)
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: active})

    raised = await job.run()

    assert raised is None
    assert job.ran_as == [active]
    assert job.ends == [{"status": "completed", "completed": True}]


@pytest.mark.asyncio
async def test_an_import_already_running_when_its_requester_is_deactivated_runs_to_its_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The account is checked once, when the import starts: deactivating it while the import
    # runs ends the account's own requests at once, and lets the import finish rather than
    # stop it part-way, which would leave its family to the import's fail-clean restore.
    active = _requester(is_active=True)
    job = _Job(monkeypatch, requester_id=REQUESTER_ID, accounts={REQUESTER_ID: active})
    job.during_import.append(
        lambda: job.accounts.update({REQUESTER_ID: _requester(is_active=False)})
    )

    raised = await job.run()

    assert raised is None
    assert job.looked_up == [REQUESTER_ID]
    assert job.ran_as == [active]
    assert job.ends == [{"status": "completed", "completed": True}]
