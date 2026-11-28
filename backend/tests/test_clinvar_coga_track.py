"""The small-variant track reads each variant's ClinVar in CoGA's release alone.

The track's wide window holds only the salient variants: a row is a diamond for an allele the
release holds (likely) pathogenic, and salient for a transcript of HIGH or MODERATE impact
unless the release holds every allele (likely) benign; without a release, for its impact alone.
The annotation's ClinVar is not read. The track is served each variant's ClinVar from the
release, which its marks and tooltip read.

ClickHouse and the release are stood in for; the filter-parity harness
(``integration/test_small_variant_filter_parity_clickhouse.py``) runs the salient set on real
ClickHouse. Synthetic values only.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from backend.app.services.analysis_scope import WHOLE_FAMILY_SCOPE
from backend.app.services.clickhouse_variant_queries import (
    _clinvar_benign,
    _clinvar_diamond,
    _small_annotation_filter_clauses,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

RELEASE = date(2026, 11, 22)


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="F1",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_uuid": "uuid-S1", "sample_id": "S1", "role": "proband", "affected": True}],
        sample_uuid_to_name={"uuid-S1": "S1"},
        sample_name_to_uuid={"S1": "uuid-S1"},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        scope=WHOLE_FAMILY_SCOPE,
    )


def _clauses(**values: Any) -> tuple[list[str], dict[str, Any]]:
    params: dict[str, Any] = {}
    filters = SmallVariantQueryFilters(page=1, page_size=10, **values)
    return _small_annotation_filter_clauses(_context(), filters, params=params), params


_IMPACT = "arrayExists(a -> (a.impact IN %(salient_impacts)s), e.transcripts)"


def _release_rows(prefix: str, where: str) -> str:
    return (
        "k -> k IN (SELECT allele_key FROM coga.`GRCh38/SNV_INDEL/clinvar` "
        f"WHERE release = %({prefix}_release)s AND {where})"
    )


def test_the_tracks_salient_set_reads_the_alleles_clinvar_in_the_release() -> None:
    [clause], params = _clauses(salient_only=True, clinvar_coga_release=RELEASE)

    # A diamond: an allele the release holds (likely) pathogenic. Else HIGH or MODERATE on a
    # transcript, unless the release holds every allele (likely) benign: a benign sibling ALT
    # never hides a salient one.
    diamond = f"arrayExists({_release_rows('clinvar_diamond', _clinvar_diamond('classification_terms'))}, e.allele_keys)"
    benign = (
        f"(notEmpty(e.allele_keys) AND arrayAll({_release_rows('clinvar_benign', _clinvar_benign('classification_terms'))}"
        ", e.allele_keys))"
    )
    assert clause == f"({diamond} OR ({_IMPACT} AND NOT {benign}))"
    assert params == {
        "salient_impacts": ("high", "moderate", "medium"),
        "clinvar_diamond_release": RELEASE,
        "clinvar_benign_release": RELEASE,
    }


def test_without_a_release_the_salient_set_is_read_by_impact_alone() -> None:
    [clause], params = _clauses(salient_only=True)

    assert clause == _IMPACT
    assert params == {"salient_impacts": ("high", "moderate", "medium")}


@pytest.mark.parametrize(
    ("track_mode", "count_only", "served", "members"),
    [(False, False, True, True), (True, False, True, False), (False, True, False, False)],
    ids=["list", "track", "count"],
)
def test_the_list_and_the_track_are_served_each_variants_clinvar(
    monkeypatch: pytest.MonkeyPatch, track_mode: bool, count_only: bool, served: bool, members: bool
) -> None:
    from backend.app.routers import families_small_variants as route
    from backend.app.schemas import VariantOut, VariantPage
    from backend.app.services.clinvar_coga import ClinvarCogaRead

    read = ClinvarCogaRead("GRCh38", RELEASE, True)
    calls: list[str] = []

    async def context(_session: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _context()

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def release(_session: Any, _assembly: Any) -> ClinvarCogaRead:
        return read

    async def search(_session: Any, **kwargs: Any) -> VariantPage:
        assert kwargs["clinvar_coga_release"] == RELEASE
        return VariantPage(total=1, variants=[VariantOut(_id="1-100-G-A", chr="1", start=100, end=100, type="SNV", ref="G", alt="A")])

    async def without_calls(_context: Any, *, source: Any) -> list[str]:
        calls.append("members")
        return []

    async def serve(served_read: Any, _page: Any) -> None:
        assert served_read is read
        calls.append("served")

    monkeypatch.setattr(route, "build_family_metadata_context", context)
    monkeypatch.setattr(route, "record_family_panel_use", nothing)
    monkeypatch.setattr(route, "clinvar_coga_read", release)
    monkeypatch.setattr(route, "get_family_small_variants_clickhouse", search)
    monkeypatch.setattr(route, "family_members_without_calls", without_calls)
    monkeypatch.setattr(route, "serve_clinvar_coga", serve)

    asyncio.run(
        route.get_family_small_variants(
            "F1",
            page=1,
            page_size=10,
            project_id=None,
            overlap=False,
            require_sv_second_hit=False,
            prioritize=False,
            track_mode=track_mode,
            track_result_limit=None,
            count_only=count_only,
            filters=route._family_small_variant_filters(),
            session=object(),  # type: ignore[arg-type]
            user=object(),  # type: ignore[arg-type]
        )
    )

    assert ("served" in calls, "members" in calls) == (served, members)
