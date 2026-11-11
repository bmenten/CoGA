"""A package on a second import root has its reads and QC report served where it lies (E2E).

On a VM a network share is mounted read-only as a second import root
(``FAMILY_IMPORT_ROOTS=/data/families,/data/share``, docs/deployment-vm.md), and its packages
are imported in place. The genome browser finds a sample's reads, and its
QC report, where the package lies, not only in the family's folders (``<data>/<family>`` and
``<data>/families/<family>``), so a package on the share shows its reads in IGV and opens its
report.

Here a one-sample package lies on the share in a folder named after its run, not its family,
its CRAM and index named as a pipeline names them (``bams/<sample>.markdup.cram``), with a QC
report. A real import job (``POST /api/family-imports``, then the worker's
``run_family_import_job``) records them in Postgres. Over the real API (an in-process
``httpx.ASGITransport`` client, one event loop, see test_e2e_api_contract.py), with the seeded
admin signed in:

* the alignment manifest lists the sample under its own name, and its URLs fetch the package's
  reads and index; HEAD answers 200;
* the QC-report link opens the package's report.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The IDs and
files are synthetic, a new family each run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import ensure_postgres_schema
from backend.tests.e2e._import_jobs import run_import_job

pytestmark = pytest.mark.integration

_WORKER = "e2e-share-package-worker"


def _package(folder: Path, family: str, sample: str) -> dict[str, bytes]:
    """The package's files, written to ``folder``; returned by package-relative path."""
    files = {
        "family.ped": f"{family}\t{sample}\t0\t0\t1\t2\n".encode(),
        f"bams/{sample}.markdup.cram": f"reads of {sample}".encode(),
        f"bams/{sample}.markdup.cram.crai": f"index of {sample}".encode(),
        f"qc/{sample}.html": f"<html>report of {sample}</html>".encode(),
        "manifest.yaml": (
            f"schema_version: 1\nfamily_id: {family}\nped: family.ped\ndatasets:\n"
            f"  alignments:\n    per_sample:\n      {sample}:\n"
            f"        file: bams/{sample}.markdup.cram\n        index: bams/{sample}.markdup.cram.crai\n"
            f"  qc:\n    per_sample:\n      {sample}:\n        report: qc/{sample}.html\n"
        ).encode(),
    }
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return files


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.routers import cram, family_qc_reports, signal_tracks
    from backend.tests.e2e import _harness

    family, sample = f"SHAREPKG{run}", f"SHAREPKG{run}_PROBAND"
    data = base / "data"
    (data / "families").mkdir(parents=True)
    package = data / "share" / f"run-{run}"
    files = _package(package, family, sample)

    await ensure_postgres_schema()
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    out: dict[str, Any] = {"files": files, "family": family, "sample": sample}
    routers = (cram, signal_tracks, family_qc_reports)
    saved = [router.DATA_DIR for router in routers], settings.family_import_roots
    for router in routers:
        router.DATA_DIR = data
    settings.family_import_roots = [str(data / "families"), str(data / "share")]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"
            out["job"] = await run_import_job(package, project_id, _WORKER)

            manifest = await admin.get(f"/api/cram/{family}/manifest", params={"sample": sample})
            entries = manifest.json() if manifest.status_code == 200 else []
            out["manifest"] = (
                manifest.status_code,
                [(entry["sample_id"], entry["format"], entry["url"], entry["index_url"]) for entry in entries],
            )
            out["fetched"] = [
                ((await admin.get(f"/api{entry['url']}")).content, (await admin.get(f"/api{entry['index_url']}")).content)
                for entry in entries
            ]
            out["head"] = (await admin.head(f"/api/cram/{family}/{sample}.cram")).status_code

            link = await admin.get(f"/api/families/{family}/qc-report/{sample}/link")
            report = (await admin.get(f"/api{link.json()['url']}")).content if link.status_code == 200 else None
            out["report"] = (link.status_code, report)
    finally:
        directories, settings.family_import_roots = saved
        for router, directory in zip(routers, directories):
            router.DATA_DIR = directory
    return out


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("share-package")
    return _harness.run_async(lambda: _exercise(base, uuid4().hex[:10].upper()))


def test_the_package_on_the_share_is_imported(served: dict[str, Any]) -> None:
    # Without this the answers below could come from a family that does not exist.
    job = served["job"]
    assert "raised" not in job, job
    assert (job["status"], job["ended"]) == ("completed", True), job


def test_igv_reads_the_reads_where_the_package_lies(served: dict[str, Any]) -> None:
    family, sample, files = served["family"], served["sample"], served["files"]
    assert served["manifest"] == (
        200,
        [(sample, "cram", f"/cram/{family}/{sample}.cram", f"/cram/{family}/{sample}.cram.crai")],
    )
    assert served["fetched"] == [
        (files[f"bams/{sample}.markdup.cram"], files[f"bams/{sample}.markdup.cram.crai"])
    ]
    assert served["head"] == 200


def test_the_qc_report_opens_where_the_package_lies(served: dict[str, Any]) -> None:
    sample = served["sample"]
    assert served["report"] == (200, served["files"][f"qc/{sample}.html"])
