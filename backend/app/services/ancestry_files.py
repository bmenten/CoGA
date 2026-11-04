"""VCF files a family package declares for the ancestry PCA without importing them.

Exploratory and outside CoGA's intended purpose (TF-01 §4). A package may name, beside its
datasets, VCF files only the ancestry PCA reads: typically a gVCF, whose reference blocks say
where each individual was sequenced and called reference, so no absence has to be assumed.

    {"metadata": {"ancestry": {"vcfs": [{"path": "ancestry/PROBAND.g.vcf.gz", "label": "gVCF"}]}}}

- **Declared** after the import completed (``register_declared_files``): a row per file in
  ``ancestry_files``. It never reads the file and never fails or delays the import: what goes
  wrong leaves a line in the import's log.
- **Scanned** once, by the ancestry worker (``scan_next_file``): the file is read in full (minutes
  for a whole-genome gVCF), its SHA-256 taken as it is read, and only its calls at the reference
  markers kept (``ancestry_file_scans``). A reference block covering a marker is an explicit
  reference call there, passing unless the block names a failed filter (a gVCF writes FILTER=.
  on its blocks and PASS on its variants). A file that cannot be read is recorded as failed, with a fixed reason.
- **Read** like a stored callset (``file:<id>``) by every calculation, from the scan: the file is
  not read again. Of the files declared under one path, the latest declaration counts.
"""
from __future__ import annotations

import asyncio
import bisect
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
import gzip
import hashlib
import io
import json
import logging
from pathlib import Path
import re
from typing import Any, BinaryIO, TextIO
import zlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ancestry_schemas import AncestryFileOut
from ..core.coga_logging import describe_error
from .ancestry_callsets import MarkerRow
from .ancestry_projection import AncestryInputError, AncestryReference
from .data_scope import normalize_chromosome
from .family_package_common import FamilyPackageBundle, _resolve_package_path
from .family_package_source import _ensure_authorized_package_path, staged_package_source_async

logger = logging.getLogger(__name__)

# At most this many files per package; more are not declared.
MAX_DECLARED = 8
_GVCF_ALTS = frozenset({"<NON_REF>", "<*>"})
_END = re.compile(r"(?:^|;)END=(\d+)(?:;|$)")
SCAN_FAILED = "The VCF file could not be read: it is missing, cut short, damaged or not a GRCh38 VCF."
_LATEST_SCANS = """
    SELECT DISTINCT ON (f.relative_path) f.id::text AS file_id, f.label, f.relative_path, f.declared_at,
           s.status, s.sha256, s.columns, s.rows, s.error, s.scanned_at
    FROM ancestry_files f
    LEFT JOIN LATERAL (
        SELECT * FROM ancestry_file_scans x WHERE x.file_id = f.id ORDER BY x.scanned_at DESC LIMIT 1
    ) s ON TRUE
    WHERE f.family_uuid = CAST(:family AS uuid) AND f.project_id = CAST(:project AS uuid)
    ORDER BY f.relative_path, f.declared_at DESC, f.id DESC
"""


def declared_files(bundle: FamilyPackageBundle) -> list[dict[str, str]]:
    """The files ``metadata.ancestry.vcfs`` declares: a path, or a path and a label."""
    declaration = bundle.manifest.metadata.get("ancestry")
    vcfs = declaration.get("vcfs") if isinstance(declaration, dict) else None
    if not isinstance(vcfs, list):
        return []
    files = []
    for item in vcfs[:MAX_DECLARED]:
        entry = {"path": item} if isinstance(item, str) else item
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].strip():
            continue
        label = entry.get("label") if isinstance(entry.get("label"), str) else ""
        shown = "".join(ch for ch in (label.strip() or Path(entry["path"]).name) if ch.isprintable())
        files.append({"path": entry["path"].strip(), "label": shown[:120] or "VCF file"})
    return files


async def register_declared_files(session: AsyncSession, *, bundle: FamilyPackageBundle, family_uuid: str,
                                  family_identifier: str, project_ids: list[str], project_id: str | None,
                                  job_id: str | None, logs: list[str]) -> None:
    """Record the package's declared ancestry VCF files, after its import completed. Reads no
    file and never raises: what goes wrong is a line in ``logs``."""
    files = declared_files(bundle)
    if not files:
        return
    try:
        project = project_id or (project_ids[0] if len(project_ids) == 1 else None)
        if project is None or project not in project_ids:
            logs.append("The VCF files declared for the exploratory ancestry PCA were not recorded: "
                        "import the package into one project.")
            return
        root = await asyncio.to_thread(bundle.root.resolve)
        recorded = 0
        for item in files:
            path = _resolve_package_path(bundle.root, item["path"])
            if path is None or not await asyncio.to_thread(path.is_file):
                logs.append(f"The ancestry VCF file {item['label']} is not in the package; it was not recorded.")
                continue
            await session.execute(text("""
                INSERT INTO ancestry_files(family_uuid, family_identifier, project_id, import_job_id,
                                           package_path, relative_path, label)
                VALUES (CAST(:family AS uuid), :identifier, CAST(:project AS uuid), CAST(:job AS uuid),
                        :package, :relative, :label)
            """), {"family": family_uuid, "identifier": family_identifier, "project": project, "job": job_id,
                    "package": bundle.source_uri or str(root), "relative": str(path.relative_to(root)),
                    "label": item["label"]})
            recorded += 1
        await session.commit()
        if recorded:
            logs.append(f"{recorded} VCF file(s) declared for the exploratory ancestry PCA; "
                        "it reads them in the background.")
    except Exception as exc:  # noqa: BLE001 - optional exploratory files; sanitized type only
        await session.rollback()
        logger.warning("Ancestry file declaration failed: %s", describe_error(exc))
        logs.append("The VCF files declared for the exploratory ancestry PCA could not be recorded.")


class _HashingReader(io.RawIOBase):
    """A binary file that hashes the bytes read from it."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        chunk = self._handle.read(len(buffer))
        self.digest.update(chunk)
        self.size += len(chunk)
        buffer[:len(chunk)] = chunk
        return len(chunk)


@contextmanager
def _open_hashed(path: Path) -> Iterator[tuple[TextIO, _HashingReader]]:
    with path.open("rb") as raw:
        compressed = raw.read(2) == b"\x1f\x8b"
        raw.seek(0)
        hashing = _HashingReader(raw)
        buffered = io.BufferedReader(hashing, buffer_size=1024 * 1024)
        binary: BinaryIO = gzip.GzipFile(fileobj=buffered) if compressed else buffered  # type: ignore[assignment]
        yield io.TextIOWrapper(binary, encoding="utf-8", errors="replace"), hashing


def scan_vcf_markers(path: Path, reference: AncestryReference) -> dict[str, Any]:
    """A VCF or gVCF's calls at the reference markers, with the file's SHA-256 and size.

    A record at a marker's position is kept with its own alleles (a gVCF's ``<NON_REF>`` dropped);
    a reference block (``END=``) holds each marker it covers, as a call of the marker's own
    alleles with the block's genotypes. Raises :class:`AncestryInputError` on a file it cannot read.
    """
    by_chrom: dict[str, list[int]] = {}
    markers = {}
    for marker in reference.markers:
        by_chrom.setdefault(marker.chrom, []).append(marker.pos)
        markers[(marker.chrom, marker.pos)] = marker
    for positions in by_chrom.values():
        positions.sort()
    rows: list[list[Any]] = []
    columns: list[str] | None = None
    try:
        with _open_hashed(path) as (stream, hashing):
            for line in stream:
                if line.startswith("#"):
                    if line.startswith("##reference=") and re.search(r"GRCh37|hg19", line, re.I):
                        raise AncestryInputError(SCAN_FAILED)
                    if line.startswith("#CHROM\t"):
                        columns = line.rstrip("\r\n").split("\t")[9:]
                    continue
                if columns is None:
                    raise AncestryInputError(SCAN_FAILED)
                head = line.split("\t", 2)
                if len(head) < 3:
                    continue
                chrom = normalize_chromosome(head[0])
                positions = by_chrom.get(chrom)
                if not positions:
                    continue
                pos = int(head[1])
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) < 10:
                    continue
                end_match = _END.search(fields[7])
                end = int(end_match.group(1)) if end_match else pos
                first = bisect.bisect_left(positions, pos)
                if first >= len(positions) or positions[first] > end:
                    continue
                filters = [] if fields[6] in ("", ".") else [fields[6]]
                fmt = fields[8].split(":")
                gt_at = fmt.index("GT") if "GT" in fmt else None
                ft_at = fmt.index("FT") if "FT" in fmt else None
                calls = [value.split(":") for value in fields[9:]]
                gts = [call[gt_at] if gt_at is not None and gt_at < len(call) else "." for call in calls]
                call_filters = [[call[ft_at]] if ft_at is not None and ft_at < len(call)
                                and call[ft_at] not in ("", ".") else [] for call in calls]
                alts = [a for a in fields[4].split(",") if a not in _GVCF_ALTS]
                index = first
                while index < len(positions) and positions[index] <= end:
                    marker = markers[(chrom, positions[index])]
                    if end_match and not alts:
                        # A reference block: the marker's own alleles, the block's genotypes. A
                        # block has no filter to pass (FILTER=.): it passes; a failed one does not.
                        ref, alt = marker.ref, marker.alt
                        rows.append([chrom, positions[index], ref, alt, filters or ["PASS"], gts, call_filters])
                        index += 1
                        continue
                    elif positions[index] == pos:
                        ref, alt = fields[3], ",".join(alts) or "."
                    else:
                        index += 1
                        continue
                    rows.append([chrom, positions[index], ref, alt, filters, gts, call_filters])
                    index += 1
            digest, size = hashing.digest.hexdigest(), hashing.size
    except (OSError, EOFError, zlib.error, ValueError, IndexError) as exc:
        if isinstance(exc, AncestryInputError):
            raise
        raise AncestryInputError(SCAN_FAILED) from exc
    if columns is None:
        raise AncestryInputError(SCAN_FAILED)
    return {"sha256": digest, "size": size, "columns": columns, "rows": rows}


@asynccontextmanager
async def package_file(package_path: str, relative_path: str) -> AsyncIterator[Path]:
    """A declared file on local disk while the block runs: from its package folder, or from a
    gs:// or s3:// package staged to a temporary folder."""
    if "://" not in package_path:
        await asyncio.to_thread(_ensure_authorized_package_path, Path(package_path))
    async with staged_package_source_async(package_path) as staged:
        path = await asyncio.to_thread(_resolve_package_path, Path(staged.root), relative_path)
        if path is None:
            raise AncestryInputError(SCAN_FAILED)
        yield path


async def scan_next_file(session: AsyncSession, reference: AncestryReference) -> bool:
    """Scan the oldest declared file without a scan against this reference, if any.

    An advisory lock on the file, held until the scan is stored, keeps another worker off it (a
    row lock would need the UPDATE privilege ``coga_app`` has not on an append-only table); a
    worker that stopped leaves it to be scanned again.
    """
    waiting = (await session.execute(text("""
        SELECT f.id::text, f.package_path, f.relative_path FROM ancestry_files f
        WHERE NOT EXISTS (SELECT 1 FROM ancestry_file_scans s
                          WHERE s.file_id = f.id AND s.reference_sha256 = :reference)
        ORDER BY f.declared_at, f.id LIMIT 20
    """), {"reference": reference.public.sha256})).mappings().all()
    row = None
    for candidate in waiting:
        if not (await session.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                                      {"key": f"ancestry-file-scan:{candidate['id']}"})).scalar_one():
            continue
        # Another worker may have stored its scan between the list and the lock.
        if (await session.execute(text("""
            SELECT 1 FROM ancestry_file_scans WHERE file_id = CAST(:file AS uuid) AND reference_sha256 = :reference
        """), {"file": candidate["id"], "reference": reference.public.sha256})).first() is None:
            row = candidate
            break
    if row is None:
        await session.rollback()
        return False
    try:
        async with package_file(row["package_path"], row["relative_path"]) as path:
            result = await asyncio.to_thread(scan_vcf_markers, path, reference)
        values = {"status": "scanned", "sha256": result["sha256"], "size": result["size"],
                  "columns": json.dumps(result["columns"]), "rows": json.dumps(result["rows"]), "error": None}
    except Exception as exc:  # noqa: BLE001 - recorded with a fixed reason; no values logged
        logger.warning("Ancestry file scan failed: %s", describe_error(exc))
        values = {"status": "failed", "sha256": None, "size": None, "columns": "[]", "rows": "[]",
                  "error": SCAN_FAILED}
    await session.execute(text("""
        INSERT INTO ancestry_file_scans(file_id, status, sha256, file_size, columns, rows, error, reference_sha256)
        VALUES (CAST(:file AS uuid), :status, :sha256, :size, CAST(:columns AS jsonb), CAST(:rows AS jsonb),
                :error, :reference)
    """), {"file": row["id"], "reference": reference.public.sha256, **values})
    await session.commit()
    return True


async def family_files(session: AsyncSession, family_uuid: str, project: str) -> list[AncestryFileOut]:
    rows = (await session.execute(text(_LATEST_SCANS), {"family": family_uuid, "project": project})).mappings().all()
    return [AncestryFileOut(id=row["file_id"], label=row["label"], relative_path=row["relative_path"],
                            declared_at=row["declared_at"], status=row["status"] or "pending",
                            sha256=row["sha256"], error=row["error"], scanned_at=row["scanned_at"],
                            columns=row["columns"] if isinstance(row["columns"], list) else [])
            for row in rows]


async def file_rows(session: AsyncSession, family_uuid: str, project: str) -> tuple[list[MarkerRow], dict[str, str]]:
    """The scanned files' calls at the markers as callset rows (``file:<id>``), and their labels."""
    rows: list[MarkerRow] = []
    labels: dict[str, str] = {}
    for row in (await session.execute(text(_LATEST_SCANS), {"family": family_uuid, "project": project})).mappings().all():
        if row["status"] != "scanned" or not isinstance(row["rows"], list):
            continue
        callset = f"file:{row['file_id']}"
        labels[callset] = f"VCF file: {row['label']}"
        samples = tuple(str(column) for column in row["columns"] or [])
        for chrom, pos, ref, alt, filters, gts, call_filters in row["rows"]:
            rows.append(MarkerRow(callset=callset, chrom=str(chrom), pos=int(pos), ref=str(ref), alt=str(alt),
                                  filters=tuple(filters), samples=samples, gts=tuple(gts),
                                  call_filters=tuple(tuple(v) for v in call_filters)))
    return rows, labels
