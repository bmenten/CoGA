"""An import gives an existing family the members of its package (E2E, real Postgres and ClickHouse).

The package's members are the pedigree an import reads (the owner's decision of 2026-11-10): an
update adds the members the family lacks and changes none it holds; an overwrite makes the
package's members the family's pedigree. Through the watcher's round, the app over HTTP (an
in-process ``httpx.ASGITransport`` client, see test_e2e_api_contract.py) as the e2e
administrator, and the import job as a worker runs it, for one family delivered three times:

- delivered with one member (``_S1``), it is imported as a new family;
- delivered again with a child of ``_S1`` besides (``_S2``), the delivery warns that the package
  adds a member (``package_adds_members``); approved with update, the family has both, ``_S2``
  with ``_S1`` as its father, recorded as the next structure version, made by the import;
- delivered again with ``_S1`` and another new member (``_S3``) but without ``_S2``, the delivery
  needs attention (``package_members_differ_from_family``): approval with update is refused (400),
  and with overwrite the family's active members are the package's, ``_S2`` made inactive and its
  link gone.

The values are synthetic: the family is new and uniquely named. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, job_outcome, one_sample_package
from backend.tests.e2e.test_e2e_import_watcher import write_package

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
    family = f"E2E_PEDIGREE_{run}"
    s1, s2, s3 = (f"{family}_S{n}" for n in (1, 2, 3))
    folder = one_sample_package(base / project / family, family, variants=2, datasets=("snv",))
    (folder / "manifest.yaml").unlink()
    roots = [base.resolve()]

    def deliver(members: list[dict[str, Any]]) -> None:
        write_package(
            folder, project=project, family_id=family, full=False, members=members, datasets={"snv": {"family_vcf": "snv/family.vcf"}}
        )

    async def latest_delivery() -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, state, issues, warnings FROM import_deliveries "
                        "WHERE folder_name = :n ORDER BY first_seen_at DESC LIMIT 1"
                    ),
                    {"n": family},
                )
            ).mappings().one()
        return dict(row)

    async def pedigree() -> dict[str, Any]:
        async with sessionmaker() as session:
            members = (
                await session.execute(
                    text(
                        "SELECT s.sample_id, s.sex, fm.active FROM family_members fm JOIN samples s ON s.id = fm.sample_id "
                        "JOIN families f ON f.id = fm.family_id WHERE f.family_id = :f"
                    ),
                    {"f": family},
                )
            ).mappings().all()
            links = (
                await session.execute(
                    text(
                        "SELECT fr.relationship_type, sa.sample_id AS a, sb.sample_id AS b, fr.role_a "
                        "FROM family_relationships fr JOIN samples sa ON sa.id = fr.sample_id_a "
                        "JOIN samples sb ON sb.id = fr.sample_id_b JOIN families f ON f.id = fr.family_id "
                        "WHERE f.family_id = :f AND fr.active"
                    ),
                    {"f": family},
                )
            ).mappings().all()
            version = (
                await session.execute(
                    text(
                        "SELECT v.version, v.metadata ->> 'reason' AS reason FROM family_structure_versions v "
                        "JOIN families f ON f.id = v.family_id WHERE f.family_id = :f ORDER BY v.version DESC LIMIT 1"
                    ),
                    {"f": family},
                )
            ).mappings().first()
        return {
            "members": {row["sample_id"]: (row["sex"], row["active"]) for row in members},
            "links": sorted((row["relationship_type"], row["a"], row["b"], row["role_a"]) for row in links),
            "version": dict(version) if version is not None else None,
        }

    out: dict[str, Any] = {"family": family, "s1": s1, "s2": s2, "s3": s3}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"

        async def approve(**body: Any) -> dict[str, Any]:
            delivery = await latest_delivery()
            return capture(
                await client.post(
                    f"/api/family-imports/deliveries/{delivery['id']}/approve",
                    json={"revision": 0, "acknowledge_warnings": True, **body},
                )
            )

        async def run_job(approved: dict[str, Any]) -> dict[str, Any]:
            job_id = approved["json"]["job"]["_id"]
            worker = f"e2e-pedigree-{uuid4().hex[:8]}"
            await claim_import_job(job_id, worker)
            await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
            await import_watcher.run_watch_round(sessionmaker, roots)
            return await job_outcome(job_id)

        deliver([{"sample_id": s1, "sex": "male", "role": "proband", "clinical_status": "affected"}])
        await import_watcher.run_watch_round(sessionmaker, roots)
        out["new_job"] = await run_job(await approve())
        out["new"] = await pedigree()

        deliver(
            [
                {"sample_id": s1, "sex": "male", "role": "proband", "clinical_status": "affected"},
                {"sample_id": s2, "sex": "female", "father": s1, "clinical_status": "unaffected"},
            ]
        )
        await import_watcher.run_watch_round(sessionmaker, roots)
        out["adds"] = await latest_delivery()
        out["update_job"] = await run_job(await approve(conflict_mode="update"))
        out["updated"] = await pedigree()

        deliver(
            [
                {"sample_id": s1, "sex": "male", "role": "proband", "clinical_status": "affected"},
                {"sample_id": s3, "sex": "female", "clinical_status": "unaffected"},
            ]
        )
        await import_watcher.run_watch_round(sessionmaker, roots)
        out["differs"] = await latest_delivery()
        out["refused_update"] = await approve(conflict_mode="update")
        out["overwrite_job"] = await run_job(await approve(conflict_mode="overwrite"))
        out["overwritten"] = await pedigree()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("pedigree_from_package")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    return _harness.run_async(lambda: _exercise(base, uuid4().hex[:10].upper()))


def _codes(items: Any) -> set[str]:
    return {item["code"] for item in items or []}


def test_a_delivered_family_is_imported_with_the_packages_member(ran) -> None:
    assert ran["new_job"]["status"] == "completed", ran["new_job"]
    assert ran["new"]["members"] == {ran["s1"]: ("male", True)}


def test_an_update_adds_the_members_the_family_lacks(ran) -> None:
    s1, s2 = ran["s1"], ran["s2"]
    adds = ran["adds"]
    assert "package_members_differ_from_family" not in _codes(adds["issues"])
    assert "package_adds_members" in _codes(adds["warnings"])
    assert ran["update_job"]["status"] == "completed", ran["update_job"]
    updated = ran["updated"]
    assert updated["members"] == {s1: ("male", True), s2: ("female", True)}
    assert ("parent_child", s1, s2, "father") in updated["links"]
    # The next structure version, made by the import.
    assert updated["version"]["version"] > ran["new"]["version"]["version"]
    assert "package import added" in updated["version"]["reason"]


def test_a_package_whose_members_differ_is_approved_only_with_overwrite(ran) -> None:
    differs = ran["differs"]
    assert differs["state"] == "needs_attention"
    assert "package_members_differ_from_family" in _codes(differs["issues"])
    refused = ran["refused_update"]
    assert refused["status"] == 400, refused
    assert "Correct the package, or overwrite" in refused["json"]["detail"]


def test_an_overwrite_makes_the_packages_members_the_familys_pedigree(ran) -> None:
    s1, s2, s3 = ran["s1"], ran["s2"], ran["s3"]
    assert ran["overwrite_job"]["status"] == "completed", ran["overwrite_job"]
    overwritten = ran["overwritten"]
    assert overwritten["members"] == {s1: ("male", True), s2: ("female", False), s3: ("female", True)}
    assert not [link for link in overwritten["links"] if s2 in link]
    assert overwritten["version"]["version"] > ran["updated"]["version"]["version"]
    assert "package's members the family's pedigree" in overwritten["version"]["reason"]
