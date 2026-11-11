"""Serving CNV caller signal files (bigWig, bedGraph) to the genome browser.

These are the files a depth caller ships alongside its calls: per-bin read depth,
minor allele fraction, and the called copy number. CoGA already imports binned
copies of them into ClickHouse for its own coverage/APCAD tracks, but IGV wants
the files themselves — it does its own windowing, and for read depth it wants the
absolute values, where the imported copy is a log2 ratio against the sample's
baseline.

Two things differ from the alignment endpoints next door:

* **Paths come from the import, not from a naming convention.** A CRAM is always
  ``<sample>.cram``; a HiFiCNV bigWig is named after the caller's own run
  (``HG002.Sample0.depth.bw``, ``HG002.HG002.maf.bw``). What the import found is
  recorded on the sample, and that is what is served. The recorded path is looked up in
  the package folder the family's import read, below an import root (a package read where
  it lies, on a mounted share or bucket too, keeps its files there), then in
  ``<data>/families/<family>``, and only inside that folder, as the alignments are: one
  that leads out (an edited row holding ``..`` or an absolute path, a symlink into another
  family's folder) names no file (``core/family_folders.py``).
* **Range requests matter.** A depth bigWig is ~19 MB and a MAF bigWig ~143 MB;
  IGV fetches slices by byte range. Starlette's ``FileResponse`` honours ``Range``,
  which is also how the CRAM endpoints work.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.family_folders import PACKAGES_FOLDER, family_folders, path_in_family_folder
from ..core.postgres import get_postgres_session
from ..core.url_path import url_path
from ..dependencies import get_current_user
from ..schemas import SignalTrackManifestEntryOut
from ..services.family_package_source import local_family_package_folder
from ..services.metadata_service import get_family_record
from ..services.access_control import CurrentUser

router = APIRouter(prefix="/signal-tracks", tags=["signal_tracks"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


class _TrackKind(TypedDict):
    """How a signal file of one kind is drawn (its manifest entry) and served."""

    label: str
    format: Literal["bigwig", "bedgraph"]
    media_type: str
    extensions: tuple[str, ...]
    min: NotRequired[float]
    max: NotRequired[float]


# The signal files a sample may carry, and how each should be drawn. `kind` is the
# URL segment and the key recorded by the import.
#
# Autoscale rather than a fixed range: read depth is unbounded and sample-specific,
# and MAF is 0..0.5 by construction — a shared scale would flatten one or clip the
# other.
_TRACK_KINDS: dict[str, _TrackKind] = {
    "depth_bigwig": {
        "label": "Read depth",
        "format": "bigwig",
        "media_type": "application/octet-stream",
        "extensions": (".bw", ".bigwig"),
    },
    "maf_bigwig": {
        "label": "Minor allele fraction",
        "format": "bigwig",
        "media_type": "application/octet-stream",
        # 0..0.5 by construction; a fixed range keeps the band structure readable
        # instead of rescaling with whatever is in view.
        "min": 0.0,
        "max": 0.5,
        "extensions": (".bw", ".bigwig"),
    },
    "copy_number_bedgraph": {
        "label": "Copy number",
        "format": "bedgraph",
        "media_type": "text/plain",
        "extensions": (".bedgraph", ".bedGraph", ".bg"),
    },
}


async def _accessible_sample_ids(
    session: AsyncSession, family_id: str, user: CurrentUser
) -> list[str]:
    family = await get_family_record(session, family_id, user)
    return [member.sample_id for member in family.members]


async def _signal_track_rows(
    session: AsyncSession, sample_ids: list[str]
) -> list[tuple[str, dict[str, Any], str | None]]:
    """``(sample_id, metadata['signal_tracks'], package folder)`` for the samples that have
    one. The package folder is the folder the sample's family's import read
    (``families.metadata.package_import.folder_path``), where a recorded path is looked up."""

    if not sample_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT s.sample_id,
                   s.metadata -> 'signal_tracks' AS signal_tracks,
                   f.metadata -> 'package_import' ->> 'folder_path' AS package_folder
            FROM samples s
            JOIN families f ON f.id = s.family_id
            WHERE s.sample_id = ANY(:sample_ids)
              AND s.metadata ? 'signal_tracks'
            """
        ),
        {"sample_ids": sample_ids},
    )
    return [
        (str(sample_id), signal_tracks, package_folder if isinstance(package_folder, str) else None)
        for sample_id, signal_tracks, package_folder in result.all()
        if isinstance(signal_tracks, dict)
    ]


def _track_paths(rows: list[tuple[str, dict[str, Any], str | None]]) -> dict[str, dict[str, dict[str, str]]]:
    """``sample_id -> source -> kind -> package-relative path``, as the import left it."""

    recorded: dict[str, dict[str, dict[str, str]]] = {}
    for sample_id, signal_tracks, _package_folder in rows:
        by_source = {
            str(source): {
                str(kind): str(path)
                for kind, path in entry.items()
                if kind in _TRACK_KINDS and isinstance(path, str) and path
            }
            for source, entry in signal_tracks.items()
            if isinstance(entry, dict)
        }
        pruned = {source: kinds for source, kinds in by_source.items() if kinds}
        if pruned:
            recorded[sample_id] = pruned
    return recorded


def _resolve_track_path(family_id: str, relative_path: str, package_folder: object = None) -> Path | None:
    """The file a recorded path names in the package folder the family's import read,
    ``package_folder`` (``families.metadata.package_import.folder_path``), else in its package
    folder ``<data>/families/<family>``; ``None`` when it is gone or lies outside them.

    The import records the path relative to the package, but it comes back from
    ``samples.metadata`` as data: an edited row can hold ``..`` or an absolute path. It is
    joined to the package folder and normalised as a string, and one that leaves that folder
    (``../F2/cnv/S2.depth.bw``, an absolute path elsewhere) is dropped before anything is
    looked up by it. One that stays is kept only when its real path lies inside that folder
    (the imported one) or one of the family's own folders, so a symlink cannot lead into
    another family's folder or out of the data directory; a family ID that is no single
    folder name has no folder (``core/family_folders.py``). The imported folder is used only
    when it lies below an import root (``local_family_package_folder``): a package on a
    mounted share or bucket is read where it lies.
    """

    if not relative_path:
        return None
    imported = local_family_package_folder(package_folder)
    if imported is not None:
        path = path_in_family_folder(imported, [imported], relative_path)
        if path is not None and os.path.isfile(path):
            return Path(path)
    folders = family_folders(DATA_DIR, family_id)
    package = folders.get(PACKAGES_FOLDER)
    if package is None:
        return None
    path = path_in_family_folder(package, folders.values(), relative_path)
    if path is None or not os.path.isfile(path):
        return None
    return Path(path)


def _track_url(
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    relative_path: str | None,
    package_folder: object = None,
) -> str | None:
    """Where the browser fetches a track, or ``None`` when there is nothing to serve: the
    backend route to the file in the package folder, each value percent-encoded as one
    segment (an ID may hold `?`, `#`, `%` or `..`). Blocking (file checks)."""
    if not relative_path or _resolve_track_path(family_id, relative_path, package_folder) is None:
        return None
    return url_path("signal-tracks", family_id, sample_id, source, kind)


@router.get("/{family_id}/manifest", response_model=list[SignalTrackManifestEntryOut])
async def get_signal_track_manifest(
    family_id: str,
    sample_ids: list[str] = Query(default_factory=list, alias="sample"),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> list[SignalTrackManifestEntryOut]:
    """The signal tracks the browser can draw, in a stable per-sample order.

    Only files that are actually present are listed: a manifest entry for a missing
    file makes the browser show a broken track rather than no track.
    """

    family_sample_ids = await _accessible_sample_ids(session, family_id, user)
    requested = [s for s in sample_ids if s in family_sample_ids] or family_sample_ids
    # Preserve request order, drop duplicates.
    ordered = list(dict.fromkeys(requested))
    rows = await _signal_track_rows(session, ordered)
    recorded = _track_paths(rows)
    package_folders = {sample_id: package_folder for sample_id, _tracks, package_folder in rows}

    def _build() -> list[SignalTrackManifestEntryOut]:
        entries: list[SignalTrackManifestEntryOut] = []
        for sample_id in ordered:
            paths = recorded.get(sample_id, {})
            for source in sorted(paths):
                for kind, spec in _TRACK_KINDS.items():
                    url = _track_url(
                        family_id,
                        sample_id,
                        source,
                        kind,
                        paths.get(source, {}).get(kind),
                        package_folders.get(sample_id),
                    )
                    if url is None:
                        continue
                    entries.append(
                        SignalTrackManifestEntryOut(
                            sample_id=sample_id,
                            source=source,
                            kind=kind,
                            name=f"{sample_id} {spec['label']}",
                            format=spec["format"],
                            url=url,
                            min=spec.get("min"),
                            max=spec.get("max"),
                        )
                    )
        return entries

    # is_file() on every candidate is blocking (a share or a mounted bucket answers slowly);
    # keep it off the event loop.
    return await asyncio.to_thread(_build)


async def _requested_track(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    user: CurrentUser,
) -> tuple[str, _TrackKind, str | None]:
    """``(recorded path, kind, package folder)`` of a track the user may read,
    after the family-membership check; 404 when nothing is recorded for it. The package folder
    is the one the family's import read (``_signal_track_rows``)."""
    if kind not in _TRACK_KINDS:
        raise HTTPException(status_code=404, detail="Unknown signal-track kind")
    family_sample_ids = await _accessible_sample_ids(session, family_id, user)
    if sample_id not in family_sample_ids:
        raise HTTPException(status_code=404, detail="Sample not found in family")
    rows = await _signal_track_rows(session, [sample_id])
    relative_path = _track_paths(rows).get(sample_id, {}).get(source, {}).get(kind)
    if not relative_path:
        raise HTTPException(status_code=404, detail="Signal track not found")
    package_folder = next((folder for _sample, _tracks, folder in rows), None)
    return relative_path, _TRACK_KINDS[kind], package_folder


async def _resolve_requested_track(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    user: CurrentUser,
) -> tuple[Path, _TrackKind]:
    relative_path, spec, package_folder = await _requested_track(
        session, family_id, sample_id, source, kind, user
    )
    path = await asyncio.to_thread(_resolve_track_path, family_id, relative_path, package_folder)
    if path is None:
        raise HTTPException(status_code=404, detail="Signal track file is missing")
    return path, spec


@router.get("/{family_id}/{sample_id}/{source}/{kind}")
async def get_signal_track(
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    path, spec = await _resolve_requested_track(session, family_id, sample_id, source, kind, user)
    # FileResponse honours Range, which is the whole point: IGV pulls byte slices
    # out of a 143 MB MAF bigWig rather than downloading it.
    return FileResponse(path, media_type=str(spec["media_type"]), filename=path.name)


@router.head("/{family_id}/{sample_id}/{source}/{kind}")
async def head_signal_track(
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    # A HEAD must answer with the headers the GET would send, minus the body: a
    # client sizing the file before it starts ranging would otherwise read
    # `Content-Length: 0` and conclude there is nothing to fetch.
    path, spec = await _resolve_requested_track(session, family_id, sample_id, source, kind, user)
    size = await asyncio.to_thread(lambda: path.stat().st_size)
    return Response(
        status_code=200,
        media_type=str(spec["media_type"]),
        headers={"content-length": str(size), "accept-ranges": "bytes"},
    )
