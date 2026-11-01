"""Prepare the pinned GRCh38 FASTA before deployment; stdlib only, no app secrets.

Compose runs this in a one-shot container. Terraform runs this file on the deploy
machine and publishes the same assets to the read-only runtime reference bucket.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE_URL = "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_50/GRCh38.primary_assembly.genome.fa.gz"
SOURCE_SHA256 = "b760d18dbb651dd14dfc290083371b3ef3bff122d43a9cefb13ca4ecf38f05ca"
RELATIVE_DIRECTORY = Path("genomes/GRCh38/gencode-v50")
FASTA_NAME = "GRCh38.primary_assembly.genome.fa"
MANIFEST_NAME = "reference.json"
DEFAULT_FASTA = Path("/data/ref-data") / RELATIVE_DIRECTORY / FASTA_NAME
# The pins are computed from the full official file, checked against GENCODE's MD5SUMS.
# MD5 is used only to compare GCS's transport checksum; SHA-256 identifies each asset.
ARTIFACTS: dict[str, dict[str, Any]] = {
    "GRCh38.primary_assembly.genome.fa": {
        "sha256": "e49b92b3e4f321bf254c042f25b726d9931c4d74c7523e8b6bb530e63b0cfd4b",
        "md5": "Sb24DSGmTcsWrPyUGEM1bg==",
        "size": 3151417447
    },
    "GRCh38.primary_assembly.genome.fa.fai": {
        "sha256": "a2c323ea4cff34d7123ace4578f7e122b2d2f5a22f40dc23eb8b97d17723d169",
        "md5": "g2kUy7mVzACZX4e65C1a/Q==",
        "size": 6482
    }
}
AUTOSOME_LENGTHS = (248956422, 242193529, 198295559, 190214555, 181538259, 170805979,
                    159345973, 145138636, 138394717, 133797422, 135086622, 133275309,
                    114364328, 107043718, 101991189, 90338345, 83257441, 80373285,
                    58617616, 64444167, 46709983, 50818468)
MAX_COMPRESSED = 2 << 30
MAX_UNCOMPRESSED = 4 << 30
CHUNK = 1 << 20


class ReferenceFastaError(ValueError):
    """A fixed, non-identifying deployment error."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReferenceFastaError("Reference requests must not redirect.")


def _open(request):
    return build_opener(_NoRedirect()).open(request, timeout=60)


def file_identity(path: Path) -> dict[str, Any]:
    sha, md5, size = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return {"sha256": sha.hexdigest(), "md5": base64.b64encode(md5.digest()).decode(), "size": size}


def provenance() -> dict[str, Any]:
    return {"genome_build": "GRCh38", "provider": "GENCODE", "release": "50",
            "source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256,
            "index_format": "FAI", "files": ARTIFACTS}


def download_archive(path: Path) -> None:
    deadline, size, digest = time.monotonic() + 1800, 0, hashlib.sha256()
    request = Request(SOURCE_URL, headers={"User-Agent": "CoGA-reference-data/1", "Accept-Encoding": "identity"})
    with _open(request) as response, path.open("wb") as handle:
        while chunk := response.read(CHUNK):
            size += len(chunk)
            if size > MAX_COMPRESSED or time.monotonic() > deadline:
                raise ReferenceFastaError("The GRCh38 download exceeded its size or time limit.")
            digest.update(chunk)
            handle.write(chunk)
    if digest.hexdigest() != SOURCE_SHA256:
        raise ReferenceFastaError("The GRCh38 archive does not match the pinned SHA-256.")


def unpack_archive(archive: Path, fasta: Path) -> None:
    if archive.stat().st_size > MAX_COMPRESSED or file_identity(archive)["sha256"] != SOURCE_SHA256:
        raise ReferenceFastaError("The GRCh38 archive does not match the pinned SHA-256.")
    size = 0
    with gzip.open(archive, "rb") as source, fasta.open("wb") as target:
        while chunk := source.read(CHUNK):
            size += len(chunk)
            if size > MAX_UNCOMPRESSED:
                raise ReferenceFastaError("The GRCh38 FASTA exceeded its decompressed size limit.")
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())


def index_fasta(fasta: Path) -> Path:
    """Write HTSlib's five-column FAI format, validating line widths and assembly.

    Keeping this stdlib-only lets terraform apply prepare the same reference without
    installing bioinformatics packages. Tests compare the entire index with pysam faidx.
    """
    rows: list[tuple[str, int, int, int, int]] = []
    seen: set[str] = set()
    name, length, offset, line_bases, line_width = "", 0, 0, 0, 0
    previous: tuple[int, int] | None = None
    with fasta.open("rb") as source:
        while line := source.readline(CHUNK):
            if len(line) >= CHUNK:
                raise ReferenceFastaError("The FASTA contains an oversized line.")
            if line.startswith(b">"):
                if name:
                    if not length:
                        raise ReferenceFastaError("The FASTA contains an empty sequence.")
                    rows.append((name, length, offset, line_bases, line_width))
                words = line[1:].split()
                if not words or re.fullmatch(rb"[A-Za-z0-9_.-]+", words[0]) is None:
                    raise ReferenceFastaError("The FASTA contains an invalid sequence name.")
                name = words[0].decode("ascii")
                if name in seen:
                    raise ReferenceFastaError("The FASTA contains duplicate sequence names.")
                seen.add(name)
                length, offset, line_bases, line_width, previous = 0, source.tell(), 0, 0, None
            else:
                bases = line.rstrip(b"\r\n")
                if not name or not bases or bases.translate(None, b"ACGTRYSWKMBDHVNacgtryswkmbdhvn"):
                    raise ReferenceFastaError("The FASTA contains an invalid sequence line.")
                if previous and previous != (line_bases, line_width):
                    raise ReferenceFastaError("The FASTA has inconsistent sequence line widths.")
                if not line_bases:
                    line_bases, line_width = len(bases), len(line)
                if len(bases) > line_bases:
                    raise ReferenceFastaError("The FASTA has inconsistent sequence line widths.")
                length += len(bases)
                previous = len(bases), len(line)
    if not name or not length:
        raise ReferenceFastaError("The FASTA contains no complete sequence.")
    rows.append((name, length, offset, line_bases, line_width))
    lengths = {row[0]: row[1] for row in rows}
    if any(lengths.get(f"chr{i}") != size for i, size in enumerate(AUTOSOME_LENGTHS, 1)):
        raise ReferenceFastaError("The FASTA does not contain the GRCh38 autosomes with their expected lengths.")
    index = Path(f"{fasta}.fai")
    index.write_text("".join("\t".join(map(str, row)) + "\n" for row in rows), encoding="ascii")
    return index


def validate_directory(directory: Path) -> None:
    if json.loads((directory / MANIFEST_NAME).read_text()) != provenance():
        raise ReferenceFastaError("The cached GRCh38 provenance does not match this release.")
    for name, expected in ARTIFACTS.items():
        if file_identity(directory / name) != expected:
            raise ReferenceFastaError("The cached GRCh38 FASTA or index failed its checksum check.")


def prepare_local(root: Path, archive: Path | None = None) -> Path:
    destination = root / RELATIVE_DIRECTORY
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A local filesystem lock serializes simultaneous Compose/VM preparation processes.
    with (destination.parent / ".prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # A killed preparer releases the lock but may leave its private staging
        # directory behind. No other live local preparer can own it under this lock.
        for stale in destination.parent.glob(".prepare-*"):
            if stale.is_symlink():
                stale.unlink()
            elif stale.is_dir():
                shutil.rmtree(stale)
        if destination.exists():
            validate_directory(destination)
            return destination / FASTA_NAME
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=destination.parent) as temporary:
            staging = Path(temporary) / "complete"
            staging.mkdir()
            if archive is None:
                archive = Path(temporary) / "source.fa.gz"
                download_archive(archive)
            fasta = staging / FASTA_NAME
            unpack_archive(archive, fasta)
            index_fasta(fasta)
            (staging / MANIFEST_NAME).write_text(json.dumps(provenance(), sort_keys=True, indent=2) + "\n")
            validate_directory(staging)
            # The version directory appears only when every file is complete and verified.
            staging.rename(destination)
    return destination / FASTA_NAME


class GcsPublisher:
    """Deploy credentials write the reference bucket; the API account stays read-only."""
    def __init__(self, bucket: str, project: str):
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket) is None or not project:
            raise ReferenceFastaError("A reference bucket and project are required.")
        self.bucket, self.project = bucket, project
        result = subprocess.run(["gcloud", "auth", "print-access-token", f"--project={project}"],
                                check=True, capture_output=True, text=True, timeout=60)
        self._token = result.stdout.strip()
        if not self._token:
            raise ReferenceFastaError("The deploy account has no Google access token.")

    def _get(self, name: str, *, media: bool = False) -> dict[str, Any] | None:
        key = (RELATIVE_DIRECTORY / name).as_posix()
        url = f"https://storage.googleapis.com/storage/v1/b/{quote(self.bucket, safe='')}/o/{quote(key, safe='')}"
        if media:
            url += "?alt=media"
        request = Request(url, headers={"Authorization": f"Bearer {self._token}"})
        try:
            with _open(request) as response:
                value = response.read(CHUNK + 1)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise ReferenceFastaError("Cannot read reference objects with the deploy account.") from None
        if len(value) > CHUNK:
            raise ReferenceFastaError("The reference object metadata exceeded its size limit.")
        record = json.loads(value)
        if not isinstance(record, dict):
            raise ReferenceFastaError("The reference object metadata is invalid.")
        return record

    def matching(self, name: str) -> bool:
        record = self._get(name)
        if record is None:
            return False
        expected = ARTIFACTS[name]
        if record.get("md5Hash") != expected["md5"] or str(record.get("size")) != str(expected["size"]):
            raise ReferenceFastaError("An existing pinned GRCh38 object has unexpected contents; deployment stopped.")
        return True

    def complete(self) -> bool:
        # Read every object's provider checksum, even when an earlier one is missing.
        matches = [self.matching(name) for name in ARTIFACTS]
        manifest = self._get(MANIFEST_NAME, media=True)
        if manifest is not None and manifest != provenance():
            raise ReferenceFastaError("The reference bucket contains inconsistent GRCh38 provenance.")
        return all(matches) and manifest is not None

    def publish(self, directory: Path) -> None:
        validate_directory(directory)
        for name in [*ARTIFACTS, MANIFEST_NAME]:
            if name != MANIFEST_NAME and self.matching(name):
                continue
            target = f"gs://{self.bucket}/{(RELATIVE_DIRECTORY / name).as_posix()}"
            subprocess.run(["gcloud", "storage", "cp", str(directory / name), target,
                                     "--if-generation-match=0", f"--project={self.project}", "--quiet"],
                                    capture_output=True, timeout=1800)
            # A concurrent successful deploy may have created exactly the same pinned object.
            matched = self._get(name, media=True) == provenance() if name == MANIFEST_NAME else self.matching(name)
            if not matched:
                raise ReferenceFastaError("Publishing GRCh38 reference data failed; the backend rollout must not continue.")
        if not self.complete():
            raise ReferenceFastaError("The GRCh38 reference bundle is incomplete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/data/ref-data"))
    parser.add_argument("--archive", type=Path, help="Use an offline copy of the same pinned official gzip.")
    parser.add_argument("--gcs-bucket")
    parser.add_argument("--project")
    args = parser.parse_args()
    try:
        if args.gcs_bucket:
            publisher = GcsPublisher(args.gcs_bucket, args.project or "")
            if not publisher.complete():
                with tempfile.TemporaryDirectory(prefix="coga-grch38-") as temporary:
                    fasta = prepare_local(Path(temporary), args.archive)
                    publisher.publish(fasta.parent)
            print("Pinned GRCh38 FASTA and index are available in the reference bucket.")
        else:
            fasta = prepare_local(args.directory, args.archive)
            print(f"Pinned GRCh38 FASTA and index are ready: {fasta}")
    except Exception as exc:  # noqa: BLE001 - fixed errors only, never credentials or subprocess output
        message = str(exc) if isinstance(exc, ReferenceFastaError) else f"GRCh38 reference preparation failed ({type(exc).__name__})."
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
