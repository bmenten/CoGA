"""Real HTTP + Postgres + ClickHouse PGS lifecycle on synthetic full-WGS imports."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.integration

WEIGHTS = "#pgs_id=PGS005091\n#genome_build=GRCh38\nchr_name\tchr_position\teffect_allele\tother_allele\tref_allele\teffect_weight\n1\t10\tG\tA\tA\t0.5\n1\t20\tC\tT\tC\t-1\n"
VCF = ("##fileformat=VCFv4.2\n##reference=GRCh38\n"
       '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
       "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\tMOTHER\tFATHER1\n"
       "1\t10\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\t1/1\t./.\n")


async def exercise(root: Path, monkeypatch):
    from backend.app.core.clickhouse import close_clickhouse_client, init_clickhouse_schema
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import pgs_service as pgs
    from backend.app.services.clickhouse_variant_storage import bump_family_small_variant_data_version
    from backend.app.services.family_package_import import run_family_import_job
    from backend.app.services.family_package_jobs import claim_next_family_import_job
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        admin, project, _assembly = await _harness.ensure_e2e_project(session)
    family = f"PGS_SYNTH_{uuid4().hex[:10]}"
    proband, mother, father = (f"{role}_{family}" for role in ("PROBAND", "MOTHER", "FATHER1"))
    root.mkdir(exist_ok=True)
    (root / "family.ped").write_text(f"{family} {proband} {father} {mother} 1 2\n{family} {mother} 0 0 2 1\n{family} {father} 0 0 1 1\n")
    (root / "family.vcf").write_text(VCF.replace("PROBAND", proband).replace("MOTHER", mother).replace("FATHER1", father))
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "family_id": family, "ped": "family.ped",
        "metadata": {"pgs": {"full_wgs": True, "genome_build": "GRCh38", "absent_record_is_hom_ref": True}},
        "datasets": {"snv": {"family_vcf": "family.vcf", "source_format": "clair3"}},
    }))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            suffix = uuid4().hex[:10]
            async def publish(label, weights=WEIGHTS):
                response = await ac.post("/api/admin/pgs/models/PGS005091/versions", data={
                    "version": label, "study": "Synthetic study; test weights only",
                    "source_url": "https://www.pgscatalog.org/score/PGS005091/"},
                    files={"file": ("synthetic.tsv", weights.encode(), "text/tab-separated-values")})
                assert response.status_code == 201, response.text
                return response.json()

            v1 = await publish(f"synthetic-v1-{suffix}")
            async def snapshot():
                response = await ac.get(f"/api/families/{family}/pgs", params={"project_id": project})
                assert response.status_code == 200, response.text
                data = response.json()
                # Other imported Catalog models can coexist in the same test database.
                # This lifecycle's arithmetic/history oracle concerns PGS005091 only.
                own_versions = {v["id"] for v in data["versions"] if v["pgs_id"] == "PGS005091"}
                data["results"] = [r for r in data["results"] if r["version_id"] in own_versions]
                return data

            async def run_import(*, dry_run=False, expected="completed"):
                response = await ac.post("/api/family-imports", json={"folder_path": str(root), "project_id": project,
                                        "dry_run": dry_run, "conflict_mode": "overwrite"})
                assert response.status_code == 200, response.text
                job = response.json()["_id"]
                if isinstance(job, dict):
                    job = job["$oid"]
                async with sm() as session:
                    claimed = await claim_next_family_import_job(session, worker_id="synthetic-pgs")
                    assert str(claimed["id"]) == job
                await run_family_import_job(job_id=job, worker_id="synthetic-pgs")
                response = await ac.get(f"/api/family-imports/{job}")
                assert response.json()["status"] == expected, response.text
                return job

            import_id = await run_import()
            data = await snapshot()
            assert data["source_state"] == "ready", data
            assert data["sources"][0]["import_job_id"] == import_id
            assert len(data["runs"]) == 1 and not data["results"]
            async with sm() as session:
                run = await pgs.claim_run(session)
            await pgs.calculate_run(run)
            data = await snapshot()
            assert data["runs"][0]["status"] == "completed", data
            results = {r["sample_id"].split("_", 1)[0]: r for r in data["results"] if r["version_id"] == v1["id"]}
            assert results["PROBAND"]["raw_score"] == -1.5
            assert results["MOTHER"]["raw_score"] == -1
            assert results["FATHER1"]["raw_score"] is None
            assert results["FATHER1"]["qc"]["no_call"] == 1
            assert all(r["qc"]["assumed_ref"] == 1 for r in results.values())
            assert all(r["calculation_version"] == "coga-pgs-gt-v1" for r in results.values())
            assert results["PROBAND"]["assumptions"]["full_wgs"] is True
            assert v1["weights_sha256"] != v1["resolved_sha256"]
            from backend.app.services.report_signout_service import build_report_snapshot
            async with sm() as session:
                signed = await build_report_snapshot(session, family_id=family, user=admin, project_id=project)
            # The exploratory source declaration and results are outside the signed record.
            assert "pgs" not in signed
            assert v1["id"] not in json.dumps(signed)
            assert "PGS005091" not in json.dumps(signed)
            assert "coga-pgs-gt-v1" not in json.dumps(signed)
            # A dry run neither adds a source nor queues another score.
            await run_import(dry_run=True)
            dry = await snapshot()
            assert len(dry["sources"]) == 1 and len(dry["runs"]) == 1
            duplicate_version = await ac.post("/api/admin/pgs/models/PGS005091/versions", data={
                "version": v1["version"], "study": "Synthetic duplicate", "source_url": "https://example.org/study"},
                files={"file": ("weights.tsv", WEIGHTS.encode())})
            assert duplicate_version.status_code == 409

            # A new version queues nothing for an already-imported family.
            v2 = await publish(f"synthetic-v2-{suffix}", WEIGHTS.replace("0.5", "1.0"))
            newer = await snapshot()
            assert len(newer["runs"]) == 1 and v2["id"] in newer["pending_version_ids"]
            response = await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})
            assert response.status_code == 202, response.text
            duplicate = await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})
            assert duplicate.status_code == 409
            async with sm() as session:
                run = await pgs.claim_run(session)
            await pgs.calculate_run(run)
            data = await snapshot()
            assert len(data["results"]) == 6
            assert next(r for r in data["results"] if r["sample_id"] == proband and r["version_id"] == v2["id"])["raw_score"] == -1

            response = await ac.put("/api/admin/pgs/models/PGS005091/archive", json={"archived": True})
            assert response.status_code == 200
            archived = await snapshot()
            assert len(archived["results"]) == 6 and all(v["archived"] for v in archived["versions"] if v["pgs_id"] == "PGS005091")
            assert not archived["pending_version_ids"]
            await ac.put("/api/admin/pgs/models/PGS005091/archive", json={"archived": False})

            # Actual project RBAC checkpoints, with no datastore or family-context mocks.
            from backend.app.dependencies import get_current_user
            reader = admin.model_copy(update={"role": "user", "metadata_project_ids": [project], "reader_project_ids": [project]})
            outsider = admin.model_copy(update={"role": "user", "metadata_project_ids": [], "reader_project_ids": []})
            app.dependency_overrides[get_current_user] = lambda: reader
            assert (await ac.get(f"/api/families/{family}/pgs", params={"project_id": project})).status_code == 200
            assert (await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})).status_code == 403
            assert (await ac.put("/api/admin/pgs/models/PGS005091/archive", json={"archived": True})).status_code == 403
            app.dependency_overrides[get_current_user] = lambda: outsider
            assert (await ac.get(f"/api/families/{family}/pgs", params={"project_id": project})).status_code == 404
            assert (await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})).status_code == 404
            app.dependency_overrides.pop(get_current_user)

            # An interrupted run rolls back every result, and can be requested again.
            await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})
            async with sm() as session:
                run = await pgs.claim_run(session)
            async def cancelled(*args, **kwargs):
                raise asyncio.CancelledError
            with monkeypatch.context() as patch:
                patch.setattr(pgs, "_source_state", cancelled)
                with pytest.raises(asyncio.CancelledError):
                    await pgs.calculate_run(run)
            interrupted = await snapshot()
            assert interrupted["runs"][0]["status"] == "interrupted"
            assert len(interrupted["results"]) == 6

            # A live row lock protects a long-running task; after a process dies its
            # unlocked old job becomes interrupted rather than being retried.
            await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})
            async with sm() as session:
                orphan = await pgs.claim_run(session)
                await session.execute(text("UPDATE pgs_runs SET started_at = now() - interval '11 minutes' WHERE id = CAST(:id AS uuid)"), {"id": orphan})
                await session.commit()
            async with sm() as held:
                await held.execute(text("SELECT id FROM pgs_runs WHERE id = CAST(:id AS uuid) FOR UPDATE"), {"id": orphan})
                async with sm() as session:
                    assert await pgs.claim_run(session) is None
                assert (await snapshot())["runs"][0]["status"] == "running"
            async with sm() as session:
                assert await pgs.claim_run(session) is None
            assert (await snapshot())["runs"][0]["status"] == "interrupted"

            # Revoking a queued requester's project rights prevents execution/publication.
            await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})
            async with sm() as session:
                revoked = await pgs.claim_run(session)
            with monkeypatch.context() as patch:
                async def revoked_user(*args):
                    return outsider
                patch.setattr(pgs, "get_current_user_by_id", revoked_user)
                await pgs.calculate_run(revoked)
            assert (await snapshot())["runs"][0]["status"] == "failed"
            assert len((await snapshot())["results"]) == 6
            # A missing ClickHouse backend retains readable Postgres score history.
            with monkeypatch.context() as patch:
                async def storage_unavailable(*args):
                    raise ConnectionError("synthetic storage failure")
                patch.setattr(pgs, "genotype_fingerprint", storage_unavailable)
                unavailable = await snapshot()
                assert unavailable["source_state"] == "genotype_storage_unavailable"
                assert len(unavailable["results"]) == 6

            # Changed and removed raw sources never trigger a new REF-filled score.
            await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})
            original = (root / "family.vcf").read_text()
            (root / "family.vcf").write_text(original + "\n")
            assert (await snapshot())["source_state"] == "source_changed"
            async with sm() as session:
                changed = await pgs.claim_run(session)
            await pgs.calculate_run(changed)
            assert (await snapshot())["runs"][0]["status"] == "failed"
            assert len((await snapshot())["results"]) == 6
            (root / "family.vcf").unlink()
            assert (await snapshot())["source_state"] == "source_unavailable"
            (root / "family.vcf").write_text(original)
            # A storage-level mutation invalidates the source while retaining history.
            async with sm() as session:
                family_uuid = (await session.execute(text("SELECT id::text FROM families WHERE family_id = :family"), {"family": family})).scalar_one()
            # Direct deletion deliberately bypasses the storage version bump.
            from backend.app.services.clickhouse_variant_storage import _execute, _small_table_name, get_family_small_variant_data_version
            token_before = await get_family_small_variant_data_version("GRCh38", family_uuid)
            table = _small_table_name("GRCh38", "entries")
            await _execute(f"ALTER TABLE {table} DELETE WHERE family_guid = %(family)s SETTINGS mutations_sync = 1", {"family": family_uuid})
            assert await get_family_small_variant_data_version("GRCh38", family_uuid) == token_before
            assert (await snapshot())["source_state"] == "genotype_storage_changed"
            await bump_family_small_variant_data_version("GRCh38", family_uuid)
            stale = await snapshot()
            assert stale["source_state"] == "genotype_storage_changed" and len(stale["results"]) == 6
            assert not any(r["current_source"] for r in stale["results"])
            assert (await ac.post(f"/api/families/{family}/pgs/calculate", params={"project_id": project})).status_code == 409
            # A real failed overwrite registers no source and schedules no scores.
            before_failure = await snapshot()
            (root / "family.vcf").write_text("not a VCF\n")
            await run_import(expected="failed")
            failed = await snapshot()
            assert len(failed["sources"]) == len(before_failure["sources"])
            assert len(failed["runs"]) == len(before_failure["runs"])
            assert len(failed["results"]) == 6
            # Deliberately failed/incomplete imports cannot be scored.
            async with sm() as session:
                await session.execute(text("UPDATE families SET metadata = metadata || CAST(:flag AS jsonb) WHERE id = CAST(:id AS uuid)"),
                                      {"id": family_uuid, "flag": json.dumps({"import_incomplete": {"failed_datasets": ["snv"]}})})
                await session.commit()
            assert (await snapshot())["source_state"] == "import_incomplete"
            assert len((await snapshot())["results"]) == 6
    finally:
        app.dependency_overrides.clear()
        await close_clickhouse_client()
        await close_postgres_engine()


def test_complete_family_pgs_lifecycle(tmp_path, monkeypatch):
    asyncio.run(exercise(tmp_path / "synthetic-pgs", monkeypatch))
