"""An assembly's cytobands: UCSC's table as published, recorded by SHA-256; a shipped backup when
UCSC cannot serve it.

Adding a human assembly from UCSC (GRCh38 at the first start) reads UCSC's cytoband table and
records it by name and SHA-256 (owner decision, 2026-11-05: a new instance starts on the newest
reference data, a shipped file only as a backup). When UCSC cannot serve it, the import loads the
shipped backup of the genome (data/ref-data/cytobands/), held to its pinned SHA-256,
and the genes, which come in the same import, load too. Nothing here goes to the network: UCSC is
an httpx mock transport.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import logging
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from backend.app.core import config
from backend.app.core.config import settings
from backend.app.services import reference_source_service as rss
from backend.tests.test_reference_import_versions import (
    GENCODE_GTF,
    GENCODE_GTF_URL,
    _by_dataset,
    _patch_lookups,
    _patch_ucsc_import,
    _RecordingSession,
)

_BACKUPS = Path(__file__).resolve().parents[2] / "data" / "ref-data" / "cytobands"
_UCSC = "https://hgdownload.soe.ucsc.edu/goldenPath"
_SERVED = gzip.compress(b"chr1\t0\t2300000\tp36.33\tgneg\nchr1\t2300000\t5300000\tp36.32\tgpos25\n")


def _ucsc(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no route to host", request=request)


def _no_table(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


# --- the shipped files ------------------------------------------------------------------------------


@pytest.mark.parametrize("genome", sorted(config.CYTOBAND_BACKUP_FILES))
def test_each_backup_is_its_pinned_file_and_its_readme_says_so(genome: str) -> None:
    pin = config.CYTOBAND_BACKUP_FILES[genome]
    raw = (_BACKUPS / pin.file_name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pin.sha256
    readme = (_BACKUPS / "README.md").read_text(encoding="utf-8")
    for value in (pin.file_name, pin.sha256, pin.served_on, f"{_UCSC}/{genome}/database/cytoBandIdeo.txt.gz"):
        assert value in readme, value
    # UCSC's five columns, and bands on every primary chromosome.
    rows = [line.split("\t") for line in gzip.decompress(raw).decode().splitlines()]
    assert {len(row) for row in rows} == {5}
    chromosomes = {row[0] for row in rows}
    assert {f"chr{name}" for name in [*map(str, range(1, 23)), "X", "Y"]} <= chromosomes


def test_the_backups_are_kept_byte_for_byte_by_git() -> None:
    attributes = (_BACKUPS.parents[2] / ".gitattributes").read_text(encoding="utf-8")
    assert "data/ref-data/cytobands/*.gz -text" in attributes


# --- UCSC first --------------------------------------------------------------------------------------


def test_ucscs_table_is_read_as_published_and_recorded_by_its_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=_SERVED)

    def no_backup(_genome: str):
        raise AssertionError("UCSC served its table: the backup is not read")

    monkeypatch.setattr(rss, "read_cytoband_backup", no_backup)

    async def run() -> rss.CytobandFile:
        async with _ucsc(serve) as client:
            return await rss._download_cytobands(client, ucsc_genome="hg38")

    cytobands = asyncio.run(run())

    assert asked == [f"{_UCSC}/hg38/database/cytoBandIdeo.txt.gz"]
    assert cytobands.text == gzip.decompress(_SERVED).decode()
    assert cytobands.source == "ucsc" and cytobands.source_url == asked[0]
    assert cytobands.source_version == (
        f"read as published: UCSC cytobands: cytoBandIdeo.txt.gz, SHA-256 {hashlib.sha256(_SERVED).hexdigest()}"
    )


# --- the backup ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("ucsc", [_unreachable, _no_table], ids=["unreachable", "no-table"])
def test_when_ucsc_cannot_serve_the_table_the_pinned_backup_is_loaded(
    caplog: pytest.LogCaptureFixture, ucsc
) -> None:
    pin = config.CYTOBAND_BACKUP_FILES["hg38"]
    caplog.set_level(logging.INFO, logger=rss.logger.name)

    async def run() -> rss.CytobandFile:
        async with _ucsc(ucsc) as client:
            return await rss._download_cytobands(client, ucsc_genome="hg38")

    cytobands = asyncio.run(run())

    assert cytobands.text == gzip.decompress((_BACKUPS / pin.file_name).read_bytes()).decode()
    assert cytobands.source == "ucsc (coga backup)"
    assert cytobands.source_url.endswith(f"cytobands/{pin.file_name}")
    # Recorded by name, the day it was taken from UCSC, and SHA-256.
    assert pin.file_name in cytobands.source_version and pin.sha256 in cytobands.source_version
    assert pin.served_on in cytobands.source_version
    [warning] = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert "backup" in warning and "hg38" in warning


def test_another_file_in_the_backups_place_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pin = config.CYTOBAND_BACKUP_FILES["hg38"]
    other = tmp_path / pin.file_name
    other.write_bytes(_SERVED)
    monkeypatch.setattr(rss, "cytoband_backup_candidates", lambda _name: [other])
    caplog.set_level(logging.INFO, logger=rss.logger.name)

    async def run() -> rss.CytobandFile:
        async with _ucsc(_unreachable) as client:
            return await rss._download_cytobands(client, ucsc_genome="hg38")

    # UCSC's failure stands: the import fails, with nothing loaded.
    with pytest.raises(HTTPException) as failed:
        asyncio.run(run())
    assert failed.value.status_code == 502
    [error] = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    for value in (str(other), hashlib.sha256(_SERVED).hexdigest(), pin.sha256, pin.file_name):
        assert value in error, value


def test_without_a_backup_file_ucscs_failure_stands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(rss, "cytoband_backup_candidates", lambda name: [tmp_path / name])
    caplog.set_level(logging.INFO, logger=rss.logger.name)

    async def run() -> rss.CytobandFile:
        async with _ucsc(_unreachable) as client:
            return await rss._download_cytobands(client, ucsc_genome="hg38")

    with pytest.raises(HTTPException):
        asyncio.run(run())
    assert any("No cytoband backup for hg38" in record.getMessage() for record in caplog.records)


def test_a_genome_without_a_backup_gets_nothing_in_its_place() -> None:
    assert rss.read_cytoband_backup("hs1") is None

    async def run() -> rss.CytobandFile:
        async with _ucsc(_unreachable) as client:
            return await rss._download_cytobands(client, ucsc_genome="mm39")

    with pytest.raises(HTTPException):
        asyncio.run(run())


# --- the assembly import ---------------------------------------------------------------------------


def test_with_ucsc_unreachable_the_import_loads_the_backup_and_still_loads_the_genes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The genes come in the same import, and a failed cytoband download does not stop them.
    real_download = rss._download_cytobands
    _patch_lookups(monkeypatch)
    _patch_ucsc_import(monkeypatch, ucsc_genome="hg38", gtf_url=GENCODE_GTF_URL, gtf_text=GENCODE_GTF)
    monkeypatch.setattr(rss, "_download_cytobands", real_download)
    monkeypatch.setattr(settings, "reference_gencode_gtf_url", GENCODE_GTF_URL)

    async def ucsc_unreachable(_client, url: str):
        raise HTTPException(status_code=502, detail=f"Failed to download reference data from {url}")

    monkeypatch.setattr(rss, "_get_optional_gzip", ucsc_unreachable)
    session = _RecordingSession()

    result = asyncio.run(
        rss.import_reference_from_ucsc(session, tax_id=9606, ucsc_genome="hg38", overwrite=True)  # type: ignore[arg-type]
    )

    records = _by_dataset(session.import_records())
    pin = config.CYTOBAND_BACKUP_FILES["hg38"]
    assert records["cytobands"]["source"] == "ucsc (coga backup)"
    assert pin.sha256 in records["cytobands"]["source_version"]
    assert records["genes"]["source"] == "gencode v50 (Ensembl 116)"
    assert result.cytoband_source_url.endswith(f"cytobands/{pin.file_name}")
