"""A UUID a request body names a record by is read once, and its record looked up before
anything is written (``core/sql.py``: ``require_uuid``).

The NIPT artifact list's add, auto-seed and table import, and the import request, bound the
``assembly_id`` or ``project_id`` of their body as they received it. A value that spells no
UUID failed asyncpg's uuid codec (or the insert's uuid cast) with a 500, and so did a UUID in
braces or after ``urn:uuid:``; a UUID of no assembly or project failed the add's or the job's
foreign key, also with a 500. Over the routes, signed in as an admin on a session that finds
nothing (synthetic ids):

* a value that spells no UUID is answered as a UUID of no record is, ``404 Assembly not
  found`` or ``404 Project not found``, before any query: it is never bound;
* a UUID of no record, in any spelling, is looked up by its canonical text and answered so;
  nothing is written: no insert, no audit event, no commit;
* a viewer is refused (403) before the body is read.

On a session that finds the assembly or the project, every statement binds its canonical id
and the artifact list's audit event records it: an id in capitals used to be recorded as it
came, and an audit search by the assembly's id missed the event. An import request that names
no project (``None`` or ``""``) is queued without one, as before.

The same over the real API and Postgres: e2e/test_e2e_request_body_uuid.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.services import family_package_jobs, nipt_artifact_pg
from backend.app.services.access_control import CurrentUser

CANONICAL = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
# Names no record: the session of the route tests finds nothing for any id.
UNKNOWN = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b"
NOT_A_UUID = "PROBEX"
SPELLINGS = ["{" + CANONICAL.upper() + "}", "urn:uuid:" + CANONICAL, CANONICAL.upper(), CANONICAL.replace("-", "")]
TABLE = "CHROM\tPOS\tREF\tALT\nchr1\t100\tA\tG\n"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "_Result":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        [row] = self._rows
        return row

    def all(self) -> list[Any]:
        return list(self._rows)


class _Session:
    """Answers a statement that contains one of ``found``'s markers with its rows, and every
    other with none; records each statement with its parameters, and each commit."""

    def __init__(self, found: dict[str, list[Any]] | None = None) -> None:
        self._found = found or {}
        self.calls: list[tuple[str, Any]] = []
        self.commits = 0

    @property
    def bound(self) -> list[str]:
        return [
            str(value)
            for _sql, params in self.calls
            for row in (params if isinstance(params, list) else [params or {}])
            for value in row.values()
        ]

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        for marker, rows in self._found.items():
            if marker in sql:
                return _Result(rows)
        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _user(role: str) -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username=role,
        email=f"{role}@example.org",
        role=role,
        created_at=NOW,
    )


@pytest.fixture()
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The clinical audit events the artifact list records, captured."""
    recorded: list[dict[str, Any]] = []

    async def record(_session: Any, **event: Any) -> None:
        recorded.append(event)

    monkeypatch.setattr(nipt_artifact_pg, "record_clinical_event", record)
    return recorded


class _Api:
    def __init__(self, client: TestClient, session: _Session) -> None:
        self.client = client
        self.session = session
        self.user = _user("admin")


@pytest.fixture()
def api(events: list[dict[str, Any]]) -> Iterator[_Api]:
    """The routes, on a database that holds no record; signed in as an admin unless a test
    signs in someone else."""
    session = _Session()
    signed_in: dict[str, _Api] = {}
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def override_get_postgres_session():
        yield session

    async def override_get_current_user() -> CurrentUser:
        return signed_in["api"].user

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as client:
            signed_in["api"] = _Api(client, session)
            yield signed_in["api"]
    finally:
        app.dependency_overrides = original_overrides


ASSEMBLY_NOT_FOUND = (404, {"detail": "Assembly not found"})
PROJECT_NOT_FOUND = (404, {"detail": "Project not found"})
# route -> (the answer to an id that names no record, the table its record is looked up in)
ROUTES = {
    "add": (ASSEMBLY_NOT_FOUND, "FROM assemblies"),
    "seed": (ASSEMBLY_NOT_FOUND, "FROM assemblies"),
    "import": (ASSEMBLY_NOT_FOUND, "FROM assemblies"),
    "job": (PROJECT_NOT_FOUND, "FROM projects"),
}


def _send(api: _Api, route: str, value: str) -> tuple[int, Any]:
    if route == "add":
        response = api.client.post("/api/admin/nipt/artifacts", json={"assembly_id": value, "variant_id": "1-100-A-G"})
    elif route == "seed":
        response = api.client.post("/api/admin/nipt/artifacts/auto-seed", json={"assembly_id": value})
    elif route == "import":
        response = api.client.post(
            "/api/admin/nipt/artifacts/import",
            data={"assembly_id": value},
            files={"file": ("recurrent.tsv", TABLE.encode(), "text/tab-separated-values")},
        )
    else:
        response = api.client.post(
            "/api/family-imports",
            json={"folder_path": "/data/families/FAM1", "project_id": value, "dry_run": True},
        )
    return response.status_code, response.json()


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize(
    "value",
    [
        NOT_A_UUID,
        "  ",
        "{" + UNKNOWN,
        # What uuid.UUID reads as a number: it would name a different-looking record.
        "0x" + UNKNOWN[2:],
    ],
)
def test_a_value_that_spells_no_uuid_is_answered_as_an_unknown_record_without_a_query(
    api: _Api, events: list[dict[str, Any]], route: str, value: str
) -> None:
    # It reached asyncpg's uuid codec, or the insert's uuid cast: a 500.
    assert _send(api, route, value) == ROUTES[route][0]
    assert api.session.calls == []
    assert events == [] and api.session.commits == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("spelling", [UNKNOWN, "{" + UNKNOWN + "}", "urn:uuid:" + UNKNOWN, UNKNOWN.upper()])
def test_a_uuid_of_no_record_is_looked_up_by_its_canonical_text_and_nothing_is_written(
    api: _Api, events: list[dict[str, Any]], route: str, spelling: str
) -> None:
    answer, table = ROUTES[route]
    # The braces and the prefix failed the uuid codec, and an unknown UUID the add's and the
    # job's foreign key: a 500 each.
    assert _send(api, route, spelling) == answer
    [(lookup, _params)] = api.session.calls
    assert table in lookup
    assert api.session.bound == [UNKNOWN]
    assert events == [] and api.session.commits == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("value", [NOT_A_UUID, UNKNOWN, CANONICAL])
def test_a_viewer_is_refused_before_the_id_is_read(api: _Api, route: str, value: str) -> None:
    api.user = _user("viewer")
    assert _send(api, route, value) == (403, {"detail": "Admin access required"})
    assert api.session.calls == []


# --- a record that is found: bound and recorded by its canonical id ----------------------


def _artifact_row(variant_id: str) -> dict[str, Any]:
    return {
        "id": "00000000-0000-4000-8000-0000000000a1",
        "assembly_id": CANONICAL,
        "assay_key": "nipt_cfdna",
        "variant_id": variant_id,
        "recurrence_count": 0,
        "source": "curated",
        "label": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_an_artifact_added_by_any_spelling_is_stored_and_audited_under_the_canonical_id(
    events: list[dict[str, Any]], spelling: str
) -> None:
    session = _Session(
        {"FROM assemblies": [("GRCh38",)], "INSERT INTO nipt_artifact_variants": [_artifact_row("1-100-A-G")]}
    )
    asyncio.run(
        nipt_artifact_pg.add_nipt_artifact(
            session,  # type: ignore[arg-type]
            assembly_id=spelling,
            assay_key="nipt_cfdna",
            variant_id="1-100-A-G",
        )
    )
    # The assembly's lookup, the entry's lookup and the insert.
    assert session.bound.count(CANONICAL) == 3
    assert spelling not in session.bound
    [event] = events
    assert event["action"] == "nipt_artifact_added"
    assert event["metadata"] == {"assembly_id": CANONICAL, "assay_key": "nipt_cfdna"}
    assert session.commits == 1


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_an_auto_seed_by_any_spelling_writes_and_audits_the_canonical_id(
    monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]], spelling: str
) -> None:
    async def recurrent(assembly_name: str, **_kwargs: Any) -> list[tuple[str, int]]:
        assert assembly_name == "GRCh38"
        return [("1-100-A-G", 6)]

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_small_variant_ids", recurrent)
    session = _Session({"FROM assemblies": [("GRCh38",)]})
    result = asyncio.run(
        nipt_artifact_pg.auto_seed_nipt_artifacts(
            session,  # type: ignore[arg-type]
            assembly_id=spelling,
            assay_key="nipt_cfdna",
        )
    )
    assert result["seeded"] == 1
    # The assembly's lookup and the upserted entry.
    assert session.bound.count(CANONICAL) == 2
    assert spelling not in session.bound
    [event] = events
    assert event["metadata"]["assembly_id"] == CANONICAL


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_table_imported_by_any_spelling_writes_and_audits_the_canonical_id(
    monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]], spelling: str
) -> None:
    async def unprotected(_assembly_name: str, _variant_ids: list[str], **_kwargs: Any) -> dict[str, str]:
        return {}

    monkeypatch.setattr(nipt_artifact_pg, "fetch_artifact_protection_flags", unprotected)
    session = _Session({"FROM assemblies": [("GRCh38",)]})
    summary = asyncio.run(
        nipt_artifact_pg.import_nipt_artifact_table(
            session,  # type: ignore[arg-type]
            assembly_id=spelling,
            assay_key="nipt_cfdna",
            text_value=TABLE,
            filename="recurrent.tsv",
        )
    )
    assert summary["imported"] == 1
    # The assembly's lookup and the inserted allele.
    assert session.bound.count(CANONICAL) == 2
    assert spelling not in session.bound
    [event] = events
    assert event["metadata"]["assembly_id"] == CANONICAL


def _job_row(project_id: str | None) -> dict[str, Any]:
    return {
        "id": "00000000-0000-4000-8000-0000000000b1",
        "submitted_path": "/data/families/FAM1",
        "family_id": None,
        "project_id": project_id,
        "status": "queued",
        "dry_run": True,
        "worker_id": None,
        "requested_by": "admin@example.org",
        "requested_at": NOW,
        "started_at": None,
        "heartbeat_at": None,
        "completed_at": None,
        "validation_errors": [],
        "validation_warnings": [],
        "logs": [],
        "dataset_summaries": [],
        "metadata": {},
        "error": None,
    }


def _queue(session: _Session, project_id: str | None) -> Any:
    return asyncio.run(
        family_package_jobs.queue_family_import_job(
            session,  # type: ignore[arg-type]
            folder_path="/data/families/FAM1",
            project_id=project_id,
            dry_run=True,
            requested_by="admin@example.org",
        )
    )


def _insert_params(session: _Session) -> dict[str, Any]:
    [params] = [params for sql, params in session.calls if sql.startswith("INSERT INTO family_import_jobs")]
    return params


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_an_import_job_names_its_project_by_the_canonical_id(spelling: str) -> None:
    session = _Session({"FROM projects": [(1,)], "INSERT INTO family_import_jobs": [_job_row(CANONICAL)]})
    _queue(session, spelling)
    # The project was looked up, and the job written, under the canonical text: the prefix
    # failed the insert's uuid cast (500).
    assert [params for _sql, params in session.calls][0] == {"project_id": CANONICAL}
    assert _insert_params(session)["project_id"] == CANONICAL
    assert spelling not in session.bound
    assert session.commits == 1


@pytest.mark.parametrize("project_id", [None, ""])
def test_an_import_job_that_names_no_project_is_queued_without_one(project_id: str | None) -> None:
    session = _Session({"INSERT INTO family_import_jobs": [_job_row(None)]})
    job = _queue(session, project_id)
    # No project to look up; the job holds none.
    assert len(session.calls) == 1
    assert _insert_params(session)["project_id"] is None
    assert job.project_id is None
