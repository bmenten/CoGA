"""The family small-variant page sends what the small-variant list reads, and no more.

Saved searches are each user's own, and the family small-variant page and the NIPT page share
them. A search saved on the NIPT page carries the NIPT list's own filters, and the small-variant
page sent them (found while fixing #1012): one of the NIPT list's inheritance views failed the
whole search (400), its fetal de novo view (``de_novo``) was read as the family's de novo /
dominant mode, and its other filters (the categories, the minimum confidence, the alleles the
fetus did not inherit, the lowest de novo priority), which the route does not declare, were
never applied though the page chipped them. The page now sends an inheritance only when the list
reads it, and none of those filters (``SMALL_VARIANT_INHERITANCE_MODES``,
``SMALL_VARIANT_INHERITANCE_ALIASES`` and ``SMALL_VARIANT_UNSUPPORTED_FILTERS`` in
``smallVariantSearch.ts``). These tests keep those lists equal to the backend's, and the route
refuses ``de_novo`` rather than read it as another mode.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import dependencies
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import families_small_variants as route
from backend.app.schemas import VariantPage
from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services import clickhouse_variant_queries, nipt_service
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_variant_queries import _normalize_small_variant_inheritance

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_SMALL_VARIANT_SEARCH_TS = _FRONTEND / "pages" / "families" / "smallVariantSearch.ts"
_VARIANT_FILTER_FORM_UTILS_TS = _FRONTEND / "pages" / "families" / "variantFilterFormUtils.ts"

_UNSUPPORTED = "Unsupported small-variant inheritance filter"


def _declaration(source: str, name: str, body: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*{body}", source)
    assert match, f"{name} not found in smallVariantSearch.ts"
    return match.group(1)


def _ts_string_list(source: str, name: str) -> list[str]:
    return re.findall(r"'(\w+)'", _declaration(source, name, r"\[([^\]]*)\]"))


def _ts_string_record(source: str, name: str) -> dict[str, str]:
    entries = re.findall(r"(\w+):\s*'(\w+)'", _declaration(source, name, r"\{([^}]*)\}"))
    assert len(entries) == len(dict(entries)), f"{name} names a key twice"
    return dict(entries)


def _ts_string_set(source: str, name: str) -> list[str]:
    return re.findall(r"'(\w+)'", _declaration(source, name, r"new Set<[^>]*>\(\s*\[([^\]]*)\]"))


def _ts_function_body(source: str, name: str) -> str:
    match = re.search(rf"export const {name}\b(.*?)\n}};\n", source, re.DOTALL)
    assert match, f"{name} not found"
    return match.group(1)


def _params_set_in(body: str) -> set[str]:
    return set(re.findall(r"params\.(?:set|append)\(\s*'(\w+)'", body))


# --- the inheritance the list reads ------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["de_novo", "De novo", "de-novo", "paternal_dominant", "maternal_dominant", "recessive_at_risk"],
)
def test_the_list_refuses_the_nipt_views_rather_than_read_one_as_another_mode(value: str) -> None:
    # The NIPT list's fetal de novo view is not the family's de novo / dominant mode, which reads
    # the members' genotypes by their affected status: read as it, a search for "De novo in the
    # fetus" listed the family's de novo / dominant variants under that name.
    with pytest.raises(HTTPException) as excinfo:
        _normalize_small_variant_inheritance(value)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == _UNSUPPORTED


def test_the_list_still_reads_its_modes_and_the_other_spellings_of_them() -> None:
    for mode in clickhouse_variant_queries._SUPPORTED_SMALL_INHERITANCE:
        assert _normalize_small_variant_inheritance(mode) == mode
    for alias, mode in clickhouse_variant_queries._SMALL_INHERITANCE_ALIASES.items():
        assert mode in clickhouse_variant_queries._SUPPORTED_SMALL_INHERITANCE
        assert _normalize_small_variant_inheritance(alias) == mode
    assert _normalize_small_variant_inheritance("Dominant") == "de_novo_dominant"
    assert _normalize_small_variant_inheritance("de novo dominant") == "de_novo_dominant"


class _FakeSession:
    async def rollback(self) -> None:
        return None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    user = CurrentUser(
        id="00000000-0000-4000-8000-000000000001",
        username="analyst",
        email="analyst@example.com",
        role="analyst",
        created_at=datetime.now(timezone.utc),
    )
    searched: list[Any] = []

    async def session():
        yield _FakeSession()

    async def current_user() -> CurrentUser:
        return user

    async def context(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(family_uuid="11111111-1111-1111-1111-111111111111", family_id="FAM1")

    async def no_panel(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_version(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def search(*_args: Any, filters: Any, **_kwargs: Any) -> VariantPage:
        searched.append(filters.inheritance)
        return VariantPage(total=0, variants=[])

    app.dependency_overrides[get_postgres_session] = session
    app.dependency_overrides[dependencies.get_current_user] = current_user
    monkeypatch.setattr(route, "build_family_metadata_context", context)
    monkeypatch.setattr(route, "record_family_panel_use", no_panel)
    monkeypatch.setattr(cfv, "_family_small_variant_data_version", no_version)
    monkeypatch.setattr(cfv, "_search_family_small_variants", search)
    with TestClient(app) as test_client:
        yield test_client, searched
    app.dependency_overrides = original_overrides


@pytest.mark.parametrize(
    "path", ["/api/families/FAM1/small-variants", "/api/families/FAM1/small-variants/export"]
)
def test_the_list_and_its_export_refuse_the_fetal_de_novo_view(client, path: str) -> None:
    test_client, searched = client

    response = test_client.get(path, params={"inheritance": "de_novo"})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == _UNSUPPORTED
    # Nothing was searched, under that name or another.
    assert searched == []


@pytest.mark.parametrize(
    "path", ["/api/families/FAM1/small-variants", "/api/families/FAM1/small-variants/export"]
)
@pytest.mark.parametrize("value", ["de_novo_dominant", "dominant"])
def test_the_list_and_its_export_search_de_novo_dominant_as_asked(client, path: str, value: str) -> None:
    test_client, searched = client

    response = test_client.get(path, params={"inheritance": value})

    assert response.status_code == 200, response.text
    assert searched == ["de_novo_dominant"]


# --- the page's lists, read from the TypeScript ------------------------------------------


def test_the_page_reads_an_inheritance_as_the_list_does() -> None:
    # The page sends an inheritance only when the list reads it, as the mode it reads, and names
    # any other as a filter the list does not apply. A mode or spelling added on one side only
    # would either never be sent or fail the whole search.
    source = _SMALL_VARIANT_SEARCH_TS.read_text(encoding="utf-8")
    modes = _ts_string_list(source, "SMALL_VARIANT_INHERITANCE_MODES")
    aliases = _ts_string_record(source, "SMALL_VARIANT_INHERITANCE_ALIASES")

    assert len(modes) == len(set(modes))
    assert set(modes) == clickhouse_variant_queries._SUPPORTED_SMALL_INHERITANCE
    assert aliases == clickhouse_variant_queries._SMALL_INHERITANCE_ALIASES
    # None of the NIPT list's views: the page does not send them, and the route refuses them.
    assert not set(nipt_service._INHERITANCE_VIEWS) & (set(modes) | set(aliases))


def test_the_page_sends_no_filter_the_list_does_not_declare() -> None:
    # What the page's request builder can send, against the query parameters the route
    # declares (a parameter it does not declare, it ignores). Left out of the request: the
    # filters of SMALL_VARIANT_UNSUPPORTED_FILTERS, each sent under its own name; and the locus,
    # which the page also sends as the chromosome, start and end, or the gene, it reads as.
    source = _SMALL_VARIANT_SEARCH_TS.read_text(encoding="utf-8")
    sent = (
        {"page", "page_size"}
        | _params_set_in(_ts_function_body(source, "buildSmallVariantQueryParams"))
        | _params_set_in(
            _ts_function_body(_VARIANT_FILTER_FORM_UTILS_TS.read_text(encoding="utf-8"), "setLocationParams")
        )
    )
    unsupported = _ts_string_set(source, "SMALL_VARIANT_UNSUPPORTED_FILTERS")
    declared = {
        parameter["name"]
        for parameter in app.openapi()["paths"]["/api/families/{family_id}/small-variants"]["get"]["parameters"]
        if parameter["in"] == "query"
    }

    assert "inheritance" in sent and "category" in sent
    assert len(unsupported) == len(set(unsupported))
    assert sent - declared == set(unsupported) | {"locus"}
