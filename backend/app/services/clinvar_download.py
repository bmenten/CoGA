"""ClinVar's newest weekly VCF for an assembly, found on NCBI and downloaded with its MD5.

NCBI keeps the newest weekly file of an assembly in its folder (``CLINVAR_ASSEMBLY_FOLDERS``)
under ``CLINVAR_SOURCE_URL``, named by its release (``clinvar_20261122.vcf.gz``), with an
``.md5`` file beside it. The newest file is the one the folder's listing names with the latest
date. The download goes over HTTPS only, to a file of the load's own, and is refused when its
MD5 is not the one NCBI states; CoGA computes the file's SHA-256 as it arrives, which the
release records.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from ..core.config import settings
from .clinvar_releases import CLINVAR_ASSEMBLY_FOLDERS
from .clinvar_vcf import CLINVAR_FILE_NAME, ClinvarFileRefused, release_of_file_name

# A read that waits this long for NCBI is a stalled download: the load fails and is run again.
HTTP_TIMEOUT = httpx.Timeout(60.0, read=120.0)
# The file as it is, not compressed for the transfer: its MD5 and SHA-256 are the file's own.
HTTP_HEADERS = {"User-Agent": "CoGA-reference-loader/1.0", "Accept-Encoding": "identity"}
# A weekly file is about 190 MB; a larger one than this is not ClinVar's, and is not written.
_MAX_FILE_BYTES = 2 << 30
_MD5 = re.compile(r"[0-9a-fA-F]{32}")


@dataclass(frozen=True, slots=True)
class ClinvarFile:
    """A weekly ClinVar file NCBI lists: its release and where it is."""

    release: date
    url: str

    @property
    def file_name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class DownloadedClinvarFile:
    path: Path
    size: int
    md5: str
    sha256: str


async def _https_only(request: httpx.Request) -> None:
    """Every request, each redirect included, goes over HTTPS."""
    if request.url.scheme != "https":
        raise ClinvarFileRefused(f"Refused to read ClinVar over a non-HTTPS URL: {request.url}")


def clinvar_http_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The client a load reads NCBI with: HTTPS only, redirects followed, the files' own bytes.
    ``transport`` stands in for the network in a test."""
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers=HTTP_HEADERS,
        event_hooks={"request": [_https_only]},
        transport=transport,
    )


def _folder_url(assembly_name: str) -> str:
    return f"{settings.clinvar_source_url.rstrip('/')}/{CLINVAR_ASSEMBLY_FOLDERS[assembly_name]}/"


async def newest_clinvar_file(client: httpx.AsyncClient, assembly_name: str) -> ClinvarFile:
    """The newest weekly file the assembly's folder lists. ``ClinvarFileRefused`` when it lists
    none."""
    folder = _folder_url(assembly_name)
    response = await client.get(folder)
    response.raise_for_status()
    releases = {
        release
        for match in CLINVAR_FILE_NAME.finditer(response.text)
        if (release := release_of_file_name(match[0])) is not None
    }
    if not releases:
        raise ClinvarFileRefused(f"NCBI's ClinVar folder for {assembly_name} lists no weekly VCF.")
    newest = max(releases)
    return ClinvarFile(release=newest, url=f"{folder}clinvar_{newest:%Y%m%d}.vcf.gz")


async def _stated_md5(client: httpx.AsyncClient, file: ClinvarFile) -> str:
    """NCBI's MD5 of the file: the first word of its ``.md5`` file."""
    response = await client.get(f"{file.url}.md5")
    response.raise_for_status()
    words = response.text.split()
    if not words or not _MD5.fullmatch(words[0]):
        raise ClinvarFileRefused(f"NCBI's {file.file_name}.md5 states no MD5.")
    return words[0].lower()


async def download_clinvar_file(
    client: httpx.AsyncClient,
    file: ClinvarFile,
    folder: Path,
    progress: Callable[[int, int | None], Awaitable[None]],
) -> DownloadedClinvarFile:
    """Download the file into ``folder``, and return it with its size, MD5 and SHA-256.
    ``ClinvarFileRefused`` when its MD5 is not the one NCBI states, or it is larger than any
    ClinVar file. ``progress(bytes_read, bytes_total)`` is awaited after each part."""
    expected_md5 = await _stated_md5(client, file)
    path = folder / file.file_name
    md5, sha256 = hashlib.md5(usedforsecurity=False), hashlib.sha256()
    size = 0
    async with client.stream("GET", file.url) as response:
        response.raise_for_status()
        length = response.headers.get("content-length")
        total = int(length) if length and length.isdigit() else None
        with path.open("wb") as handle:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_FILE_BYTES:
                    raise ClinvarFileRefused(f"{file.file_name} is larger than any ClinVar VCF.")
                md5.update(chunk)
                sha256.update(chunk)
                handle.write(chunk)
                await progress(size, total)
    if md5.hexdigest() != expected_md5:
        raise ClinvarFileRefused(
            f"{file.file_name} has MD5 {md5.hexdigest()}, not the {expected_md5} NCBI states: "
            "the download is not NCBI's file."
        )
    return DownloadedClinvarFile(path=path, size=size, md5=expected_md5, sha256=sha256.hexdigest())
