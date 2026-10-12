"""The URLs the backend hands out carry each family and sample ID as one encoded segment (SEC-2).

A family or sample ID is printable text without spaces (``family_identifiers.py``), so it may
hold ``/``, ``?``, ``#``, ``%`` or ``..``. The alignment and signal-track manifests and the
QC-report link give the browser a URL built from IDs. With the IDs put in as they were, ``?``
and ``#`` cut the path short, ``%41`` reached the server as ``A`` (another family's sample),
and a sample ID such as ``../F2/S2`` let the client resolve the request to another family's
file (the class of #521). Each value is now percent-encoded as one path segment
(``core/url_path.py``), as the frontend's ``apiPath`` does.

The URLs are read as a browser reads them: ``?`` starts the query, ``#`` the fragment, dot
segments are resolved, and the server decodes the path once. The requests through the app
are made the same way (``httpx`` on the ASGI app decodes the path once, as uvicorn does; the
``TestClient`` would decode it twice).

Two kinds of ID still cannot be reached through a path segment, whatever the encoding: one
that is exactly ``.`` or ``..`` (a browser resolves ``%2E%2E`` too), and one holding ``/``,
since the server matches its routes on the decoded path. Such a request answers 404 rather
than reaching another resource; whether the ID rule should refuse them is the owner's call
(SEC-15). All IDs and files are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, quote, unquote

from fastapi import HTTPException
import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.core.url_path import url_path
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import cram, family_qc_reports, signal_tracks
from backend.app.services.access_control import CurrentUser

ORIGIN = "http://coga.test"

# IDs holding each character the ID rule allows that has a meaning in a URL, as
# (family, sample, source).
UNSAFE_IDS = {
    "slash": ("F/1", "S/1", "caller/1"),
    "question mark": ("F?1", "S?1", "caller?1"),
    "hash": ("F#1", "S#1", "caller#1"),
    "percent": ("F%1", "S%1", "caller%1"),
    "percent escape": ("F%41", "S%41", "caller%41"),
    "dot dot and slash": ("../F2", "../F2/S2", "../caller"),
    "dot dot": ("F..1", "..S1", "caller.."),
}


def _requested(url: str) -> tuple[list[str], str]:
    """What a browser asks for when the API hands it ``url``: the path segments the server
    reads (each decoded once) and the query string."""
    target = httpx.URL(f"{ORIGIN}/api{url}")
    raw_path = target.raw_path.split(b"?", 1)[0].decode("ascii")
    return [unquote(segment) for segment in raw_path.split("/")[1:]], target.query.decode("ascii")


# --------------------------------------------------------------------------- #
# The helper
# --------------------------------------------------------------------------- #


def test_url_path_encodes_every_value_as_one_segment() -> None:
    # The vectors of the frontend's apiPath test, which the helper mirrors.
    assert url_path("admin", "samples", "../families/F1") == "/admin/samples/..%2Ffamilies%2FF1"
    assert url_path("families", "F 1?x=1#y", "hpo") == "/families/F%201%3Fx%3D1%23y/hpo"
    # A percent sign is itself encoded, so the server decodes the ID, not what follows it.
    assert url_path("families", "F%41") == "/families/F%2541"
    # An ordinary ID is unchanged.
    assert url_path("cram", "FAM-001", "S1.cram") == "/cram/FAM-001/S1.cram"


@pytest.mark.parametrize("family_id, sample_id, source", UNSAFE_IDS.values(), ids=UNSAFE_IDS.keys())
def test_url_path_values_come_back_as_they_went_in(family_id: str, sample_id: str, source: str) -> None:
    assert _requested(url_path(family_id, sample_id, source)) == (["api", family_id, sample_id, source], "")


# --------------------------------------------------------------------------- #
# The URLs the routers build
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("family_id, sample_id, _source", UNSAFE_IDS.values(), ids=UNSAFE_IDS.keys())
def test_the_alignment_manifest_names_the_family_and_sample_it_was_issued_for(
    monkeypatch: pytest.MonkeyPatch, family_id: str, sample_id: str, _source: str
) -> None:
    monkeypatch.setattr(cram, "storage_is_remote", lambda: False)
    monkeypatch.setattr(cram, "_alignment_exists", lambda family, sample, ext, suffix="": ext == "cram")

    entry = cram._resolve_alignment_manifest_entry(family_id, sample_id)

    assert entry is not None
    assert _requested(entry.url) == (["api", "cram", family_id, f"{sample_id}.cram"], "")
    assert _requested(entry.index_url) == (["api", "cram", family_id, f"{sample_id}.cram.crai"], "")


@pytest.mark.parametrize("family_id, sample_id, source", UNSAFE_IDS.values(), ids=UNSAFE_IDS.keys())
def test_the_signal_track_manifest_names_the_family_sample_and_source_it_was_issued_for(
    monkeypatch: pytest.MonkeyPatch, family_id: str, sample_id: str, source: str
) -> None:
    monkeypatch.setattr(signal_tracks, "storage_is_remote", lambda: False)
    monkeypatch.setattr(signal_tracks, "_resolve_track_path", lambda family, relative: Path("depth.bw"))

    url = signal_tracks._track_url(family_id, sample_id, source, "depth_bigwig", "cnv/depth.bw", None)

    assert url is not None
    assert _requested(url) == (["api", "signal-tracks", family_id, sample_id, source, "depth_bigwig"], "")


@pytest.mark.asyncio
@pytest.mark.parametrize("family_id, sample_id, _source", UNSAFE_IDS.values(), ids=UNSAFE_IDS.keys())
async def test_the_qc_report_link_names_the_family_and_sample_it_was_issued_for(
    monkeypatch: pytest.MonkeyPatch, family_id: str, sample_id: str, _source: str
) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "secret_key", "x" * 40)

    async def recorded_report_path(session: Any, **_: Any) -> str:
        return "qc/report.html"

    monkeypatch.setattr(family_qc_reports, "_recorded_report_path", recorded_report_path)
    monkeypatch.setattr(family_qc_reports, "_resolve_report_file", lambda family, relative: Path("report.html"))

    link = await family_qc_reports.get_family_qc_report_link(family_id, sample_id, session=None, user=None)

    segments, query = _requested(link.url)
    assert segments == ["api", "families", family_id, "qc-report", sample_id]
    # The query holds the link token alone, and the token is the one for this sample.
    tokens = parse_qs(query, keep_blank_values=True)
    assert list(tokens) == ["token"] and len(tokens["token"]) == 1
    family_qc_reports._verify_qc_report_token(tokens["token"][0], family_id, sample_id)


# --------------------------------------------------------------------------- #
# Through the app: following a URL reaches the file it was issued for
# --------------------------------------------------------------------------- #


def _viewer() -> CurrentUser:
    return CurrentUser(
        id="user-viewer",
        username="viewer@example.com",
        email="viewer@example.com",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )


class _Families:
    """Families, their members and their files in a local data directory, served by the app.

    Every family is readable: what is checked is which file a URL reaches."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.members: dict[str, list[str]] = {}
        self.signal_tracks: dict[str, dict[str, dict[str, str]]] = {}
        self.reports: dict[tuple[str, str], str] = {}

    def add(self, family_id: str, sample_id: str, *, files: bool = True, source: str = "hificnv") -> None:
        self.members.setdefault(family_id, []).append(sample_id)
        if not files:
            return
        content = self.content(family_id, sample_id)
        alignments = self.root / family_id
        alignments.mkdir(parents=True, exist_ok=True)
        (alignments / f"{sample_id}.cram").write_bytes(content)
        (alignments / f"{sample_id}.cram.crai").write_bytes(content)
        package = self.root / "families" / family_id
        number = len(self.members[family_id])
        (package / "cnv").mkdir(parents=True, exist_ok=True)
        (package / "cnv" / f"depth{number}.bw").write_bytes(content)
        self.signal_tracks[sample_id] = {source: {"depth_bigwig": f"cnv/depth{number}.bw"}}
        (package / "qc").mkdir(parents=True, exist_ok=True)
        (package / "qc" / f"report{number}.html").write_bytes(content)
        self.reports[(family_id, sample_id)] = f"qc/report{number}.html"

    @staticmethod
    def content(family_id: str, sample_id: str) -> bytes:
        return f"{family_id} | {sample_id}".encode()


@pytest.fixture()
def families(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Families:
    """The app in local storage mode over a data directory in ``tmp_path``, a signed-in
    viewer, and the family lookups and recorded paths read from ``_Families``."""
    store = _Families(tmp_path)
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    for module in (cram, signal_tracks, family_qc_reports):
        monkeypatch.setattr(module, "DATA_DIR", tmp_path)

    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        if family_id not in store.members:
            raise HTTPException(status_code=404, detail="Family not found")
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample_id) for sample_id in store.members[family_id]])

    async def signal_track_rows(session: Any, sample_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        return [(sample_id, store.signal_tracks[sample_id]) for sample_id in sample_ids if sample_id in store.signal_tracks]

    async def recorded_report(session: Any, *, family_id: str, sample_id: str) -> str:
        if (family_id, sample_id) not in store.reports:
            raise HTTPException(status_code=404, detail="No QC report is recorded for this sample")
        return store.reports[(family_id, sample_id)]

    async def no_session():
        yield None

    async def a_viewer() -> CurrentUser:
        return _viewer()

    async def keep_audit_row(payload: Any) -> None:
        return None

    for module in (cram, signal_tracks, family_qc_reports):
        monkeypatch.setattr(module, "get_family_record", family_record)
    monkeypatch.setattr(signal_tracks, "_signal_track_rows", signal_track_rows)
    monkeypatch.setattr(family_qc_reports, "_recorded_report", recorded_report)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, a_viewer)
    return store


async def _manifest(client: httpx.AsyncClient, router: str, family_id: str, sample_id: str) -> list[dict[str, Any]]:
    # Asked for as the frontend asks (apiPath): the family ID one encoded segment.
    response = await client.get(f"/api/{router}/{quote(family_id, safe='')}/manifest", params={"sample": sample_id})
    assert response.status_code == 200, response.text
    return response.json()


async def _follow(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Fetch a URL the API handed out, as the browser does: under the API base, against the
    page's origin (``?`` starts the query, ``#`` the fragment, dot segments resolved)."""
    return await client.get(f"{ORIGIN}/api{url}")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


# The IDs of UNSAFE_IDS a path segment can carry once encoded: all but those holding `/`.
REACHABLE_IDS = {name: ids for name, ids in UNSAFE_IDS.items() if "/" not in "".join(ids)}


@pytest.mark.asyncio
@pytest.mark.parametrize("family_id, sample_id, source", REACHABLE_IDS.values(), ids=REACHABLE_IDS.keys())
async def test_following_the_manifests_and_the_qc_link_reaches_the_samples_own_files(
    families: _Families, family_id: str, sample_id: str, source: str
) -> None:
    families.add(family_id, sample_id, source=source)
    if unquote(family_id) != family_id:
        # Where `%41` landed once the server decoded it: another family's sample.
        families.add(unquote(family_id), unquote(sample_id), source=unquote(source))
    expected = families.content(family_id, sample_id)

    async with _client() as client:
        [alignment] = await _manifest(client, "cram", family_id, sample_id)
        [signal] = await _manifest(client, "signal-tracks", family_id, sample_id)
        link = await client.get(f"/api/families/{quote(family_id, safe='')}/qc-report/{quote(sample_id, safe='')}/link")
        assert link.status_code == 200, link.text
        fetched = {
            "reads": await _follow(client, alignment["url"]),
            "index": await _follow(client, alignment["index_url"]),
            "depth": await _follow(client, signal["url"]),
            # A browser navigation: no bearer token, the link's own token is the authorisation.
            "report": await _follow(client, link.json()["url"]),
        }

    assert {name: (response.status_code, response.content) for name, response in fetched.items()} == {
        name: (200, expected) for name in fetched
    }
    assert fetched["report"].headers["content-security-policy"] == "sandbox allow-scripts allow-popups"


@pytest.mark.asyncio
async def test_an_id_with_dot_dot_and_slash_never_reaches_another_familys_files(families: _Families) -> None:
    # Family F1 lists a sample whose ID climbs into family F2. Its manifest entries are
    # found (the file lookups join the ID into a path, which is only kept inside the data
    # directory), but following them must not fetch F2's reads or depth track under F1's
    # sample: the server reads the ID's `/` as a separator, and no route matches.
    families.add("F2", "S2")
    families.add("F1", "S1")
    families.add("F1", "../F2/S2", files=False)
    families.signal_tracks["../F2/S2"] = {"hificnv": {"depth_bigwig": "cnv/climb.bw"}}
    (families.root / "families" / "F1" / "cnv" / "climb.bw").write_bytes(b"F1 track")

    async with _client() as client:
        [alignment] = await _manifest(client, "cram", "F1", "../F2/S2")
        [signal] = await _manifest(client, "signal-tracks", "F1", "../F2/S2")
        fetched = [
            await _follow(client, alignment["url"]),
            await _follow(client, alignment["index_url"]),
            await _follow(client, signal["url"]),
        ]

    assert [(response.status_code, response.content) for response in fetched] == [
        (404, b'{"detail":"Not Found"}')
    ] * 3
