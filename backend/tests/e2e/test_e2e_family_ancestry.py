"""Real HTTP/DB ancestry lifecycle, with synthetic genomes and an independent PCA oracle."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def writer_could_lock(sessionmaker, family_uuid: str) -> bool:
    """Whether a writer of the family's variants would get its locks now, without waiting."""
    from backend.app.services.family_variant_write_lock import VARIANT_TYPES, family_variant_lock_key

    async with sessionmaker() as session:
        granted = [
            (await session.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                                   {"k": family_variant_lock_key(family_uuid, kind)})).scalar_one()
            for kind in VARIANT_TYPES
        ]
        await session.rollback()
    return all(granted)


async def exercise(root: Path, monkeypatch):
    from backend.app.ancestry_schemas import AncestryReferenceOut
    from backend.app.core.clickhouse import close_clickhouse_client, init_clickhouse_schema
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.services import ancestry_service as service
    from backend.app.services.access_control import RecordNotVisible
    from backend.app.services.ancestry_projection import AncestryReference
    from backend.app.services.family_package_import import run_family_import_job
    from backend.app.services.family_package_jobs import claim_next_family_import_job
    from backend.app.services.ancestry_projection import AncestryMarker
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        admin, project, _ = await _harness.ensure_e2e_project(session)
    family = f"ANCESTRY_SYNTH_{uuid4().hex[:8]}"
    proband, mother, father = (f"{role}_{family}" for role in ("PROBAND", "MOTHER", "FATHER1"))
    loci = 1000
    # Four nonoverlapping unit-length loading columns. The first four called
    # genotypes alone determine the expected PCs, independently of service code.
    loadings = np.zeros((loci, 4))
    loadings[:4, :] = np.eye(4)
    public = AncestryReferenceOut(sha256="a" * 64, name="Synthetic reference", source_url="https://example.org/synthetic",
        source_revision="synthetic-v1", method="Synthetic orthogonal PCA", variant_count=loci, sample_count=5,
        explained_variance=[0.4, 0.3, 0.2, 0.1], populations={"SYN": "Synthetic"},
        points=[{"id": f"SYN-{i}", "population": "SYN", "pcs": [float(i)] * 4} for i in range(5)])
    reference = AncestryReference(public, [AncestryMarker("1", i + 1, "A", "G") for i in range(loci)],
        np.ones(loci), np.ones(loci), loadings)
    monkeypatch.setattr(service, "configured_reference", lambda: reference)
    from backend.app.core.config import settings
    monkeypatch.setattr(settings, "family_import_roots", [str(root.parent)])
    root.mkdir(exist_ok=True)
    (root / "family.ped").write_text(f"{family} {proband} {father} {mother} 1 2\n{family} {mother} 0 0 2 1\n{family} {father} 0 0 1 1\n")
    (root / "family.vcf").write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{proband}\t{mother}\t{father}\n" +
        "".join(f"1\t{i + 1}\t.\tA\tG\t50\tPASS\t.\tGT\t{['0/0', '0/1', '1/1'][i % 3]}\t1/1\t./.\n" for i in range(loci)))
    (root / "manifest.json").write_text(json.dumps({"schema_version": 1, "family_id": family, "ped": "family.ped",
        "metadata": {"ancestry": {"full_wgs": True, "genome_build": "GRCh38", "absent_record_is_hom_ref": True}},
        "datasets": {"snv": {"family_vcf": "family.vcf", "source_format": "clair3"}}}))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            url = f"/api/families/{family}/ancestry"
            params = {"project_id": project}
            response = await ac.post("/api/family-imports", json={"folder_path": str(root), "project_id": project,
                                    "dry_run": False, "conflict_mode": "overwrite"})
            assert response.status_code == 200, response.text
            job = response.json()["_id"]
            if isinstance(job, dict):
                job = job["$oid"]
            async with sm() as session:
                claimed = await claim_next_family_import_job(session, worker_id="synthetic-ancestry")
                assert str(claimed["id"]) == job
            await run_family_import_job(job_id=job, worker_id="synthetic-ancestry")
            imported = (await ac.get(f"/api/family-imports/{job}")).json()
            assert imported["status"] == "completed", imported.get("validation_errors")
            assert any("registered as the WGS source" in line for line in imported["logs"]), imported["logs"]

            async def snapshot(**extra):
                response = await ac.get(url, params={**params, **extra})
                assert response.status_code == 200, response.text
                return response.json()

            initial = await snapshot()
            assert initial["analysis"] is None and initial["reference"]["sample_count"] == 5
            assert initial["can_calculate"] and initial["source_state"] == "ready"
            assert initial["runs"] == []  # importing a WGS source never silently queues PCA
            response = await ac.post(url + "/calculate", params=params)
            assert response.status_code == 202, response.text
            first_run = response.json()["id"]
            assert (await ac.post(url + "/calculate", params=params)).status_code == 409
            # The VCF is read with no lock of the family's variant writes held: an import or a
            # sign-out of the family never waits for a calculation.
            async with sm() as session:
                family_uuid = (await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"),
                                                     {"f": family})).scalar_one()
            loop = asyncio.get_running_loop()
            during_read: list[bool] = []
            original_project_file = service.project_file

            def project_and_probe(*args, **kwargs):
                during_read.append(asyncio.run_coroutine_threadsafe(writer_could_lock(sm, family_uuid), loop).result())
                return original_project_file(*args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(service, "project_file", project_and_probe)
                assert await service.process_next_run()
            assert during_read == [True]
            completed = await snapshot()
            assert completed["runs"][0]["status"] == "completed", completed
            analysis = completed["analysis"]
            assert analysis["current_source"] and analysis["current_reference"]
            samples = {r["sample_id"]: r for r in analysis["samples"]}
            assert samples[proband]["pcs"] == [-1, 0, 1, -1]
            assert samples[mother]["pcs"] == [1, 1, 1, 1]
            assert samples[father]["pcs"] is None and samples[father]["status"] == "insufficient_data"
            assert samples[father]["qc"]["no_call"] == 1000 and samples[father]["qc"]["assumed_ref"] == 0
            assert analysis["assumptions"]["min_call_rate"] == 0.95

            # Historical views retain their own reference, even after configuration changes.
            other_public = public.model_copy(update={"sha256": "b" * 64, "name": "Synthetic reference v2"})
            other = AncestryReference(other_public, reference.markers, reference.means, reference.scales, reference.loadings)
            monkeypatch.setattr(service, "configured_reference", lambda: other)
            old = await snapshot(run_id=first_run)
            assert old["reference"]["sha256"] == "a" * 64 and not old["analysis"]["current_reference"]
            assert (await ac.get(url, params={**params, "run_id": str(uuid4())})).status_code == 404

            # Pin the reference when queued: a replaced model never changes a running request.
            assert (await ac.post(url + "/calculate", params=params)).status_code == 202
            monkeypatch.setattr(service, "configured_reference", lambda: reference)
            assert await service.process_next_run()
            assert (await snapshot())["runs"][0]["status"] == "failed"
            assert (await snapshot())["analysis"]["run_id"] == first_run

            # Readers can inspect but cannot queue; outside-project users get 404.
            outsider = admin.model_copy(update={"role": "user", "metadata_project_ids": [], "reader_project_ids": []})
            reader = admin.model_copy(update={"role": "user", "metadata_project_ids": [project], "reader_project_ids": [project]})
            with monkeypatch.context():
                async def scoped_user():
                    return reader
                app.dependency_overrides[get_current_user] = scoped_user
                assert not (await snapshot())["can_calculate"]
                assert (await ac.post(url + "/calculate", params=params)).status_code == 403
                async def outside_user():
                    return outsider
                app.dependency_overrides[get_current_user] = outside_user
                assert (await ac.get(url, params=params)).status_code == 404
                assert (await ac.post(url + "/calculate", params=params)).status_code == 404
            app.dependency_overrides.clear()
            with pytest.raises(RecordNotVisible):
                async with sm() as session:
                    await service.family_ancestry(session, family, outsider, project)

            # An interrupted/cancelled calculation never publishes a partial projection.
            assert (await ac.post(url + "/calculate", params=params)).status_code == 202
            async with sm() as session:
                cancelled = await service.claim_run(session)
            with monkeypatch.context() as patch:
                async def cancel_staging(*args, **kwargs):
                    raise asyncio.CancelledError()
                # Raising while the run's row lock is held exercises transaction rollback.
                patch.setattr(service, "get_current_user_by_id", cancel_staging)
                with pytest.raises(asyncio.CancelledError):
                    await service.calculate_run(cancelled)
            assert (await snapshot())["runs"][0]["status"] == "interrupted"

            # A revoked requester, or a changed file, cannot publish a newer result.
            assert (await ac.post(url + "/calculate", params=params)).status_code == 202
            async with sm() as session:
                revoked = await service.claim_run(session)
            with monkeypatch.context() as patch:
                async def revoked_user(*args):
                    return outsider
                patch.setattr(service, "get_current_user_by_id", revoked_user)
                await service.calculate_run(revoked)
            assert (await snapshot())["runs"][0]["status"] == "failed"
            assert (await ac.post(url + "/calculate", params=params)).status_code == 202
            with (root / "family.vcf").open("a") as stream:
                stream.write("\n")
            assert await service.process_next_run()
            stale = await snapshot()
            assert stale["source_state"] == "source_changed" and not stale["can_calculate"]
            assert not stale["analysis"]["current_source"] and stale["analysis"]["run_id"] == first_run
            assert stale["runs"][0]["status"] == "failed"
            assert (await ac.post(url + "/calculate", params=params)).status_code == 409

            # No ancestry values or reference snapshots enter the clinical signed record.
            from backend.app.services.report_signout_service import build_report_snapshot
            async with sm() as session:
                signed = await build_report_snapshot(session, family_id=family, user=admin, project_id=project)
                count = (await session.execute(text("SELECT count(*) FROM ancestry_results WHERE run_id IN (SELECT id FROM ancestry_runs WHERE family_identifier = :f)"),
                                               {"f": family})).scalar_one()
            assert count == 1 and "coga-ancestry-pca-v1" not in json.dumps(signed)
            assert "ancestry" not in signed and first_run not in json.dumps(signed)
    finally:
        app.dependency_overrides.clear()
        await close_clickhouse_client()
        await close_postgres_engine()


def test_family_ancestry_lifecycle(tmp_path, monkeypatch):
    asyncio.run(exercise(tmp_path / "synthetic-ancestry", monkeypatch))
