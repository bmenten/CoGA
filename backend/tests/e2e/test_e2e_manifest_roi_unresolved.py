"""A manifest's region of interest the import does not find clears the family's, and says so (E2E, DATA-10).

The PGT embryo calls read the family's region of interest. An import that kept the family's
earlier region, without a word, when it could not find the one its manifest names would call the
embryos of the package at a region the package does not name. Over the synthetic PGT family
of ``_pgt_package.py`` (an affected father, the mother, their affected child as the index and two
embryos, a phased GLIMPSE2 family VCF on chr1, AD, an ROI on chr1), imported from a package and
read through the API, this:

* imports the package: the family's ROI is the manifest's, and the embryos are called;
* points the manifest's ``roi`` at a gene the assembly does not have, and runs a dry run: it
  warns ``manifest_roi_unresolved`` and writes nothing, the family's ROI and calls as they were;
* imports the package again (``update``): the family's ROI is cleared, the import's result warns
  and names the region it cleared, and no embryo is called.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._pgt_package import ROI, write_pgt_package

pytestmark = pytest.mark.integration

UNKNOWN_GENE = "NOT_A_GENE_E2E"


def _issues(result) -> list[dict]:
    return [issue.model_dump() for issue in result.validation.warnings]


async def _collect(root: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    async def run(*, dry_run: bool, conflict_mode: str) -> dict:
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                project_id=project_id,
                dry_run=dry_run,
                user=admin,
                requested_family_id=None if conflict_mode == "cancel" else family_id,
                conflict_mode=conflict_mode,
            )
        return {"completed": result.completed, "error": result.error, "warnings": _issues(result), "logs": result.logs}

    out: dict = {"responses": {}}
    r = out["responses"]
    out["first"] = await run(dry_run=False, conflict_mode="cancel")
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"
        r["imported"] = capture(await ac.get(f"{family}/pgt-analysis"))

        manifest = root / "manifest.yaml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(f'roi: "{ROI}"', f"roi: {UNKNOWN_GENE}"))
        out["dry_run"] = await run(dry_run=True, conflict_mode="update")
        r["after_checks"] = capture(await ac.get(f"{family}/pgt-analysis"))

        out["again"] = await run(dry_run=False, conflict_mode="update")
        r["after_import"] = capture(await ac.get(f"{family}/pgt-analysis"))
        r["family"] = capture(await ac.get(family))
    return out


@pytest.fixture(scope="module")
def unresolved(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_manifest_roi")
    family_id = f"FAM_ROI_{uuid4().hex[:8]}"
    names = write_pgt_package(base / family_id, family_id)
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    out = _harness.run_async(lambda: _collect(base / family_id, family_id))
    out["names"] = names
    assert out["first"]["completed"] is True and out["first"]["error"] is None, out["first"]
    return out


def _ok(out: dict, key: str) -> dict:
    resp = out["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _roi_warnings(warnings: list[dict]) -> list[dict]:
    return [issue for issue in warnings if issue["code"].startswith("manifest_roi_")]


def _calls(analysis: dict) -> dict[str, str | None]:
    return {embryo["sample"]: embryo["call"] for embryo in analysis["embryos"]}


def test_the_import_gives_the_family_the_manifests_roi(unresolved) -> None:
    names = unresolved["names"]
    analysis = _ok(unresolved, "imported")

    assert _roi_warnings(unresolved["first"]["warnings"]) == []
    assert analysis["region_of_interest"]["query"] == ROI
    assert _calls(analysis) == {names["E_RISK"]: "affected_or_at_risk", names["E_CLEAR"]: "unaffected_non_carrier"}


def test_the_dry_run_warns_ahead_and_changes_nothing(unresolved) -> None:
    dry_run = unresolved["dry_run"]

    assert dry_run["completed"] is True and dry_run["error"] is None
    [warning] = _roi_warnings(dry_run["warnings"])
    assert warning["code"] == "manifest_roi_unresolved"
    assert f"({UNKNOWN_GENE}) was not found" in warning["message"]
    assert "The import leaves the family without a region of interest" in warning["message"]
    # A check writes nothing: the family's region and its calls are as they were.
    assert _ok(unresolved, "after_checks") == _ok(unresolved, "imported")


def test_the_import_clears_the_familys_roi_and_names_it(unresolved) -> None:
    again = unresolved["again"]

    assert again["completed"] is True and again["error"] is None, again
    [warning] = _roi_warnings(again["warnings"])
    assert warning["code"] == "manifest_roi_unresolved"
    assert warning["message"].startswith(f"The manifest's region of interest ({UNKNOWN_GENE}) was not found")
    assert f"The family's region of interest ({ROI}, 1:1004500-1010500) was cleared" in warning["message"]
    assert "The manifest's region of interest was not found: see the warnings." in again["logs"]


def test_no_embryo_is_called_at_the_cleared_roi(unresolved) -> None:
    analysis = _ok(unresolved, "after_import")
    family = _ok(unresolved, "family")

    assert analysis["region_of_interest"] is None
    assert set(_calls(analysis).values()) == {None}
    assert family.get("roi") is None
