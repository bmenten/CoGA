"""The family and sample metadata contexts every family view is built on.

``build_family_metadata_context`` reads the family the user may see, the projects of it they
may see, its active members (ordered by sample ID, from ``family_members`` alone: a sample's
own project links play no part), its relationships, its ROI and the assembly of its
projects. ``_resolve_family_assembly`` refuses (400) a family whose visible projects span
more than one assembly: returning None made every variant and track view render empty with
a 200, indistinguishable from "no variants". ``build_sample_metadata_context`` does the same
for one sample, and answers 404 for a sample it cannot read. The sessions here answer the
statements with canned rows and record them; all IDs are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import family_metadata_context
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    _resolve_family_assembly,
    build_family_metadata_context,
    build_sample_metadata_context,
    family_sample_contexts,
)

PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER_PROJECT = "22222222-2222-2222-2222-222222222222"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


def _member_row(sample_id: str, *, clinical_status: str = "affected") -> dict[str, Any]:
    return {
        "sample_uuid": f"sample-{sample_id.lower()}",
        "sample_id": sample_id,
        "sex": "male",
        "role": "proband",
        "affected": True,
        "clinical_status": clinical_status,
    }


class _FamilySession:
    """Answers the members' query with ``members``, the relationships' with none and an
    assembly query with one assembly; records each statement and its parameters."""

    def __init__(self, members: list[dict[str, Any]] | None = None) -> None:
        self.members = members if members is not None else [_member_row("PROBAND")]
        self.calls: list[tuple[str, Any]] = []

    @property
    def statements(self) -> list[str]:
        return [sql for sql, _params in self.calls]

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM family_relationships" in sql:
            return _Rows([])
        if "assemblies" in sql:
            return _Rows([{"assembly_id": "assembly-uuid", "assembly_name": "GRCh38"}])
        return _Rows(self.members)


def _user(project_ids: list[str], *, role: str = "viewer") -> CurrentUser:
    return CurrentUser(
        id="user-1",
        username=role,
        email=f"{role}@example.com",
        role=role,
        metadata_project_ids=project_ids,
        created_at=datetime.now(timezone.utc),
    )


def _family_is(monkeypatch: pytest.MonkeyPatch, **family: Any) -> None:
    """The family the user asks for, as the access check returns it."""

    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "family-uuid", "family_id": "FAM1", **family}

    monkeypatch.setattr(family_metadata_context, "get_accessible_family_mapping", mapping)


def _assembly_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(_session: Any, **_kwargs: Any) -> tuple[str, str]:
        return "assembly-uuid", "GRCh38"

    monkeypatch.setattr(family_metadata_context, "_resolve_family_assembly", resolve)


# --------------------------------------------------------------------------- #
# The family's context
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_family_metadata_context_uses_family_members_for_project_scoped_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _family_is(monkeypatch, project_ids=[PROJECT, OTHER_PROJECT])
    session = _FamilySession()

    context = await build_family_metadata_context(
        session,  # type: ignore[arg-type]
        family_identifier="FAM1",
        user=_user([PROJECT, OTHER_PROJECT]),
        project_id=PROJECT,
    )

    assert context.project_ids == [PROJECT, OTHER_PROJECT]
    assert context.sample_name_to_uuid == {"PROBAND": "sample-proband"}
    assert context.sample_uuid_to_name == {"sample-proband": "PROBAND"}
    assert "sample_projects" not in session.statements[0]
    assert context.roi is None
    # The project asked for decides the assembly.
    assert (context.assembly_id, context.assembly_name) == ("assembly-uuid", "GRCh38")
    assert session.calls[-1][1] == {"project_id": PROJECT}


@pytest.mark.asyncio
async def test_a_viewer_sees_only_the_family_projects_they_belong_to(monkeypatch: pytest.MonkeyPatch) -> None:
    _family_is(monkeypatch, project_ids=[PROJECT, OTHER_PROJECT])
    _assembly_is_resolved(monkeypatch)

    viewer = await build_family_metadata_context(
        _FamilySession(),  # type: ignore[arg-type]
        family_identifier="FAM1",
        user=_user([PROJECT]),
    )
    admin = await build_family_metadata_context(
        _FamilySession(),  # type: ignore[arg-type]
        family_identifier="FAM1",
        user=_user([], role="admin"),
    )

    assert viewer.project_ids == [PROJECT]
    assert admin.project_ids == [PROJECT, OTHER_PROJECT]


@pytest.mark.asyncio
async def test_a_project_of_the_family_the_user_cannot_see_is_refused_before_any_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _family_is(monkeypatch, project_ids=[PROJECT, OTHER_PROJECT])
    session = _FamilySession()

    with pytest.raises(HTTPException) as refused:
        await build_family_metadata_context(
            session,  # type: ignore[arg-type]
            family_identifier="FAM1",
            user=_user([PROJECT]),
            project_id=OTHER_PROJECT,
        )

    assert (refused.value.status_code, refused.value.detail) == (400, "Project is not linked to this family")
    assert session.calls == []


@pytest.mark.asyncio
async def test_family_metadata_context_carries_the_families_roi(monkeypatch: pytest.MonkeyPatch) -> None:
    _family_is(monkeypatch, project_ids=[PROJECT], roi_chr="chr7", roi_start=117_480_000, roi_end=117_670_000)

    context = await build_family_metadata_context(
        _FamilySession(),  # type: ignore[arg-type]
        family_identifier="FAM1",
        user=_user([PROJECT]),
        project_id=PROJECT,
    )

    assert context.roi == ("chr7", 117_480_000, 117_670_000)


@pytest.mark.asyncio
async def test_the_affected_members_are_those_whose_clinical_status_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _family_is(monkeypatch, project_ids=[PROJECT])
    _assembly_is_resolved(monkeypatch)
    session = _FamilySession(
        [
            _member_row("CHILD1", clinical_status="Affected"),
            _member_row("FATHER1", clinical_status="unaffected"),
            _member_row("MOTHER1", clinical_status="unknown"),
            _member_row("CHILD2", clinical_status="affected"),
        ]
    )

    context = await build_family_metadata_context(
        session,  # type: ignore[arg-type]
        family_identifier="FAM1",
        user=_user([PROJECT]),
    )

    assert context.affected_sample_names == ["CHILD1", "CHILD2"]


@pytest.mark.asyncio
async def test_build_family_metadata_context_orders_distinct_sample_query_by_selected_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _family_is(monkeypatch, project_ids=["project-uuid"])
    _assembly_is_resolved(monkeypatch)
    session = _FamilySession([_member_row("proband")])

    context = await build_family_metadata_context(
        session,  # type: ignore[arg-type]
        family_identifier="FAM1",
        user=_user(["project-uuid"], role="user"),
        project_id="project-uuid",
    )

    sample_sql = session.calls[0][0]
    assert "SELECT DISTINCT" not in sample_sql
    assert "FROM family_members" in sample_sql
    assert "sample_projects" not in sample_sql
    assert "ORDER BY lower(s.sample_id)" in sample_sql
    assert context.sample_rows == [_member_row("proband")]


@pytest.mark.asyncio
async def test_build_family_metadata_context_uses_uuid_project_filter_for_visible_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_uuid = "11111111-1111-4111-8111-111111111111"
    _family_is(monkeypatch, project_ids=[project_uuid])
    _assembly_is_resolved(monkeypatch)
    session = _FamilySession()

    await build_family_metadata_context(
        session,  # type: ignore[arg-type]
        family_identifier="FAM1",
        user=_user([project_uuid], role="user"),
    )

    sample_sql, sample_params = session.calls[0]
    assert "FROM family_members" in sample_sql
    assert "sample_projects" not in sample_sql
    assert "sp.project_id" not in sample_sql
    assert sample_params == {"family_uuid": "family-uuid"}


# --------------------------------------------------------------------------- #
# The family's assembly
# --------------------------------------------------------------------------- #


class _AssemblySession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        self.calls.append((str(statement), params))
        return _Rows(self._rows)


@pytest.mark.asyncio
async def test_resolve_family_assembly_raises_on_ambiguous_multi_assembly() -> None:
    # A family whose visible projects span >1 assembly, with no project specified, is
    # ambiguous. Returning None here made every variant/track view render empty (200 OK);
    # it must fail loud (400) so the caller disambiguates with a project_id.
    session = _AssemblySession(
        [
            {"assembly_id": "a1", "assembly_name": "GRCh38"},
            {"assembly_id": "a2", "assembly_name": "T2T-CHM13v2.0"},
        ]
    )
    with pytest.raises(HTTPException) as excinfo:
        await _resolve_family_assembly(session, family_uuid="fam")  # type: ignore[arg-type]
    assert excinfo.value.status_code == 400
    assert "single assembly" in str(excinfo.value.detail)
    # So does a family whose projects the user may see span both.
    with pytest.raises(HTTPException) as excinfo:
        await _resolve_family_assembly(session, family_uuid="fam", project_ids=[PROJECT, OTHER_PROJECT])  # type: ignore[arg-type]
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_resolve_family_assembly_single_resolves_and_empty_is_none() -> None:
    single = _AssemblySession([{"assembly_id": "a1", "assembly_name": "GRCh38"}])
    assert await _resolve_family_assembly(single, family_uuid="fam") == ("a1", "GRCh38")  # type: ignore[arg-type]
    # No assembly linked yet is legitimately empty, not an error.
    empty = _AssemblySession([])
    assert await _resolve_family_assembly(empty, family_uuid="fam") == (None, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_user_who_sees_none_of_the_familys_projects_resolves_no_assembly_and_reads_nothing() -> None:
    session = _AssemblySession([{"assembly_id": "a1", "assembly_name": "GRCh38"}])

    assert await _resolve_family_assembly(session, family_uuid="fam", project_ids=[]) == (None, None)  # type: ignore[arg-type]
    assert session.calls == []


@pytest.mark.asyncio
async def test_the_assembly_is_read_from_the_projects_the_user_sees_or_the_one_asked_for() -> None:
    visible = _AssemblySession([{"assembly_id": "a1", "assembly_name": "GRCh38"}])
    assert await _resolve_family_assembly(visible, family_uuid="fam", project_ids=[PROJECT]) == ("a1", "GRCh38")  # type: ignore[arg-type]
    [(sql, params)] = visible.calls
    assert "fp.project_id IN" in sql
    assert [str(value) for value in params["project_ids"]] == [PROJECT]

    asked = _AssemblySession([{"assembly_id": "a1", "assembly_name": "GRCh38"}])
    assert await _resolve_family_assembly(asked, family_uuid="fam", project_id=PROJECT) == ("a1", "GRCh38")  # type: ignore[arg-type]
    assert asked.calls[0][1] == {"project_id": PROJECT}


# --------------------------------------------------------------------------- #
# One sample's context
# --------------------------------------------------------------------------- #


class _SampleSession:
    """Answers the sample's query with ``sample`` (or no row) and the assembly's with one."""

    def __init__(self, sample: dict[str, Any] | None) -> None:
        self.sample = sample
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM samples s" in sql:
            return _Rows([self.sample] if self.sample else [])
        return _Rows([{"assembly_id": "assembly-uuid", "assembly_name": "GRCh38"}])


def _sample_is_accessible(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "sample-uuid"}

    monkeypatch.setattr(family_metadata_context, "get_accessible_sample_mapping", mapping)


def _sample_row(project_ids: list[str]) -> dict[str, Any]:
    return {
        "sample_uuid": "sample-uuid",
        "sample_id": "PROBAND",
        "sex": "female",
        "family_uuid": "family-uuid",
        "family_id": "FAM1",
        "project_ids": project_ids,
    }


@pytest.mark.asyncio
async def test_a_sample_that_cannot_be_read_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _sample_is_accessible(monkeypatch)
    session = _SampleSession(None)

    with pytest.raises(HTTPException) as refused:
        await build_sample_metadata_context(
            session,  # type: ignore[arg-type]
            sample_identifier="PROBAND",
            user=_user([PROJECT]),
        )

    assert (refused.value.status_code, refused.value.detail) == (404, "Sample not found")
    # No assembly is looked up for it.
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_a_samples_context_holds_the_projects_the_user_sees_and_their_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sample_is_accessible(monkeypatch)
    # One visible project: the assembly is that project's.
    one = _SampleSession(_sample_row([PROJECT, OTHER_PROJECT]))
    context = await build_sample_metadata_context(
        one,  # type: ignore[arg-type]
        sample_identifier="PROBAND",
        user=_user([PROJECT]),
    )
    assert (context.sample_id, context.family_id, context.sex) == ("PROBAND", "FAM1", "female")
    assert context.project_ids == [PROJECT]
    assert (context.assembly_id, context.assembly_name) == ("assembly-uuid", "GRCh38")
    assert one.calls[-1][1] == {"project_id": PROJECT}

    # Several: the family's assembly over the projects the user sees.
    several = _SampleSession(_sample_row([PROJECT, OTHER_PROJECT]))
    context = await build_sample_metadata_context(
        several,  # type: ignore[arg-type]
        sample_identifier="PROBAND",
        user=_user([PROJECT, OTHER_PROJECT]),
    )
    assert context.project_ids == [PROJECT, OTHER_PROJECT]
    assert [str(value) for value in several.calls[-1][1]["project_ids"]] == [PROJECT, OTHER_PROJECT]


def test_each_member_is_a_samples_context_of_the_family() -> None:
    # The upload route and the package import each built these with a copy of their own.
    family = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM1",
        project_ids=[PROJECT],
        sample_rows=[
            {"sample_uuid": "proband-uuid", "sample_id": "PROBAND", "sex": "female"},
            {"sample_uuid": "father-uuid", "sample_id": "FATHER1", "sex": "male"},
        ],
        sample_uuid_to_name={},
        sample_name_to_uuid={},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    contexts = family_sample_contexts(family)

    assert list(contexts) == ["PROBAND", "FATHER1"]
    assert contexts["FATHER1"] == SampleMetadataContext(
        sample_uuid="father-uuid",
        sample_id="FATHER1",
        family_uuid="family-uuid",
        family_id="FAM1",
        sex="male",
        project_ids=[PROJECT],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
