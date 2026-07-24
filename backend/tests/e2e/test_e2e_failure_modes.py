"""M4-B end-to-end: failure / degradation handling + job lifecycle (real stack).

Asserts the import behaves correctly under bad input and that the queue -> run ->
terminal-status worker path works (no existing test covers it). This suite
encodes the M4-A fail-clean hardening: an import that fails leaves no partial
family behind, and the job ends ``failed`` rather than a silent ``completed``.

Scenarios (each on a fresh family id so the shared dev DB stays clean):
  * manifest references a missing file -> validation fails, nothing created;
  * a malformed VCF body (passes validation, fails on import) -> failed + the
    freshly-created family shell is rolled back (the hardening);
  * a PED whose family id mismatches the manifest -> validation fails;
  * idempotent re-import (overwrite) -> no row duplication;
  * job lifecycle: queue -> run -> terminal status (completed for a good package,
    failed for a malformed one, with no partial family).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_MANIFEST = (
    "schema_version: 1\n"
    "family_id: {family_id}\n"
    "ped: family.ped\n"
    "datasets:\n"
    "  snv:\n"
    "    family_vcf: {snv_path}\n"
)
# {sample} is substituted per package — sample ids are globally unique, so reusing a
# literal "S1" across coexisting families would 409.
_GOOD_VCF = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
    "1\t100\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n"
)
# Passes validation (file exists, uncompressed .vcf needs no index; #CHROM lists the
# family sample) but raises on int(POS) during import -> dataset fails.
_MALFORMED_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
    "1\tNOT_A_NUMBER\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n"
)
# Two variants — used to prove an atomic restore: after a failed overwrite the family's
# small-variant count must return to the pre-import value (1), not the overwrite's (2).
_TWO_VARIANT_VCF = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
    "1\t100\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n"
    "1\t200\t.\tC\tT\t60\tPASS\t.\tGT\t0/1\n"
)


def _write_pkg(
    root: Path,
    *,
    family_id: str,
    ped_family_id: str | None = None,
    snv_body: str | None = _GOOD_VCF,
    snv_path: str = "snv/family.vcf",
) -> Path:
    """Write a minimal single-sample package. ``snv_body=None`` leaves the
    referenced VCF absent (missing-file case). ``ped_family_id`` differing from
    ``family_id`` triggers ped_family_mismatch."""
    root.mkdir(parents=True, exist_ok=True)
    sample = f"{family_id}_S1"  # globally-unique sample id
    (root / "family.ped").write_text(
        f"{ped_family_id or family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8"
    )
    (root / "manifest.yaml").write_text(
        _MANIFEST.format(family_id=family_id, snv_path=snv_path), encoding="utf-8"
    )
    if snv_body is not None:
        snv_file = root / snv_path
        snv_file.parent.mkdir(parents=True, exist_ok=True)
        snv_file.write_text(snv_body.format(sample=sample), encoding="utf-8")
    return root


async def _collect(base: Path) -> dict:
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)

    sm = get_postgres_sessionmaker()
    async with sm() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    async def family_uuid(fid: str) -> str | None:
        async with sm() as s:
            return (
                await s.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": fid})
            ).scalar_one_or_none()

    async def import_incomplete_flag(fid: str):
        async with sm() as s:
            return (
                await s.execute(
                    text("SELECT metadata -> 'import_incomplete' FROM families WHERE family_id = :f"),
                    {"f": fid},
                )
            ).scalar_one_or_none()

    async def run_import(root: Path, *, conflict_mode: str = "cancel"):
        async with sm() as s:
            return await package_import.execute_family_package_import(
                s,
                folder_path=str(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=conflict_mode,
            )

    out: dict = {}

    # --- missing file: validation fails, nothing created ---
    fid = f"FAM_MISS_{uuid4().hex[:8]}"
    res = await run_import(_write_pkg(base / fid, family_id=fid, snv_path="snv/does_not_exist.vcf", snv_body=None))
    out["missing_file"] = {
        "completed": res.completed,
        "error": res.error,
        "codes": [e.code for e in res.validation.errors],
        "family_exists": (await family_uuid(fid)) is not None,
    }

    # --- malformed VCF body: passes validation, fails on import -> fail-clean ---
    fid = f"FAM_BADVCF_{uuid4().hex[:8]}"
    res = await run_import(_write_pkg(base / fid, family_id=fid, snv_body=_MALFORMED_VCF))
    fu = await family_uuid(fid)
    out["malformed_vcf"] = {
        "completed": res.completed,
        "error": res.error,
        "statuses": {d.dataset_type: d.status for d in res.datasets},
        "family_exists": fu is not None,
    }

    # --- bad PED (family id mismatch): validation fails ---
    fid = f"FAM_BADPED_{uuid4().hex[:8]}"
    res = await run_import(_write_pkg(base / fid, family_id=fid, ped_family_id="SOME_OTHER_FAMILY"))
    out["bad_ped"] = {
        "completed": res.completed,
        "codes": [e.code for e in res.validation.errors],
        "family_exists": (await family_uuid(fid)) is not None,
    }

    # --- idempotent re-import (overwrite): no duplication ---
    fid = f"FAM_IDEM_{uuid4().hex[:8]}"
    root = _write_pkg(base / fid, family_id=fid, snv_body=_GOOD_VCF)
    res1 = await run_import(root, conflict_mode="cancel")
    fu = await family_uuid(fid)
    count1 = await count_family_small_variants(_harness.ASSEMBLY, fu, project_ids=[project_id]) if fu else 0
    res2 = await run_import(root, conflict_mode="overwrite")
    count2 = await count_family_small_variants(_harness.ASSEMBLY, fu, project_ids=[project_id]) if fu else 0
    out["idempotent"] = {
        "completed1": res1.completed,
        "completed2": res2.completed,
        "count1": count1,
        "count2": count2,
    }

    # --- failed OVERWRITE of a PRE-EXISTING family: atomic ClickHouse restore (#365) ---
    # Import 1 variant cleanly, then overwrite with a 2-variant package whose dataset
    # import raises *after* it has already replaced the family's small variants. Without
    # the snapshot/restore the family would be left with the overwrite's 2 variants + an
    # import_incomplete flag; with it the family is rolled back to its pre-import 1.
    fid = f"FAM_RESTORE_{uuid4().hex[:8]}"
    root_v1 = _write_pkg(base / fid, family_id=fid, snv_body=_GOOD_VCF)
    await run_import(root_v1, conflict_mode="cancel")
    fu = await family_uuid(fid)
    count_before = await count_family_small_variants(_harness.ASSEMBLY, fu, project_ids=[project_id]) if fu else 0
    root_v2 = _write_pkg(base / f"{fid}_v2", family_id=fid, snv_body=_TWO_VARIANT_VCF)

    real_import_dataset = package_import._import_dataset

    async def _import_then_fail(*args, **kwargs):
        # Let the dataset actually import (overwriting the family's ClickHouse rows),
        # then raise to simulate a mid-import failure of a later step.
        await real_import_dataset(*args, **kwargs)
        raise RuntimeError("injected failure after dataset import")

    package_import._import_dataset = _import_then_fail
    try:
        res_restore = await run_import(root_v2, conflict_mode="overwrite")
    finally:
        package_import._import_dataset = real_import_dataset
    count_after = await count_family_small_variants(_harness.ASSEMBLY, fu, project_ids=[project_id]) if fu else 0
    out["restore"] = {
        "completed": res_restore.completed,
        "error": res_restore.error,
        "count_before": count_before,
        "count_after": count_after,
        "flag": await import_incomplete_flag(fid),
    }

    # --- restore itself fails -> fall back to the import_incomplete flag ---
    fid = f"FAM_RESTOREFB_{uuid4().hex[:8]}"
    root_v1 = _write_pkg(base / fid, family_id=fid, snv_body=_GOOD_VCF)
    await run_import(root_v1, conflict_mode="cancel")
    fu = await family_uuid(fid)
    root_v2 = _write_pkg(base / f"{fid}_v2", family_id=fid, snv_body=_TWO_VARIANT_VCF)

    real_restore = package_import.restore_family_clickhouse_state

    async def _failing_restore(*args, **kwargs):
        raise RuntimeError("injected restore failure")

    package_import._import_dataset = _import_then_fail
    package_import.restore_family_clickhouse_state = _failing_restore
    try:
        res_fallback = await run_import(root_v2, conflict_mode="overwrite")
    finally:
        package_import._import_dataset = real_import_dataset
        package_import.restore_family_clickhouse_state = real_restore
    out["restore_fallback"] = {
        "completed": res_fallback.completed,
        "flag": await import_incomplete_flag(fid),
    }

    # --- Postgres snapshot/restore of the loop-modified tables (#365 postgres) ---
    # Create a real family, seed a loop-modified table + a samples.metadata marker, snapshot,
    # then mutate both (delete/insert a different row; change the marker) and restore — the
    # mutation must be gone and the original state (row + marker) back, proving the
    # DELETE + jsonb_populate_recordset reinsert and the samples.metadata UPDATE round-trip.
    from backend.app.services.postgres_family_snapshot import (
        restore_family_postgres_state,
        snapshot_family_postgres_state,
    )

    fid = f"FAM_PGSNAP_{uuid4().hex[:8]}"
    await run_import(_write_pkg(base / fid, family_id=fid, snv_body=_GOOD_VCF), conflict_mode="cancel")
    fu = await family_uuid(fid)
    async with sm() as s:
        sid = (
            await s.execute(
                text("SELECT id::text FROM samples WHERE family_id = CAST(:f AS uuid) LIMIT 1"), {"f": fu}
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO sample_interval_track_sources "
                "(sample_id, family_id, track_type, source, filename, row_count) "
                "VALUES (CAST(:sid AS uuid), CAST(:fid AS uuid), 'coverage', 'orig', 'orig.bed', 7)"
            ),
            {"sid": sid, "fid": fu},
        )
        await s.execute(
            text(
                "UPDATE samples SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), "
                "'{pg_snap_marker}', '\"original\"'::jsonb) WHERE id = CAST(:sid AS uuid)"
            ),
            {"sid": sid},
        )
        await s.commit()

    async with sm() as s:
        pg_snap = await snapshot_family_postgres_state(s, fu)

    async with sm() as s:  # mutate as a failed overwrite would
        await s.execute(
            text("DELETE FROM sample_interval_track_sources WHERE family_id = CAST(:fid AS uuid)"), {"fid": fu}
        )
        await s.execute(
            text(
                "INSERT INTO sample_interval_track_sources "
                "(sample_id, family_id, track_type, source, filename, row_count) "
                "VALUES (CAST(:sid AS uuid), CAST(:fid AS uuid), 'segments', 'mutated', 'new.bed', 99)"
            ),
            {"sid": sid, "fid": fu},
        )
        await s.execute(
            text(
                "UPDATE samples SET metadata = jsonb_set(metadata, '{pg_snap_marker}', '\"mutated\"'::jsonb) "
                "WHERE id = CAST(:sid AS uuid)"
            ),
            {"sid": sid},
        )
        await s.commit()

    async with sm() as s:
        await restore_family_postgres_state(s, pg_snap)

    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT source, filename, row_count FROM sample_interval_track_sources "
                    "WHERE family_id = CAST(:fid AS uuid) ORDER BY source"
                ),
                {"fid": fu},
            )
        ).all()
        marker = (
            await s.execute(
                text("SELECT metadata ->> 'pg_snap_marker' FROM samples WHERE id = CAST(:sid AS uuid)"),
                {"sid": sid},
            )
        ).scalar_one()
    out["pg_restore"] = {"sources": [list(r) for r in rows], "marker": marker}

    # --- job lifecycle: queue -> claim -> run -> terminal status (mirrors the worker) ---
    async def lifecycle(root: Path) -> dict:
        async with sm() as s:
            job = await package_import.queue_family_import_job(
                s,
                folder_path=str(root),
                project_id=project_id,
                dry_run=False,
                requested_by=admin.email,
                conflict_mode="cancel",
            )
            await s.commit()
            job_id = job.id
        # claim_next picks the oldest queued/stale job and sets worker_id (run's
        # fencing requires it). Drain (running each) until our job is processed; any
        # stale jobs from prior runs point at gone paths and fail fast, harmlessly.
        worker_id = f"e2e-{uuid4().hex[:8]}"
        for _ in range(50):
            async with sm() as s:
                claimed = await package_import.claim_next_family_import_job(s, worker_id=worker_id)
            if claimed is None:
                break
            await package_import.run_family_import_job(job_id=claimed["id"], worker_id=worker_id)
            if claimed["id"] == job_id:
                break
        async with sm() as s:
            row = await package_import.get_family_import_job(s, job_id=job_id, user=admin)
        return {"status": row.status, "error": row.error, "completed_at": row.completed_at is not None}

    fid = f"FAM_JOB_OK_{uuid4().hex[:8]}"
    out["lifecycle_ok"] = await lifecycle(_write_pkg(base / fid, family_id=fid, snv_body=_GOOD_VCF))

    fid = f"FAM_JOB_FAIL_{uuid4().hex[:8]}"
    out["lifecycle_fail"] = await lifecycle(_write_pkg(base / fid, family_id=fid, snv_body=_MALFORMED_VCF))
    out["lifecycle_fail"]["family_exists"] = (await family_uuid(fid)) is not None

    return out


@pytest.fixture(scope="module")
def results(tmp_path_factory, request) -> dict:
    from backend.app.services import family_package_import as package_import
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_failure")

    mp = pytest.MonkeyPatch()
    mp.setattr(package_import.settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)

    return _harness.run_async(lambda: _collect(base))


# --------------------------------------------------------------------------- validation


def test_missing_file_fails_validation_with_no_partial_state(results):
    r = results["missing_file"]
    assert r["completed"] is False, r
    assert r["error"] == "Package validation failed", r
    assert "dataset_file_missing" in r["codes"], r
    assert r["family_exists"] is False, r


def test_bad_ped_fails_validation(results):
    r = results["bad_ped"]
    assert r["completed"] is False, r
    assert "ped_family_mismatch" in r["codes"], r
    assert r["family_exists"] is False, r


# ---------------------------------------------------------------- malformed VCF (hardening)


def test_malformed_vcf_fails_clean(results):
    r = results["malformed_vcf"]
    # Fail-clean hardening: a dataset that fails on import now fails the whole job...
    assert r["completed"] is False, r
    assert r["error"] and "snv" in r["error"], r
    assert r["statuses"].get("snv") == "failed", r
    # ...and the freshly-created family shell is rolled back (no partial ingestion).
    assert r["family_exists"] is False, r


# --------------------------------------------------------------------------- idempotency


def test_reimport_overwrite_does_not_duplicate(results):
    r = results["idempotent"]
    assert r["completed1"] is True and r["completed2"] is True, r
    assert r["count1"] == 1, r
    assert r["count2"] == r["count1"], r


# ----------------------------------------------------------- import atomicity (#365)


def test_failed_overwrite_restores_pre_import_state(results):
    r = results["restore"]
    # The overwrite failed, so the job still reports failure...
    assert r["completed"] is False, r
    assert r["error"], r
    # ...but the family's variant data was atomically rolled back to the pre-import
    # value (1), NOT left at the overwrite's 2, and no incompleteness flag remains.
    assert r["count_before"] == 1, r
    assert r["count_after"] == r["count_before"], r
    assert r["flag"] is None, r


def test_restore_failure_falls_back_to_incomplete_flag(results):
    r = results["restore_fallback"]
    # When the snapshot restore itself fails, the family is flagged import-incomplete
    # (the conservative fallback) rather than silently left in a half-overwritten state.
    assert r["completed"] is False, r
    assert r["flag"] is not None, r


def test_postgres_snapshot_restore_reverts_loop_tables(results):
    r = results["pg_restore"]
    # The mutated 'segments/mutated' row is gone and the original 'coverage/orig' row is
    # back verbatim (row_count 7), and samples.metadata reverted from 'mutated' to 'original'.
    assert r["sources"] == [["orig", "orig.bed", 7]], r
    assert r["marker"] == "original", r


# ------------------------------------------------------------------------- job lifecycle


def test_job_lifecycle_good_package_completes(results):
    r = results["lifecycle_ok"]
    assert r["status"] == "completed", r
    assert r["completed_at"] is True, r


def test_job_lifecycle_malformed_package_fails_clean(results):
    r = results["lifecycle_fail"]
    assert r["status"] == "failed", r
    assert r["error"], r
    assert r["completed_at"] is True, r
    assert r["family_exists"] is False, r
