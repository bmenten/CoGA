"""A watched import folder holds a folder per project, and each delivery lies in the folder of its project (E2E, real Postgres).

The owner's decision of 2026-11-10: a pipeline delivers each package folder into the folder of its
project in a watched import folder (``<watched folder>/<project>/<package>``), and the folder's name
and the project its package file names must agree. Through the watcher's round and the app over
HTTP (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py), as the e2e administrator, with synthetic one-sample packages:

- a package in the folder of its project is a ready delivery;
- a package directly in the watched folder needs attention (``package_not_in_project_folder``);
- a package in the folder of another project than its file names needs attention
  (``package_project_folder_differs``);
- a package whose file names a project CoGA lacks, in the folder of the e2e project, needs
  attention for both; its project corrected in Deliveries to the folder's, it is ready.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import one_sample_package
from backend.tests.e2e.test_e2e_import_watcher import write_package

pytestmark = pytest.mark.integration

_PLACED = {"package_not_in_project_folder", "package_project_folder_differs", "package_project_unknown"}


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import import_watcher
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project = (
            await session.execute(text("SELECT name FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_id})
        ).scalar_one()

    def package(where: Path, name: str, named_project: str) -> str:
        folder = one_sample_package(where / name, name, variants=2, datasets=("snv",))
        (folder / "manifest.yaml").unlink()
        write_package(folder, project=named_project, family_id=name)
        return name

    names = {
        "placed": package(base / project, f"E2E_PF_PLACED_{run}", project),
        "loose": package(base, f"E2E_PF_LOOSE_{run}", project),
        "elsewhere": package(base / f"E2E_PF_OTHER_{run}", f"E2E_PF_ELSEWHERE_{run}", project),
        "unknown": package(base / project, f"E2E_PF_UNKNOWN_{run}", f"E2E_PF_NO_SUCH_PROJECT_{run}"),
    }
    out: dict[str, Any] = {"project": project}
    await import_watcher.run_watch_round(sessionmaker, [base.resolve()])
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"

        async def by_family() -> dict[str, dict[str, Any]]:
            listed = (await client.get("/api/family-imports/deliveries", params={"limit": 200})).json()
            return {item["family_id"]: item for item in listed if item["family_id"] in names.values()}

        found = await by_family()
        out["seen"] = {key: found.get(name) for key, name in names.items()}
        unknown = found[names["unknown"]]
        out["corrected"] = capture(
            await client.post(
                f"/api/family-imports/deliveries/{unknown['_id']}/revisions",
                json={
                    "revision": 0,
                    "reason": "The package names the project its folder is for.",
                    "changes": [{"path": ["project"], "value": project}],
                },
            )
        )
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("watch_project_folders")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    return _harness.run_async(lambda: _exercise(base, uuid4().hex[:10].upper()))


def _placed(delivery: dict[str, Any]) -> list[str]:
    return sorted(issue["code"] for issue in delivery["issues"] if issue["code"] in _PLACED)


def test_a_package_in_the_folder_of_its_project_is_ready(ran) -> None:
    placed = ran["seen"]["placed"]
    assert placed is not None and placed["state"] == "ready", placed


def test_a_package_directly_in_the_watched_folder_needs_attention(ran) -> None:
    loose = ran["seen"]["loose"]
    assert loose["state"] == "needs_attention"
    assert _placed(loose) == ["package_not_in_project_folder"]


def test_a_package_in_the_folder_of_another_project_needs_attention(ran) -> None:
    elsewhere = ran["seen"]["elsewhere"]
    assert elsewhere["state"] == "needs_attention"
    assert _placed(elsewhere) == ["package_project_folder_differs"]


def test_a_project_corrected_to_its_folders_makes_the_delivery_ready(ran) -> None:
    assert _placed(ran["seen"]["unknown"]) == ["package_project_folder_differs", "package_project_unknown"]
    corrected = ran["corrected"]
    assert corrected["status"] == 200, corrected
    delivery = corrected["json"]["delivery"]
    assert (delivery["state"], _placed(delivery)) == ("ready", []), delivery
