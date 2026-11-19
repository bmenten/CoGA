"""What the admin page *NIPT artefact list* reads besides the list itself.

* **The scopes** (``GET /admin/nipt/artifacts/scopes``): the default ``nipt_cfdna``, each
  panel a cfDNA sample's ``assay_panel`` names (resolved as the analysis resolves it) and each
  scope the assembly's list holds entries for, with the cfDNA samples whose calls the
  auto-seed reads and the entries listed.
* **The auto-seed's preview** (``GET /admin/nipt/artifacts/auto-seed/preview``): what the
  seed would write now, read as the seed reads it (the same samples, rules and threshold), with
  no write and no audit event; each allele says whether the list holds it already.

Both are an admin's alone (``test_admin_route_gating.py`` walks every admin route), and read a
UUID once (``test_request_uuid.py``). The preview against real ClickHouse:
``e2e/test_e2e_nipt_artifact_auto_seed.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.services import nipt_artifact_pg
from backend.app.services.access_control import CurrentUser
from backend.app.services.nipt_analysis import NiptQualityThresholds
from backend.app.services.nipt_artifact_pg import (
    auto_seed_nipt_artifacts,
    list_nipt_artifact_scopes,
    preview_auto_seed_nipt_artifacts,
    recurrence_rules,
)

ASSEMBLY = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


class _Rows:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _Session:
    """Answers a statement that contains one of ``found``'s markers with its rows, and every
    other with none; records each statement with its parameters, and each commit."""

    def __init__(self, found: dict[str, list[Any]] | None = None) -> None:
        self._found = found or {}
        self.calls: list[tuple[str, Any]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> _Rows:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        for marker, rows in self._found.items():
            if marker in sql:
                return _Rows(rows)
        return _Rows()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    def wrote(self) -> bool:
        return any(sql.split()[0] in {"INSERT", "UPDATE", "DELETE"} for sql, _params in self.calls)


@pytest.fixture()
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The clinical audit events the artifact list records, captured."""
    recorded: list[dict[str, Any]] = []

    async def record(_session: Any, **event: Any) -> None:
        recorded.append(event)

    monkeypatch.setattr(nipt_artifact_pg, "record_clinical_event", record)
    return recorded


# The cfDNA samples, with their assay_panel, and the entries the GRCh38 list holds.
_SAMPLES = [("CF1", None), ("CF2", "  PANEL_A "), ("CF3", ""), ("CF4", "PANEL_A"), ("CF5", None)]
_LISTED = [("1-100-A-G", "curated"), ("3-300-G-A", "auto")]


def _seed_session() -> _Session:
    return _Session(
        {
            "FROM assemblies": [("GRCh38",)],
            "FROM samples": _SAMPLES,
            "SELECT variant_id, source": _LISTED,
        }
    )


@pytest.fixture()
def candidates(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The recurrent alleles ClickHouse answers, and what the reading was asked."""
    asked: dict[str, Any] = {}

    async def fake(assembly_name: str, *, carrier_samples, rules, min_unexplained_samples):
        asked.update(
            assembly_name=assembly_name,
            carrier_samples=list(carrier_samples),
            rules=rules,
            min_unexplained_samples=min_unexplained_samples,
        )
        return [("2-200-C-T", 9), ("1-100-A-G", 4)]

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_artifact_candidates", fake)
    return asked


# --- the preview ---------------------------------------------------------------------------


def test_the_preview_reads_what_the_seed_would_write_and_writes_nothing(
    candidates: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    session = _seed_session()
    preview = asyncio.run(
        preview_auto_seed_nipt_artifacts(
            session,  # type: ignore[arg-type]
            assembly_id=ASSEMBLY,
            assay_key="nipt_cfdna",
            min_unexplained_samples=4,
        )
    )
    assert preview == {
        "assay_key": "nipt_cfdna",
        "min_unexplained_samples": 4,
        # CF1, CF3 and CF5: no panel or a blank one is the default scope.
        "cfdna_samples": 3,
        "variants": [
            # Not on the list: the seed would add it.
            {"variant_id": "2-200-C-T", "recurrence_count": 9, "listed_source": None},
            # Curated already: the seed would refresh its count and keep its source and label.
            {"variant_id": "1-100-A-G", "recurrence_count": 4, "listed_source": "curated"},
        ],
    }
    # Read as the seed reads it: the scope's samples, the default thresholds, the threshold.
    assert candidates == {
        "assembly_name": "GRCh38",
        "carrier_samples": ["CF1", "CF3", "CF5"],
        "rules": recurrence_rules(NiptQualityThresholds()),
        "min_unexplained_samples": 4,
    }
    # Nothing written, nothing audited, nothing committed.
    assert not session.wrote()
    assert (events, session.commits) == ([], 0)


def test_the_seed_writes_what_its_preview_showed(candidates: dict[str, Any], events: list[dict[str, Any]]) -> None:
    kwargs = {"assembly_id": ASSEMBLY, "assay_key": "PANEL_A", "min_unexplained_samples": 3}
    preview = asyncio.run(preview_auto_seed_nipt_artifacts(_seed_session(), **kwargs))  # type: ignore[arg-type]
    preview_read = dict(candidates)
    seeded = asyncio.run(auto_seed_nipt_artifacts(_seed_session(), **kwargs))  # type: ignore[arg-type]

    assert candidates == preview_read
    assert candidates["carrier_samples"] == ["CF2", "CF4"]
    assert seeded == {**preview, "seeded": 2}
    # The audit event names what it wrote, as it did before the preview existed.
    [event] = events
    assert event["after"] == {
        "variants": [
            {"variant_id": "2-200-C-T", "recurrence_count": 9},
            {"variant_id": "1-100-A-G", "recurrence_count": 4},
        ]
    }


def test_the_preview_of_an_unknown_assembly_is_a_404_before_any_read(candidates: dict[str, Any]) -> None:
    for assembly_id in ("5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b", "GRCh38"):
        session = _Session()  # no assembly
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                preview_auto_seed_nipt_artifacts(
                    session,  # type: ignore[arg-type]
                    assembly_id=assembly_id,
                    assay_key="nipt_cfdna",
                    min_unexplained_samples=3,
                )
            )
        assert (refused.value.status_code, refused.value.detail) == (404, "Assembly not found")
        assert all("FROM samples" not in sql for sql, _params in session.calls)
    assert candidates == {}


def test_a_preview_without_candidates_does_not_read_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*_args: Any, **_kwargs: Any) -> list[tuple[str, int]]:
        return []

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_artifact_candidates", none)
    session = _seed_session()
    preview = asyncio.run(
        preview_auto_seed_nipt_artifacts(
            session,  # type: ignore[arg-type]
            assembly_id=ASSEMBLY,
            assay_key="PANEL_B",
            min_unexplained_samples=3,
        )
    )
    assert preview == {"assay_key": "PANEL_B", "min_unexplained_samples": 3, "cfdna_samples": 0, "variants": []}
    assert all("nipt_artifact_variants" not in sql for sql, _params in session.calls)


# --- the scopes ----------------------------------------------------------------------------


def test_the_scopes_are_the_default_the_samples_panels_and_those_with_entries() -> None:
    session = _Session({"FROM samples": _SAMPLES, "GROUP BY assay_key": [("PANEL_A", 4), ("OLD_PANEL", 2)]})
    scopes = asyncio.run(list_nipt_artifact_scopes(session, assembly_id=ASSEMBLY))  # type: ignore[arg-type]
    assert scopes == [
        # The default first, then by name.
        {"assay_key": "nipt_cfdna", "cfdna_samples": 3, "listed": 0},
        # A scope with entries and no cfDNA sample left stays choosable.
        {"assay_key": "OLD_PANEL", "cfdna_samples": 0, "listed": 2},
        # The panel trimmed, as the analysis reads it.
        {"assay_key": "PANEL_A", "cfdna_samples": 2, "listed": 4},
    ]
    [(_samples_sql, samples_params), (_listed_sql, listed_params)] = session.calls
    assert samples_params == {"assays": "[\"nipt_cfdna\"]"}
    assert listed_params == {"assembly_id": ASSEMBLY}


def test_the_default_scope_is_there_without_any_sample_or_entry() -> None:
    scopes = asyncio.run(list_nipt_artifact_scopes(_Session(), assembly_id=ASSEMBLY))  # type: ignore[arg-type]
    assert scopes == [{"assay_key": "nipt_cfdna", "cfdna_samples": 0, "listed": 0}]


def test_an_assembly_id_that_spells_no_uuid_has_no_entries() -> None:
    session = _Session({"FROM samples": _SAMPLES, "GROUP BY assay_key": [("PANEL_A", 4)]})
    scopes = asyncio.run(list_nipt_artifact_scopes(session, assembly_id="GRCh38"))  # type: ignore[arg-type]
    assert [scope["listed"] for scope in scopes] == [0, 0]
    assert all("nipt_artifact_variants" not in sql for sql, _params in session.calls)


# --- the routes ----------------------------------------------------------------------------


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username="admin",
        email="admin@example.org",
        role="admin",
        created_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )


@pytest.fixture()
def client(candidates: dict[str, Any]) -> Iterator[tuple[TestClient, _Session]]:
    """The admin, signed in, on a database with one assembly and the cfDNA samples above."""
    session = _seed_session()
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def override_get_postgres_session():
        yield session

    async def override_get_current_user() -> CurrentUser:
        return _admin()

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client, session
    app.dependency_overrides = original_overrides


def test_the_preview_route_reads_at_three_samples_by_default(
    client: tuple[TestClient, _Session], candidates: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    test_client, session = client
    response = test_client.get("/api/admin/nipt/artifacts/auto-seed/preview", params={"assembly_id": ASSEMBLY})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["assay_key"], body["min_unexplained_samples"], body["cfdna_samples"]) == ("nipt_cfdna", 3, 3)
    assert [variant["listed_source"] for variant in body["variants"]] == [None, "curated"]
    assert candidates["min_unexplained_samples"] == 3
    assert not session.wrote() and events == []


def test_the_preview_route_reads_the_scope_and_threshold_it_is_given(
    client: tuple[TestClient, _Session], candidates: dict[str, Any]
) -> None:
    test_client, _session = client
    response = test_client.get(
        "/api/admin/nipt/artifacts/auto-seed/preview",
        params={"assembly_id": ASSEMBLY, "assay_key": "PANEL_A", "min_unexplained_samples": 5},
    )
    assert response.status_code == 200, response.text
    assert (candidates["carrier_samples"], candidates["min_unexplained_samples"]) == (["CF2", "CF4"], 5)


@pytest.mark.parametrize("threshold", ["0", "-1", "three"])
def test_the_preview_route_refuses_a_threshold_below_one(
    client: tuple[TestClient, _Session], candidates: dict[str, Any], threshold: str
) -> None:
    test_client, _session = client
    response = test_client.get(
        "/api/admin/nipt/artifacts/auto-seed/preview",
        params={"assembly_id": ASSEMBLY, "min_unexplained_samples": threshold},
    )
    assert response.status_code == 422
    assert candidates == {}


def test_the_seed_route_answers_what_it_wrote(
    client: tuple[TestClient, _Session], events: list[dict[str, Any]]
) -> None:
    test_client, _session = client
    response = test_client.post(
        "/api/admin/nipt/artifacts/auto-seed", json={"assembly_id": ASSEMBLY, "min_unexplained_samples": 4}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "assay_key": "nipt_cfdna",
        "min_unexplained_samples": 4,
        "cfdna_samples": 3,
        "variants": [
            {"variant_id": "2-200-C-T", "recurrence_count": 9, "listed_source": None},
            {"variant_id": "1-100-A-G", "recurrence_count": 4, "listed_source": "curated"},
        ],
        "seeded": 2,
    }
    assert [event["action"] for event in events] == ["nipt_artifacts_auto_seeded"]


def test_the_scopes_route_answers_each_scope(client: tuple[TestClient, _Session]) -> None:
    test_client, _session = client
    response = test_client.get("/api/admin/nipt/artifacts/scopes", params={"assembly_id": ASSEMBLY})
    assert response.status_code == 200, response.text
    assert response.json() == [
        {"assay_key": "nipt_cfdna", "cfdna_samples": 3, "listed": 0},
        {"assay_key": "PANEL_A", "cfdna_samples": 2, "listed": 0},
    ]
