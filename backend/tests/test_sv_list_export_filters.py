"""The family SV list and its CSV export read one set of filters (ENG-2).

``GET /families/{family_id}/structural-variants`` and ``…/structural-variants/export`` each
declared the same 26 filter parameters, so a filter added to one route alone would have made
the downloaded file differ from the list on screen. Both now read their filters through one
dependency, ``_family_structural_variant_filters``, which builds the
``StructuralVariantQueryFilters`` both searches take.

The tests pin the declaration (each route takes every filter from that dependency and declares
none itself, under the names, types and defaults the routes had), the wiring (a request that
sets every filter hands both searches the same filters, none dropped), the result (for each
filter the list and the CSV hold the same SVs, in the same order: the real SV search over
synthetic calls, with ClickHouse and the Postgres lookups stood in for) and when an unreadable
filter value is refused. The family, its calls and its reviews are synthetic.
"""

from __future__ import annotations

import csv
import io
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app import dependencies
from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import create_access_token, get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import families_structural_variants as sv_router
from backend.app.schemas import VariantPage
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_family_variants import VariantExport
from backend.app.services.clickhouse_variant_queries import _structural_record_matches
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

_LIST_ROUTE = "/{family_id}/structural-variants"
_EXPORT_ROUTE = "/{family_id}/structural-variants/export"
_LIST = "/api/families/FAM001/structural-variants"
_EXPORT = "/api/families/FAM001/structural-variants/export"
_M = "backend.app.services.clickhouse_family_variants."
_USER = CurrentUser(
    id="u1",
    username="reviewer",
    email="reviewer@example.org",
    role="viewer",
    created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
)

# The filters both routes take, by their query names, with each one's JSON type.
_FILTERS = {
    "chr": "string",
    "start": "integer",
    "end": "integer",
    "length": "integer",
    "min_length": "integer",
    "type": "string",
    "source": "string",
    "sample_filter": "array",
    "sample": "array",
    "remote_chr": "string",
    "remote_start": "integer",
    "gene": "string",
    "panel_id": "string",
    "inheritance": "string",
    "phenotype": "string",
    "hpo": "string",
    "moi": "string",
    "gencc_support": "string",
    "region_flag": "array",
    "max_control_af": "number",
    "max_population_af": "number",
    "min_pli": "number",
    "classification": "array",
    "review_tag": "array",
    "exclude_review_tag": "array",
    "has_notes": "boolean",
}
# Each route's own query parameters, none of them a filter: the project and the ranking on
# both, and the list's page and genome-track options (``overlap`` is the track's region rule:
# the SV page never sends it, and the export never took it).
_OWN = {
    _LIST_ROUTE: {"project_id", "prioritize", "page", "page_size", "overlap", "track_mode", "count_only"},
    _EXPORT_ROUTE: {"project_id", "prioritize"},
}


def _family() -> FamilyMetadataContext:
    roles = {
        "PROBAND": ("proband", True, "male"),
        "MOTHER": ("mother", False, "female"),
        "FATHER": ("father", False, "male"),
    }
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_id": sample, "role": role, "affected": affected, "sex": sex}
            for sample, (role, affected, sex) in roles.items()
        ],
        sample_uuid_to_name={f"uuid-{sample}": sample for sample in roles},
        sample_name_to_uuid={sample: f"uuid-{sample}" for sample in roles},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.fixture()
def routes(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app with ``_USER`` signed in and the synthetic family; no Postgres session and no
    audit rows."""

    async def family_context(_session: Any, *, family_identifier: str, user: Any, project_id: Any = None):
        assert family_identifier == "FAM001"
        return _family()

    async def no_session():
        yield None

    async def user() -> CurrentUser:
        return _USER

    async def no_audit_row(_payload: Any) -> None:
        return None

    monkeypatch.setattr(sv_router, "build_family_metadata_context", family_context)
    monkeypatch.setattr(request_logging, "write_audit_log_event", no_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, user)
    monkeypatch.setattr(app.state, "skip_startup_tasks", True, raising=False)
    with TestClient(app) as client:
        yield client


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


def _route(path: str) -> APIRoute:
    (route,) = [
        route
        for route in sv_router.router.routes
        if isinstance(route, APIRoute) and route.path == path and "GET" in route.methods
    ]
    return route


def _query_parameters(path: str) -> dict[str, dict[str, Any]]:
    operation = app.openapi()["paths"][f"/api/families{path}"]["get"]
    return {parameter["name"]: parameter for parameter in operation["parameters"] if parameter["in"] == "query"}


def _json_type(schema: dict[str, Any]) -> str:
    """A parameter's JSON type: its own, or the one beside null of an optional one."""
    if "type" in schema:
        return schema["type"]
    (kind,) = [option["type"] for option in schema["anyOf"] if option["type"] != "null"]
    return kind


def test_each_route_reads_every_filter_through_the_one_dependency() -> None:
    # Before: each route declared the 26 filters itself, so one could gain a filter the
    # other lacked. The dependency comes after the sign-in, which also holds the request's
    # values to the control-character rule, so no filter is read before either.
    for path, own in _OWN.items():
        dependant = _route(path).dependant
        calls = [dependency.call for dependency in dependant.dependencies]
        assert calls.count(sv_router._family_structural_variant_filters) == 1, path
        assert calls.index(get_current_user) < calls.index(sv_router._family_structural_variant_filters), path
        assert {parameter.alias for parameter in dependant.query_params} == own, path


def test_both_routes_take_the_same_filters_under_their_names_types_and_defaults() -> None:
    listed, exported = _query_parameters(_LIST_ROUTE), _query_parameters(_EXPORT_ROUTE)

    assert set(listed) == set(_FILTERS) | _OWN[_LIST_ROUTE]
    assert set(exported) == set(_FILTERS) | _OWN[_EXPORT_ROUTE]
    for name, kind in {**_FILTERS, "project_id": "string", "prioritize": "boolean"}.items():
        # One declaration: the same name, type, default and title on both routes.
        assert listed[name] == exported[name], name
        assert _json_type(listed[name]["schema"]) == kind, name
        assert listed[name]["required"] is False, name
    assert listed["has_notes"]["schema"]["default"] is False
    assert listed["prioritize"]["schema"]["default"] is False


# --------------------------------------------------------------------------- #
# The wiring
# --------------------------------------------------------------------------- #

# A request that sets every filter, a list-valued one twice, as the SV page writes them.
_EVERY_FILTER: list[tuple[str, str]] = [
    ("chr", "1"),
    ("start", "1000"),
    ("end", "900000"),
    ("length", "5000"),
    ("min_length", "50"),
    ("type", "DEL,DUP"),
    ("source", "sniffles"),
    ("sample_filter", "MOTHER:het"),
    ("sample_filter", "FATHER:ref::5:PASS"),
    ("sample", "PROBAND"),
    ("sample", "MOTHER"),
    ("remote_chr", "5"),
    ("remote_start", "3000000"),
    ("gene", "GENEA"),
    ("panel_id", "panel-1"),
    ("inheritance", "AD"),
    ("phenotype", "cardiomyopathy"),
    ("hpo", "HP:0001638"),
    ("moi", "AD"),
    ("gencc_support", "Definitive"),
    ("region_flag", "Segdup"),
    ("region_flag", "Repeat"),
    ("max_control_af", "0.01"),
    ("max_population_af", "0.02"),
    ("min_pli", "0.9"),
    ("classification", "pathogenic"),
    ("classification", "likely pathogenic"),
    ("review_tag", "report"),
    ("review_tag", "follow-up"),
    ("exclude_review_tag", "artefact"),
    ("exclude_review_tag", "benign-polymorphism"),
    ("has_notes", "true"),
]
# The filters that request names. The page is the search's to set; these hold its place.
_EVERY_FILTER_READ = StructuralVariantQueryFilters(
    page=1,
    page_size=0,
    chromosome="1",
    start=1000,
    end=900000,
    length=5000,
    min_length=50,
    variant_type="DEL,DUP",
    source="sniffles",
    sample_filters=["MOTHER:het", "FATHER:ref::5:PASS"],
    selected_samples=["PROBAND", "MOTHER"],
    remote_chr="5",
    remote_start=3000000,
    gene="GENEA",
    panel_id="panel-1",
    inheritance="AD",
    phenotype="cardiomyopathy",
    hpo="HP:0001638",
    moi="AD",
    gencc_support="Definitive",
    region_flags=["Segdup", "Repeat"],
    max_control_af=0.01,
    max_population_af=0.02,
    min_pli=0.9,
    review_classifications=["pathogenic", "likely pathogenic"],
    review_tags=["report", "follow-up"],
    exclude_review_tags=["artefact", "benign-polymorphism"],
    has_notes=True,
)


def test_the_request_sets_every_filter_the_search_knows() -> None:
    # A filter field no query parameter sets is one neither route reads. Of the others, the
    # page is the search's to set, and overlap is the list's own parameter (the track's).
    unset = StructuralVariantQueryFilters(page=1, page_size=0)
    assert [
        field.name
        for field in fields(StructuralVariantQueryFilters)
        if getattr(_EVERY_FILTER_READ, field.name) == getattr(unset, field.name)
    ] == ["page", "page_size", "overlap"]


def test_a_request_with_every_filter_hands_both_searches_the_same_filters(
    routes: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, dict[str, Any]] = {}

    async def list_search(_session: Any, **kwargs: Any) -> VariantPage:
        seen["list"] = kwargs
        return VariantPage(total=0, variants=[])

    async def export_search(_session: Any, **kwargs: Any) -> VariantExport:
        seen["export"] = kwargs
        return VariantExport(rows=[], truncated_reason=None, limit=50_000)

    monkeypatch.setattr(sv_router, "get_family_structural_variants_clickhouse", list_search)
    monkeypatch.setattr(sv_router, "export_family_structural_variants", export_search)
    query = [*_EVERY_FILTER, ("prioritize", "true")]

    assert routes.get(_LIST, params=query).status_code == 200
    assert routes.get(_EXPORT, params=query).status_code == 200

    # Each filter of the request, read once for both searches: neither lacks one the other has.
    assert seen["list"]["filters"] == _EVERY_FILTER_READ
    assert seen["export"]["filters"] == _EVERY_FILTER_READ
    assert seen["list"]["prioritize"] is True and seen["export"]["prioritize"] is True
    # The list alone asks for a page and takes the genome track's options.
    assert (seen["list"]["page"], seen["list"]["page_size"]) == (1, 100)
    assert set(seen["export"]) == {"context", "filters", "prioritize"}

    assert routes.get(_LIST, params=[("chr", "1"), ("overlap", "true")]).status_code == 200
    assert seen["list"]["filters"].overlap is True


# --------------------------------------------------------------------------- #
# The result: the same SVs on screen and in the file
# --------------------------------------------------------------------------- #


def _call(sample: str, gt: str) -> StructuralVariantCall:
    return StructuralVariantCall(sample=sample, gt=gt, qual=50.0, read_support=10, filter="PASS")


def _sv(
    variant_id: str,
    chrom: str,
    start: int,
    end: int,
    sv_type: str,
    source: str,
    *,
    sv_len: int | None,
    calls: list[StructuralVariantCall],
    genes: tuple[str, ...] = (),
    info: dict[str, str] | None = None,
    mate: tuple[str | None, int | None] = (None, None),
) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=start,
        end=end,
        sv_type=sv_type,
        source=source,
        remote_chr=mate[0],
        remote_start=mate[1],
        remote_end=None,
        sv_len=sv_len,
        filters=["PASS"],
        gene_symbols=list(genes),
        annotations=[{"info": dict(info or {})}],
        calls=calls,
    )


# The family's SVs as the search reads them back, each filter keeping some and not others.
_CALLSET = [
    _sv(
        "sv-del-a", "1", 10_000, 15_000, "DEL", "sniffles",
        sv_len=-5_000,
        genes=("GENEA",),
        info={
            "Inheritance": "AD",
            "OMIM_phenotype": "cardiomyopathy",
            "HPO_terms": "HP:0001638",
            "OMIM_MOI": "AD",
            "GENCC_support": "Definitive",
            "Segdup": "1",
            "Allele_Freq_ALL_Control": "0.001",
            "Allele_Freq_ALL": "0.001",
            "pLI": "0.99",
        },
        calls=[_call("PROBAND", "0/1"), _call("MOTHER", "0/1"), _call("FATHER", "0/0")],
    ),
    _sv(
        "sv-del-b", "1", 20_000, 20_400, "DEL", "spectre",
        sv_len=-400,
        genes=("GENEB",),
        info={
            "Inheritance": "AR",
            "OMIM_phenotype": "epilepsy",
            "HPO_terms": "HP:0001250",
            "OMIM_MOI": "AR",
            "GENCC_support": "Limited",
            "Allele_Freq_ALL_Control": "0.2",
            "Allele_Freq_ALL": "0.3",
            "pLI": "0.1",
        },
        calls=[_call("PROBAND", "1/1"), _call("MOTHER", "0/1"), _call("FATHER", "0/1")],
    ),
    _sv(
        "sv-dup-c", "1", 30_000, 34_000, "DUP", "sniffles",
        sv_len=4_000,
        genes=("GENEA",),
        calls=[_call("PROBAND", "0/1"), _call("FATHER", "0/1")],
    ),
    _sv("sv-ins-d", "2", 5_000, 5_000, "INS", "sniffles", sv_len=5_000, calls=[_call("PROBAND", "0/1")]),
    _sv(
        "sv-bnd-e", "1", 40_000, 40_000, "BND", "sniffles",
        sv_len=None,
        mate=("5", 3_000_000),
        calls=[_call("PROBAND", "0/1"), _call("FATHER", "0/1")],
    ),
    _sv("sv-del-f", "1", 1_000, 6_000, "DEL", "needlr", sv_len=-5_000, calls=[_call("MOTHER", "0/1")]),
    _sv("sv-inv-g", "1", 150_000, 250_000, "INV", "sniffles", sv_len=100_000, calls=[_call("FATHER", "0/1")]),
]
# Each SV's review: its classification, its tags and its note.
_REVIEWS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "sv-del-a": ("pathogenic", ("report",), ""),
    "sv-del-b": ("", (), "Seen in a relative."),
    "sv-dup-c": ("benign", ("artefact",), ""),
}
_GENE_REGIONS = {"GENEA": [Region("1", 9_000, 35_000)]}
_PANELS = {"panel-1": PanelFilterConstraints(genes=("GENEB",), regions=(Region("1", 19_000, 21_000),))}

# One case per filter: the SV page's query for it.
_CASES: dict[str, list[tuple[str, str]]] = {
    "chr": [("chr", "2")],
    "start": [("start", "5000")],
    "end": [("end", "100000")],
    "length": [("length", "5000")],
    "min_length": [("min_length", "1000")],
    "type": [("type", "DEL")],
    "source": [("source", "spectre")],
    "sample_filter": [("sample_filter", "MOTHER:het")],
    "sample": [("sample", "MOTHER")],
    "remote_chr": [("remote_chr", "5")],
    "remote_start": [("remote_start", "1000000")],
    "gene": [("gene", "GENEA")],
    "panel_id": [("panel_id", "panel-1")],
    "inheritance": [("inheritance", "AD")],
    "phenotype": [("phenotype", "cardiomyopathy")],
    "hpo": [("hpo", "HP:0001638")],
    "moi": [("moi", "AR")],
    "gencc_support": [("gencc_support", "Definitive")],
    "region_flag": [("region_flag", "Segdup")],
    "max_control_af": [("max_control_af", "0.01")],
    "max_population_af": [("max_population_af", "0.01")],
    "min_pli": [("min_pli", "0.9")],
    "classification": [("classification", "pathogenic")],
    "review_tag": [("review_tag", "report")],
    "exclude_review_tag": [("exclude_review_tag", "artefact")],
    "has_notes": [("has_notes", "true")],
}

_Shown = list[tuple[str, int, int, str, str]]


@pytest.fixture()
def search(routes: TestClient, monkeypatch: pytest.MonkeyPatch) -> Callable[[list[tuple[str, str]]], tuple[_Shown, _Shown]]:
    """The SV list and its CSV for one query, each as the SVs it holds in order (chromosome,
    start, end, type, source). ClickHouse holds ``_CALLSET``; Postgres the reviews, the
    gene's loci and the panel, and no cytobands or phenotypes."""

    async def fetch_rows(
        _context: Any,
        filters: StructuralVariantQueryFilters,
        *,
        limit: int | None = None,
        offset: int = 0,
        include_regions: Any = (),
        **_kwargs: Any,
    ) -> list[StructuralVariantRecord]:
        # ClickHouse narrows by what its SQL reads: here by the search's own rule.
        rows = [record for record in _CALLSET if _structural_record_matches(record, filters, include_regions, [])]
        rows = rows[offset:]
        return rows if limit is None else rows[:limit]

    async def summary(context: Any, filters: StructuralVariantQueryFilters) -> tuple[int, dict]:
        rows = await fetch_rows(context, filters)
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            by_source = counts.setdefault(row.sv_type, {})
            by_source[row.source or ""] = by_source.get(row.source or "", 0) + 1
        return len(rows), counts

    async def review_ids(
        _session: Any,
        *,
        family_uuid: str,
        classifications: list[str] | None = None,
        tags: list[str] | None = None,
        has_notes: bool = False,
    ) -> set[str]:
        # The rule of list_matching_structural_variant_review_ids, over _REVIEWS.
        wanted_classes = {value.strip() for value in classifications or [] if value.strip()}
        wanted_tags = {value.strip() for value in tags or [] if value.strip()}
        if not (wanted_classes or wanted_tags or has_notes):
            return set()
        return {
            variant_id
            for variant_id, (classification, review_tags, note) in _REVIEWS.items()
            if (not wanted_classes or classification in wanted_classes)
            and (not wanted_tags or bool(wanted_tags.intersection(review_tags)))
            and (not has_notes or bool(note))
        }

    async def gene_regions(_session: Any, *, gene_query: str, assembly_id: Any) -> list[Region]:
        return _GENE_REGIONS.get(gene_query, [])

    async def panel(_session: Any, panel_id: str, *, assembly_id: Any = None) -> PanelFilterConstraints:
        return _PANELS.get(panel_id, PanelFilterConstraints())

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_phenotypes(*_args: Any, **_kwargs: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    monkeypatch.setattr(_M + "_fetch_structural_variant_rows", fetch_rows)
    monkeypatch.setattr(_M + "_fetch_structural_variant_summary", summary)
    monkeypatch.setattr(_M + "list_matching_structural_variant_review_ids", review_ids)
    monkeypatch.setattr(_M + "_fetch_gene_regions", gene_regions)
    monkeypatch.setattr(_M + "_fetch_panel_constraints", panel)
    monkeypatch.setattr(_M + "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(_M + "_fetch_structural_cytoband_map", nothing)
    monkeypatch.setattr(_M + "_affected_present_hpo", no_phenotypes)

    def _run(query: list[tuple[str, str]]) -> tuple[_Shown, _Shown]:
        listed = routes.get(_LIST, params=[("page_size", "100"), *query])
        exported = routes.get(_EXPORT, params=query)
        assert listed.status_code == 200, listed.text
        assert exported.status_code == 200, exported.text
        shown = [
            (variant["chr"], variant["start"], variant["end"], variant["type"], variant["source"])
            for variant in listed.json()["variants"]
        ]
        rows = [
            (row["Chromosome"], int(row["Start"]), int(row["End"]), row["Type"], row["Source"])
            for row in csv.DictReader(io.StringIO(exported.text))
        ]
        return shown, rows

    return _run


def test_every_filter_has_a_case() -> None:
    assert set(_CASES) == set(_FILTERS)


@pytest.mark.parametrize("name", sorted(_CASES))
def test_the_list_and_its_csv_hold_the_same_svs_for_each_filter(search, name: str) -> None:
    shown, exported = search(_CASES[name])

    # A filter that reached one route alone left the other holding SVs the filter drops.
    assert exported == shown
    # The filter decides: it keeps some of the family's SVs and drops others.
    assert 0 < len(shown) < len(_CALLSET), shown


def test_the_ranked_list_and_its_ranked_csv_hold_the_same_svs_in_the_same_order(search) -> None:
    shown, exported = search([("prioritize", "true"), ("type", "DEL")])

    assert exported == shown
    assert sorted(shown) == [
        ("1", 1_000, 6_000, "DEL", "needlr"),
        ("1", 10_000, 15_000, "DEL", "sniffles"),
        ("1", 20_000, 20_400, "DEL", "spectre"),
    ]


# --------------------------------------------------------------------------- #
# When an unreadable filter value is refused
# --------------------------------------------------------------------------- #

_UNREADABLE_QUAL = ("sample_filter", "MOTHER:het:abc")  # a QUAL minimum that is no number


class _FakeSession:
    async def rollback(self) -> None:
        return None


@pytest.fixture()
def signed_in(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The app with the real sign-in (``get_current_user``, a signed bearer); the family lookup
    records what it is asked and knows no family."""
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    # Long enough for HS256 (the test default is shorter); the bearer is signed below.
    monkeypatch.setattr(settings, "secret_key", "x" * 40)

    async def user_by_email(_session: Any, email: str) -> CurrentUser | None:
        return _USER if email == _USER.email else None

    lookups: list[str] = []

    async def unknown_family(_session: Any, *, family_identifier: str, **_kwargs: Any) -> Any:
        lookups.append(family_identifier)
        raise HTTPException(status_code=404, detail="Family not found")

    async def fake_session():
        yield _FakeSession()

    async def no_audit_row(_payload: Any) -> None:
        return None

    monkeypatch.setattr(dependencies, "get_current_user_by_email", user_by_email)
    monkeypatch.setattr(sv_router, "build_family_metadata_context", unknown_family)
    monkeypatch.setattr(request_logging, "write_audit_log_event", no_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, fake_session)
    monkeypatch.setattr(app.state, "skip_startup_tasks", True, raising=False)
    with TestClient(app) as client:
        yield {
            "client": client,
            "lookups": lookups,
            "headers": {"Authorization": "Bearer " + create_access_token({"sub": _USER.email})},
        }


@pytest.mark.parametrize("path", [_LIST, _EXPORT])
def test_an_unreadable_sample_filter_waits_for_the_sign_in(signed_in: dict[str, Any], path: str) -> None:
    response = signed_in["client"].get(path, params=[_UNREADABLE_QUAL])

    assert response.status_code == 401
    assert signed_in["lookups"] == []


@pytest.mark.parametrize("path", [_LIST, _EXPORT])
def test_a_control_character_is_refused_before_a_filter_is_read(signed_in: dict[str, Any], path: str) -> None:
    response = signed_in["client"].get(
        path, params=[("sample_filter", "MOTHER:het:abc\x00")], headers=signed_in["headers"]
    )

    assert response.status_code == 400
    assert signed_in["lookups"] == []


@pytest.mark.parametrize("path", [_LIST, _EXPORT])
@pytest.mark.parametrize("value", [("start", "abc"), _UNREADABLE_QUAL], ids=["start", "sample_filter"])
def test_an_unreadable_filter_value_is_refused_before_the_family_is_looked_up(
    signed_in: dict[str, Any], path: str, value: tuple[str, str]
) -> None:
    # A start that is no number always was: FastAPI reads the query before the route runs. A
    # sample filter's number is read with the other filters now, by the dependency. Before,
    # the search read it after the family lookup, so a family the caller cannot see (or one
    # nobody uses) answered 404 first.
    response = signed_in["client"].get(path, params=[value], headers=signed_in["headers"])

    assert response.status_code == 422
    assert signed_in["lookups"] == []
    if value == _UNREADABLE_QUAL:
        assert response.json() == {"detail": "Sample filter for MOTHER: QUAL 'abc' is not a number."}
