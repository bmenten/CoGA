"""Real HTTP/DB ancestry lifecycle on a family's stored callset, with an independent PCA oracle.

A synthetic family is imported; the worker's sweep queues an automatic run once its genotypes
are stored; the run reads them from ClickHouse with no lock of the family's variant writes held,
and publishes only if nothing was written in between. All values are synthetic.
"""
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
    from backend.app.services.ancestry_projection import AncestryMarker, AncestryReference
    from backend.app.services.clickhouse_variant_storage import bump_family_small_variant_data_version
    from backend.app.services.family_package_import import run_family_import_job
    from backend.app.services.family_package_jobs import claim_next_family_import_job
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
    # The mother carries ALT at every marker, so every row holds an explicit call of each
    # individual: the proband's and the mother's callsets are complete, the father's all no-calls.
    (root / "family.vcf").write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{proband}\t{mother}\t{father}\n" +
        "".join(f"1\t{i + 1}\t.\tA\tG\t50\tPASS\t.\tGT\t{['0/0', '0/1', '1/1'][i % 3]}\t1/1\t./.\n" for i in range(loci)))
    # A gVCF the package declares for ancestry only, not imported: the father's reference
    # blocks over every marker, and one variant record at a marker.
    (root / "father.g.vcf").write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\n"
        "1\t1\t.\tA\t<NON_REF>\t.\t.\tEND=2\tGT\t0/0\n"
        "1\t3\t.\tA\tG,<NON_REF>\t50\tPASS\t.\tGT\t1/1\n"
        "1\t4\t.\tA\t<NON_REF>\t.\t.\tEND=1000\tGT\t0/0\n")
    (root / "manifest.json").write_text(json.dumps({"schema_version": 1, "family_id": family, "ped": "family.ped",
        "metadata": {"ancestry": {"vcfs": [{"path": "father.g.vcf", "label": "Father gVCF"}]}},
        "datasets": {"snv": {"family_vcf": "family.vcf", "source_format": "clair3"}}}))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            url = f"/api/families/{family}/ancestry"
            params = {"project_id": project}
            from backend.tests.e2e._import_jobs import queue_import

            job = await queue_import(root, project, conflict_mode="overwrite")
            async with sm() as session:
                # The import is queued: the sweep queues nothing for the family while it runs.
                claimed = await claim_next_family_import_job(session, worker_id="synthetic-ancestry")
                assert str(claimed["id"]) == job
            await run_family_import_job(job_id=job, worker_id="synthetic-ancestry")
            imported = (await ac.get(f"/api/family-imports/{job}")).json()
            assert imported["status"] == "completed", imported.get("validation_errors")
            assert any("declared for the exploratory ancestry PCA" in line for line in imported["logs"]), imported["logs"]
            async with sm() as session:
                family_uuid = (await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"),
                                                     {"f": family})).scalar_one()

            async def snapshot(**extra):
                response = await ac.get(url, params={**params, **extra})
                assert response.status_code == 200, response.text
                return response.json()

            async def sweep() -> int:
                async with sm() as session:
                    return await service.queue_changed_families(session, [family_uuid])

            initial = await snapshot()
            assert initial["analysis"] is None and initial["reference"]["sample_count"] == 5
            assert initial["can_calculate"] and initial["state"] == "ready"
            assert initial["runs"] == []  # nothing in the import itself queues a run

            # The worker's sweep queues one automatic run for the stored genotypes, and only one.
            assert await sweep() == 1
            assert await sweep() == 0
            queued = (await snapshot())["runs"]
            assert [(r["trigger"], r["status"]) for r in queued] == [("automatic", "queued")]
            assert (await ac.post(url + "/calculate", params=params)).status_code == 409
            # The callsets are read with no lock of the family's variant writes held: an import
            # or a sign-out of the family never waits for a calculation. The second read checks,
            # under the locks, that the rows did not change before publishing.
            during_read: list[bool] = []
            original_read = service.read_marker_rows

            async def read_and_probe(*args, **kwargs):
                during_read.append(await writer_could_lock(sm, family_uuid))
                return await original_read(*args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(service, "read_marker_rows", read_and_probe)
                assert await service.process_next_run()
            assert during_read == [True, False]
            completed = await snapshot()
            assert completed["runs"][0]["status"] == "completed", completed
            first_run = completed["runs"][0]["id"]
            analysis = completed["analysis"]
            assert analysis["current_genotypes"] and analysis["current_reference"]
            assert analysis["calculation_version"] == "coga-ancestry-pca-v2" and "source_id" not in analysis
            samples = {r["sample_id"]: r for r in analysis["samples"]}
            assert samples[proband]["pcs"] == [-1, 0, 1, -1]
            assert samples[mother]["pcs"] == [1, 1, 1, 1]
            assert (samples[proband]["callset"], samples[proband]["kind"]) == ("clair3", "complete")
            assert samples[father]["pcs"] is None and samples[father]["status"] == "insufficient_data"
            assert samples[father]["kind"] == "partial" and samples[father]["qc"]["no_call"] == 1000
            assert samples[father]["candidates"][0]["carriers"] == 0
            assert analysis["assumptions"]["min_call_rate"] == 0.95
            assert analysis["assumptions"]["marker_rows"].startswith("ancestry-rows-v1:")

            # Historical views retain their own reference, even after configuration changes.
            other_public = public.model_copy(update={"sha256": "b" * 64, "name": "Synthetic reference v2"})
            other = AncestryReference(other_public, reference.markers, reference.means, reference.scales, reference.loadings)
            monkeypatch.setattr(service, "configured_reference", lambda: other)
            old = await snapshot(run_id=first_run)
            assert old["reference"]["sha256"] == "a" * 64 and not old["analysis"]["current_reference"]
            assert (await ac.get(url, params={**params, "run_id": str(uuid4())})).status_code == 404

            # Pin the reference when queued: a replaced model never changes a running request.
            response = await ac.post(url + "/calculate", params=params)
            assert response.status_code == 202 and response.json()["trigger"] == "user"
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

            # A revoked requester cannot publish a newer result.
            assert (await ac.post(url + "/calculate", params=params)).status_code == 202
            async with sm() as session:
                revoked = await service.claim_run(session)
            with monkeypatch.context() as patch:
                async def revoked_user(*args):
                    return outsider
                patch.setattr(service, "get_current_user_by_id", revoked_user)
                await service.calculate_run(revoked)
            assert (await snapshot())["runs"][0]["status"] == "failed"

            # A write of the family's variants while the callsets are read: nothing is published.
            assert (await ac.post(url + "/calculate", params=params)).status_code == 202
            with monkeypatch.context() as patch:
                async def read_then_write(*args, **kwargs):
                    rows = await original_read(*args, **kwargs)
                    await bump_family_small_variant_data_version("GRCh38", family_uuid)
                    return rows
                patch.setattr(service, "read_marker_rows", read_then_write)
                assert await service.process_next_run()
            interrupted = await snapshot()
            assert interrupted["runs"][0]["status"] == "failed"
            assert interrupted["runs"][0]["error"].startswith("Genotypes were written during calculation")
            # The projection shown is now of earlier genotypes; the sweep queues a new one.
            assert interrupted["analysis"]["run_id"] == first_run and not interrupted["analysis"]["current_genotypes"]
            assert await sweep() == 1
            assert await service.process_next_run()
            current = await snapshot()
            assert current["runs"][0]["trigger"] == "automatic" and current["runs"][0]["status"] == "completed"
            assert current["analysis"]["current_genotypes"] and current["analysis"]["run_id"] != first_run
            assert await sweep() == 0

            # A user's choice of how an individual is read: saved, read by the calculation it queues.
            choices = f"{url}/choices"
            response = await ac.put(f"{choices}/{father}", params=params, json={"mode": "exclude"})
            assert response.status_code == 200, response.text
            chosen = response.json()
            assert [(c["sample_id"], c["mode"]) for c in chosen["choices"]] == [(father, "exclude")]
            assert chosen["can_choose"] and chosen["runs"][0]["trigger"] == "user"
            assert chosen["runs"][0]["status"] == "queued"
            assert await service.process_next_run()
            samples = {r["sample_id"]: r for r in (await snapshot())["analysis"]["samples"]}
            assert samples[father]["status"] == "excluded" and samples[father]["choice"]["mode"] == "exclude"
            assert samples[proband]["pcs"] == [-1, 0, 1, -1] and samples[proband]["choice"] is None
            # A BED file's regions: only the markers inside it and its checksum are kept.
            bed = "".join(f"1\t{i}\t{i + 1}\n" for i in range(0, 500))
            response = await ac.put(f"{choices}/{proband}/bed", params=params, data={"callset": "clair3"},
                                    files={"file": ("kits/capture.bed", bed.encode(), "text/plain")})
            assert response.status_code == 200, response.text
            regions = next(c for c in response.json()["choices"] if c["sample_id"] == proband)["regions"]
            assert (regions["source"], regions["name"], regions["covered_markers"]) == ("bed", "capture.bed", 500)
            assert len(regions["sha256"]) == 64 and "loci" not in regions
            async with sm() as session:
                stored = (await session.execute(text(
                    "SELECT regions FROM ancestry_sample_choices WHERE sample_id = :s ORDER BY chosen_at DESC LIMIT 1"),
                    {"s": proband})).scalar_one()
            assert len(stored["loci"]) == 500 and "1\t0\t1" not in json.dumps(stored)
            assert await service.process_next_run()
            samples = {r["sample_id"]: r for r in (await snapshot())["analysis"]["samples"]}
            # The proband's callset has a call at every marker: complete, whatever the regions.
            assert samples[proband]["kind"] == "complete" and samples[proband]["choice"]["regions"]["covered_markers"] == 500
            # Back to CoGA's own reading; an unknown sample, an inconsistent choice, a BED that is
            # no list of intervals, a reader and an outsider are refused.
            assert (await ac.put(f"{choices}/{proband}", params=params, json={"mode": "auto"})).status_code == 200
            assert (await ac.put(f"{choices}/UNKNOWN_{family}", params=params, json={"mode": "auto"})).status_code == 404
            assert (await ac.put(f"{choices}/{proband}", params=params, json={"mode": "callset"})).status_code == 422
            assert (await ac.put(f"{choices}/{proband}/bed", params=params, data={"callset": "clair3"},
                                 files={"file": ("x.bed", b"chr1\tten\t20\n", "text/plain")})).status_code == 400
            with monkeypatch.context():
                app.dependency_overrides[get_current_user] = scoped_user
                assert (await ac.put(f"{choices}/{proband}", params=params, json={"mode": "auto"})).status_code == 403
                app.dependency_overrides[get_current_user] = outside_user
                assert (await ac.put(f"{choices}/{proband}", params=params, json={"mode": "auto"})).status_code == 404
            app.dependency_overrides.clear()
            assert await service.process_next_run()
            current = await snapshot()
            assert {c["sample_id"]: c["mode"] for c in current["choices"]} == {father: "exclude", proband: "auto"}
            # A choice made while a run was running: the sweep queues one that reads it.
            async with sm() as session:
                await session.execute(text(
                    "INSERT INTO ancestry_sample_choices (family_uuid, project_id, sample_id, mode, chosen_by) "
                    "VALUES (CAST(:f AS uuid), CAST(:p AS uuid), :s, 'auto', CAST(:u AS uuid))"),
                    {"f": family_uuid, "p": project, "s": father, "u": admin.id})
                await session.commit()
            assert await sweep() == 1 and await sweep() == 0
            assert await service.process_next_run()
            samples = {r["sample_id"]: r for r in (await snapshot())["analysis"]["samples"]}
            assert samples[father]["status"] == "insufficient_data" and samples[father]["choice"] is None

            # The declared gVCF: scanned once in the background, then read like a callset. Its
            # reference blocks are explicit reference calls, so the father, without a call in the
            # family VCF, is projected from it.
            files = (await snapshot())["files"]
            assert [(f["label"], f["status"]) for f in files] == [("Father gVCF", "pending")]
            assert await service.scan_next_declared_file() and not await service.scan_next_declared_file()
            scanned = (await snapshot())["files"][0]
            assert scanned["status"] == "scanned" and scanned["columns"] == [father] and len(scanned["sha256"]) == 64
            assert await sweep() == 1
            assert await service.process_next_run()
            samples = {r["sample_id"]: r for r in (await snapshot())["analysis"]["samples"]}
            file_callset = f"file:{scanned['id']}"
            assert (samples[father]["callset"], samples[father]["kind"]) == (file_callset, "complete")
            # Dosages 0 everywhere but marker 3 (ALT/ALT): the first four PCs are the oracle's.
            assert samples[father]["pcs"] == [-1, -1, 1, -1]
            assert [c["callset"] for c in samples[father]["candidates"]] == ["clair3", file_callset]
            # A choice can name the file.
            response = await ac.put(f"{choices}/{proband}", params=params, json={"mode": "callset", "callset": file_callset})
            assert response.status_code == 200, response.text
            assert (await ac.put(f"{choices}/{proband}", params=params,
                                 json={"mode": "callset", "callset": "file:not-a-uuid"})).status_code == 422

            # No ancestry values or reference snapshots enter the clinical signed record.
            from backend.app.services.report_signout_service import build_report_snapshot
            async with sm() as session:
                signed = await build_report_snapshot(session, family_id=family, user=admin, project_id=project)
                count = (await session.execute(text("SELECT count(*) FROM ancestry_results WHERE run_id IN (SELECT id FROM ancestry_runs WHERE family_identifier = :f)"),
                                               {"f": family})).scalar_one()
            assert count == 7 and "coga-ancestry-pca" not in json.dumps(signed)
            assert "ancestry" not in signed and first_run not in json.dumps(signed)
    finally:
        app.dependency_overrides.clear()
        await close_clickhouse_client()
        await close_postgres_engine()


def test_family_ancestry_lifecycle(tmp_path, monkeypatch):
    asyncio.run(exercise(tmp_path / "synthetic-ancestry", monkeypatch))
