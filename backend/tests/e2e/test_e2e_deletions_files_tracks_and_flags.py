"""A deletion keeps every file its records name until it has committed, a family replaced by a
PED upload loses its interval tracks, and a sample's deletion strikes the sample off the import
flags (SAFE-35), E2E.

Into real Postgres and ClickHouse, over the real API (an in-process ``httpx.ASGITransport``
client, one event loop, see test_e2e_api_contract.py), with the seeded admin signed in:

* a family of two members made through the Family Builder, with the kept copy of an upload of
  the family and of one of the second member's (``raw_import_files`` rows, ``managed``, as an
  upload writes them, the files in the upload folder). A deferred trigger refuses the commit of
  the member's deletion, then of the family's: each answers 500, and its rollback keeps every
  record. The files they name are still there; before, they were unlinked before the commit,
  and the records named files that were gone. Without the trigger each deletion completes, and
  unlinks its files once it has committed, counting them;
* a trio uploaded through the PED route, with the child's coverage BED, then replaced by the
  same PED uploaded with overwrite: the replaced family's interval rows go from ClickHouse
  (before: they stayed, under a family that was gone), and Postgres's record of the track goes
  with the family;
* a trio whose ``import_incomplete`` flag names the child's TRGT calls, and the child's and the
  father's, as a failed import leaves it, and whose ``import_unfinished`` holds the entry of an
  import that stopped while writing the child's coverage: deleting the child strikes it off both,
  the entry going. The father's TRGT calls uploaded again then clear the flag, and the report
  signs out past the import gate. Before, the child stayed named, so no upload or import could
  clear either, and sign-out stayed refused.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The IDs and
files are synthetic, and the families are dropped at the end.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ACK = "e2e: the import gate is under test"
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}

# The commit of a transaction that deleted a sample or a family whose ID holds this fails: a
# deferred trigger raises at COMMIT, after every statement of the deletion has run.
_REFUSED = "COMMITREFUSED"
_REFUSE_COMMIT_FUNCTION = """
CREATE FUNCTION e2e_refuse_deletion_commit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF strpos(to_jsonb(OLD) ->> TG_ARGV[0], 'COMMITREFUSED') > 0 THEN
        RAISE EXCEPTION 'e2e: the commit of the deletion is refused';
    END IF;
    RETURN NULL;
END
$$
"""
_REFUSE_COMMIT_TRIGGERS = (
    "CREATE CONSTRAINT TRIGGER e2e_refuse_deletion_commit AFTER DELETE ON samples "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION e2e_refuse_deletion_commit('sample_id')",
    "CREATE CONSTRAINT TRIGGER e2e_refuse_deletion_commit AFTER DELETE ON families "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION e2e_refuse_deletion_commit('family_id')",
)

_COVERAGE_BED = b"1\t0\t1000\tbin1\t31.0\n1\t1000\t2000\tbin2\t29.5\n"


def _trgt_vcf(column: str) -> str:
    """A TRGT VCF of one sample with one HTT record."""
    return (
        "##fileformat=VCFv4.2\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
        "chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG,CAA\tGT:MC\t0/0:18_0\n"
    )


def _ped(family_id: str, names: dict[str, str]) -> bytes:
    return (
        f"{family_id}\t{names['father']}\t0\t0\t1\t1\n"
        f"{family_id}\t{names['mother']}\t0\t0\t2\t1\n"
        f"{family_id}\t{names['child']}\t{names['father']}\t{names['mother']}\t2\t2\n"
    ).encode()


def _place(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def _refuse_commits(refused: bool) -> None:
    """Put the trigger that refuses a deletion's commit in place, or take it away."""
    from backend.app.core.postgres import get_postgres_engine

    async with get_postgres_engine().begin() as conn:
        await conn.exec_driver_sql("DROP TRIGGER IF EXISTS e2e_refuse_deletion_commit ON samples")
        await conn.exec_driver_sql("DROP TRIGGER IF EXISTS e2e_refuse_deletion_commit ON families")
        await conn.exec_driver_sql("DROP FUNCTION IF EXISTS e2e_refuse_deletion_commit()")
        if refused:
            await conn.exec_driver_sql(_REFUSE_COMMIT_FUNCTION)
            for trigger in _REFUSE_COMMIT_TRIGGERS:
                await conn.exec_driver_sql(trigger)


async def _setup() -> tuple[Any, str]:
    """The schema, the assembly's ClickHouse tables and the e2e project: (sessionmaker,
    project id)."""
    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
    return sessionmaker, project_id


async def _drop_families(sessionmaker: Any, family_ids: list[str]) -> None:
    from sqlalchemy import text

    async with sessionmaker() as session:
        await session.execute(text("DELETE FROM families WHERE family_id = ANY(:f)"), {"f": family_ids})
        await session.commit()


# --- the kept copies of uploads go once the deletion has committed --------------------------


async def _files_after_commit(run: str, data: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.main import app
    from backend.tests.e2e import _harness

    sessionmaker, project_id = await _setup()
    family, s1, s2 = (f"{_REFUSED}_{run}_{suffix}" for suffix in ("F", "S1", "S2"))
    files = {
        "family": _place(data / "raw_imports" / family / "0a1b2c__family.vcf", b"family upload"),
        "s2": _place(data / "raw_imports" / family / "3d4e5f__s2.vcf", b"S2 upload"),
    }
    out: dict[str, Any] = {"steps": {}}

    async def state(name: str, response: Any) -> None:
        async with sessionmaker() as session:
            records = sorted(
                str(row)
                for row in (
                    await session.execute(
                        text(
                            "SELECT r.file_name FROM raw_import_files r JOIN families f ON f.id = r.family_id "
                            "WHERE f.family_id = :f"
                        ),
                        {"f": family},
                    )
                ).scalars()
            )
            members = sorted(
                str(row)
                for row in (
                    await session.execute(
                        text(
                            "SELECT s.sample_id FROM samples s JOIN families f ON f.id = s.family_id "
                            "WHERE f.family_id = :f"
                        ),
                        {"f": family},
                    )
                ).scalars()
            )
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        out["steps"][name] = {
            "status": response.status_code,
            "deleted": body.get("deleted") if isinstance(body, dict) else None,
            "records": records,
            "members": members,
            "files": {key: path.exists() for key, path in files.items()},
        }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"
            created = await admin.post(
                "/api/ped/manual",
                json={
                    "family_id": family,
                    "project_id": project_id,
                    "members": [
                        {"sample_id": s1, "sex": "female", "is_proband": True},
                        {"sample_id": s2, "sex": "male", "is_proband": False},
                    ],
                },
            )
            out["created"] = created.status_code
            async with sessionmaker() as session:
                family_uuid = (
                    await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family})
                ).scalar_one()
                s2_uuid = (
                    await session.execute(text("SELECT id::text FROM samples WHERE sample_id = :s"), {"s": s2})
                ).scalar_one()
                # As an upload records its kept copy (record_uploaded_file).
                for name, sample_uuid in (("family", None), ("s2", s2_uuid)):
                    content = files[name].read_bytes()
                    await session.execute(
                        text(
                            """
                            INSERT INTO raw_import_files (
                                family_id, sample_id, scope, dataset, file_name, file_type,
                                storage_path, managed, file_size, sha256, source, metadata
                            )
                            VALUES (
                                CAST(:family AS uuid), CAST(:sample AS uuid), :scope, 'small_variants',
                                :file_name, 'VCF', :storage_path, TRUE, :size, :sha256, 'web', '{}'::jsonb
                            )
                            """
                        ),
                        {
                            "family": family_uuid,
                            "sample": sample_uuid,
                            "scope": "individual" if sample_uuid else "family",
                            "file_name": f"{name}.vcf",
                            "storage_path": str(files[name]),
                            "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        },
                    )
                await session.commit()

            await _refuse_commits(True)
            await state("sample_refused", await admin.delete(f"/api/admin/samples/{s2}", params={"confirm": "true"}))
            await _refuse_commits(False)
            await state("sample_deleted", await admin.delete(f"/api/admin/samples/{s2}", params={"confirm": "true"}))

            await _refuse_commits(True)
            await state(
                "family_refused",
                await admin.request(
                    "DELETE", f"/api/admin/families/{family}", params={"confirm": "true"}, json={"reason": "e2e"}
                ),
            )
            await _refuse_commits(False)
            await state(
                "family_deleted",
                await admin.request(
                    "DELETE", f"/api/admin/families/{family}", params={"confirm": "true"}, json={"reason": "e2e"}
                ),
            )
    finally:
        await _refuse_commits(False)
        await _drop_families(sessionmaker, [family])
    return out


@pytest.fixture(scope="module")
def files_after_commit(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    data = tmp_path_factory.mktemp("files_after_commit").resolve()
    mp = pytest.MonkeyPatch()
    mp.setattr(raw_import_files_pg, "DATA_DIR", data)
    try:
        return _harness.run_async(lambda: _files_after_commit(uuid4().hex[:8].upper(), data))
    finally:
        mp.undo()


def test_the_family_is_made_with_its_members(files_after_commit) -> None:
    assert files_after_commit["created"] == 200


def test_a_sample_deletion_whose_commit_fails_keeps_the_files_its_records_name(files_after_commit) -> None:
    step = files_after_commit["steps"]["sample_refused"]
    assert step["status"] == 500
    # Rolled back: the member and both records are there, and so are both files (before: the
    # member's copy was unlinked, its record naming a file that was gone).
    assert step["records"] == ["family.vcf", "s2.vcf"] and len(step["members"]) == 2
    assert step["files"] == {"family": True, "s2": True}


def test_a_sample_deletion_unlinks_its_file_once_it_has_committed(files_after_commit) -> None:
    step = files_after_commit["steps"]["sample_deleted"]
    assert (step["status"], step["deleted"]["raw_import_files"]) == (200, 1)
    assert step["records"] == ["family.vcf"] and len(step["members"]) == 1
    assert step["files"] == {"family": True, "s2": False}


def test_a_family_deletion_whose_commit_fails_keeps_the_files_its_records_name(files_after_commit) -> None:
    step = files_after_commit["steps"]["family_refused"]
    assert step["status"] == 500
    # Before: the family's copy was unlinked.
    assert step["records"] == ["family.vcf"] and step["files"]["family"] is True


def test_a_family_deletion_unlinks_its_file_once_it_has_committed(files_after_commit) -> None:
    step = files_after_commit["steps"]["family_deleted"]
    assert (step["status"], step["deleted"]["raw_files"]) == (200, 1)
    assert step["records"] == [] and step["members"] == []
    assert step["files"] == {"family": False, "s2": False}


# --- a replaced family's interval tracks go with it ------------------------------------------


async def _replaced_tracks(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.main import app
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_rows
    from backend.tests.e2e import _harness

    sessionmaker, project_id = await _setup()
    family = f"REPLACED_{run}"
    names = {role: f"{role.upper()}_{run}" for role in ("father", "mother", "child")}
    out: dict[str, Any] = {"steps": {}}

    async def state(name: str, response: Any, family_uuid: str | None = None) -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text("SELECT id::text AS uuid, metadata -> 'import_unfinished' AS unfinished FROM families WHERE family_id = :f"),
                    {"f": family},
                )
            ).mappings().one()
            of = family_uuid or row["uuid"]
            sources = (
                await session.execute(
                    text("SELECT count(*) FROM sample_interval_track_sources WHERE family_id = CAST(:u AS uuid)"),
                    {"u": of},
                )
            ).scalar_one()
        step = {
            "status": response.status_code,
            "text": response.text,
            "family_uuid": row["uuid"],
            "unfinished": row["unfinished"],
            "rows": await count_interval_track_rows(
                _harness.ASSEMBLY, family_uuid=of, sample_uuid=None, track_type="coverage"
            ),
            "sources": int(sources),
        }
        out["steps"][name] = step
        return step

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def upload_ped(overwrite: str) -> Any:
                return await ac.post(
                    "/api/ped/upload",
                    params={"project_id": project_id, "overwrite": overwrite},
                    files={"file": (f"{family}.ped", _ped(family, names), "text/plain")},
                )

            await state("ped", await upload_ped("false"))
            stored = await state(
                "bed",
                await ac.post(
                    f"/api/bed/upload/{names['child']}/coverage",
                    params={"overwrite": "false", "content": "read_depth"},
                    files={"file": ("coverage.bed", _COVERAGE_BED, "text/plain")},
                ),
            )
            replaced = await upload_ped("true")
            # What the replaced family left, read by its uuid; then the new family.
            await state("replaced_family", replaced, family_uuid=stored["family_uuid"])
            await state("new_family", replaced)
    finally:
        await _drop_families(sessionmaker, [family])
    return out


@pytest.fixture(scope="module")
def replaced_tracks(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The uploads keep a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("replaced_tracks"))
    try:
        return _harness.run_async(lambda: _replaced_tracks(uuid4().hex[:8].upper()))
    finally:
        mp.undo()


def test_the_trio_and_the_childs_coverage_are_uploaded(replaced_tracks) -> None:
    for name in ("ped", "bed"):
        assert replaced_tracks["steps"][name]["status"] == 200, replaced_tracks["steps"][name]["text"]
    assert (replaced_tracks["steps"]["bed"]["rows"], replaced_tracks["steps"]["bed"]["sources"]) == (2, 1)


def test_a_ped_upload_that_replaces_the_family_erases_its_interval_tracks(replaced_tracks) -> None:
    step = replaced_tracks["steps"]["replaced_family"]
    assert step["status"] == 200, step["text"]
    # The replaced family's coverage rows are gone from ClickHouse (before: 2 stayed, under a
    # family that was gone), and its record from Postgres, with the family.
    assert (step["rows"], step["sources"]) == (0, 0)
    new = replaced_tracks["steps"]["new_family"]
    assert new["family_uuid"] != replaced_tracks["steps"]["bed"]["family_uuid"]
    assert (new["rows"], new["sources"], new["unfinished"]) == (0, 0, None)


# --- a sample's deletion strikes it off the import flags --------------------------------------


async def _flags_cleared(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.main import app
    from backend.app.services.family_package_registration import ImportMark, merged_import_failure
    from backend.tests.e2e import _harness

    sessionmaker, project_id = await _setup()
    family = f"FLAGGED_{run}"
    names = {role: f"{role.upper()}_{run}" for role in ("father", "mother", "child")}
    child, father = names["child"], names["father"]
    # An import that failed for the child's TRGT calls and for the child's and the father's,
    # as two imports leave the flag, and one that stopped while writing the child's coverage.
    flag = merged_import_failure(
        None,
        at="2026-10-27T08:00:00+00:00",
        job_id=None,
        failed=["repeats_trgt"],
        imported=[],
        scopes={"repeats_trgt": {"source": None, "samples": [child]}},
        imported_scopes={},
    )
    flag = merged_import_failure(
        flag,
        at="2026-10-27T09:00:00+00:00",
        job_id=None,
        failed=["repeats_trgt"],
        imported=[],
        scopes={"repeats_trgt": {"source": None, "samples": [child, father]}},
        imported_scopes={},
    )
    stopped = ImportMark.begin(
        job_id=None, datasets=["coverage"], scopes={"coverage": {"source": None, "samples": [child]}}
    )
    out: dict[str, Any] = {"steps": {}, "names": names, "stopped": stopped.key}

    async def state(name: str, response: Any) -> None:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT metadata -> 'import_incomplete' AS flag, metadata -> 'import_unfinished' AS unfinished "
                        "FROM families WHERE family_id = :f"
                    ),
                    {"f": family},
                )
            ).mappings().one()
        out["steps"][name] = {
            "status": response.status_code,
            "text": response.text,
            "flag": row["flag"],
            "unfinished": row["unfinished"],
        }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
            await state(
                "ped",
                await ac.post(
                    "/api/ped/upload",
                    params={"project_id": project_id, "overwrite": "false"},
                    files={"file": (f"{family}.ped", _ped(family, names), "text/plain")},
                ),
            )
            async with sessionmaker() as session:
                await session.execute(
                    text(
                        "UPDATE families SET metadata = COALESCE(metadata, '{}'::jsonb) "
                        "|| jsonb_build_object('import_incomplete', CAST(:flag AS jsonb), "
                        "'import_unfinished', jsonb_build_object(CAST(:key AS text), CAST(:entry AS jsonb))) "
                        "WHERE family_id = :f"
                    ),
                    {"f": family, "flag": json.dumps(flag), "key": stopped.key, "entry": json.dumps(stopped.entry())},
                )
                await session.commit()
            sign_out = f"/api/families/{family}/report/sign-out"
            await state("signout_flagged", await ac.post(sign_out, json=_OTHER_GATES))
            await state("child_deleted", await ac.delete(f"/api/admin/samples/{child}", params={"confirm": "true"}))
            await state(
                "father_trgt",
                await ac.post(
                    f"/api/repeat-expansions/upload/{father}",
                    params={"overwrite": "false"},
                    files={"file": ("father.trgt.vcf", _trgt_vcf(father).encode(), "text/plain")},
                ),
            )
            await state("signout", await ac.post(sign_out, json=_OTHER_GATES))
    finally:
        await _drop_families(sessionmaker, [family])
    return out


@pytest.fixture(scope="module")
def flags_cleared(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("flags_cleared"))
    try:
        return _harness.run_async(lambda: _flags_cleared(uuid4().hex[:8].upper()))
    finally:
        mp.undo()


def test_the_flagged_family_is_refused_sign_out(flags_cleared) -> None:
    assert flags_cleared["steps"]["ped"]["status"] == 200, flags_cleared["steps"]["ped"]["text"]
    refused = flags_cleared["steps"]["signout_flagged"]
    assert refused["status"] == 409, refused["text"]
    assert json.loads(refused["text"])["detail"]["gate"] == "import_incomplete"


def test_a_sample_deletion_strikes_the_sample_off_the_flag_and_the_stopped_imports_entry(flags_cleared) -> None:
    step = flags_cleared["steps"]["child_deleted"]
    assert step["status"] == 200, step["text"]
    father = flags_cleared["names"]["father"]
    # The father's TRGT calls may still be missing; nothing of the child can be. The import
    # that stopped while writing the child's coverage alone is gone. Before: both named the
    # child, whose data no upload or import can bring again.
    assert step["flag"]["failed_datasets"] == ["repeats_trgt"]
    assert step["flag"]["failed_scopes"] == {
        "repeats_trgt": [{"scope": {"source": None, "samples": [father]}, "job_id": None}]
    }
    assert step["unfinished"] is None


def test_the_fathers_upload_then_clears_the_flag_and_the_report_signs_out(flags_cleared) -> None:
    upload = flags_cleared["steps"]["father_trgt"]
    assert upload["status"] == 200, upload["text"]
    # Before: the flag stayed, naming the child, and sign-out was refused.
    assert upload["flag"] is None and upload["unfinished"] is None
    signed = flags_cleared["steps"]["signout"]
    assert signed["status"] == 200, signed["text"]
    snapshot = json.loads(signed["text"])["snapshot"]
    assert snapshot["import_incomplete"] is None and not snapshot["import_unfinished"]
