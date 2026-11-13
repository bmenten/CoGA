"""A folder added to Deliveries by hand: the rules, without a database.

CoGA makes the folder's package (the project and its assembly, the family, the analysis, and the
members and datasets Discover proposes), checks it as a pipeline's delivery without a file list
with checksums, and records it as a delivery of origin ``manual``. Refused: a folder that holds
its own package file, a family ID no family can be stored under, a project CoGA
does not have, and a second open delivery of the folder. The paths through the real stores are in
e2e/test_e2e_import_manual_delivery.py.

All samples and values here are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

from fastapi import HTTPException
import pytest

from backend.app.core.config import settings
from backend.app.services import import_manual_delivery as manual
from backend.app.schemas.imports import ImportDeliveryAdd
from backend.app.services.coga_package import PACKAGE_FILE_NAME, AnalysisType
from backend.app.services.import_watcher import DeliveryAssessment

_USER = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa", username="superuser", email="superuser@example.org")
_PROJECT = "00000000-0000-4000-8000-0000000000bb"


class _Session:
    """Answers the project read with ``project``, the open-delivery read with ``open_id``, and the
    insert with a new id; keeps every statement."""

    def __init__(self, *, project: dict[str, Any] | None, open_id: str | None = None) -> None:
        self.project = project
        self.open_id = open_id
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append((sql, dict(params or {})))
        if "FROM projects" in sql:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: self.project))
        if "FROM import_deliveries" in sql:
            return SimpleNamespace(first=lambda: (self.open_id,) if self.open_id else None)
        if "INSERT INTO import_deliveries" in sql:
            return SimpleNamespace(scalar_one=lambda: "00000000-0000-4000-8000-0000000000dd")
        return SimpleNamespace()

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    root = tmp_path / "FAM001"
    root.mkdir()
    (root / "FAM001.ped").write_text("FAM001\tPROBAND\t0\t0\t1\t2\n", encoding="utf-8")
    return root


@pytest.fixture
def checked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The proposal, the check and the record stand in: what each was given is kept."""
    calls: list[dict[str, Any]] = []

    async def proposal(folder: Path, package: Any) -> Any:
        calls.append({"proposal": package})
        return package, [], []

    async def assess(_factory: Any, folder: Path, assessment: DeliveryAssessment, *, base: Any, effective: Any) -> Any:
        calls.append({"assess": assessment, "folder": folder})
        assessment.state = "ready"
        assessment.project_id = _PROJECT
        assessment.manifest_text = "{}"
        return assessment

    async def record(_session: Any, **fields: Any) -> None:
        calls.append({"record": fields})

    async def delivery(_session: Any, delivery_id: str) -> Any:
        return SimpleNamespace(id=delivery_id)

    monkeypatch.setattr(manual, "_proposal", proposal)
    monkeypatch.setattr(manual, "assess_content", assess)
    monkeypatch.setattr(manual, "_record", record)
    monkeypatch.setattr(manual, "_delivery", delivery)
    monkeypatch.setattr(manual, "get_postgres_sessionmaker", lambda: None)
    return calls


async def _add(session: _Session, folder: Path | str, **fields: Any) -> Any:
    options = {"project_id": _PROJECT, "family_id": None, "analysis_type": None, **fields}
    return await manual.add_manual_delivery(session, folder_path=str(folder), user=_USER, **options)  # type: ignore[arg-type]


_GRCH38 = {"id": _PROJECT, "name": "WGS", "assembly_name": "GRCh38"}


@pytest.mark.asyncio
async def test_a_folder_added_by_hand_gets_the_package_coga_makes_and_is_recorded(folder: Path, checked: list[dict[str, Any]]) -> None:
    session = _Session(project=_GRCH38)

    delivery = await _add(session, folder, analysis_type="long_read_wgs")

    assert delivery.id == "00000000-0000-4000-8000-0000000000dd"
    made = checked[0]["proposal"]
    assert (made.project, made.assembly, made.analysis_type, made.family.family_id) == ("WGS", "GRCh38", "long_read_wgs", "FAM001")
    assert made.pipeline.name == manual.MANUAL_PIPELINE_NAME and made.pipeline.run_id.startswith("manual-")
    assert made.extra == {"origin": "manual"} and made.files is None
    assessment = checked[1]["assess"]
    assert assessment.package_check["origin"] == "manual"
    assert assessment.package_check["proposed"] == ["members", "datasets"]
    assert [warning["code"] for warning in assessment.package_check["warnings"]] == ["added_by_hand"]
    insert_sql, insert = next(statement for statement in session.statements if "INSERT INTO import_deliveries" in statement[0])
    assert "'manual'" in insert_sql
    assert (insert["folder"], insert["name"], insert["family_id"], insert["state"]) == (str(folder), "FAM001", "FAM001", "ready")
    assert insert["root"] == str(folder.parent) and len(insert["fingerprint"]) == 64
    assert json.loads(insert["package"])["family"] == {"family_id": "FAM001"}
    event = checked[2]["record"]
    assert (event["action"], event["user"], event["after"]["origin"]) == ("import_delivery_added", _USER, "manual")
    assert session.commits == 1
    # One addition of a folder at a time.
    assert any("pg_advisory_xact_lock" in sql for sql, _params in session.statements)


@pytest.mark.asyncio
async def test_the_family_id_given_is_used_and_a_project_without_assembly_names_none(folder: Path, checked: list[dict[str, Any]]) -> None:
    await _add(_Session(project={**_GRCH38, "assembly_name": None}), folder, family_id=" FAM_OTHER ")

    made = checked[0]["proposal"]
    assert (made.family.family_id, made.assembly, made.analysis_type) == ("FAM_OTHER", None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "status", "detail"),
    [
        ({"family_id": "FAM 001"}, 400, "The family ID contains a space."),
        ({"project": None}, 404, "Project not found"),
        ({"project_id": "nope"}, 404, "Project not found"),
        ({"open_id": "00000000-0000-4000-8000-0000000000ee"}, 409, "The folder is a delivery waiting for a decision already"),
        ({"folder_name": "MISSING"}, 404, "Package folder not found"),
    ],
)
async def test_what_is_not_added_by_hand(
    folder: Path, checked: list[dict[str, Any]], change: dict[str, Any], status: int, detail: str
) -> None:
    session = _Session(project=change.pop("project", _GRCH38), open_id=change.pop("open_id", None))
    target = folder
    if "folder_name" in change:
        target = folder.parent / change.pop("folder_name")

    with pytest.raises(HTTPException) as refused:
        await _add(session, target, **change)

    assert refused.value.status_code == status
    assert str(refused.value.detail).startswith(detail)
    assert not any("INSERT" in sql for sql, _params in session.statements)


@pytest.mark.asyncio
async def test_a_folder_with_its_own_package_file_is_delivered_through_a_watched_folder(folder: Path, checked: list[dict[str, Any]]) -> None:
    (folder / PACKAGE_FILE_NAME).write_text("{}", encoding="utf-8")

    with pytest.raises(HTTPException) as refused:
        await _add(_Session(project=_GRCH38), folder)

    assert refused.value.status_code == 409 and "watched import folder" in refused.value.detail


@pytest.mark.asyncio
async def test_a_folder_that_cannot_be_read_whole_is_refused(
    folder: Path, checked: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(manual, "package_listing", refuse)

    with pytest.raises(HTTPException) as refused:
        await _add(_Session(project=_GRCH38), folder)

    assert (refused.value.status_code, refused.value.detail) == (400, "The folder could not be read whole.")


def test_the_import_root_is_the_configured_root_the_folder_lies_under(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    assert manual._import_root((tmp_path / "share" / "FAM1").resolve()) == str(tmp_path.resolve())
    monkeypatch.setattr(settings, "family_import_roots", [])
    assert manual._import_root(tmp_path / "FAM1") == str(tmp_path)


def test_a_folder_added_by_hand_takes_the_analyses_a_package_names() -> None:
    """The form's analyses are the format's (``coga_package.AnalysisType``), in its order: the
    laboratory's LR-WGS, LR-AS, SR-WGS, PGT, expanded carrier screening and mNIPT."""
    schema = ImportDeliveryAdd.model_json_schema()["properties"]["analysis_type"]
    offered = next(option["enum"] for option in schema["anyOf"] if "enum" in option)
    assert offered == list(get_args(AnalysisType)) == [
        "long_read_wgs",
        "long_read_adaptive_sampling",
        "short_read_wgs",
        "pgt",
        "expanded_carrier_screening",
        "monogenic_nipt",
    ]
