"""A person's decision on a delivery of a watched import folder: the rules, without a database.

A delivery is approved when it is ready, or when it needs attention only because its family
exists and the approver chooses update or overwrite; a new family is imported with cancel; its
warnings must be acknowledged; its folder must be as it was checked; the approval names the
revision of the package it saw, and one corrected since is refused. The import job is queued as
the approver, with the delivery's manifest, project and record (its corrections included), and in the same transaction the
delivery is queued (a dry run leaves it as it was) and the decision recorded. An open delivery is
refused with a reason in plain text. Overwrite stays an administrator's. The paths through the
real stores are in e2e/test_e2e_import_delivery_approval.py.

All samples and values here are synthetic.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from backend.app.services import import_delivery_review as review
from backend.tests._family_import_fakes import signed_in_client

_ID = "00000000-0000-4000-8000-000000000001"
_FINGERPRINT = "a" * 64


def _delivery(**fields: Any) -> dict[str, Any]:
    row = {
        "id": _ID,
        "folder_path": "/data/share/FAM1",
        "folder_name": "FAM1",
        "state": "ready",
        "package_fingerprint": _FINGERPRINT,
        "marker_sha256": "b" * 64,
        "run_id": "run-1",
        "family_id": "FAM1",
        "project_id": "00000000-0000-4000-8000-0000000000bb",
        "issues": [],
        "warnings": [],
        "manifest_text": None,
        "package": None,
        "origin": "watched",
    }
    row.update(fields)
    return row


class _Session:
    """Answers the locked read with ``row`` and keeps every statement after it."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append((str(statement), dict(params or {})))
        row = self.row
        # The changed deliveries a refusal ends (package_changed_after_import.resolve_refused): none.
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row), all=lambda: [])

    async def commit(self) -> None:
        self.commits += 1


_USER = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa", username="superuser", email="superuser@example.org")


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The jobs queued, the events recorded and the folder as it was checked."""
    calls: list[dict[str, Any]] = []

    async def queue(session: Any, **kwargs: Any) -> Any:
        calls.append({"queue": kwargs})
        return SimpleNamespace(id="00000000-0000-4000-8000-0000000000cc")

    async def record(session: Any, **kwargs: Any) -> None:
        calls.append({"event": kwargs})

    async def fingerprint(_folder: str) -> str:
        return _FINGERPRINT

    async def delivery(_session: Any, delivery_id: str) -> Any:
        return SimpleNamespace(id=delivery_id)

    monkeypatch.setattr(review, "queue_family_import_job", queue)
    monkeypatch.setattr(review, "record_clinical_event", record)
    monkeypatch.setattr(review, "_folder_fingerprint", fingerprint)
    async def no_revision(_session: Any, _delivery_id: str) -> tuple[int, None]:
        return 0, None

    monkeypatch.setattr(review, "get_import_delivery", delivery)
    monkeypatch.setattr(review, "latest_revision", no_revision)
    return calls


async def _approve(session: _Session, **kwargs: Any) -> Any:
    options = {"revision": 0, "dry_run": False, "conflict_mode": "cancel", "acknowledge_warnings": False, **kwargs}
    return await review.approve_delivery(session, delivery_id=_ID, user=_USER, **options)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_ready_delivery_is_queued_as_the_approver_in_one_transaction(queued: list[dict[str, Any]]) -> None:
    session = _Session(_delivery(manifest_text="schema_version: 1\n"))

    _delivery_out, job = await _approve(session)

    [queue, event] = queued
    assert queue["queue"] == {
        "folder_path": "/data/share/FAM1",
        "project_id": "00000000-0000-4000-8000-0000000000bb",
        "dry_run": False,
        "requested_family_id": None,
        "conflict_mode": "cancel",
        "requested_by": _USER,
        "manifest_text": "schema_version: 1\n",
        "delivery": {
            "id": _ID,
            "run_id": "run-1",
            "marker_sha256": "b" * 64,
            "package_fingerprint": _FINGERPRINT,
            "revision": 0,
            "package_sha256": None,
            "corrections": [],
            "origin": "watched",
        },
        "commit": False,
    }
    assert "FOR UPDATE" in session.statements[0][0]
    update_sql, update = session.statements[1]
    assert "UPDATE import_deliveries" in update_sql and update == {"job": str(job.id), "dry_run": False, "id": _ID}
    assert (event["event"]["action"], event["event"]["actor"], event["event"]["actor_id"]) == (
        "import_delivery_queued",
        "superuser",
        _USER.id,
    )
    assert event["event"]["after"]["state"] == "queued"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_a_dry_run_is_recorded_and_leaves_the_delivery_as_it_was(queued: list[dict[str, Any]]) -> None:
    session = _Session(_delivery())

    await _approve(session, dry_run=True)

    assert queued[0]["queue"]["dry_run"] is True
    assert session.statements[1][1]["dry_run"] is True
    assert (queued[1]["event"]["action"], queued[1]["event"]["after"]["state"]) == ("import_delivery_dry_run", "ready")


@pytest.mark.asyncio
async def test_a_delivery_of_an_existing_family_is_queued_into_it_with_the_choice(queued: list[dict[str, Any]]) -> None:
    issues = [{"code": "existing_family", "message": "x"}, {"code": "existing_samples", "message": "y"}]
    session = _Session(_delivery(state="needs_attention", issues=json.dumps(issues)))

    await _approve(session, conflict_mode="update")

    assert (queued[0]["queue"]["requested_family_id"], queued[0]["queue"]["conflict_mode"]) == ("FAM1", "update")


@pytest.mark.asyncio
async def test_a_delivery_whose_members_differ_from_its_family_is_queued_only_to_overwrite(
    queued: list[dict[str, Any]],
) -> None:
    # An update changes no member the family holds; an overwrite makes the package's members the
    # family's pedigree.
    issues = [{"code": "existing_family", "message": "x"}, {"code": "package_members_differ_from_family", "message": "y"}]

    with pytest.raises(HTTPException) as refused:
        await _approve(_Session(_delivery(state="needs_attention", issues=json.dumps(issues))), conflict_mode="update")
    assert (refused.value.status_code, refused.value.detail) == (400, review.MEMBERS_DIFFER_NEED_AN_OVERWRITE)
    assert queued == []

    await _approve(_Session(_delivery(state="needs_attention", issues=json.dumps(issues))), conflict_mode="overwrite")
    assert (queued[0]["queue"]["requested_family_id"], queued[0]["queue"]["conflict_mode"]) == ("FAM1", "overwrite")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "options", "status"),
    [
        ({"state": "queued"}, {}, 409),
        ({"state": "refused"}, {}, 409),
        ({"state": "needs_attention", "issues": [{"code": "file_checksum_differs"}]}, {}, 409),
        ({"state": "needs_attention", "issues": [{"code": "existing_samples"}]}, {"conflict_mode": "update"}, 409),
        ({"state": "needs_attention", "issues": [{"code": "existing_family"}]}, {}, 400),
        # The members differ, and the family does not exist: nothing a choice resolves.
        ({"state": "needs_attention", "issues": [{"code": "package_members_differ_from_family"}]}, {"conflict_mode": "overwrite"}, 409),
        ({}, {"conflict_mode": "update"}, 400),
        ({"warnings": [{"code": "optional_dataset_missing"}]}, {}, 400),
        ({"project_id": None}, {}, 409),
    ],
)
async def test_a_delivery_that_cannot_be_approved_so_is_refused_and_nothing_queued(
    queued: list[dict[str, Any]], row: dict[str, Any], options: dict[str, Any], status: int
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _approve(_Session(_delivery(**row)), **options)

    assert refused.value.status_code == status
    assert queued == []


@pytest.mark.asyncio
async def test_a_delivery_whose_folder_changed_is_not_approved(queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def changed(_folder: str) -> str:
        return "c" * 64

    monkeypatch.setattr(review, "_folder_fingerprint", changed)

    with pytest.raises(HTTPException) as refused:
        await _approve(_Session(_delivery()))

    assert (refused.value.status_code, refused.value.detail) == (409, review.DELIVERY_CHANGED)
    assert queued == []


@pytest.mark.asyncio
async def test_a_delivery_corrected_since_the_approver_looked_is_not_approved(
    queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def revised(_session: Any, _delivery_id: str) -> tuple[int, dict[str, Any]]:
        return 2, {"schema": "coga-package/1"}

    monkeypatch.setattr(review, "latest_revision", revised)

    with pytest.raises(HTTPException) as refused:
        await _approve(_Session(_delivery()), revision=1)

    assert refused.value.status_code == 409 and refused.value.detail == review.DELIVERY_REVISED_SINCE
    assert queued == []


@pytest.mark.asyncio
async def test_a_corrected_delivery_is_queued_with_its_package_and_corrections(
    queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    package = {"schema": "coga-package/1", "members": [{"sample_id": "PROBAND", "sex": "male"}]}
    when = __import__("datetime").datetime(2026, 11, 10, 12, 0, tzinfo=__import__("datetime").timezone.utc)
    correction = {
        "revision": 1,
        "changes": [{"path": ["members", "PROBAND", "clinical_status"], "kind": "laboratory", "before": None, "after": "affected"}],
        "reason": "Clinical status from the request form.",
        "created_by_name": "superuser",
        "created_at": when,
    }

    class _Corrected(_Session):
        async def execute(self, statement: Any, params: Any = None) -> Any:
            if "FROM import_delivery_revisions" in str(statement):
                return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [correction]))
            return await super().execute(statement, params)

    async def revised(_session: Any, _delivery_id: str) -> tuple[int, dict[str, Any]]:
        return 1, package

    monkeypatch.setattr(review, "latest_revision", revised)

    await _approve(_Corrected(_delivery()), revision=1)

    delivery = queued[0]["queue"]["delivery"]
    assert (delivery["revision"], delivery["package_sha256"]) == (1, review.package_document_sha256(package))
    assert delivery["corrections"] == [
        {
            "revision": 1,
            "changes": correction["changes"],
            "reason": "Clinical status from the request form.",
            "by": "superuser",
            "at": "2026-11-10T12:00:00+00:00",
        }
    ]
    assert queued[1]["event"]["after"]["revision"] == 1


@pytest.mark.asyncio
async def test_a_delivery_that_is_not_there_answers_404(queued: list[dict[str, Any]]) -> None:
    with pytest.raises(HTTPException) as refused:
        await _approve(_Session(None))
    assert refused.value.status_code == 404

    with pytest.raises(HTTPException) as not_an_id:
        await review.approve_delivery(
            _Session(None),  # type: ignore[arg-type]
            delivery_id="nope",
            user=_USER,  # type: ignore[arg-type]
            revision=0,
            dry_run=False,
            conflict_mode="cancel",
            acknowledge_warnings=False,
        )
    assert not_an_id.value.status_code == 404


@pytest.mark.asyncio
async def test_an_open_delivery_is_refused_with_its_reason(queued: list[dict[str, Any]]) -> None:
    session = _Session(_delivery(state="incomplete"))

    await review.refuse_delivery(session, delivery_id=_ID, user=_USER, reason="Rerun requested.")  # type: ignore[arg-type]

    assert "SET state = 'refused'" in session.statements[1][0]
    [event] = queued
    assert (event["event"]["action"], event["event"]["after"]["reason"]) == ("import_delivery_refused", "Rerun requested.")
    assert session.commits == 1


@pytest.mark.asyncio
async def test_a_decided_delivery_is_not_refused(queued: list[dict[str, Any]]) -> None:
    with pytest.raises(HTTPException) as refused:
        await review.refuse_delivery(_Session(_delivery(state="imported")), delivery_id=_ID, user=_USER, reason="x")  # type: ignore[arg-type]

    assert refused.value.status_code == 409
    assert queued == []


@pytest.mark.asyncio
async def test_a_folder_that_cannot_be_fingerprinted_has_none(tmp_path) -> None:
    assert await review._folder_fingerprint(str(tmp_path / "missing")) is None


def test_overwrite_stays_an_administrators_and_a_refusal_needs_a_reason_in_plain_text() -> None:
    with signed_in_client("superuser") as client:
        overwrite = client.post(
            f"/api/family-imports/deliveries/{_ID}/approve", json={"revision": 0, "conflict_mode": "overwrite"}
        )
        blank = client.post(f"/api/family-imports/deliveries/{_ID}/refuse", json={"reason": "   "})
        nul = client.post(f"/api/family-imports/deliveries/{_ID}/refuse", json={"reason": "a\u0000b"})
        empty = client.post(f"/api/family-imports/deliveries/{_ID}/refuse", json={"reason": ""})

    assert overwrite.status_code == 403
    assert (blank.status_code, nul.status_code, empty.status_code) == (400, 400, 422)
