"""NCBI's ClinVar folder stood in for, for the integration tests that load the synthetic ClinVar
file (``fixtures/clinvar``) through the real load job, as a release of an assembly of their own;
and a release's rows made directly (``release_rows``), for the tests that store one themselves.
"""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "clinvar" / "clinvar_20261122.vcf"
SOURCE = "https://clinvar.example/pub/clinvar"
# The folder of the test's own assembly; NCBI's folder of any other assembly is not there.
FOLDER = "vcf_TEST"


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
    return gzip.compress("\n".join([*header, *body[:records]]).encode() + b"\n", mtime=0)


class StandInNcbi:
    """NCBI's ClinVar folder of the test assembly, listing the files put in it; no other. The
    ``.md5`` of a release in ``md5_withheld`` is not there yet, as while NCBI publishes."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.md5_withheld: set[date] = set()

    def put(self, release: date, body: bytes) -> None:
        self.files[f"clinvar_{release:%Y%m%d}.vcf.gz"] = body

    def handle(self, request: httpx.Request) -> httpx.Response:
        if f"/{FOLDER}/" not in request.url.path:
            return httpx.Response(404)
        name = request.url.path.rsplit("/", 1)[-1]
        if not name:
            return httpx.Response(200, text="".join(f'<a href="{file}">{file}</a>\n' for file in self.files))
        withheld = {f"clinvar_{release:%Y%m%d}.vcf.gz.md5" for release in self.md5_withheld}
        if name.endswith(".md5") and name[:-4] in self.files and name not in withheld:
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
    monkeypatch.setitem(CLINVAR_ASSEMBLY_FOLDERS, name, FOLDER)
    monkeypatch.setattr(clinvar_load_jobs.settings, "clinvar_source_url", SOURCE)
    monkeypatch.setattr(clinvar_load_jobs, "_start", lambda job_id: None)
    monkeypatch.setattr(
        clinvar_load_jobs,
        "clinvar_http_client",
        lambda: clinvar_download.clinvar_http_client(httpx.MockTransport(ncbi.handle)),
    )
    return clinvar_load_jobs, name, ncbi


async def load_clinvar_release(jobs: Any, assembly: str) -> dict[str, Any]:
    """A load of NCBI's newest file for ``assembly``, run to its end, as its job ended."""
    job = await jobs.run_clinvar_load(assembly, requested_by=jobs.STARTUP_REQUESTER)
    assert job is not None, "another ClinVar load is active"
    return job.model_dump()


def release_rows(assembly: str, classifications: Mapping[str, str]) -> list[Any]:
    """A ClinVar release's rows (``clinvar_vcf.ClinvarRow``) holding each ``chrom-pos-ref-alt``
    allele of ``classifications`` with its classification, keyed as the import keys a row's
    alleles: its minimal allele on ``assembly``."""
    from backend.app.services.clickhouse_variant_ids import minimal_allele_id, small_allele_key
    from backend.app.services.clickhouse_variant_records import _status_terms
    from backend.app.services.clinvar_vcf import ClinvarRow

    rows = []
    for index, (variant_id, classification) in enumerate(sorted(classifications.items())):
        chrom, pos, ref, alt = variant_id.split("-")
        allele = minimal_allele_id(chrom, int(pos), ref, alt)
        assert allele is not None
        rows.append(
            ClinvarRow(
                allele_key=small_allele_key(assembly, allele),
                variant_id=allele,
                variation_id=990_000 + index,
                allele_id=0,
                classification=classification,
                classification_terms=tuple(sorted(_status_terms(classification))),
                conflicting="",
                review_status="criteria provided, single submitter",
                conditions=(),
                genes=(),
                included_only=False,
            )
        )
    return rows
