"""A folder added to Deliveries by hand goes the way a pipeline's delivery goes (E2E, real Postgres and ClickHouse).

Phase 3 of the owner's plan of 2026-11-10 (``import_manual_delivery.py``). Through the app over
HTTP, as the e2e administrator, on a synthetic one-sample package folder with a PED and a VCF but
no package file and no manifest, lying in a watched import folder:

- added by hand for the e2e project, as a WGS analysis, it is a delivery of origin ``manual``: CoGA made its package
  (the members and the dataset Discover proposes, written into it), checked it without a file list
  (a warning says so), and it is ready; the addition is on the ``system:import-inbox`` chain;
- the folder is not added twice while its delivery is open (409); a folder that holds its own
  package file, a bucket package and a project CoGA does not have are refused;
- the watcher's rounds over the watched folder leave it as it is: never superseded, never gone;
- corrected (a clinical status) and approved on that revision, its import runs, the family's
  member has the corrected status, and the family's record names the delivery as added by hand;
- added again once imported, the unchanged folder is a new delivery whose family exists; a folder
  added without an analysis, which Discover does not name, needs one.

The values are synthetic: the family is new and uniquely named. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, job_outcome
from backend.tests.e2e.test_e2e_import_watcher import package_folder, write_package

pytestmark = pytest.mark.integration


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import import_watcher
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project = (
            await session.execute(text("SELECT name FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_id})
        ).scalar_one()
    family = f"E2E_HAND_{run}"
    sample = f"{family}_S1"
    folder = package_folder(base, family)
    with_file = package_folder(base, f"E2E_HAND_FILE_{run}")
    package_folder(base, f"E2E_HAND_NONE_{run}")
    write_package(with_file, project=project, family_id=with_file.name)

    out: dict[str, Any] = {"family": family, "sample": sample, "project_id": project_id}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"

        async def add(**body: Any) -> dict[str, Any]:
            return capture(await client.post("/api/family-imports/deliveries", json={"project_id": project_id, **body}))

        out["added"] = await add(folder_path=str(folder), analysis_type="wgs")
        delivery_id = out["added"]["json"]["_id"]
        url = f"/api/family-imports/deliveries/{delivery_id}"
        out["added_again"] = await add(folder_path=str(folder))
        out["with_file"] = await add(folder_path=str(with_file))
        out["bucket"] = await add(folder_path="gs://bucket/imports/FAM1")
        out["unknown_project"] = await add(folder_path=str(folder), project_id=str(uuid4()))
        out["package"] = capture(await client.get(f"{url}/package"))

        # The watcher's rounds over the folder the manual delivery lies in.
        out["rounds"] = [await import_watcher.run_watch_round(sessionmaker, [base.resolve()]) for _ in range(3)]
        out["after_rounds"] = capture(await client.get("/api/family-imports/deliveries", params={"limit": 200}))

        out["corrected"] = capture(
            await client.post(
                f"{url}/revisions",
                json={
                    "revision": 0,
                    "reason": "Clinical status from the request form.",
                    "changes": [{"path": ["members", sample, "clinical_status"], "value": "unaffected"}],
                },
            )
        )
        out["approved"] = capture(await client.post(f"{url}/approve", json={"revision": 1, "acknowledge_warnings": True}))
        assert out["approved"]["status"] == 200, out["approved"]
        job_id = out["approved"]["json"]["job"]["_id"]
        worker = f"e2e-hand-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        out["job"] = await job_outcome(job_id)
        out["re_added"] = await add(folder_path=str(folder), analysis_type="wgs")
        out["no_analysis"] = await add(folder_path=str(base / f"E2E_HAND_NONE_{run}"))
        out["verified"] = capture(
            await client.get(
                "/api/admin/integrity/verify",
                params={"table": "clinical_audit_events", "family_id": import_watcher.IMPORT_INBOX_CHAIN},
            )
        )

    async with sessionmaker() as session:
        out["member"] = (
            await session.execute(
                text(
                    "SELECT fm.clinical_status FROM family_members fm JOIN samples s ON s.id = fm.sample_id "
                    "JOIN families f ON f.id = fm.family_id WHERE f.family_id = :f AND s.sample_id = :s"
                ),
                {"f": family, "s": sample},
            )
        ).scalar_one()
        out["record"] = (
            await session.execute(
                text("SELECT metadata -> 'package_import' -> 'delivery' FROM families WHERE family_id = :f"), {"f": family}
            )
        ).scalar_one()
        out["events"] = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT action, actor, after ->> 'origin' AS origin FROM clinical_audit_events "
                        "WHERE family_identifier = :chain AND metadata ->> 'delivery_id' = :d ORDER BY created_at, id"
                    ),
                    {"chain": import_watcher.IMPORT_INBOX_CHAIN, "d": delivery_id},
                )
            ).mappings().all()
        ]
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_manual_delivery")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def test_a_folder_added_by_hand_is_a_delivery_whose_package_coga_made(ran) -> None:
    added = ran["added"]
    assert added["status"] == 200, added
    delivery = added["json"]
    assert (delivery["origin"], delivery["state"], delivery["family_id"]) == ("manual", "ready", ran["family"])
    assert delivery["pipeline_name"] == "CoGA, added by hand" and delivery["project"] is not None
    assert delivery["run_id"].startswith("manual-") and delivery["marker_sha256"] is None
    warnings = {warning["code"] for warning in delivery["warnings"]}
    assert {"added_by_hand", "package_members_proposed", "package_datasets_proposed"} <= warnings
    package = ran["package"]["json"]
    assert (package["original"], package["proposed"], package["revision"]) == (None, ["members", "datasets"], 0)
    assert [member["sample_id"] for member in package["package"]["members"]] == [ran["sample"]]
    assert package["package"]["extra"] == {"origin": "manual"}
    assert ran["events"][0]["action"] == "import_delivery_added"
    assert (ran["events"][0]["actor"], ran["events"][0]["origin"]) == ("admin", "manual")


def test_what_is_not_added_by_hand(ran) -> None:
    assert ran["added_again"]["status"] == 409
    assert ran["with_file"]["status"] == 409 and "watched import folder" in ran["with_file"]["json"]["detail"]
    assert ran["bucket"]["status"] == 400
    assert ran["unknown_project"]["status"] == 404


def test_the_watcher_leaves_a_delivery_added_by_hand_as_it_is(ran) -> None:
    mine = [item for item in ran["after_rounds"]["json"] if item["folder_name"] == ran["family"]]
    assert [(item["origin"], item["state"]) for item in mine] == [("manual", "ready")]
    assert all(done.gone == 0 and done.superseded_for_run == 0 for done in ran["rounds"])


def test_corrected_and_approved_it_imports_and_the_family_names_it_as_added_by_hand(ran) -> None:
    assert ran["corrected"]["status"] == 200, ran["corrected"]
    assert ran["corrected"]["json"]["delivery"]["state"] == "ready"
    assert ran["approved"]["status"] == 200, ran["approved"]
    assert ran["job"]["status"] == "completed", ran["job"]
    assert ran["member"] == "unaffected"
    record = ran["record"]
    assert (record["origin"], record["revision"], record["marker_sha256"]) == ("manual", 1, None)
    assert [correction["reason"] for correction in record["corrections"]] == ["Clinical status from the request form."]
    verified = ran["verified"]
    assert verified["status"] == 200 and verified["json"]["verified"] is True


def test_a_package_without_an_analysis_needs_one(ran) -> None:
    # The folder shows no analysis Discover names: the delivery needs one, corrected in CoGA.
    none = ran["no_analysis"]
    assert none["status"] == 200, none
    assert none["json"]["state"] == "needs_attention"
    assert [issue["message"] for issue in none["json"]["issues"]] == ["analysis_type: a complete package needs it"]


def test_an_imported_folder_is_added_again_as_a_delivery_whose_family_exists(ran) -> None:
    re_added = ran["re_added"]
    assert re_added["status"] == 200, re_added
    assert re_added["json"]["_id"] != ran["added"]["json"]["_id"]
    assert re_added["json"]["state"] == "needs_attention"
    assert "existing_family" in {issue["code"] for issue in re_added["json"]["issues"]}
    assert json.dumps(re_added["json"]["warnings"])  # still says it was added by hand
