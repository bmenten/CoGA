"""A UUID in a request is read once, and bound as its canonical text (``core/sql.py``).

asyncpg's uuid codec takes nothing but hex digits and hyphens. A route that bound the value
it received into ``CAST(:x AS uuid)`` answered a non-UUID in its path or query string with a
500 (the raw-file download and verify, the import job, the HPO annotation and NIPT artifact
routes); a route that checked the value with ``uuid.UUID`` and then bound it as received did
the same for a spelling ``uuid.UUID`` reads and asyncpg does not, such as ``{…}`` or
``urn:uuid:…`` (the panel, preset, user, project, species and clinical CNV routes, and the
panel filter of the variant pages). Each now reads the value with ``canonical_uuid`` or
``require_uuid``:

* a value that spells no UUID names no record, and is answered as an unknown record is (the
  route's own 404, or the 400 it answers an invalid id with) without being bound;
* a value that spells one is bound as its canonical text, so each spelling of a record's id
  names that record: a panel read by its id in braces keeps its genes, the variant explorer
  filters by that panel, and a member may name their own project in capitals.

What ``uuid.UUID`` merely tolerates (an underscore, a space or a sign among the digits,
``0x``, digits of another script) spells no UUID: read as a number, such a value names a
different-looking record.

The same over the real API and Postgres: e2e/test_e2e_request_malformed_uuid.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator
from urllib.parse import quote
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.core.sql import canonical_uuid, require_uuid
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.routers import families as families_router
from backend.app.routers import families_small_variants as small_variants_router
from backend.app.routers import families_structural_variants as structural_variants_router
from backend.app.routers import variant_explorer as variant_explorer_router
from backend.app.services import (
    hpo_service,
    metadata_service,
    panel_metadata_service,
    ped_service,
    reference_metadata_service,
    small_variant_review_tags,
)
from backend.app.services.access_control import CurrentUser

CANONICAL = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
# Names no record: the session below finds nothing for any id.
UNKNOWN = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b"
NOT_A_UUID = "PROBEX"


# --- the helper --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        CANONICAL,
        CANONICAL.upper(),
        CANONICAL.replace("-", ""),
        "{" + CANONICAL + "}",
        "urn:uuid:" + CANONICAL,
        "{" + CANONICAL.upper().replace("-", "") + "}",
    ],
)
def test_each_spelling_of_a_uuid_reads_as_its_canonical_text(spelling: str) -> None:
    assert canonical_uuid(spelling) == CANONICAL
    assert require_uuid(spelling, "Invalid id") == CANONICAL


def test_a_uuid_object_reads_as_its_canonical_text() -> None:
    assert canonical_uuid(UUID(CANONICAL)) == CANONICAL


@pytest.mark.parametrize(
    "value",
    [
        NOT_A_UUID,
        "",
        None,
        123,
        CANONICAL[:-1],
        CANONICAL + "0",
        " " + CANONICAL,
        CANONICAL + "\n",
        "{" + CANONICAL,
        CANONICAL + "}",
        "urn:uuid:{" + CANONICAL + "}",
        "URN:UUID:" + CANONICAL,
        # Hyphens other than 8-4-4-4-12, or only some of them.
        "0a1b-2c3d-4e5f-4a6b-8c7d-9e0f-1a2b3c4d",
        "0a1b2c3d-4e5f4a6b-8c7d-9e0f1a2b3c4d",
        # What uuid.UUID reads as a number: each would name a different-looking record.
        "0_1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        " a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        "+a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        "0x1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        "０a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        "٠" * 32,
        "0a1b2c3duuid:-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        CANONICAL[:-1] + "\x00",
    ],
)
def test_what_is_no_uuid_spelling_names_no_record(value: Any) -> None:
    assert canonical_uuid(value) is None
    with pytest.raises(HTTPException) as refused:
        require_uuid(value, "Invalid id")
    assert (refused.value.status_code, refused.value.detail) == (400, "Invalid id")


def test_a_route_answers_with_its_own_not_found() -> None:
    with pytest.raises(HTTPException) as refused:
        require_uuid(NOT_A_UUID, "File not found", status_code=404)
    assert (refused.value.status_code, refused.value.detail) == (404, "File not found")


# --- the routes ---------------------------------------------------------------------------


class _Empty:
    """No rows, however the result is read."""

    rowcount = 0

    def mappings(self) -> "_Empty":
        return self

    def scalars(self) -> "_Empty":
        return self

    def first(self) -> None:
        return None

    def all(self) -> list[Any]:
        return []

    def scalar_one_or_none(self) -> None:
        return None

    def scalar(self) -> None:
        return None


class _Rows(_Empty):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _Scalar(_Empty):
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _RecordingSession:
    """Answers each statement with the next queued result (no rows once they run out), and
    records every value a statement binds."""

    def __init__(self, *results: _Empty) -> None:
        self._results = list(results)
        self.bound: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Empty:
        for row in params if isinstance(params, list) else [params or {}]:
            for value in row.values():
                self.bound.extend(str(item) for item in (value if isinstance(value, (list, tuple)) else [value]))
        return self._results.pop(0) if self._results else _Empty()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username="admin",
        email="admin@example.org",
        role="admin",
        created_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
    )


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, _RecordingSession]]:
    """The admin, signed in, on a database that holds no record; the family routes find
    their family."""
    session = _RecordingSession()
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def override_get_postgres_session():
        yield session

    async def override_get_current_user() -> CurrentUser:
        return _admin()

    async def family_context(_session: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(family_uuid="00000000-0000-4000-8000-0000000000f1")

    async def hpo_tables_available(_session: Any, _tables: Any) -> bool:
        return True

    for router in (families_router, small_variants_router, structural_variants_router):
        monkeypatch.setattr(router, "build_family_metadata_context", family_context)
    monkeypatch.setattr(hpo_service, "_postgres_tables_available", hpo_tables_available)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as client:
        yield client, session
    app.dependency_overrides = original_overrides


SAME = "as an unknown record"

# (method, path, JSON body, the answer to a UUID that names no record, the answer to a value
# that is no UUID: SAME, or the 400 the route answers an invalid id with)
ROUTES: list[tuple[str, str, Any, tuple[int, Any], Any]] = [
    ("GET", "/api/admin/data/files/{v}/download", None, (404, {"detail": "File not found"}), SAME),
    ("POST", "/api/admin/data/files/{v}/verify", None, (404, {"detail": "File not found"}), SAME),
    ("GET", "/api/family-imports/{v}", None, (404, {"detail": "Family import job not found"}), SAME),
    ("PUT", "/api/families/FAM1/hpo/{v}", {}, (404, {"detail": "HPO annotation was not found"}), SAME),
    ("DELETE", "/api/families/FAM1/hpo/{v}", None, (404, {"detail": "HPO annotation was not found"}), SAME),
    ("DELETE", "/api/admin/nipt/artifacts/{v}", None, (404, {"detail": "Artifact not found"}), SAME),
    ("GET", "/api/admin/nipt/artifacts?assembly_id={v}", None, (200, []), SAME),
    ("DELETE", "/api/auth/small-variant-filter-presets/{v}", None, (404, {"detail": "Preset not found"}), SAME),
    ("DELETE", "/api/families/FAM1/small-variant-filter-presets/{v}", None, (404, {"detail": "Preset not found"}), SAME),
    (
        "DELETE",
        "/api/families/FAM1/structural-variant-filter-presets/{v}",
        None,
        (404, {"detail": "Preset not found"}),
        SAME,
    ),
    ("GET", "/api/assemblies/{v}", None, (200, []), (400, {"detail": "Invalid species id"})),
    ("PATCH", "/api/auth/users/{v}", {}, (404, {"detail": "User not found"}), (400, {"detail": "Invalid user id"})),
    (
        "GET",
        "/api/cnvs/entry/{v}",
        None,
        (404, {"detail": "Clinical CNV not found"}),
        (400, {"detail": "Invalid clinical CNV id"}),
    ),
    ("GET", "/api/panels/{v}", None, (404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    (
        "GET",
        "/api/panels/{v}/versions",
        None,
        (404, {"detail": "Panel not found"}),
        (400, {"detail": "Invalid panel id"}),
    ),
    (
        "GET",
        "/api/panels/{v}/versions/1",
        None,
        (404, {"detail": "Panel version not found"}),
        (400, {"detail": "Invalid panel id"}),
    ),
    ("PUT", "/api/panels/{v}", {"genes": []}, (404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    ("DELETE", "/api/panels/{v}", None, (404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    ("PUT", "/api/projects/{v}", {}, (404, {"detail": "Project not found"}), (400, {"detail": "Invalid project id"})),
    ("DELETE", "/api/projects/{v}", None, (404, {"detail": "Project not found"}), (400, {"detail": "Invalid project id"})),
]


def _answer(client: TestClient, method: str, path: str, body: Any, value: str) -> tuple[int, Any]:
    response = client.request(method, path.replace("{v}", quote(value, safe="")), json=body)
    return response.status_code, response.json()


@pytest.mark.parametrize(("method", "path", "body", "unknown", "not_a_uuid"), ROUTES)
def test_a_value_that_is_no_uuid_is_answered_without_being_bound(
    admin_client: tuple[TestClient, _RecordingSession],
    method: str,
    path: str,
    body: Any,
    unknown: tuple[int, Any],
    not_a_uuid: Any,
) -> None:
    client, session = admin_client
    # It used to reach asyncpg's uuid codec, and the request failed with a 500.
    assert _answer(client, method, path, body, NOT_A_UUID) == (unknown if not_a_uuid is SAME else not_a_uuid)
    assert NOT_A_UUID not in session.bound
    assert _answer(client, method, path, body, UNKNOWN) == unknown


@pytest.mark.parametrize(("method", "path", "body", "unknown", "not_a_uuid"), ROUTES)
@pytest.mark.parametrize("spelling", ["{" + UNKNOWN + "}", "urn:uuid:" + UNKNOWN, UNKNOWN.upper()])
def test_each_spelling_of_a_uuid_is_bound_as_its_canonical_text(
    admin_client: tuple[TestClient, _RecordingSession],
    method: str,
    path: str,
    body: Any,
    unknown: tuple[int, Any],
    not_a_uuid: Any,
    spelling: str,
) -> None:
    client, session = admin_client
    # The braces and the URN prefix passed uuid.UUID, and then failed asyncpg (500).
    assert _answer(client, method, path, body, spelling) == unknown
    assert UNKNOWN in session.bound
    assert spelling not in session.bound


# --- what a spelling other than the canonical one used to miss ---------------------------


def _panel_row(panel_id: str) -> dict[str, Any]:
    return {
        "id": panel_id,
        "name": "Cardiac",
        "version": 1,
        "created_by": "00000000-0000-4000-8000-000000000001",
        "created_at": datetime(2026, 10, 7, tzinfo=timezone.utc),
        "description": None,
        "source": "local",
        "source_metadata": {},
    }


def test_a_panel_read_by_its_id_in_braces_keeps_its_genes() -> None:
    session = _RecordingSession(
        _Rows([_panel_row(CANONICAL)]),
        _Rows([{"panel_id": CANONICAL, "gene_symbol": "MYH7"}]),
        _Rows([]),
    )
    panel = asyncio.run(panel_metadata_service.get_panel_or_404(session, "{" + CANONICAL.upper() + "}"))  # type: ignore[arg-type]
    # The genes were looked up under the id as written, and the panel came back without any.
    assert panel.genes == ["MYH7"]


def test_the_variant_explorer_filters_by_a_panel_named_in_braces() -> None:
    def build(panel_id: str, session: _RecordingSession) -> Any:
        return asyncio.run(
            variant_explorer_router._build_global_variant_filters(
                panel_id=panel_id,
                impact=[],
                effect=[],
                clinvar=[],
                exclude_clinvar=[],
                classification=[],
                review_tag=[],
                sample_gt=[],
                session=session,  # type: ignore[arg-type]
            )
        )

    found = _RecordingSession(_Rows([{"panel_id": CANONICAL, "gene_symbol": "MYH7"}]))
    # The panel filter was dropped: every variant was listed, not the panel's.
    assert build("{" + CANONICAL + "}", found).panel_genes == ["MYH7"]
    not_a_uuid = _RecordingSession()
    assert build(NOT_A_UUID, not_a_uuid).panel_genes == []
    assert not_a_uuid.bound == []


def test_a_reference_upload_binds_the_assembly_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_existing(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(reference_metadata_service, "_assembly_dataset_count", no_existing)
    session = _RecordingSession(_Rows([{"id": CANONICAL, "assembly_name": "GRCh38", "version": "p14"}]))
    result = asyncio.run(
        reference_metadata_service.apply_reference_dataset_text(
            session,  # type: ignore[arg-type]
            assembly_id="urn:uuid:" + CANONICAL,
            dataset_type="cytobands",
            text_value="chr1\t0\t2300000\tp36.33\tgneg\n",
            overwrite=False,
            commit=False,
        )
    )
    assert result.inserted == 1
    # Every statement after the lookup bound the id as received, which asyncpg refuses.
    assert "urn:uuid:" + CANONICAL not in session.bound
    assert session.bound.count(CANONICAL) >= 3


def test_a_project_named_twice_in_two_spellings_is_one_project() -> None:
    session = _RecordingSession(_Rows([{"id": CANONICAL}]))
    validated = asyncio.run(
        metadata_service._validate_project_ids(session, ["{" + CANONICAL + "}", CANONICAL.upper()])  # type: ignore[arg-type]
    )
    # Two entries met one project row, and the assignment was refused as naming a missing one.
    assert validated == [CANONICAL]
    assert small_variant_review_tags._normalize_project_scope_ids([" {" + CANONICAL + "} ", CANONICAL]) == [CANONICAL]


def test_a_member_may_name_their_project_in_capitals() -> None:
    viewer = CurrentUser(
        id="00000000-0000-4000-8000-000000000002",
        username="viewer",
        email="viewer@example.org",
        role="viewer",
        metadata_project_ids=[CANONICAL],
        created_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
    )
    session = _RecordingSession(_Scalar(CANONICAL))
    resolved = asyncio.run(
        ped_service._resolve_accessible_project_id(session, viewer, CANONICAL.upper())  # type: ignore[arg-type]
    )
    # The capitals made the viewer's own project look like one they may not use (403).
    assert resolved == CANONICAL
    assert session.bound == [CANONICAL]
