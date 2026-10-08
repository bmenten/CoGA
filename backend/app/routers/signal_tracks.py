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
  recorded on the sample, and that is what is served.
* **Range requests matter.** A depth bigWig is ~19 MB and a MAF bigWig ~143 MB;
  IGV fetches slices by byte range. Starlette's ``FileResponse`` honours ``Range``,
  which is also how the CRAM endpoints work.

In remote mode (STORAGE_BACKEND=gcs/s3) the files are served from the object store
through signed URLs, as the alignments are: the staging copy of a package imported
from a bucket is deleted after the import. The location the import recorded (``uris``)
comes first, under the CRAM endpoint's rules (an object with the file's own extension
under the configured import roots), so a tampered row can at most point at another file of
that kind there, never at any other object; the fallback is the package layout under the storage prefix,
``<prefix>/<family>/<recorded path>``, where the CRAM endpoint probes too. The manifest
hands out the signed URLs; the GET redirects to one; the HEAD answers the object's
size itself, since a URL signed for GET cannot be used for a HEAD.
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.object_storage import (
    configured_object_key,
    configured_object_uri,
    object_exists,
    object_key,
    presigned_get_url,
    remote_object_identity,
    storage_is_remote,
)
from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import SignalTrackManifestEntryOut
from ..services.family_package_source import within_remote_import_roots
from ..services.metadata_service import get_family_record
from ..services.access_control import CurrentUser

router = APIRouter(prefix="/signal-tracks", tags=["signal_tracks"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


# The signal files a sample may carry, and how each should be drawn. `kind` is the
# URL segment and the key recorded by the import.
#
# Autoscale rather than a fixed range: read depth is unbounded and sample-specific,
# and MAF is 0..0.5 by construction — a shared scale would flatten one or clip the
# other.
_TRACK_KINDS: dict[str, dict[str, Any]] = {
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


def _family_package_root(family_id: str) -> Path:
    return DATA_DIR / "families" / family_id


def _within_data_dir(path: Path) -> bool:
    """Reject a candidate that resolves outside the data directory.

    The recorded path is package-relative and written by the import, but it reaches
    here through the database and is joined with a `family_id` from the URL; the
    containment check is what makes that safe regardless.
    """

    try:
        path.resolve().relative_to(DATA_DIR.resolve())
    except (ValueError, OSError):
        return False
    return True


async def _accessible_sample_ids(
    session: AsyncSession, family_id: str, user: CurrentUser
) -> list[str]:
    family = await get_family_record(session, family_id, user)
    return [member.sample_id for member in family.members]


async def _signal_track_rows(
    session: AsyncSession, sample_ids: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    """``(sample_id, metadata['signal_tracks'])`` for the samples that have one."""

    if not sample_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT sample_id, metadata -> 'signal_tracks' AS signal_tracks
            FROM samples
            WHERE sample_id = ANY(:sample_ids)
              AND metadata ? 'signal_tracks'
            """
        ),
        {"sample_ids": sample_ids},
    )
    return [
        (str(sample_id), signal_tracks)
        for sample_id, signal_tracks in result.all()
        if isinstance(signal_tracks, dict)
    ]


def _track_paths(rows: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, dict[str, str]]]:
    """``sample_id -> source -> kind -> package-relative path``, as the import left it."""

    recorded: dict[str, dict[str, dict[str, str]]] = {}
    for sample_id, signal_tracks in rows:
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


def _track_uris(rows: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    """``sample_id -> source -> kind -> recorded object URI``, for a package imported from
    a bucket. The values are data from the database and are checked before any use."""

    recorded: dict[str, dict[str, dict[str, Any]]] = {}
    for sample_id, signal_tracks in rows:
        by_source: dict[str, dict[str, Any]] = {}
        for source, entry in signal_tracks.items():
            uris = entry.get("uris") if isinstance(entry, dict) else None
            if not isinstance(uris, dict):
                continue
            kinds = {str(kind): uri for kind, uri in uris.items() if kind in _TRACK_KINDS}
            if kinds:
                by_source[str(source)] = kinds
        if by_source:
            recorded[sample_id] = by_source
    return recorded


def _resolve_track_path(family_id: str, relative_path: str) -> Path | None:
    """The file for a recorded path, or ``None`` if it is gone or out of bounds."""

    if not relative_path:
        return None
    candidate = _family_package_root(family_id) / relative_path
    if not _within_data_dir(candidate):
        return None
    return candidate if candidate.is_file() else None


# ---------------------------------------------------------------------------
# Remote mode (STORAGE_BACKEND=gcs/s3)
# ---------------------------------------------------------------------------


def _has_kind_extension(name: str, kind: str) -> bool:
    extensions = tuple(extension.lower() for extension in _TRACK_KINDS[kind]["extensions"])
    return name.lower().endswith(extensions)


def _recorded_track_key(uri: object, kind: str) -> str | None:
    """The key of a recorded object when it may be served, else ``None``.

    The CRAM endpoint's rules (routers/cram.py): an object in the configured bucket
    whose URI holds nothing but scheme, bucket and a key without empty, ``.`` or ``..``
    segments (``configured_object_key``), below a remote FAMILY_IMPORT_ROOTS entry --
    and with an extension of the track's kind.
    """
    key = configured_object_key(uri)
    if key is None or not _has_kind_extension(key, kind) or not within_remote_import_roots(str(uri)):
        return None
    return key


def _probed_track_key(family_id: str, relative_path: str | None, kind: str) -> str | None:
    """The package layout under the storage prefix, ``<prefix>/<family>/<recorded path>``,
    for a row that recorded no usable URI. The recorded path must be relative with no
    empty, ``.`` or ``..`` segment, so the key stays in the family's folder."""
    if not relative_path or not _has_kind_extension(relative_path, kind):
        return None
    for value in (family_id, relative_path):
        if any(segment in {"", ".", ".."} for segment in value.split("/")):
            return None
    return object_key(family_id, relative_path)


def _remote_track_key(family_id: str, kind: str, relative_path: str | None, uri: object) -> str | None:
    """The object to serve for a recorded track: the recorded location when it passes
    the checks and the object is there, else the layout probe; ``None`` if neither."""
    for key in (_recorded_track_key(uri, kind), _probed_track_key(family_id, relative_path, kind)):
        if key is not None and object_exists(key):
            return key
    return None


def _track_url(
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    relative_path: str | None,
    uri: object,
) -> str | None:
    """Where the browser fetches a track, or ``None`` when there is nothing to serve: a
    signed URL of its object in remote mode, else the backend route to the file in the
    data directory. Blocking (store requests, file checks)."""
    if storage_is_remote():
        key = _remote_track_key(family_id, kind, relative_path, uri)
        return presigned_get_url(key, filename=PurePosixPath(key).name) if key else None
    if not relative_path or _resolve_track_path(family_id, relative_path) is None:
        return None
    return f"/signal-tracks/{family_id}/{sample_id}/{source}/{kind}"


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
    recorded_uris = _track_uris(rows) if storage_is_remote() else {}

    def _build() -> list[SignalTrackManifestEntryOut]:
        entries: list[SignalTrackManifestEntryOut] = []
        for sample_id in ordered:
            paths = recorded.get(sample_id, {})
            uris = recorded_uris.get(sample_id, {})
            for source in sorted(set(paths) | set(uris)):
                for kind, spec in _TRACK_KINDS.items():
                    url = _track_url(
                        family_id,
                        sample_id,
                        source,
                        kind,
                        paths.get(source, {}).get(kind),
                        uris.get(source, {}).get(kind),
                    )
                    if url is None:
                        continue
                    entries.append(
                        SignalTrackManifestEntryOut(
                            sample_id=sample_id,
                            source=source,
                            kind=kind,
                            name=f"{sample_id} {spec['label']}",
                            format=str(spec["format"]),
                            url=url,
                            min=spec.get("min"),
                            max=spec.get("max"),
                        )
                    )
        return entries

    # is_file() and store requests on every candidate are blocking; keep them off the
    # event loop.
    return await asyncio.to_thread(_build)


async def _requested_track(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    user: CurrentUser,
) -> tuple[str | None, object, dict[str, Any]]:
    """``(recorded path, recorded URI, kind)`` of a track the user may read, after the
    family-membership check; 404 when nothing is recorded for it."""
    if kind not in _TRACK_KINDS:
        raise HTTPException(status_code=404, detail="Unknown signal-track kind")
    family_sample_ids = await _accessible_sample_ids(session, family_id, user)
    if sample_id not in family_sample_ids:
        raise HTTPException(status_code=404, detail="Sample not found in family")
    rows = await _signal_track_rows(session, [sample_id])
    relative_path = _track_paths(rows).get(sample_id, {}).get(source, {}).get(kind)
    uri = _track_uris(rows).get(sample_id, {}).get(source, {}).get(kind) if storage_is_remote() else None
    if not relative_path and uri is None:
        raise HTTPException(status_code=404, detail="Signal track not found")
    return relative_path, uri, _TRACK_KINDS[kind]


async def _resolve_requested_track(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    user: CurrentUser,
) -> tuple[Path, dict[str, Any]]:
    relative_path, _uri, spec = await _requested_track(session, family_id, sample_id, source, kind, user)
    if not relative_path:
        raise HTTPException(status_code=404, detail="Signal track not found")
    path = await asyncio.to_thread(_resolve_track_path, family_id, relative_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Signal track file is missing")
    return path, spec


async def _resolve_requested_object(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    user: CurrentUser,
) -> tuple[str, dict[str, Any]]:
    """Remote mode: the key of the object to serve for a track the user may read."""
    relative_path, uri, spec = await _requested_track(session, family_id, sample_id, source, kind, user)
    key = await asyncio.to_thread(_remote_track_key, family_id, kind, relative_path, uri)
    if key is None:
        raise HTTPException(status_code=404, detail="Signal track file is missing")
    return key, spec


@router.get("/{family_id}/{sample_id}/{source}/{kind}")
async def get_signal_track(
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    if storage_is_remote():
        key, _spec = await _resolve_requested_object(session, family_id, sample_id, source, kind, user)
        # IGV follows the redirect and reads byte ranges straight from the store.
        url = await asyncio.to_thread(presigned_get_url, key, filename=PurePosixPath(key).name)
        return RedirectResponse(url, status_code=302)
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
    if storage_is_remote():
        key, spec = await _resolve_requested_object(session, family_id, sample_id, source, kind, user)
        identity = await asyncio.to_thread(remote_object_identity, configured_object_uri(key))
        if identity is None or identity.get("size") is None:
            raise HTTPException(status_code=404, detail="Signal track file is missing")
        size = int(identity["size"])
    else:
        path, spec = await _resolve_requested_track(session, family_id, sample_id, source, kind, user)
        size = await asyncio.to_thread(lambda: path.stat().st_size)
    return Response(
        status_code=200,
        media_type=str(spec["media_type"]),
        headers={"content-length": str(size), "accept-ranges": "bytes"},
    )
