"""End-to-end coverage of the GCS storage backend against a real GCS-compatible
server (fake-gcs-server), complementing the mocked unit tests in
test_object_storage.py.

Exercises the operations that hit the live client — object_exists, download_prefix,
and remote package discovery — over the JSON API. Signed-URL generation is not
covered here (it needs real IAM SignBlob; see the unit test).

Gated by RUN_INTEGRATION=1 (integration conftest). The fake-gcs-server endpoint is
taken from GCS_ENDPOINT_URL if set, otherwise a container is started via docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request

import pytest

pytest.importorskip("google.cloud.storage")

from google.auth.credentials import AnonymousCredentials  # noqa: E402
from google.cloud import storage  # noqa: E402

from app.core import object_storage as s  # noqa: E402

pytestmark = pytest.mark.integration

_BUCKET = "coga-it-bucket"
_PORT = 4443


def _wait_ready(endpoint: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"{endpoint}/storage/v1/b?project=coga"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:
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

    name = "coga-fake-gcs-it"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-p", f"{_PORT}:{_PORT}",
            # The same pinned image CI starts (#520).
            "fsouza/fake-gcs-server:1.56.1@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef",
            "-scheme", "http",
            "-port", str(_PORT),
            "-public-host", f"127.0.0.1:{_PORT}",
        ],
        check=True,
        capture_output=True,
    )
    endpoint = f"http://127.0.0.1:{_PORT}"
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
    except Exception:
        bucket = admin.bucket(_BUCKET)
    for key, body in {
        "F1/S1.cram": b"CRAMDATA",
        "fam/F1/manifest.yaml": b"family_id: F1\n",
        "fam/F1/sub/x.vcf.gz": b"\x1f\x8b\x08vcf",
        "fam/F2/trio.ped": b"#ped\n",
        "fam/empty/readme.txt": b"nothing importable here\n",
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
