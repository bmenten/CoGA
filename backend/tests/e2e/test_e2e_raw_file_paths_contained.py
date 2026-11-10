"""Raw-file download, Verify, the list and the deletes keep to the family's own folders (E2E).

The admin page lists a family's ``raw_import_files`` rows, downloads a file and verifies its
SHA-256 at the recorded ``storage_path``, and deleting a sample or the family unlinks the
``managed`` copies (web uploads, under ``data/raw_imports``). Each keeps to the family's own
folders: used as found, an edited row's path would let an admin download or hash any file the
backend can read, and turn a sample or family delete into deleting any file the backend may
write.

Over the real API (an in-process ``httpx.ASGITransport`` client, one event loop, see
test_e2e_api_contract.py) and Postgres and ClickHouse, with the seeded admin signed in, a
family of two samples made through the Family Builder, its package folder recorded as its
import records it (``families.metadata.package_import.folder_path``), and its
``raw_import_files`` rows written as the import writes them and as an edited row would hold
them. The files lie in the test's own folders: ``imports`` (FAMILY_IMPORT_ROOTS), holding the
family's package and another family's, ``data`` (the data directory, whose ``raw_imports`` is
the upload folder) and ``elsewhere``, which is neither.

* a package file, in a package folder not named after the family, and an upload are listed,
  downloaded and verified; a row naming a file elsewhere, a path that climbs out of the import
  root with ``..`` or a link in the package that leads out is listed unavailable, and its
  download and Verify answer 403; so does one naming a file of another family's package in
  the import area, or a link in the package to it;
* deleting the second sample unlinks its upload and leaves the file elsewhere that another of
  its managed rows names; deleting the family unlinks its upload, also one in the folder of a
  former ID, and leaves the package file and the file elsewhere that its managed rows name;
  each delete counts only what it unlinked.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The IDs and
files are synthetic, and the family is dropped at the end.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import ensure_postgres_schema, place_file

pytestmark = pytest.mark.integration

OUTSIDE = (
    "The file's recorded storage path lies outside its family's package folder (below "
    "FAMILY_IMPORT_ROOTS) and CoGA's upload folder, so the file is not read."
)


async def _exercise(run: str, base: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    family, s1, s2 = f"RAWPATH_{run}_F1", f"RAWPATH_{run}_S1", f"RAWPATH_{run}_S2"
    imports, data, elsewhere = base / "imports", base / "data", base / "elsewhere"
    package_folder = imports / f"run-{run}"
    files = {
        "package": place_file(package_folder / "trio.vcf.gz", b"package file"),
        "other_package": place_file(imports / f"run-other-{run}" / "trio.vcf.gz", b"another family's package"),
        "upload": place_file(data / "raw_imports" / family / "0a1b2c__trio.vcf", b"family upload"),
        "former_upload": place_file(data / "raw_imports" / f"FORMER_{run}" / "3d4e5f__trio.vcf", b"upload under a former ID"),
        "sample_upload": place_file(data / "raw_imports" / family / "6a7b8c__s2.vcf", b"sample upload"),
        "secret": place_file(elsewhere / "secret.txt", b"secret"),
        "victim_family": place_file(elsewhere / "victim-family.txt", b"named by a managed row of the family"),
        "victim_sample": place_file(elsewhere / "victim-sample.txt", b"named by a managed row of the sample"),
    }
    (package_folder / "linked-out.txt").symlink_to(files["secret"])
    (package_folder / "linked-across.vcf.gz").symlink_to(files["other_package"])

    # name: (sample, managed, recorded path, the file it reaches)
    rows: dict[str, tuple[str | None, bool, str, str]] = {
        "package": (None, False, str(files["package"]), "package"),
        "upload": (None, True, str(files["upload"]), "upload"),
        # As an edited row could hold them:
        "elsewhere": (None, False, str(files["secret"]), "secret"),
        "climbing": (None, False, str(package_folder / ".." / ".." / "elsewhere" / "secret.txt"), "secret"),
        "linked_out": (None, False, str(package_folder / "linked-out.txt"), "secret"),
        "other_package": (None, False, str(files["other_package"]), "other_package"),
        "linked_across": (None, False, str(package_folder / "linked-across.vcf.gz"), "other_package"),
        "managed_package": (s1, True, str(files["package"]), "package"),
        "managed_elsewhere": (None, True, str(files["victim_family"]), "victim_family"),
        "former_upload": (None, True, str(files["former_upload"]), "former_upload"),
        "sample_upload": (s2, True, str(files["sample_upload"]), "sample_upload"),
        "sample_elsewhere": (s2, True, str(files["victim_sample"]), "victim_sample"),
    }

    await ensure_postgres_schema()
    # The deletes run against the assembly's ClickHouse tables.
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    out: dict[str, Any] = {"files": {name: str(path) for name, path in files.items()}}
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
            ids: dict[str, str] = {}
            async with sessionmaker() as session:
                family_uuid = (
                    await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family})
                ).scalar_one()
                # The package folder the family's import read, as the import's registration
                # records it: its files are read only there and in the upload folder.
                await session.execute(
                    text(
                        "UPDATE families SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object("
                        "'package_import', jsonb_build_object('folder_path', CAST(:folder AS text))) "
                        "WHERE id = CAST(:f AS uuid)"
                    ),
                    {"folder": str(package_folder), "f": family_uuid},
                )
                sample_uuids = {
                    row["sample_id"]: row["id"]
                    for row in (
                        await session.execute(
                            text("SELECT sample_id, id::text AS id FROM samples WHERE family_id = CAST(:f AS uuid)"),
                            {"f": family_uuid},
                        )
                    ).mappings()
                }
                for name, (sample, managed, storage_path, reaches) in rows.items():
                    ids[name] = (
                        await session.execute(
                            text(
                                """
                                INSERT INTO raw_import_files (
                                    family_id, sample_id, scope, dataset, file_name, file_type,
                                    storage_path, managed, file_size, sha256, source, metadata
                                )
                                VALUES (
                                    CAST(:family AS uuid), CAST(:sample AS uuid), :scope, 'small_variants',
                                    :file_name, 'VCF', :storage_path, :managed, :size, :sha256, 'web',
                                    CAST(:metadata AS jsonb)
                                )
                                RETURNING id::text
                                """
                            ),
                            {
                                "family": family_uuid,
                                "sample": sample_uuids[sample] if sample else None,
                                "scope": "individual" if sample else "family",
                                "file_name": f"{name}.vcf",
                                "storage_path": storage_path,
                                "managed": managed,
                                "size": len(files[reaches].read_bytes()),
                                "sha256": hashlib.sha256(files[reaches].read_bytes()).hexdigest(),
                                "metadata": json.dumps({}),
                            },
                        )
                    ).scalar_one()
                await session.commit()

            listed = await admin.get(f"/api/admin/data/families/{family}/files")
            out["listed_status"] = listed.status_code
            by_id = {row["id"]: row for part in ("family_files", "individual_files") for row in listed.json()[part]}
            out["listed"] = {
                name: (by_id[record_id]["exists"], by_id[record_id]["download_available"]) for name, record_id in ids.items()
            }

            out["downloads"], out["verified"] = {}, {}
            for name in ("package", "upload", "elsewhere", "climbing", "linked_out", "other_package", "linked_across"):
                download = await admin.get(f"/api/admin/data/files/{ids[name]}/download")
                out["downloads"][name] = (
                    download.status_code,
                    download.content if download.status_code == 200 else download.json()["detail"],
                )
                verify = await admin.post(f"/api/admin/data/files/{ids[name]}/verify")
                body = verify.json()
                out["verified"][name] = (verify.status_code, body.get("status") or body.get("detail"))

            sample_delete = await admin.delete(f"/api/admin/samples/{s2}", params={"confirm": "true"})
            out["sample_delete"] = (sample_delete.status_code, sample_delete.json().get("deleted", {}).get("raw_import_files"))
            out["after_sample_delete"] = {name: path.exists() for name, path in files.items()}

            family_delete = await admin.request(
                "DELETE", f"/api/admin/families/{family}", params={"confirm": "true"}, json={"reason": "e2e"}
            )
            # The family delete counts the files it unlinked as `raw_files`, the sample delete as
            # `raw_import_files`.
            out["family_delete"] = (family_delete.status_code, family_delete.json().get("deleted", {}).get("raw_files"))
            out["after_family_delete"] = {name: path.exists() for name, path in files.items()}
            out["contents"] = {name: path.read_bytes() for name, path in files.items() if path.exists()}
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": family})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def exercised(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("raw-file-paths").resolve()
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base / "imports")])
    mp.setattr(raw_import_files_pg, "DATA_DIR", base / "data")
    request.addfinalizer(mp.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run, base))


def test_the_family_is_made_and_listed(exercised: dict[str, Any]) -> None:
    # Without this the answers below could come from a family that does not exist.
    assert (exercised["created"], exercised["listed_status"]) == (200, 200)


def test_the_list_shows_a_file_outside_the_folders_unavailable(exercised: dict[str, Any]) -> None:
    available, unavailable = (True, True), (False, False)
    assert exercised["listed"] == {
        "package": available,
        "upload": available,
        "elsewhere": unavailable,
        "climbing": unavailable,
        "linked_out": unavailable,
        # another family's package, below the import root too.
        "other_package": unavailable,
        "linked_across": unavailable,
        # Read where it lies (the family's package); not deleted for it, below.
        "managed_package": available,
        "managed_elsewhere": unavailable,
        "former_upload": available,
        "sample_upload": available,
        "sample_elsewhere": unavailable,
    }


def test_a_file_outside_the_folders_is_neither_downloaded_nor_verified(exercised: dict[str, Any]) -> None:
    assert exercised["downloads"] == {
        "package": (200, b"package file"),
        "upload": (200, b"family upload"),
        "elsewhere": (403, OUTSIDE),
        "climbing": (403, OUTSIDE),
        "linked_out": (403, OUTSIDE),
        "other_package": (403, OUTSIDE),
        "linked_across": (403, OUTSIDE),
    }
    assert exercised["verified"] == {
        "package": (200, "verified"),
        "upload": (200, "verified"),
        "elsewhere": (403, OUTSIDE),
        "climbing": (403, OUTSIDE),
        "linked_out": (403, OUTSIDE),
        "other_package": (403, OUTSIDE),
        "linked_across": (403, OUTSIDE),
    }


def test_a_sample_delete_unlinks_only_its_upload(exercised: dict[str, Any]) -> None:
    assert exercised["sample_delete"] == (200, 1)
    after = exercised["after_sample_delete"]
    assert after["sample_upload"] is False
    # Another of the sample's managed rows names it, and it is left.
    assert after["victim_sample"] is True


def test_a_family_delete_unlinks_only_its_uploads(exercised: dict[str, Any]) -> None:
    # Its two uploads alone, not the package file or the file elsewhere.
    assert exercised["family_delete"] == (200, 2)
    assert exercised["after_family_delete"] == {
        "package": True,
        "other_package": True,
        "upload": False,
        "former_upload": False,
        "sample_upload": False,
        "secret": True,
        "victim_family": True,
        "victim_sample": True,
    }
    assert exercised["contents"] == {
        "package": b"package file",
        "other_package": b"another family's package",
        "secret": b"secret",
        "victim_family": b"named by a managed row of the family",
        "victim_sample": b"named by a managed row of the sample",
    }
