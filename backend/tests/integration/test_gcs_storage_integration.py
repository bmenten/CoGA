"""End-to-end coverage of the GCS storage backend against a real GCS-compatible
server (fake-gcs-server), complementing the mocked unit tests in
test_object_storage.py.

Exercises the operations that hit the live client — object_exists, download_prefix,
and remote package discovery — over the JSON API. Signed-URL generation is not
covered here (it needs real IAM SignBlob; see the unit test).

It also imports a package's alignments from the bucket the way Terraform lays it out
(FAMILY_IMPORT_ROOTS=gs://<bucket>/imports): staging leaves the CRAM in the store, the
importer records the object's URI on the sample (real Postgres), and the CRAM endpoint
resolves that recorded object. The mocked unit tests are in
test_remote_package_alignments.py.

Gated by RUN_INTEGRATION=1 (integration conftest). The fake-gcs-server endpoint is
taken from GCS_ENDPOINT_URL if set, otherwise a container is started via docker.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
import shutil
import socket
import subprocess
import time
from typing import Any
import urllib.request
from uuid import uuid4

import pytest

pytest.importorskip("google.cloud.storage")

from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from app.core import object_storage as s
from backend.tests._pinned_images import pinned_image

from backend.tests._kept_manifest import kept_manifest

pytestmark = pytest.mark.integration

_BUCKET = "coga-it-bucket"
_ALIGNMENTS_MANIFEST = b"""schema_version: 1
family_id: F1
ped: family.ped
datasets:
  alignments:
    per_sample:
      S1:
        file: bams/S1.cram
        index: bams/S1.cram.crai
  qc:
    per_sample:
      S1:
        read_stats: qc/S1.NanoStats.txt
"""
_NANOSTATS = b"General summary:\nNumber of reads:  1,000\n"


def _free_port() -> int:
    """A local port nothing listens on now, for this run's emulator."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_ready(endpoint: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"{endpoint}/storage/v1/b?project=coga"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:  # noqa: BLE001 - keep polling until the emulator answers
            time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def gcs_endpoint():
    env = os.environ.get("GCS_ENDPOINT_URL")
    if env:
        if not _wait_ready(env):
            pytest.skip(f"GCS_ENDPOINT_URL set but not reachable: {env}")
        yield env
        return

    if not shutil.which("docker"):
        pytest.skip("no GCS_ENDPOINT_URL and docker unavailable")

    # A container and a port of this run's own: a fixed name had a second run at once,
    # another worktree's say, remove the first run's emulator as it started its own, and both
    # bind the same port.
    name = f"coga-fake-gcs-it-{uuid4().hex[:12]}"
    port = _free_port()
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-p", f"127.0.0.1:{port}:{port}",
            # The image CI starts, as ci/pinned-images.Dockerfile pins it (OPS-12).
            pinned_image("fake-gcs-server"),
            "-scheme", "http",
            "-port", str(port),
            "-public-host", f"127.0.0.1:{port}",
        ],
        check=True,
        capture_output=True,
    )
    endpoint = f"http://127.0.0.1:{port}"
    try:
        if not _wait_ready(endpoint):
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
            pytest.skip(f"fake-gcs-server did not become ready: {logs.stderr[-500:]}")
        yield endpoint
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture(scope="module")
def gcs_backend(gcs_endpoint):
    """Point the app at the emulator (gcs backend + bucket + endpoint), seed objects,
    and restore settings afterwards."""
    saved = {
        k: getattr(s.settings, k)
        for k in ("storage_backend", "gcs_bucket", "gcs_prefix", "gcs_endpoint_url", "gcs_project")
    }
    s.settings.storage_backend = "gcs"
    s.settings.gcs_bucket = _BUCKET
    s.settings.gcs_prefix = ""
    s.settings.gcs_endpoint_url = gcs_endpoint
    s.settings.gcs_project = "coga"
    s._gcs_client.cache_clear()

    # Seed via a direct admin client pointed at the emulator.
    admin = storage.Client(
        project="coga",
        credentials=AnonymousCredentials(),
        client_options={"api_endpoint": gcs_endpoint},
    )
    try:
        bucket = admin.create_bucket(_BUCKET)
    except Exception:  # noqa: BLE001 - the bucket already exists on a re-run
        bucket = admin.bucket(_BUCKET)
    for key, body in {
        "F1/S1.cram": b"CRAMDATA",
        "fam/F1/manifest.yaml": b"family_id: F1\n",
        "fam/F1/sub/x.vcf.gz": b"\x1f\x8b\x08vcf",
        "fam/F2/trio.ped": b"#ped\n",
        "fam/empty/readme.txt": b"nothing importable here\n",
        # A family package where Terraform's FAMILY_IMPORT_ROOTS points.
        "imports/F1/manifest.yaml": _ALIGNMENTS_MANIFEST,
        "imports/F1/family.ped": b"F1 S1 0 0 1 2\n",
        "imports/F1/bams/S1.cram": b"CRAMDATA",
        "imports/F1/bams/S1.cram.crai": b"CRAIDATA",
        "imports/F1/snv/F1.vcf.gz.csi": b"CSI",
        "imports/F1/qc/S1.NanoStats.txt": _NANOSTATS,
    }.items():
        bucket.blob(key).upload_from_string(body)

    yield
    s._gcs_client.cache_clear()
    for k, v in saved.items():
        setattr(s.settings, k, v)


def test_object_exists_against_fake_gcs(gcs_backend):
    assert s.object_exists("F1/S1.cram") is True
    assert s.object_exists("F1/missing.cram") is False


def test_download_prefix_against_fake_gcs(gcs_backend, tmp_path):
    written = s.download_prefix(f"gs://{_BUCKET}/fam/F1", tmp_path)
    assert written == 2
    assert (tmp_path / "manifest.yaml").read_bytes() == b"family_id: F1\n"
    assert (tmp_path / "sub" / "x.vcf.gz").exists()


def test_list_remote_package_candidates_against_fake_gcs(gcs_backend):
    candidates = {c["name"]: c for c in s.list_remote_package_candidates(f"gs://{_BUCKET}/fam")}
    assert set(candidates) == {"F1", "F2"}  # 'empty' has neither manifest nor ped
    assert candidates["F1"]["has_manifest"] and not candidates["F1"]["has_ped"]
    assert candidates["F2"]["has_ped"] and not candidates["F2"]["has_manifest"]
    assert candidates["F1"]["uri"] == f"gs://{_BUCKET}/fam/F1"


def test_remote_object_exists_against_fake_gcs(gcs_backend):
    assert s.remote_object_exists(f"gs://{_BUCKET}/imports/F1/bams/S1.cram") is True
    assert s.remote_object_exists(f"gs://{_BUCKET}/imports/F1/bams/S9.cram") is False


def _import_from_the_bucket(monkeypatch, tmp_path):
    from app.services import family_package_source

    monkeypatch.setattr(s.settings, "family_import_roots", [f"gs://{_BUCKET}/imports"])
    monkeypatch.setattr(family_package_source, "_staging_root", lambda: tmp_path.resolve())
    return family_package_source.staged_package_source(f"gs://{_BUCKET}/imports/F1")


def test_staging_leaves_alignments_in_fake_gcs(gcs_backend, monkeypatch, tmp_path):
    from app.services.family_package_validation import load_validated_family_package

    with _import_from_the_bucket(monkeypatch, tmp_path) as staged:
        root = Path(staged.root)
        staged_files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        assert staged_files == ["family.ped", "manifest.yaml", "qc/S1.NanoStats.txt", "snv/F1.vcf.gz.csi"]
        assert staged.remote_only_files == {"bams/S1.cram", "bams/S1.cram.crai"}

        validation, bundle = load_validated_family_package(
            staged.root, manifest_text=kept_manifest(staged.root), remote_only_files=staged.remote_only_files
        )
    assert validation.valid, validation.errors
    assert bundle is not None and bundle.remote_only_files == staged.remote_only_files


def _on_postgres_with_a_fresh_family(body) -> None:
    """Run ``await body(sessionmaker, family)`` against real Postgres, with a family and
    one sample made for the test (unique ids: the database is shared by the integration
    tests), deleted again afterwards."""
    from types import SimpleNamespace

    from sqlalchemy import text

    from app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema

    label = f"gcs-it-{uuid4().hex[:8]}"

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as session:
                family_uuid = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": label},
                    )
                ).scalar_one()
                sample_uuid = (
                    await session.execute(
                        text(
                            "INSERT INTO samples (sample_id, family_id, sex) "
                            "VALUES (:s, CAST(:f AS uuid), 'und') RETURNING id::text"
                        ),
                        {"s": f"{label}-S1", "f": family_uuid},
                    )
                ).scalar_one()
                await session.commit()
            family = SimpleNamespace(
                label=label, uuid=family_uuid, sample_label=f"{label}-S1", sample_uuid=sample_uuid
            )
            try:
                await body(sm, family)
            finally:
                async with sm() as session:
                    await session.execute(
                        text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": family_uuid}
                    )
                    await session.commit()
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_an_alignment_imported_from_fake_gcs_is_served_from_where_it_lies(
    gcs_backend, monkeypatch, tmp_path
):
    """Stage -> validate -> alignments importer (real GCS existence checks, real Postgres
    write) -> the CRAM endpoint's lookup of the recorded location (real Postgres read,
    real GCS existence checks). Only the URL signing is faked: it needs IAM SignBlob."""
    from sqlalchemy import text

    from app.routers import cram
    from app.schemas import FamilyImportDatasetSummary
    from app.services import family_package_datasets
    from app.services.family_metadata_context import SampleMetadataContext
    from app.services.family_package_validation import load_validated_family_package

    monkeypatch.setattr(
        cram, "presigned_get_url", lambda key, filename=None, expires=None: f"https://signed.example/{key}"
    )

    async def body(sm, family) -> None:
        sample_context = SampleMetadataContext(
            sample_uuid=family.sample_uuid,
            sample_id="S1",
            family_uuid=family.uuid,
            family_id=family.label,
            sex="und",
            project_ids=[],
            assembly_id=None,
            assembly_name=None,
        )
        with _import_from_the_bucket(monkeypatch, tmp_path) as staged:
            validation, bundle = load_validated_family_package(
                staged.root,
                manifest_text=kept_manifest(staged.root),
                remote_only_files=staged.remote_only_files,
                source_uri=staged.source_uri,
            )
            assert validation.valid, validation.errors
            assert bundle is not None
            async with sm() as session:
                result = await family_package_datasets._import_alignments_dataset(
                    family_package_datasets.DatasetImportJob(
                        session=session,
                        bundle=bundle,
                        dataset=bundle.manifest.datasets["alignments"],
                        summary=FamilyImportDatasetSummary(
                            dataset_type="alignments", enabled=True, status="valid"
                        ),
                        family_context=None,  # not read by this importer
                        sample_contexts={"S1": sample_context},
                    )
                )
        assert result.status == "imported", result.message

        async with sm() as session:
            # The package folder the family's import read, as the import's registration
            # records it: a recorded location is served only from there.
            await session.execute(
                text(
                    "UPDATE families SET metadata = jsonb_build_object("
                    "'package_import', jsonb_build_object('folder_path', CAST(:folder AS text))) "
                    "WHERE id = CAST(:f AS uuid)"
                ),
                {"folder": staged.source_uri, "f": family.uuid},
            )
            await session.commit()
            recorded = await cram._recorded_alignments(session, [family.sample_label])
        assert set(recorded) == {family.sample_label}
        entry = await asyncio.to_thread(
            cram._resolve_alignment_manifest_entry, "F1", family.sample_label, recorded[family.sample_label]
        )
        assert entry is not None
        assert entry.url == "https://signed.example/imports/F1/bams/S1.cram"
        assert entry.index_url == "https://signed.example/imports/F1/bams/S1.cram.crai"

    _on_postgres_with_a_fresh_family(body)


def test_remote_object_identity_against_fake_gcs(gcs_backend):
    identity = s.remote_object_identity(f"gs://{_BUCKET}/imports/F1/bams/S1.cram")
    assert identity is not None
    assert (identity["store"], identity["size"]) == ("gcs", len(b"CRAMDATA"))
    assert identity["generation"]  # the store's own version of the object
    checksums = {checksum["algorithm"]: checksum for checksum in identity["checksums"]}
    assert set(checksums) == {"md5", "crc32c"}
    assert checksums["md5"] == {
        "algorithm": "md5",
        "encoding": "base64",
        "value": base64.b64encode(hashlib.md5(b"CRAMDATA").digest()).decode(),
    }
    assert s.remote_object_identity(f"gs://{_BUCKET}/imports/F1/bams/S9.cram") is None


def test_provenance_of_a_package_imported_from_fake_gcs(gcs_backend, monkeypatch, tmp_path):
    """raw_import_files rows (real Postgres) for a package staged from the bucket: the
    staged file hashed from its staged copy, the CRAM left in the store identified by the
    store's record of it, both named by their gs:// URI; the manifest CoGA kept for the
    import stored as a managed copy with its SHA-256."""
    from sqlalchemy import text

    from app.services import family_package_registration
    from app.services import raw_import_files_pg as rif
    from app.services.family_package_validation import load_validated_family_package

    managed = tmp_path / "managed"
    monkeypatch.setattr(rif, "DATA_DIR", managed)

    async def body(sm, family) -> None:
        with _import_from_the_bucket(monkeypatch, tmp_path) as staged:
            manifest_text = kept_manifest(staged.root)
            validation, bundle = load_validated_family_package(
                staged.root,
                manifest_text=manifest_text,
                remote_only_files=staged.remote_only_files,
                source_uri=staged.source_uri,
            )
            assert validation.valid, validation.errors
            assert bundle is not None
            async with sm() as session:
                await family_package_registration._record_package_raw_files(
                    session, bundle=bundle, family_uuid=family.uuid, family_identifier=family.label
                )
                await session.commit()
        async with sm() as session:
            rows: Sequence[Any] = (
                await session.execute(
                    text(
                        "SELECT id, storage_path, sha256, file_size, metadata, dataset, managed "
                        "FROM raw_import_files WHERE family_id = CAST(:f AS uuid)"
                    ),
                    {"f": family.uuid},
                )
            ).mappings().all()
        kept_rows = [row for row in rows if row["dataset"] == "manifest"]
        assert len(kept_rows) == 1
        kept_row = kept_rows[0]
        kept_bytes = manifest_text.encode("utf-8")
        assert kept_row["managed"] is True
        assert Path(kept_row["storage_path"]).is_relative_to(managed)
        assert Path(kept_row["storage_path"]).read_bytes() == kept_bytes
        assert (kept_row["sha256"], kept_row["file_size"]) == (hashlib.sha256(kept_bytes).hexdigest(), len(kept_bytes))
        by_path = {row["storage_path"]: row for row in rows if row["dataset"] != "manifest"}
        source = f"gs://{_BUCKET}/imports/F1"
        assert set(by_path) == {
            f"{source}/bams/S1.cram",
            f"{source}/bams/S1.cram.crai",
            f"{source}/qc/S1.NanoStats.txt",
        }
        stats = by_path[f"{source}/qc/S1.NanoStats.txt"]
        assert (stats["sha256"], stats["file_size"]) == (hashlib.sha256(_NANOSTATS).hexdigest(), len(_NANOSTATS))
        cram_row = by_path[f"{source}/bams/S1.cram"]
        assert (cram_row["sha256"], cram_row["file_size"]) == (None, len(b"CRAMDATA"))
        store_object = cram_row["metadata"]["store_object"]
        assert store_object["uri"] == f"{source}/bams/S1.cram" and store_object["generation"]
        assert {checksum["algorithm"] for checksum in store_object["checksums"]} == {"md5", "crc32c"}
        # The staged file carries the store's record too, for Verify.
        assert by_path[f"{source}/qc/S1.NanoStats.txt"]["metadata"]["store_object"]["generation"]

        # Verify reads the row back as the admin page does, with the package folder the
        # family's import read (as the import's registration records it), and checks the
        # object: only an object in that folder is asked about. The kept manifest's
        # managed copy is checked where CoGA stored it.
        async with sm() as session:
            await session.execute(
                text(
                    "UPDATE families SET metadata = jsonb_build_object("
                    "'package_import', jsonb_build_object('folder_path', CAST(:folder AS text))) "
                    "WHERE id = CAST(:f AS uuid)"
                ),
                {"folder": source, "f": family.uuid},
            )
            await session.commit()
            ids: Sequence[str] = (
                await session.execute(
                    text(
                        "SELECT id::text FROM raw_import_files "
                        "WHERE family_id = CAST(:f AS uuid) AND dataset <> 'manifest'"
                    ),
                    {"f": family.uuid},
                )
            ).scalars().all()
            records = [await rif.get_raw_import_file(session, file_id) for file_id in ids]
            kept_record = await rif.get_raw_import_file(session, str(kept_row["id"]))
        assert len(records) == 3
        for record in records:
            assert record is not None and record["in_object_store"] and record["exists"] is None
            assert record["package_folder"] == source and not record["outside_read_folders"]
            assert (await rif.verify_raw_import_file(record))["status"] == "verified", record["storage_path"]
        assert kept_record is not None and not kept_record["in_object_store"] and kept_record["exists"]
        assert (await rif.verify_raw_import_file(kept_record))["status"] == "verified"

    _on_postgres_with_a_fresh_family(body)


def test_verify_checks_a_bucket_file_against_the_stores_record(gcs_backend, gcs_endpoint, monkeypatch):
    """Verify on a file kept in the bucket compares the object with the store's record of
    it taken at import (real generations): the same object verifies; a replaced one --
    same size, new generation -- is a mismatch; a deleted one is missing. The object lies in
    the family's package folder below FAMILY_IMPORT_ROOTS, where Verify asks the store about a
    recorded object."""
    from app.services import raw_import_files_pg as rif

    monkeypatch.setattr(s.settings, "family_import_roots", [f"gs://{_BUCKET}/imports"])
    admin = storage.Client(
        project="coga",
        credentials=AnonymousCredentials(),
        client_options={"api_endpoint": gcs_endpoint},
    )
    package = f"imports/verify-{uuid4().hex[:8]}"
    key = f"{package}/S1.cram"
    blob = admin.bucket(_BUCKET).blob(key)
    blob.upload_from_string(b"CRAMDATA")
    uri = f"gs://{_BUCKET}/{key}"
    identity = s.remote_object_identity(uri)
    record = {
        "id": "rec",
        "package_folder": f"gs://{_BUCKET}/{package}",
        "storage_path": uri,
        "sha256": None,
        "file_size": identity["size"],
        "metadata": {"store_object": identity},
    }

    assert asyncio.run(rif.verify_raw_import_file(record))["status"] == "verified"

    blob.upload_from_string(b"CRAMDAT2")  # same size, a new generation
    replaced = asyncio.run(rif.verify_raw_import_file(record))
    assert replaced["status"] == "mismatch" and "generation" in replaced["message"]

    blob.delete()
    assert asyncio.run(rif.verify_raw_import_file(record))["status"] == "missing"
