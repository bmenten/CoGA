"""Real HTTP/DB import jobs with a synthetic PGS Catalog HTTP transport, never real data."""
import asyncio
import gzip

import httpx
import pytest
from sqlalchemy import text

from backend.tests.test_pgs_catalog_service import WEIGHTS, metadata

pytestmark = pytest.mark.integration


async def exercise(tmp_path, monkeypatch):
    from backend.app.core.postgres import init_postgres_schema, get_postgres_sessionmaker, close_postgres_engine
    from backend.app.main import app
    from backend.app.dependencies import get_current_user
    from backend.app.services import pgs_catalog_service as catalog, pgs_service
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        admin, _project, _assembly = await _harness.ensure_e2e_project(session)
        before_runs = (await session.execute(text("SELECT count(*) FROM pgs_runs"))).scalar_one()
    monkeypatch.setattr(catalog.settings, "pgs_reference_fasta", "")
    file_state = {"weights": WEIGHTS, "missing": False}
    def serve(request):
        pgs = request.url.path.split("/")[-2] if request.url.host == "www.pgscatalog.org" else request.url.path.split("/")[-1].split("_")[0]
        if request.url.host == "www.pgscatalog.org":
            return httpx.Response(404 if file_state["missing"] else 200, json=metadata(pgs))
        return httpx.Response(200, content=gzip.compress(file_state["weights"].replace("PGS900001", pgs).encode()))
    monkeypatch.setattr(catalog, "catalog_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(serve)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://e2e") as ac:
            ac.headers["Authorization"] = "Bearer " + await _harness.login_admin_token(ac)
            async def imported(pgs="PGS900001"):
                response = await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": pgs})
                assert response.status_code == 202, response.text
                job = response.json()
                duplicate = await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": pgs})
                assert duplicate.json()["id"] == job["id"]
                assert await catalog.process_next_import()
                jobs = (await ac.get("/api/admin/pgs/catalog-imports")).json()["jobs"]
                return next(row for row in jobs if row["id"] == job["id"])
            async def version(job):
                versions = (await ac.get("/api/pgs/models")).json()["versions"]
                return next(v for v in versions if v["id"] == job["version_id"])

            first_job = await imported()
            assert first_job["status"] == "completed", first_job
            first = await version(first_job)
            assert first["scoring_status"] == "ready"
            assert first["catalog_metadata"]["record"]["license"] == "Synthetic licence"
            assert first["catalog_metadata"]["record"]["ancestry_distribution"]
            assert first["catalog_metadata"]["scoring_file"]["variant_count"] == 2
            assert first["version"].startswith("catalog-")
            repeated = await imported()
            assert repeated["version_id"] == first["id"]
            file_state["weights"] = WEIGHTS.replace("0.5", "1.0")
            newer = await version(await imported())
            assert newer["id"] != first["id"]
            assert newer["weights_sha256"] != first["weights_sha256"]
            file_state["weights"] = WEIGHTS
            restored = await version(await imported())
            assert restored["id"] not in {first["id"], newer["id"]}
            assert restored["revision"] > newer["revision"]
            assert restored["weights_sha256"] == first["weights_sha256"]

            # Missing REF never substitutes other_allele; acquisition still keeps the full file.
            file_state["weights"] = WEIGHTS.replace("\tref_allele", "").replace("G\tA\tA", "G\tA").replace("C\tT\tC", "C\tT")
            pending = await version(await imported("PGS900002"))
            assert pending["scoring_status"] == "needs_reference"
            assert pending["variant_count"] == 2 and pending["scoring_error"]
            async with sm() as session:
                data = (await session.execute(text("SELECT weights_text, weights FROM pgs_model_versions WHERE id = CAST(:id AS uuid)"), {"id": pending["id"]})).mappings().one()
                assert len(data["weights_text"].splitlines()) == len(WEIGHTS.splitlines())
                assert data["weights"] == []
                assert pending["id"] not in [v.id for v in pgs_service.active_versions((await pgs_service.catalogue(session)).versions)]

            # Re-import after reference configuration appends a prepared version.
            import pysam
            fasta = tmp_path / "synthetic-grch38.fa"
            fasta.write_text(">chr1\n" + "A" * 19 + "C" + "A" * 10 + "\n")
            pysam.faidx(str(fasta))
            monkeypatch.setattr(catalog.settings, "pgs_reference_fasta", str(fasta))
            prepared = await version(await imported("PGS900002"))
            assert prepared["scoring_status"] == "ready" and prepared["id"] != pending["id"]
            file_state["weights"] = WEIGHTS.replace("1\t10\tG", "1\t10\tGG")
            unsupported = await version(await imported("PGS900003"))
            assert unsupported["scoring_status"] == "unsupported"
            assert unsupported["variant_count"] == 2
            # Oversized models retain complete source and metadata, never partial weights.
            file_state["weights"] = WEIGHTS
            with monkeypatch.context() as patch:
                patch.setattr(catalog.settings, "pgs_max_variants", 1)
                limited = await version(await imported("PGS900009"))
            assert limited["scoring_status"] == "unsupported" and limited["variant_count"] == 2
            assert "PGS_MAX_VARIANTS=1" in limited["scoring_error"]
            async with sm() as session:
                kept = (await session.execute(text("SELECT weights_text, weights, catalog_metadata FROM pgs_model_versions WHERE id = CAST(:id AS uuid)"), {"id": limited["id"]})).mappings().one()
                assert kept["weights_text"] == WEIGHTS.replace("PGS900001", "PGS900009")
                assert kept["weights"] == [] and kept["catalog_metadata"]["scoring_file"]["variant_count"] == 2
            # Raising the tested budget appends a usable revision; history is preserved.
            prepared_limit = await version(await imported("PGS900009"))
            assert prepared_limit["scoring_status"] == "ready" and prepared_limit["id"] != limited["id"]
            file_state["missing"] = True
            failed = await imported("PGS900004")
            assert failed["status"] == "failed" and failed["version_id"] is None
            file_state["missing"] = False
            assert (await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": "../PGS900001"})).status_code == 422

            reader = admin.model_copy(update={"role": "viewer", "metadata_project_ids": [], "reader_project_ids": []})
            app.dependency_overrides[get_current_user] = lambda: reader
            assert (await ac.get("/api/admin/pgs/catalog-imports")).status_code == 403
            assert (await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": "PGS900001"})).status_code == 403
            app.dependency_overrides.clear()

            # Lost administrator rights and cancellation both roll back unpublished versions.
            queued = (await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": "PGS900005"})).json()
            with monkeypatch.context() as patch:
                async def revoked(*args):
                    return reader
                patch.setattr(catalog, "get_current_user_by_id", revoked)
                await catalog.process_next_import()
            jobs = (await ac.get("/api/admin/pgs/catalog-imports")).json()["jobs"]
            assert next(j for j in jobs if j["id"] == queued["id"])["status"] == "failed"
            file_state["weights"] = WEIGHTS
            queued = (await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": "PGS900007"})).json()
            with monkeypatch.context() as patch:
                checks = iter([admin, reader])
                async def revoked_after_download(*args):
                    return next(checks)
                patch.setattr(catalog, "get_current_user_by_id", revoked_after_download)
                await catalog.process_next_import()
            jobs = (await ac.get("/api/admin/pgs/catalog-imports")).json()["jobs"]
            assert next(j for j in jobs if j["id"] == queued["id"])["status"] == "failed"
            queued = (await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": "PGS900006"})).json()
            with monkeypatch.context() as patch:
                async def cancelled(*args):
                    raise asyncio.CancelledError
                patch.setattr(catalog, "fetch_catalog", cancelled)
                with pytest.raises(asyncio.CancelledError):
                    await catalog.process_next_import()
            jobs = (await ac.get("/api/admin/pgs/catalog-imports")).json()["jobs"]
            assert next(j for j in jobs if j["id"] == queued["id"])["status"] == "interrupted"
            async with sm() as session:
                assert (await session.execute(text("SELECT count(*) FROM pgs_runs"))).scalar_one() == before_runs
                assert (await session.execute(text("SELECT count(*) FROM pgs_model_versions WHERE pgs_id IN ('PGS900004','PGS900005','PGS900006','PGS900007')"))).scalar_one() == 0
                kept = (await session.execute(text("SELECT catalog_metadata FROM pgs_model_versions WHERE id = CAST(:id AS uuid)"), {"id": first["id"]})).scalar_one()
                assert kept["record"]["name"] == "Synthetic catalog model"
            orphan = (await ac.post("/api/admin/pgs/catalog-imports", json={"pgs_id": "PGS900008"})).json()["id"]
            async with sm() as session:
                await session.execute(text("UPDATE pgs_catalog_imports SET status = 'running', started_at = now() - interval '11 minutes' WHERE id = CAST(:id AS uuid)"), {"id": orphan})
                await session.commit()
            async with sm() as held:
                await held.execute(text("SELECT id FROM pgs_catalog_imports WHERE id = CAST(:id AS uuid) FOR UPDATE"), {"id": orphan})
                assert await catalog.process_next_import() is False
            assert await catalog.process_next_import() is False
            jobs = (await ac.get("/api/admin/pgs/catalog-imports")).json()["jobs"]
            assert next(j for j in jobs if j["id"] == orphan)["status"] == "interrupted"
    finally:
        app.dependency_overrides.clear()
        await close_postgres_engine()


def test_catalog_import_id_metadata_readiness_versions_and_rights(tmp_path, monkeypatch):
    asyncio.run(exercise(tmp_path, monkeypatch))
