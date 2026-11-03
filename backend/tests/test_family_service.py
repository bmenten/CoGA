"""The family's region of interest (``family_service``): what an ROI query resolves to.

A query that reads as a region (``chr7:117,480,000-117,670,000``, one position, the bounds
in either order) is stored as that region without a look-up; any other query is a gene,
looked up in the family's assembly with exact symbol matches first, then the largest span,
then the symbol; a gene the assembly lacks is refused (404). The assembly is the requested
project's, which must be one of the family's, or the family's one assembly: a family with
none or with several is refused (400). An empty query clears the ROI. The ROI drives what a
PGT family's lineage and QC read, so a query never resolves to a guess. Sessions are faked.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.schemas import FamilyRegionOfInterestUpdate
from backend.app.services import family_service
from backend.app.services.family_service import (
    _build_family_roi_payload,
    _parse_region_of_interest,
    _resolve_family_assembly,
)


class _FakeMappingsResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeExecuteResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _FakeMappingsResult(self._row)


class _RecordingSession:
    def __init__(self, row) -> None:
        self.row = row
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _FakeExecuteResult(self.row)


@pytest.mark.asyncio
async def test_build_family_roi_payload_orders_gene_query_by_span_desc() -> None:
    session = _RecordingSession(
        {
            "hgnc_symbol": "BRCA1",
            "gene_id": "ENSG00000012048",
            "chr": "17",
            "start": 43_044_295,
            "end": 43_125_482,
        }
    )

    payload = await _build_family_roi_payload(
        session,
        assembly_id="assembly-uuid",
        query="BRCA1",
    )

    assert payload == {
        "query": "BRCA1",
        "label": "BRCA1",
        "source": "gene",
        "assembly_id": "assembly-uuid",
        "chr": "17",
        "start": 43_044_295,
        "end": 43_125_482,
    }
    assert session.sql is not None
    # Exact symbol matches rank first, then largest span, then symbol name.
    normalized_sql = " ".join(session.sql.split())
    assert "CASE WHEN" in normalized_sql
    assert '("end" - start) DESC, hgnc_symbol' in normalized_sql
    assert session.params == {"assembly_id": "assembly-uuid", "query": "BRCA1"}


@pytest.mark.parametrize(
    ("query", "region"),
    [
        ("chr7:117,480,000-117,670,000", ("7", 117_480_000, 117_670_000)),
        ("  7:117480000-117670000 ", ("7", 117_480_000, 117_670_000)),
        # The bounds in the other order are the same region.
        ("chrX:2000-1000", ("X", 1000, 2000)),
        # One position is a region of one base.
        ("CHR1:12345", ("1", 12_345, 12_345)),
    ],
)
def test_a_query_that_reads_as_a_region_is_that_region(query: str, region: tuple[str, int, int]) -> None:
    assert _parse_region_of_interest(query) == region


@pytest.mark.parametrize("query", ["CFTR", "chr7", "chr7:", "chr7:12-", "7:-5-10", "chr7:1.5-2"])
def test_a_query_that_is_no_region_is_left_to_the_gene_lookup(query: str) -> None:
    assert _parse_region_of_interest(query) is None


@pytest.mark.asyncio
async def test_a_region_is_stored_as_given_without_a_lookup() -> None:
    session = _RecordingSession(None)

    payload = await _build_family_roi_payload(session, assembly_id="assembly-uuid", query=" chr7:2,000-1,000 ")

    assert payload == {
        "query": "chr7:2,000-1,000",
        "label": "chr7:2,000-1,000",
        "source": "region",
        "assembly_id": "assembly-uuid",
        "chr": "7",
        "start": 1000,
        "end": 2000,
    }
    assert session.sql is None


@pytest.mark.asyncio
async def test_a_gene_the_assembly_lacks_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        await _build_family_roi_payload(_RecordingSession(None), assembly_id="assembly-uuid", query="NOTAGENE")

    assert (refused.value.status_code, refused.value.detail) == (
        404,
        "ROI gene or genomic location could not be resolved",
    )


class _AssemblySession:
    """Answers the project's assembly (``scalar_one_or_none``) and the family's assemblies
    (``all``) with ``assemblies``."""

    def __init__(self, assemblies: list[str]) -> None:
        self.assemblies = assemblies
        self.params: list[Any] = []

    async def execute(self, statement, params=None):
        self.params.append(params)
        assemblies = self.assemblies

        class _Result:
            def scalar_one_or_none(self_inner):
                return assemblies[0] if assemblies else None

            def all(self_inner):
                return [(assembly,) for assembly in assemblies]

        return _Result()


@pytest.mark.asyncio
async def test_the_roi_is_resolved_in_the_requested_projects_assembly_or_the_familys_one() -> None:
    asked = _AssemblySession(["assembly-38"])
    assert await _resolve_family_assembly(asked, family_uuid="fam", project_id="project-1") == "assembly-38"  # type: ignore[arg-type]
    assert asked.params == [{"family_id": "fam", "project_id": "project-1"}]

    family = _AssemblySession(["assembly-38"])
    assert await _resolve_family_assembly(family, family_uuid="fam", project_id=None) == "assembly-38"  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_id", "assemblies", "detail"),
    [
        ("project-9", [], "Project is not linked to this family"),
        (None, [], "Family does not define an assembly"),
        (None, ["assembly-38", "assembly-t2t"], "Could not resolve a single assembly for this family"),
    ],
    ids=["project-not-the-familys", "no-assembly", "two-assemblies"],
)
async def test_a_family_without_one_assembly_to_resolve_the_roi_in_is_refused(
    project_id: str | None, assemblies: list[str], detail: str
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _resolve_family_assembly(_AssemblySession(assemblies), family_uuid="fam", project_id=project_id)  # type: ignore[arg-type]

    assert (refused.value.status_code, refused.value.detail) == (400, detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [None, "", "   "])
async def test_an_empty_roi_query_clears_the_roi(monkeypatch: pytest.MonkeyPatch, query: str | None) -> None:
    stored: list[Any] = []

    async def context(*_args: Any, **_kwargs: Any) -> Any:
        return type("Context", (), {"family_uuid": "family-uuid"})()

    async def update_roi(_session: Any, *, family_identifier: str, roi: Any) -> None:
        stored.append((family_identifier, roi))

    async def family_record(*_args: Any, **_kwargs: Any) -> str:
        return "the family"

    async def no_lookup(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an empty query resolves nothing")

    monkeypatch.setattr(family_service, "build_family_metadata_context", context)
    monkeypatch.setattr(family_service, "update_family_roi_record", update_roi)
    monkeypatch.setattr(family_service, "get_family_record", family_record)
    monkeypatch.setattr(family_service, "_resolve_family_assembly", no_lookup)

    result = await family_service.update_family_roi_for_admin(
        object(),  # type: ignore[arg-type]
        "FAM1",
        FamilyRegionOfInterestUpdate(query=query),
        object(),  # type: ignore[arg-type]
    )

    assert result == "the family"
    assert stored == [("FAM1", None)]
