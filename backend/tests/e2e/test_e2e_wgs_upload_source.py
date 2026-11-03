"""Retained synthetic uploads become verified WGS sources without a genotype rewrite."""
from __future__ import annotations

import asyncio
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
    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.services import ancestry_service as ancestry, wgs_source_storage as wgs, raw_import_files_pg
    from backend.app.services import wgs_source_service
    from backend.app.services.ancestry_projection import AncestryReference
    from backend.app.services.clickhouse_variant_storage import (
        _execute, _small_table_name, bump_family_small_variant_data_version, get_family_small_variant_data_version,
    )
    from backend.app.services.ancestry_projection import AncestryMarker
    from backend.tests.e2e import _harness

    monkeypatch.setattr(raw_import_files_pg, "DATA_DIR", root)
    await init_postgres_schema()
    await init_clickhouse_schema()
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        admin, project, _ = await _harness.ensure_e2e_project(session)
    family = f"WGS_SYNTH_{uuid4().hex[:8]}"
    sample = f"PROBAND_{family}"
    vcf = ("##fileformat=VCFv4.2\n##reference=GRCh38\n"
           '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
           f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n" +
           "".join(f"chr1\t{i + 1}\t.\tA\tG\t50\t.\t.\tGT\t{['0/0', '0/1', '1/1'][i % 3]}\n" for i in range(1000)))
    params = {"project_id": project}
    url = f"/api/families/{family}"
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        response = await ac.post("/api/ped/upload", params=params,
            files={"file": ("family.ped", f"{family}\t{sample}\t0\t0\t1\t1\n".encode())})
        assert response.status_code == 200, response.text
        upload = await ac.post(url + "/small-variants/upload", params={"source_format": "clair3"},
            files={"file": ("synthetic.vcf", vcf.encode())})
        assert upload.status_code == 200, upload.text
        assert (await ac.get(url + "/ancestry", params=params)).json()["source_state"] == "no_full_wgs_source"
        files = await ac.get(url + "/wgs-source", params=params)
        assert files.status_code == 200 and files.json()["can_register"], files.text
        file_id = files.json()["candidates"][0]["file_id"]
        declaration = {"file_id": file_id, "full_wgs": True, "genome_build": "GRCh38",
                       "absent_record_is_hom_ref": True, "allow_unfiltered": True}
        async with sm() as session:
            family_uuid = (await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family})).scalar_one()
            retained = (await session.execute(text("SELECT storage_path FROM raw_import_files WHERE id = CAST(:id AS uuid)"), {"id": file_id})).scalar_one()
        token = await get_family_small_variant_data_version("GRCh38", family_uuid)
        fingerprint = await wgs.genotype_fingerprint(family_uuid, project)
        # Typed, family-bound UUID lookup; no arbitrary filesystem path is accepted.
        for bad in ("not-a-uuid", str(uuid4())):
            assert (await ac.post(url + "/wgs-source", params=params, json={**declaration, "file_id": bad})).status_code == 404
        assert (await ac.post(url + "/wgs-source", params=params, json={"file_id": file_id})).status_code == 422
        reader = admin.model_copy(update={"role": "user", "metadata_project_ids": [project], "reader_project_ids": [project]})
        outsider = admin.model_copy(update={"role": "user", "metadata_project_ids": [], "reader_project_ids": []})
        app.dependency_overrides[get_current_user] = lambda: reader
        assert not (await ac.get(url + "/wgs-source", params=params)).json()["can_register"]
        assert (await ac.post(url + "/wgs-source", params=params, json=declaration)).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: outsider
        assert (await ac.get(url + "/wgs-source", params=params)).status_code == 404
        assert (await ac.post(url + "/wgs-source", params=params, json=declaration)).status_code == 404
        app.dependency_overrides.pop(get_current_user)
        # The whole file is read with no lock of the family's variant writes held: an import or
        # a sign-out of the family never waits for a registration.
        loop = asyncio.get_running_loop()
        during_read: list[bool] = []
        verify = wgs_source_service.verify_retained_vcf

        def verify_and_probe(*args):
            during_read.append(asyncio.run_coroutine_threadsafe(writer_could_lock(sm, family_uuid), loop).result())
            return verify(*args)

        monkeypatch.setattr(wgs_source_service, "verify_retained_vcf", verify_and_probe)
        registered = await ac.post(url + "/wgs-source", params=params, json=declaration)
        assert during_read == [True]
        assert registered.status_code == 200, registered.text
        data = registered.json()
        assert (await ac.get(url + "/ancestry", params=params)).json()["source_state"] == "ready"
        assert data["raw_file_id"] == file_id
        assert data["qualification"]["allow_unfiltered"]
        assert data["qualification"]["assessment"]["filter_counts"] == {".": 1000}
        assert await get_family_small_variant_data_version("GRCh38", family_uuid) == token
        assert await wgs.genotype_fingerprint(family_uuid, project) == fingerprint
        repeated = await ac.post(url + "/wgs-source", params=params, json=declaration)
        assert repeated.status_code == 200 and repeated.json()["id"] == data["id"]
        await bump_family_small_variant_data_version("GRCh38", family_uuid)
        assert (await ac.get(url + "/ancestry", params=params)).json()["source_state"] == "genotype_storage_changed"
        refreshed = await ac.post(url + "/wgs-source", params=params, json=declaration)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["id"] != data["id"]
        public = AncestryReferenceOut(sha256="a" * 64, name="Synthetic reference", source_url="https://example.org/synthetic",
            source_revision="synthetic-v1", method="Synthetic orthogonal PCA", variant_count=1001, sample_count=5,
            explained_variance=[0.4, 0.3, 0.2, 0.1], populations={"SYN": "Synthetic"},
            points=[{"id": f"SYN-{i}", "population": "SYN", "pcs": [float(i)] * 4} for i in range(5)])
        loadings = np.zeros((1001, 4))
        loadings[:4, :] = np.eye(4)
        reference = AncestryReference(public, [AncestryMarker("1", i + 1, "A", "G") for i in range(1001)],
                                     np.ones(1001), np.ones(1001), loadings)
        monkeypatch.setattr(ancestry, "configured_reference", lambda: reference)
        assert (await ac.post(url + "/ancestry/calculate", params=params)).status_code == 202
        assert await ancestry.process_next_run()
        projected = (await ac.get(url + "/ancestry", params=params)).json()["analysis"]
        assert projected["samples"][0]["pcs"] == [-1, 0, 1, -1]
        assert projected["samples"][0]["qc"]["called_unfiltered"] == 1000
        assert projected["samples"][0]["qc"]["assumed_ref"] == 1
        assert projected["assumptions"]["allow_unfiltered"] and projected["assumptions"]["absent_record_is_hom_ref"]
        assert projected["calculation_version"].endswith("+unfiltered-v1")
        # Same bytes with a different filter cannot quietly replace a qualified source.
        path = Path(retained)
        original = path.read_bytes()
        path.write_bytes(original.replace(b"\t.\t.\tGT\t", b"\tPASS\t.\tGT\t", 1))
        assert (await ac.post(url + "/wgs-source", params=params, json=declaration)).status_code == 409
        assert (await ac.get(url + "/ancestry", params=params)).json()["source_state"] == "source_changed"
        path.write_bytes(original)
        # An upload again (overwrite) rewrites the genotypes: the source is no longer current
        # until it is registered again, now from the new retained file. The clinical upload
        # itself registers nothing.
        again = await ac.post(url + "/small-variants/upload", params={"source_format": "clair3", "overwrite": True},
            files={"file": ("synthetic.vcf", vcf.encode())})
        assert again.status_code == 200, again.text
        assert "wgs_source_registered" not in again.json()
        assert (await ac.get(url + "/ancestry", params=params)).json()["source_state"] == "genotype_storage_changed"
        newest = (await ac.get(url + "/wgs-source", params=params)).json()["candidates"][0]["file_id"]
        reregistered = await ac.post(url + "/wgs-source", params=params, json={**declaration, "file_id": newest})
        assert reregistered.status_code == 200, reregistered.text
        assert (await ac.get(url + "/ancestry", params=params)).json()["source_state"] == "ready"
        # Direct row loss, even without a version bump, invalidates the source where it counts:
        # a calculation is refused when it is queued (reading the page compares the version
        # token only; the full genotype fingerprint runs at queueing and before publishing),
        # and the source cannot be registered again.
        table = _small_table_name("GRCh38", "entries")
        await _execute(f"ALTER TABLE {table} DELETE WHERE family_guid = %(f)s AND pos = 1 SETTINGS mutations_sync = 1", {"f": family_uuid})
        queued = await ac.post(url + "/ancestry/calculate", params=params)
        assert queued.status_code == 409, queued.text
        refused = await ac.post(url + "/wgs-source", params=params, json={**declaration, "file_id": newest})
        assert refused.status_code == 409 and "do not match" in refused.text, refused.text


def test_retained_upload_wgs_registration_and_policy_results(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.tests.e2e import _harness
    try:
        _harness.run_async(lambda: exercise(tmp_path, monkeypatch))
    finally:
        app.dependency_overrides.clear()
