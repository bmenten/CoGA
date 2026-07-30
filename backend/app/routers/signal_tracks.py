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
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import SignalTrackManifestEntryOut
from ..services.metadata_service import CurrentUser, get_family_record

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

TrackKind = Literal["depth_bigwig", "maf_bigwig", "copy_number_bedgraph"]


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


async def _recorded_signal_tracks(
    session: AsyncSession, sample_ids: list[str]
) -> dict[str, dict[str, dict[str, str]]]:
    """``sample_id -> source -> kind -> package-relative path``, as the import left it."""

    if not sample_ids:
        return {}
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
    recorded: dict[str, dict[str, dict[str, str]]] = {}
    for sample_id, signal_tracks in result.all():
        if not isinstance(signal_tracks, dict):
            continue
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
            recorded[str(sample_id)] = pruned
    return recorded


def _resolve_track_path(family_id: str, relative_path: str) -> Path | None:
    """The file for a recorded path, or ``None`` if it is gone or out of bounds."""

    if not relative_path:
        return None
    candidate = _family_package_root(family_id) / relative_path
    if not _within_data_dir(candidate):
        return None
    return candidate if candidate.is_file() else None


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
    recorded = await _recorded_signal_tracks(session, ordered)

    def _build() -> list[SignalTrackManifestEntryOut]:
        entries: list[SignalTrackManifestEntryOut] = []
        for sample_id in ordered:
            for source in sorted(recorded.get(sample_id, {})):
                kinds = recorded[sample_id][source]
                for kind, spec in _TRACK_KINDS.items():
                    relative_path = kinds.get(kind)
                    if not relative_path or _resolve_track_path(family_id, relative_path) is None:
                        continue
                    entries.append(
                        SignalTrackManifestEntryOut(
                            sample_id=sample_id,
                            source=source,
                            kind=kind,
                            name=f"{sample_id} {spec['label']}",
                            format=str(spec["format"]),
                            url=f"/signal-tracks/{family_id}/{sample_id}/{source}/{kind}",
                            min=spec.get("min"),
                            max=spec.get("max"),
                        )
                    )
        return entries

    # is_file() on every candidate is blocking; keep it off the event loop.
    return await asyncio.to_thread(_build)


async def _resolve_requested_track(
    session: AsyncSession,
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    user: CurrentUser,
) -> tuple[Path, dict[str, Any]]:
    if kind not in _TRACK_KINDS:
        raise HTTPException(status_code=404, detail="Unknown signal-track kind")
    family_sample_ids = await _accessible_sample_ids(session, family_id, user)
    if sample_id not in family_sample_ids:
        raise HTTPException(status_code=404, detail="Sample not found in family")
    recorded = await _recorded_signal_tracks(session, [sample_id])
    relative_path = recorded.get(sample_id, {}).get(source, {}).get(kind)
    if not relative_path:
        raise HTTPException(status_code=404, detail="Signal track not found")
    path = await asyncio.to_thread(_resolve_track_path, family_id, relative_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Signal track file is missing")
    return path, _TRACK_KINDS[kind]


@router.get("/{family_id}/{sample_id}/{source}/{kind}")
async def get_signal_track(
    family_id: str,
    sample_id: str,
    source: str,
    kind: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> FileResponse:
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
    path, spec = await _resolve_requested_track(session, family_id, sample_id, source, kind, user)
    # A HEAD must answer with the headers the GET would send, minus the body: a
    # client sizing the file before it starts ranging would otherwise read
    # `Content-Length: 0` and conclude there is nothing to fetch.
    size = await asyncio.to_thread(lambda: path.stat().st_size)
    return Response(
        status_code=200,
        media_type=str(spec["media_type"]),
        headers={"content-length": str(size), "accept-ranges": "bytes"},
    )
