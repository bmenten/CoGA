"""Characterisation test for ``get_family_small_variants_page`` (#528).

The function decides, from its filters, which of several paths serves a small-variant
page: the dashboard presence probe, the prioritised ranking, the capped track, the
native ClickHouse page, or the Python-side candidate path (inheritance, carrier
screening, track sampling). This test fakes every collaborator the function calls —
the ClickHouse fetches and counts, the Postgres lookups — and records, per scenario,
each call with its arguments and the page returned. The pure helpers run for real.

The recorded behaviour was captured from the function before it was split up
(``fixtures/small_variant_page_golden.json``); a refactor must reproduce every call, in
order, and every page. Regenerate only for an intended behaviour change:

    COGA_REGENERATE_GOLDEN=1 pytest tests/test_small_variant_page_golden.py
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from backend.app.schemas import SmallVariantSummaryOut, VariantPage
from backend.app.services import clickhouse_family_variants as hub
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext

GOLDEN = Path(__file__).parent / "fixtures" / "small_variant_page_golden.json"


def _context() -> FamilyMetadataContext:
    roles = [
        ("PROBAND", "proband", True, "male"),
        ("MOM", "mother", False, "female"),
        ("DAD", "father", False, "male"),
        ("SIB", "sibling", False, "female"),
    ]
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="demo_family",
        project_ids=["project-uuid"],
        sample_rows=[
            {
                "sample_id": name,
                "role": role,
                "affected": affected,
                "clinical_status": "affected" if affected else "unaffected",
                "carrier_status": "unknown",
                "sex": sex,
            }
            for name, role, affected, sex in roles
        ],
        sample_uuid_to_name={f"uuid-{name}": name for name, *_ in roles},
        sample_name_to_uuid={name: f"uuid-{name}" for name, *_ in roles},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _record(variant_id: str, gene: str, start: int, gts: dict[str, str], chr: str = "1") -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chr,
        start=start,
        end=start,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=[gene],
        annotations=[{"gene": gene, "impact": "MODERATE", "effect": "missense_variant"}],
        calls=[
            SmallVariantCall(sample=sample, gt=gt, gq=40, dp=30, af=[], ad=[], ps=None)
            for sample, gt in gts.items()
        ],
    )


def _family_rows() -> list[SmallVariantRecord]:
    """A proband with a compound het in GENE1 (one allele from each parent, the sibling
    carries one), a homozygous variant with carrier parents, a de novo, and a variant
    everyone carries."""
    return [
        _record("v1", "GENE1", 100, {"PROBAND": "0/1", "MOM": "0/1", "DAD": "0/0", "SIB": "0/1"}),
        _record("v2", "GENE1", 200, {"PROBAND": "0/1", "MOM": "0/0", "DAD": "0/1", "SIB": "0/0"}),
        _record("v3", "GENE2", 300, {"PROBAND": "1/1", "MOM": "0/1", "DAD": "0/1", "SIB": "0/1"}),
        _record("v4", "GENE3", 400, {"PROBAND": "0/1", "MOM": "0/0", "DAD": "0/0", "SIB": "0/0"}),
        _record("v5", "GENE4", 500, {"PROBAND": "0/1", "MOM": "0/1", "DAD": "0/1", "SIB": "0/1"}),
    ]


def _many_rows(count: int) -> list[SmallVariantRecord]:
    return [
        _record(f"m{i}", f"G{i % 7}", 1_000 + i, {"PROBAND": "0/1", "MOM": "0/1", "DAD": "0/0", "SIB": "0/0"})
        for i in range(count)
    ]


def _field_default(field: dataclasses.Field) -> Any:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    return dataclasses.MISSING


def _normalize(value: Any) -> Any:
    # Only what differs from a default is recorded, so the fixture reads as the request.
    if isinstance(value, FamilyMetadataContext):
        return "<context>"
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_defaults=True)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type": type(value).__name__,
            **{
                field.name: _normalize(getattr(value, field.name))
                for field in dataclasses.fields(value)
                if getattr(value, field.name) != _field_default(field)
            },
        }
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return f"<{type(value).__name__}>"


# Each scenario: the page request, and what the fakes answer.
SCENARIOS: dict[str, dict[str, Any]] = {
    "count_only": {"request": {"count_only": True}},
    "native_page": {"request": {"page_size": 2}, "count": (5, False)},
    "native_empty_page": {"request": {"page_size": 2, "page": 3}, "rows": [], "count": (0, False)},
    "native_huge_page_is_clamped": {"request": {"page_size": 2, "page": 10**12}, "rows": [], "count": (5, False)},
    "sv_second_hit": {"request": {"page_size": 2, "require_sv_second_hit": True}, "count": (5, False)},
    "panel_without_genes_or_regions": {"request": {"panel_id": "panel-empty"}, "panel": PanelFilterConstraints()},
    "panel_with_regions": {
        "request": {"panel_id": "panel-1", "page_size": 10},
        "panel": PanelFilterConstraints(genes=("GENE1",), regions=(Region("1", 50, 250),)),
        "count": (2, False),
    },
    "review_filter_without_matches": {"request": {"review_tags": ["report"]}, "review_ids": set()},
    "review_filters": {
        "request": {"review_tags": ["report"], "exclude_review_tags": ["excluded"], "has_notes": True, "page_size": 10},
        "review_ids": {"v1", "v3"},
        "exclude_ids": {"v5"},
        "count": (2, False),
    },
    # Refused (422), no longer answered as a page without variants (#604).
    "intervals_unparseable": {"request": {"intervals": "not an interval"}},
    # A list of blank lines restricts nothing; it used to be answered as empty (#604).
    "intervals_blank_lines": {"request": {"intervals": " \n \n", "page_size": 10}},
    "intervals_and_exclusions": {
        "request": {
            "intervals": "1:50-450",
            "exclude_intervals": "1:390-410",
            "exclude_gene": "GENE4",
            "page_size": 10,
        },
        "count": (4, False),
    },
    "every_scope_filter": {
        "request": {
            "panel_id": "panel-1",
            "review_classifications": ["Pathogenic - class 5"],
            "exclude_review_tags": ["excluded"],
            "intervals": "1:50-450",
            "exclude_intervals": "1:390-410",
            "exclude_gene": "GENE4",
            "inheritance": "recessive",
            "gene": "GENE1",
            "page_size": 10,
        },
        "panel": PanelFilterConstraints(genes=("GENE1", "GENE2"), regions=(Region("1", 50, 350),)),
        "review_ids": {"v1", "v2", "v3"},
        "exclude_ids": {"v3"},
        "gene_regions": {"GENE1": [Region("1", 90, 210)], "GENE4": [Region("1", 490, 510)]},
    },
    "prioritized": {"request": {"prioritize": True, "page_size": 10, "max_gnomad_af": 0.01}},
    "prioritized_is_ignored_in_track_mode": {"request": {"prioritize": True, "track_mode": True, "page_size": 3}},
    "track_limit_on_a_python_path": {
        "request": {"track_mode": True, "track_result_limit": 50, "inheritance": "compound_het", "page_size": 49},
    },
    "track_limit_reached": {
        "request": {"track_mode": True, "track_result_limit": 3, "page_size": 2},
        "count": (3, False),
    },
    "track_limit_estimated": {
        "request": {"track_mode": True, "track_result_limit": 10, "page_size": 9},
        "count": (10, True),
    },
    "track_limit_empty_region": {
        "request": {"track_mode": True, "track_result_limit": 10, "page_size": 9},
        "rows": [],
        "count": (0, False),
    },
    "track_limit_page": {
        "request": {"track_mode": True, "track_result_limit": 100_000, "page_size": 3},
        "count": (5, False),
    },
    "track_sampling_without_limit": {"request": {"track_mode": True, "page_size": 3}},
    "compound_het": {"request": {"inheritance": "compound_het", "page_size": 10}},
    "compound_het_alias_second_page": {"request": {"inheritance": "compound_heterozygous", "page_size": 1, "page": 2}},
    "recessive": {"request": {"inheritance": "recessive", "page_size": 10}},
    "recessive_with_gene": {
        "request": {"inheritance": "recessive", "gene": "GENE1", "page_size": 10},
        "gene_regions": {"GENE1": [Region("1", 90, 210)]},
    },
    "expanded_carrier_screening": {"request": {"expanded_carrier_screening": True, "page_size": 10}},
    "candidates_capped": {"request": {"inheritance": "compound_het", "page_size": 2}, "rows": _many_rows(1_001)},
}


async def _run(scenario: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: list[list[Any]] = []
    rows = scenario.get("rows", _family_rows())

    def record(name: str, *args: Any, **kwargs: Any) -> None:
        calls.append([name, _normalize(list(args)), _normalize(kwargs)])

    async def family_has_small_variants(*args, **kwargs):
        record("_family_has_small_variants", *args, **kwargs)
        return True

    async def ensure_sv_gene_index(*args, **kwargs):
        record("_ensure_family_sv_gene_index", *args, **kwargs)

    async def get_sv_hit_genes(*args, **kwargs):
        record("get_sv_hit_genes", *args, **kwargs)
        return {"GENE1"}

    async def fetch_summary(*args, **kwargs):
        record("_fetch_small_variant_summary", *args, **kwargs)
        return SmallVariantSummaryOut(total_variants=42, snv_count=40, indel_count=2)

    async def fetch_panel(*args, **kwargs):
        record("_fetch_panel_constraints", *args, **kwargs)
        return scenario.get("panel", PanelFilterConstraints())

    async def review_ids(*args, **kwargs):
        record("list_matching_small_variant_review_ids", *args, **kwargs)
        if kwargs.get("tags") == scenario["request"].get("exclude_review_tags") and "classifications" not in kwargs:
            return set(scenario.get("exclude_ids", set()))
        return set(scenario.get("review_ids", set()))

    async def gene_regions(*args, **kwargs):
        record("_fetch_gene_regions", *args, **kwargs)
        return list(scenario.get("gene_regions", {}).get(kwargs.get("gene_query"), []))

    async def prioritized(*args, **kwargs):
        record("_prioritized_small_variants_page", *args, **kwargs)
        return VariantPage(total=7, variants=[])

    async def count_rows(*args, **kwargs):
        record("_count_small_variant_rows_bounded", *args, **kwargs)
        return scenario.get("count", (len(rows), False))

    async def fetch_rows(*args, **kwargs):
        record("_fetch_small_variant_rows", *args, **kwargs)
        offset = kwargs.get("offset", 0) or 0
        limit = kwargs.get("limit")
        selected = rows[offset:] if limit is None else rows[offset : offset + limit]
        return [dataclasses.replace(row) for row in selected]

    async def hydrate(*args, **kwargs):
        record("_hydrate_small_variant_outs", *args, variants=[str(v.id) for v in kwargs.get("variants", [])])

    for name, fake in {
        "_family_has_small_variants": family_has_small_variants,
        "_ensure_family_sv_gene_index": ensure_sv_gene_index,
        "get_sv_hit_genes": get_sv_hit_genes,
        "_fetch_small_variant_summary": fetch_summary,
        "_fetch_panel_constraints": fetch_panel,
        "list_matching_small_variant_review_ids": review_ids,
        "_fetch_gene_regions": gene_regions,
        "_prioritized_small_variants_page": prioritized,
        "_count_small_variant_rows_bounded": count_rows,
        "_fetch_small_variant_rows": fetch_rows,
        "_hydrate_small_variant_outs": hydrate,
    }.items():
        monkeypatch.setattr(hub, name, fake)

    request = {"page": 1, "page_size": 100, **scenario["request"]}
    try:
        page = await hub.get_family_small_variants_page(
            "<session>",  # type: ignore[arg-type]
            context=_context(),
            **request,
        )
    except HTTPException as refused:
        return {"calls": calls, "refused": {"status": refused.status_code, "detail": refused.detail}}
    return {"calls": calls, "page": page.model_dump(mode="json", exclude_defaults=True)}


@pytest.mark.asyncio
async def test_small_variant_page_matches_the_recorded_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}
    for name, scenario in SCENARIOS.items():
        with monkeypatch.context() as patch:
            observed[name] = await _run(scenario, patch)

    if os.environ.get("COGA_REGENERATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(observed, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("golden fixture regenerated")

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(observed) == sorted(expected), "scenario set changed"
    for name in SCENARIOS:
        assert observed[name]["calls"] == expected[name]["calls"], f"{name}: collaborator calls differ"
        assert observed[name].get("page") == expected[name].get("page"), f"{name}: page differs"
        assert observed[name].get("refused") == expected[name].get("refused"), f"{name}: refusal differs"


def test_the_scenarios_reach_every_path() -> None:
    """The fixture must keep exercising each way the function can answer."""
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    called = {name: {call[0] for call in result["calls"]} for name, result in expected.items()}
    assert called["count_only"] == {"_family_has_small_variants"}
    assert "_prioritized_small_variants_page" in called["prioritized"]
    assert "_prioritized_small_variants_page" not in called["prioritized_is_ignored_in_track_mode"]
    assert expected["track_limit_on_a_python_path"]["page"].get("variants", []) == []
    assert expected["compound_het"]["page"]["variant_groups"], "no compound-het pair was formed"
    assert expected["candidates_capped"]["page"]["total_is_estimated"] is True
    assert "_fetch_gene_regions" in called["recessive_with_gene"]
    assert "get_sv_hit_genes" in called["sv_second_hit"]
    assert expected["intervals_unparseable"]["refused"]["status"] == 422
    assert "_fetch_small_variant_rows" not in called["intervals_unparseable"]
