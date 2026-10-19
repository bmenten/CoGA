"""The gene panels a family's searches apply, on its clinical audit trail (REQ-CARR-003).

A search that applies a panel records it before it runs, the first time it applies that
version of the panel (local version and the release of its source); a sign-out freezes the
panels recorded. Nothing recorded it before: the signed record could not say which panel
version the analysis used.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.services import panel_use_audit as pua

_PANEL_ID = "33333333-3333-3333-3333-333333333333"


def _panel_row(**overrides: Any) -> dict[str, Any]:
    return {
        "panel_id": _PANEL_ID,
        "name": "Epilepsy",
        "version": 2,
        "source": "panelapp",
        "external_id": "402",
        "external_version": "3.1",
        "source_updated_at": datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
        "source_metadata": {
            "version_created": "2026-08-01T00:00:00Z",
            "import_options": {"confidence_levels": ["3", "2"]},
        },
        "gene_count": 120,
        **overrides,
    }


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self.rows)


class _Session:
    """A panel table of one row and a clinical audit trail held in memory."""

    def __init__(self, panel: dict[str, Any] | None) -> None:
        self.panel = panel
        self.events: list[dict[str, Any]] = []
        self.steps: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Rows:
        sql = str(statement)
        if "FROM gene_panels" in sql:
            self.steps.append("panel read")
            return _Rows([self.panel] if self.panel else [])
        if "pg_advisory_xact_lock" in sql:
            self.steps.append(f"locked {params['k']}")
            return _Rows([])
        if "SELECT 1" in sql:
            self.steps.append("looked up")
            recorded = [
                event
                for event in self.events
                if event["after"]["panel_id"] == params["panel_id"]
                and str(event["after"]["version"]) == params["version"]
                and (event["after"]["external_version"] or "") == params["external_version"]
            ]
            return _Rows([{"?column?": 1}] if recorded else [])
        raise AssertionError(f"unexpected statement: {sql}")

    async def commit(self) -> None:
        self.steps.append("commit")


def _context() -> types.SimpleNamespace:
    return types.SimpleNamespace(family_uuid="11111111-1111-1111-1111-111111111111", family_id="FAM1")


def _user() -> types.SimpleNamespace:
    return types.SimpleNamespace(username="analyst", email="a@example.org", id=None)


@pytest.fixture
def recorded(monkeypatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _record(session, **kwargs):
        session.steps.append("recorded")
        session.events.append(kwargs)
        events.append(kwargs)

    monkeypatch.setattr(pua, "record_clinical_event", _record)
    return events


def _apply(session: _Session, panel_id: str | None = _PANEL_ID, search: str = "small_variants") -> None:
    asyncio.run(
        pua.record_family_panel_use(
            session, context=_context(), panel_id=panel_id, user=_user(), search=search
        )
    )


def test_the_first_search_with_a_panel_version_records_it(recorded) -> None:
    session = _Session(_panel_row())
    _apply(session)

    [event] = recorded
    assert event["action"] == "panel_applied"
    assert (event["family_uuid"], event["family_identifier"], event["variant_id"]) == (
        "11111111-1111-1111-1111-111111111111",
        "FAM1",
        None,
    )
    assert event["actor"] == "analyst"
    assert event["after"] == {
        "panel_id": _PANEL_ID,
        "name": "Epilepsy",
        "version": 2,
        "source": "panelapp",
        "gene_count": 120,
        "external_id": "402",
        "external_version": "3.1",
        "external_version_created": "2026-08-01T00:00:00Z",
        "confidence_levels": ["2", "3"],
        "source_updated_at": "2026-09-01T08:00:00+00:00",
        "search": "small_variants",
    }
    assert event["summary"] == (
        "Gene panel Epilepsy version 2 (PanelApp panel 402, version 3.1) first applied to the "
        "small-variant search"
    )
    # Looked up, then again under the family's audit-chain lock, then recorded and committed.
    assert session.steps == ["panel read", "looked up", "locked cae:FAM1", "looked up", "recorded", "commit"]


def test_a_panel_version_already_recorded_is_not_recorded_again(recorded) -> None:
    session = _Session(_panel_row())
    _apply(session)
    _apply(session, search="structural_variants")
    _apply(session, search="small_variants_export")
    assert len(recorded) == 1
    # Nothing written, nothing locked, for a version the trail already holds.
    assert session.steps[-2:] == ["panel read", "looked up"]


def test_a_new_version_or_a_new_panelapp_release_is_recorded_again(recorded) -> None:
    session = _Session(_panel_row())
    _apply(session)
    # The panel edited since (#809 archives version 3), then PanelApp's next release
    # imported with the same genes: the local version stays, the source's release moves.
    session.panel = _panel_row(version=3)
    _apply(session)
    session.panel = _panel_row(version=3, external_version="3.2")
    _apply(session)
    assert [(event["after"]["version"], event["after"]["external_version"]) for event in recorded] == [
        (2, "3.1"),
        (3, "3.1"),
        (3, "3.2"),
    ]


def test_a_second_search_that_recorded_it_first_under_the_lock_records_nothing(recorded) -> None:
    session = _Session(_panel_row())
    looks = iter([False, True])

    async def _execute(statement: Any, params: Any = None):
        sql = str(statement)
        if "SELECT 1" in sql:
            session.steps.append("looked up")
            return _Rows([{"?column?": 1}] if next(looks) else [])
        return await _Session.execute(session, statement, params)

    session.execute = _execute  # type: ignore[method-assign]
    _apply(session)
    assert recorded == []
    assert session.steps == ["panel read", "looked up", "locked cae:FAM1", "looked up", "commit"]


@pytest.mark.parametrize("panel_id", [None, "", "not-a-uuid", "1; DROP TABLE gene_panels"])
def test_a_value_that_names_no_panel_records_nothing(recorded, panel_id) -> None:
    session = _Session(_panel_row())
    _apply(session, panel_id=panel_id)
    assert recorded == [] and session.steps == []


def test_a_panel_that_does_not_exist_records_nothing(recorded) -> None:
    session = _Session(None)
    _apply(session)
    assert recorded == [] and session.steps == ["panel read"]


def test_a_local_panel_names_no_source_release(recorded) -> None:
    session = _Session(
        _panel_row(source="local", external_id=None, external_version=None, source_updated_at=None, source_metadata={})
    )
    _apply(session)
    assert recorded[0]["summary"] == "Gene panel Epilepsy version 2 first applied to the small-variant search"
    assert recorded[0]["after"]["external_version"] is None
    assert recorded[0]["after"]["confidence_levels"] is None


def test_the_mendeliome_names_its_monarch_release(recorded) -> None:
    session = _Session(_panel_row(name="Mendeliome", source="mendeliome", external_id="mendeliome", external_version="2026-09-01"))
    _apply(session)
    assert recorded[0]["summary"] == (
        "Gene panel Mendeliome version 2 (mendeliome release 2026-09-01) first applied to the "
        "small-variant search"
    )


def test_the_signed_record_lists_each_panel_version_once_with_its_first_use() -> None:
    first = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
    later = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    epilepsy = pua.panel_identity(_panel_row())
    rows = [
        {"created_at": first, "actor": "analyst", "after": {**epilepsy, "search": "small_variants"}},
        # The same version again (two searches at once before the lock existed): once.
        {"created_at": later, "actor": "second", "after": {**epilepsy, "search": "structural_variants"}},
        {
            "created_at": later,
            "actor": "second",
            "after": {**epilepsy, "version": 3, "search": "structural_variants"},
        },
        {
            "created_at": later,
            "actor": "analyst",
            "after": {
                **pua.panel_identity(_panel_row(panel_id="44444444-4444-4444-4444-444444444444", name="Cardiomyopathy")),
                "search": "small_variants",
            },
        },
        {"created_at": later, "actor": "analyst", "after": {"name": "no panel id"}},
    ]

    class _AuditSession:
        async def execute(self, statement: Any, params: Any = None) -> _Rows:
            assert "action = :action" in str(statement)
            assert params == {"family_uuid": "u1", "action": "panel_applied"}
            return _Rows(rows)

    applied = asyncio.run(pua.applied_gene_panels(_AuditSession(), "u1"))
    assert [(panel["name"], panel["version"]) for panel in applied] == [
        ("Cardiomyopathy", 2),
        ("Epilepsy", 2),
        ("Epilepsy", 3),
    ]
    epilepsy_v2 = applied[1]
    assert epilepsy_v2["first_applied_at"] == first.isoformat()
    assert epilepsy_v2["first_applied_by"] == "analyst"
    assert epilepsy_v2["first_search"] == "small_variants"
    assert (epilepsy_v2["external_id"], epilepsy_v2["external_version"]) == ("402", "3.1")


def test_an_event_read_back_as_text_reads_the_same_and_one_that_is_not_json_names_no_panel() -> None:
    import json

    epilepsy = pua.panel_identity(_panel_row(source_metadata=json.dumps(_panel_row()["source_metadata"])))
    assert epilepsy["confidence_levels"] == ["2", "3"]
    rows = [
        {"created_at": datetime(2026, 10, 10, tzinfo=timezone.utc), "actor": "analyst", "after": json.dumps(epilepsy)},
        {"created_at": datetime(2026, 10, 11, tzinfo=timezone.utc), "actor": "analyst", "after": "{not json"},
    ]

    class _AuditSession:
        async def execute(self, statement: Any, params: Any = None) -> _Rows:
            return _Rows(rows)

    [panel] = asyncio.run(pua.applied_gene_panels(_AuditSession(), "u1"))
    assert (panel["name"], panel["version"], panel["external_version"]) == ("Epilepsy", 2, "3.1")


# --- The searches record the panel before they run -------------------------------------


@pytest.fixture
def order(monkeypatch) -> list[str]:
    steps: list[str] = []

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return _context()

    async def _record(session, *, context, panel_id, user, search):
        steps.append(f"recorded {panel_id} for {search}")

    from backend.app.routers import families_small_variants as small
    from backend.app.routers import families_structural_variants as structural

    for module in (small, structural):
        monkeypatch.setattr(module, "build_family_metadata_context", _ctx)
        monkeypatch.setattr(module, "record_family_panel_use", _record)
    return steps


def test_the_small_variant_search_records_its_panel_before_it_runs(monkeypatch, order) -> None:
    from backend.app.routers import families_small_variants as small

    async def _search(session, **kwargs):
        order.append(f"searched {kwargs['panel_id']}")
        return types.SimpleNamespace(total=0)

    monkeypatch.setattr(small, "get_family_small_variants_clickhouse", _search)
    filters = small._family_small_variant_filters(panel_id=_PANEL_ID)
    asyncio.run(
        small.get_family_small_variants(
            "FAM1",
            page=1,
            page_size=10,
            project_id=None,
            overlap=False,
            require_sv_second_hit=False,
            prioritize=False,
            track_mode=False,
            track_result_limit=None,
            count_only=False,
            filters=filters,
            session=object(),
            user=_user(),
        )
    )
    assert order == [f"recorded {_PANEL_ID} for small_variants", f"searched {_PANEL_ID}"]


def _export(rows: list[Any]) -> types.SimpleNamespace:
    return types.SimpleNamespace(rows=rows, truncated=False, limit=50_000, truncated_reason=None)


def test_the_exports_record_their_panel_before_they_run(monkeypatch, order) -> None:
    from backend.app.routers import families_small_variants as small
    from backend.app.routers import families_structural_variants as structural

    async def _small_export(session, **kwargs):
        order.append(f"exported small {kwargs['panel_id']}")
        return _export([])

    async def _sv_export(session, **kwargs):
        order.append(f"exported sv {kwargs['panel_id']}")
        return _export([])

    monkeypatch.setattr(small, "export_family_small_variants", _small_export)
    monkeypatch.setattr(structural, "export_family_structural_variants", _sv_export)
    asyncio.run(
        small.export_family_small_variants_csv(
            "FAM1",
            project_id=None,
            prioritize=False,
            filters=small._family_small_variant_filters(panel_id=_PANEL_ID),
            session=object(),
            user=_user(),
        )
    )
    asyncio.run(
        structural.export_family_structural_variants_csv(
            "FAM1",
            project_id=None,
            prioritize=False,
            chr=None,
            start=None,
            end=None,
            length=None,
            min_length=None,
            type=None,
            source=None,
            sample_filters=[],
            samples=[],
            remote_chr=None,
            remote_start=None,
            gene=None,
            panel_id=_PANEL_ID,
            inheritance=None,
            phenotype=None,
            hpo=None,
            moi=None,
            gencc_support=None,
            region_flags=[],
            max_control_af=None,
            max_population_af=None,
            min_pli=None,
            classifications=[],
            review_tags=[],
            exclude_review_tags=[],
            has_notes=False,
            session=object(),
            user=_user(),
        )
    )
    assert order == [
        f"recorded {_PANEL_ID} for small_variants_export",
        f"exported small {_PANEL_ID}",
        f"recorded {_PANEL_ID} for structural_variants_export",
        f"exported sv {_PANEL_ID}",
    ]


def test_the_nipt_coverage_records_its_panel_before_it_reads_the_panel(monkeypatch) -> None:
    from backend.app.services import nipt_service

    steps: list[str] = []

    async def _trio_context(session, *, family_id, user, project_id=None):
        return object(), types.SimpleNamespace(cfdna_sample_id="CFDNA"), _context()

    async def _record(session, *, context, panel_id, user, search):
        steps.append(f"recorded {panel_id} for {search}")

    async def _targets(context, sample_id, with_metadata=False):
        steps.append("targets read")
        return []

    async def _stop(*args, **kwargs):
        steps.append("panel regions read")
        raise RuntimeError("stop here")

    monkeypatch.setattr(nipt_service, "_resolve_family_trio_context", _trio_context)
    monkeypatch.setattr(nipt_service, "record_family_panel_use", _record)
    monkeypatch.setattr(nipt_service, "_load_target_rows", _targets)
    monkeypatch.setattr(nipt_service, "_resolve_nipt_target_regions", _stop)
    with pytest.raises(RuntimeError):
        asyncio.run(
            nipt_service.get_family_nipt_coverage(object(), family_id="FAM1", user=_user(), panel_id=_PANEL_ID)
        )
    assert steps == [f"recorded {_PANEL_ID} for nipt_coverage", "targets read", "panel regions read"]


def test_the_structural_variant_search_records_its_panel_before_it_runs(monkeypatch, order) -> None:
    from backend.app.routers import families_structural_variants as structural

    async def _search(session, **kwargs):
        order.append(f"searched {kwargs['panel_id']}")
        return types.SimpleNamespace(total=0)

    monkeypatch.setattr(structural, "get_family_structural_variants_clickhouse", _search)
    asyncio.run(
        structural.get_family_structural_variants(
            "FAM1",
            page=1,
            page_size=10,
            chr=None,
            start=None,
            end=None,
            length=None,
            min_length=None,
            type=None,
            source=None,
            sample_filters=[],
            samples=[],
            remote_chr=None,
            remote_start=None,
            gene=None,
            panel_id=_PANEL_ID,
            inheritance=None,
            phenotype=None,
            hpo=None,
            moi=None,
            gencc_support=None,
            region_flags=[],
            max_control_af=None,
            max_population_af=None,
            min_pli=None,
            classifications=[],
            review_tags=[],
            exclude_review_tags=[],
            has_notes=False,
            project_id=None,
            overlap=False,
            prioritize=False,
            track_mode=False,
            count_only=False,
            session=object(),
            user=_user(),
        )
    )
    assert order == [f"recorded {_PANEL_ID} for structural_variants", f"searched {_PANEL_ID}"]
