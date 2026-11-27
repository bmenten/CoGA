"""NCBI's ClinVar folder stood in for, for the integration tests that load the synthetic ClinVar
file (``fixtures/clinvar``) through the real load job, as a release of an assembly of their own.
"""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

FIXTURE = Path(__file__).parents[1] / "fixtures" / "clinvar" / "clinvar_20261122.vcf"
SOURCE = "https://clinvar.example/pub/clinvar"


def clinvar_file(
    assembly: str,
    release: date,
    *,
    records: int | None = None,
    edit: Callable[[str], str] = lambda record: record,
) -> bytes:
    """The synthetic ClinVar file as the release of ``release`` for ``assembly``: its first
    ``records`` records only when given, each record as ``edit`` writes it."""
    lines = FIXTURE.read_text().splitlines()
    header = [
        f"##fileDate={release.isoformat()}" if line.startswith("##fileDate=")
        else f"##reference={assembly}" if line.startswith("##reference=")
        else line
        for line in lines
        if line.startswith("#")
    ]
    body = [edit(line) for line in lines if not line.startswith("#")]
    return gzip.compress("\n".join([*header, *body[:records]]).encode() + b"\n")


class StandInNcbi:
    """NCBI's ClinVar folder of the test assembly, listing the files put in it."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def put(self, release: date, body: bytes) -> None:
        self.files[f"clinvar_{release:%Y%m%d}.vcf.gz"] = body

    def handle(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if not name:
            return httpx.Response(200, text="".join(f'<a href="{file}">{file}</a>\n' for file in self.files))
        if name.endswith(".md5") and name[:-4] in self.files:
            return httpx.Response(200, text=f"{hashlib.md5(self.files[name[:-4]]).hexdigest()}  {name[:-4]}\n")
        if name in self.files:
            return httpx.Response(200, content=self.files[name])
        return httpx.Response(404)


def stand_in_ncbi(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str, StandInNcbi]:
    """The load-job module, an assembly name ClinVar publishes for in this test only, and NCBI.
    Each test runs a queued job itself (``load_clinvar_release``)."""
    from backend.app.services import clinvar_download, clinvar_load_jobs
    from backend.app.services.clinvar_releases import CLINVAR_ASSEMBLY_FOLDERS

    name = f"clinvar-{uuid4()}"
    ncbi = StandInNcbi()
    monkeypatch.setitem(CLINVAR_ASSEMBLY_FOLDERS, name, "vcf_TEST")
    monkeypatch.setattr(clinvar_load_jobs.settings, "clinvar_source_url", SOURCE)
    monkeypatch.setattr(clinvar_load_jobs, "_start", lambda job_id: None)
    monkeypatch.setattr(
        clinvar_load_jobs,
        "clinvar_http_client",
        lambda: clinvar_download.clinvar_http_client(httpx.MockTransport(ncbi.handle)),
    )
    return clinvar_load_jobs, name, ncbi


async def load_clinvar_release(jobs: Any, sm: Any, assembly: str) -> dict[str, Any]:
    """Queue a load of NCBI's newest file for ``assembly``, run it to its end, and return the
    job as recorded."""
    async with sm() as s:
        job = await jobs.queue_clinvar_load(s, assembly=assembly, requested_by=jobs.STARTUP_REQUESTER)
    await jobs._run_job(str(job.id))
    async with sm() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, error, release, source_sha256, records_read, records_stored, "
                    "records_skipped FROM clinvar_load_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": str(job.id)},
            )
        ).mappings().one()
    return dict(row)
