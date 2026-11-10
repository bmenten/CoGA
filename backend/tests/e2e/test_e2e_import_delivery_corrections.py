"""A delivery's package is corrected in CoGA before it is approved (E2E, real Postgres and ClickHouse).

The pipeline's package file is never changed: each correction is a revision
(``import_delivery_revisions``, append-only), made with a reason, and the delivery is checked again
at once with the package it ends with (``import_delivery_revisions.py``). Through the app over
HTTP, as the e2e administrator, on a synthetic delivery whose package file names its member, its
dataset, and that the pipeline computed that dataset with the pedigree:

- its package reads as the watcher checked it, revision 0, nothing proposed;
- the laboratory's fields change with a reason (a clinical status, HPO terms): revision 1, the
  delivery still ready; one made on an old revision is refused (409), a fixed field (a sample ID,
  the assembly) or a member CoGA does not have refused (400), a reason that is blank refused;
- a sex the pipeline used changes: the delivery needs attention (``package_pedigree_changed``) and
  is not approved; put back, it is ready again;
- an approval of an old revision is refused; of the latest, the import runs, the family's members
  have the corrected clinical status, and the family's record and the job name the revision, the
  package's SHA-256 and every correction;
- the revisions cannot be rewritten (their trigger refuses it), each is on the
  ``system:import-inbox`` chain with what it changed and why, and the chain verifies.

The values are synthetic: the family is new and uniquely named. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
    family = f"E2E_CORRECT_{run}"
    sample = f"{family}_S1"
    folder = package_folder(base, family)
    write_package(folder, project=project, family_id=family, pipeline_used_pedigree=["snv"])
    roots = [base.resolve()]
    await import_watcher.run_watch_round(sessionmaker, roots)
    async with sessionmaker() as session:
        delivery_id = (
            await session.execute(text("SELECT id::text FROM import_deliveries WHERE folder_name = :n"), {"n": family})
        ).scalar_one()

    out: dict[str, Any] = {"family": family, "sample": sample, "project_id": project_id, "delivery_id": delivery_id}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"
        url = f"/api/family-imports/deliveries/{delivery_id}"

        async def revise(revision: int, reason: str, *changes: tuple[list[str], Any]) -> dict[str, Any]:
            body = {"revision": revision, "reason": reason, "changes": [{"path": path, "value": value} for path, value in changes]}
            return capture(await client.post(f"{url}/revisions", json=body))

        out["package0"] = capture(await client.get(f"{url}/package"))
        out["lab"] = await revise(
            0,
            "Clinical status and phenotype from the request form.",
            (["members", sample, "clinical_status"], "unaffected"),
            (["members", sample, "hpo"], {"present": ["HP:0001250"], "absent": []}),
        )
        out["stale"] = await revise(0, "Made on the package as it was.", (["members", sample, "role"], "proband"))
        out["fixed_sample"] = await revise(1, "Another ID.", (["members", sample, "sample_id"], "OTHER"))
        out["fixed_assembly"] = await revise(1, "Another assembly.", (["assembly"], "GRCh37"))
        out["unknown_member"] = await revise(1, "A member CoGA lacks.", (["members", "NOBODY", "role"], "sibling"))
        out["blank_reason"] = await revise(1, "   ", (["members", sample, "role"], "proband"))
        out["sex"] = await revise(1, "The request form gives another sex.", (["members", sample, "sex"], "female"))
        out["approve_attention"] = capture(
            await client.post(f"{url}/approve", json={"revision": 2, "acknowledge_warnings": True})
        )
        out["sex_back"] = await revise(2, "The pipeline's sex was right.", (["members", sample, "sex"], "male"))
        out["package3"] = capture(await client.get(f"{url}/package"))
        out["approve_stale"] = capture(await client.post(f"{url}/approve", json={"revision": 2, "acknowledge_warnings": True}))
        out["approved"] = capture(await client.post(f"{url}/approve", json={"revision": 3, "acknowledge_warnings": True}))
        job_id = out["approved"]["json"]["job"]["_id"]
        worker = f"e2e-correct-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        out["job"] = await job_outcome(job_id)
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
                    "SELECT fm.clinical_status, fm.role, s.sex FROM family_members fm "
                    "JOIN samples s ON s.id = fm.sample_id JOIN families f ON f.id = fm.family_id "
                    "WHERE f.family_id = :f AND s.sample_id = :s"
                ),
                {"f": family, "s": sample},
            )
        ).one()
        out["record"] = (
            await session.execute(
                text("SELECT metadata -> 'package_import' -> 'delivery' FROM families WHERE family_id = :f"), {"f": family}
            )
        ).scalar_one()
        out["job_delivery"] = (
            await session.execute(
                text("SELECT metadata -> 'delivery' FROM family_import_jobs WHERE id = CAST(:j AS uuid)"), {"j": job_id}
            )
        ).scalar_one()
        out["events"] = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT action, actor, after ->> 'revision' AS revision, after ->> 'reason' AS reason, "
                        "after -> 'changes' AS changes, after ->> 'state' AS state FROM clinical_audit_events "
                        "WHERE family_identifier = :chain AND metadata ->> 'delivery_id' = :d ORDER BY created_at, id"
                    ),
                    {"chain": import_watcher.IMPORT_INBOX_CHAIN, "d": delivery_id},
                )
            ).mappings().all()
        ]
    out["rewrite"] = None
    out["delete"] = None
    for key, statement in (
        ("rewrite", "UPDATE import_delivery_revisions SET reason = 'rewritten' WHERE delivery_id = CAST(:d AS uuid)"),
        ("delete", "DELETE FROM import_delivery_revisions WHERE delivery_id = CAST(:d AS uuid)"),
    ):
        async with sessionmaker() as session:
            try:
                await session.execute(text(statement), {"d": delivery_id})
                await session.commit()
            except DBAPIError as exc:
                out[key] = str(exc.orig)
                await session.rollback()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_delivery_corrections")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def test_the_package_reads_as_the_watcher_checked_it(ran) -> None:
    package = ran["package0"]
    assert package["status"] == 200, package
    body = package["json"]
    assert (body["revision"], body["revisions"], body["proposed"]) == (0, [], [])
    assert body["package"]["members"] == [
        {"sample_id": ran["sample"], "sex": "male", "role": "proband", "clinical_status": "affected", "related_to": []}
    ]
    assert body["package"]["pipeline_used_pedigree"] == ["snv"]
    assert body["original"]["family"] == {"family_id": ran["family"]}
    assert len(body["package_sha256"]) == 64


def test_the_laboratorys_fields_change_with_a_reason(ran) -> None:
    lab = ran["lab"]
    assert lab["status"] == 200, lab
    assert lab["json"]["delivery"]["state"] == "ready"
    assert lab["json"]["delivery"]["revision"] == 1
    [revision] = lab["json"]["package"]["revisions"]
    assert revision["reason"] == "Clinical status and phenotype from the request form."
    assert revision["created_by_name"] == "admin"
    assert revision["changes"] == [
        {"path": ["members", ran["sample"], "clinical_status"], "kind": "laboratory", "before": "affected", "after": "unaffected"},
        {
            "path": ["members", ran["sample"], "hpo"],
            "kind": "laboratory",
            "before": None,
            "after": {"present": ["HP:0001250"], "absent": []},
        },
    ]


def test_a_stale_fixed_unknown_or_unexplained_correction_is_refused(ran) -> None:
    from backend.app.services.import_delivery_revisions import DELIVERY_REVISED

    assert (ran["stale"]["status"], ran["stale"]["json"]["detail"]) == (409, DELIVERY_REVISED)
    for key in ("fixed_sample", "fixed_assembly"):
        assert ran[key]["status"] == 400, ran[key]
        assert "is the pipeline's and is not changed in CoGA" in ran[key]["json"]["detail"]
    assert ran["unknown_member"]["status"] == 400
    assert "no member of the package" in ran["unknown_member"]["json"]["detail"]
    assert ran["blank_reason"]["status"] == 400


def test_a_sex_the_pipeline_used_needs_attention_until_it_is_put_back(ran) -> None:
    sex = ran["sex"]
    assert sex["status"] == 200, sex
    delivery = sex["json"]["delivery"]
    assert delivery["state"] == "needs_attention"
    assert "package_pedigree_changed" in {issue["code"] for issue in delivery["issues"]}
    assert sex["json"]["package"]["revisions"][-1]["changes"][0]["kind"] == "pipeline_used"
    assert ran["approve_attention"]["status"] == 409
    assert ran["sex_back"]["json"]["delivery"]["state"] == "ready"
    assert ran["package3"]["json"]["revision"] == 3


def test_the_approved_import_reads_the_corrected_package_and_names_its_corrections(ran) -> None:
    from backend.app.services.import_delivery_review import DELIVERY_REVISED_SINCE

    assert (ran["approve_stale"]["status"], ran["approve_stale"]["json"]["detail"]) == (409, DELIVERY_REVISED_SINCE)
    assert ran["approved"]["status"] == 200, ran["approved"]
    assert ran["job"]["status"] == "completed", ran["job"]
    assert tuple(ran["member"]) == ("unaffected", "proband", "male")
    record = ran["record"]
    assert (record["id"], record["revision"]) == (ran["delivery_id"], 3)
    assert record["package_sha256"] == ran["package3"]["json"]["package_sha256"]
    assert [correction["revision"] for correction in record["corrections"]] == [1, 2, 3]
    assert record["corrections"][1]["reason"] == "The request form gives another sex."
    assert record["corrections"][0]["by"] == "admin"
    assert ran["job_delivery"] == record


def test_the_revisions_cannot_be_rewritten_and_each_is_on_the_chain(ran) -> None:
    assert ran["rewrite"] is not None and "append-only" in ran["rewrite"]
    assert ran["delete"] is not None and "append-only" in ran["delete"]
    revised = [event for event in ran["events"] if event["action"] == "import_delivery_revised"]
    assert [(event["revision"], event["state"]) for event in revised] == [
        ("1", "ready"),
        ("2", "needs_attention"),
        ("3", "ready"),
    ]
    assert {event["actor"] for event in revised} == {"admin"}
    assert revised[0]["reason"] == "Clinical status and phenotype from the request form."
    assert revised[1]["changes"][0]["path"] == ["members", ran["sample"], "sex"]
    verified = ran["verified"]
    assert verified["status"] == 200, verified
    assert verified["json"]["verified"] is True
