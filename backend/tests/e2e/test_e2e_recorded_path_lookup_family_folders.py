"""Signal tracks and QC reports are served only from their own family's folders (E2E).

With local storage the CNV caller's signal files and the sequencing-QC report are served from
the path the import recorded on the sample (``samples.metadata``: ``signal_tracks``,
``sequencing_qc.report``), relative to the family's package folder ``<data>/families/<family>``.
The signal-track lookup checked only that the path stayed inside the data directory, so an
edited row holding ``..`` or an absolute path served another family's track, and the manifest
listed it; the QC-report lookup followed a package folder linked to another family's package
and served that family's report (SEC-25).

Over the real API (an in-process ``httpx.ASGITransport`` client, one event loop, see
test_e2e_api_contract.py), with the families in Postgres made through the Family Builder and
the seeded admin signed in, and the recorded paths written into the samples' rows as an edited
row would hold them. The files lie in a data directory of the test's own, its packages folder
the import root (as deployed, ``/data/families``): F2 (sample S2) has its track and report in
its package; F1's S1 records its own; S3 records ``../<F2>/...``, S4 the absolute path of F2's
track and S5 a path through a folder of F1's package that is a link into F2's; F3's package
folder is a link to F2's, and its S6 records the name of F2's report.

* F1's signal-track manifest lists S1 alone (before: also S3, S4 and S5 with F2's track), its
  URL fetches S1's track, and S3, S4 and S5's tracks answer 404 to GET and HEAD (before: 200
  with F2's track); F2's manifest still lists S2, and its URL fetches F2's track;
* F1's S1 gets a QC-report link that opens its report; S3's report and F3's S6's answer 404
  (before: 400 for S3, and 200 with F2's report for S6); F2's S2 still opens its report.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The IDs
and files are synthetic, and the families are dropped at the end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

KIND = "depth_bigwig"


def _content(family: str, sample: str, what: str) -> bytes:
    return f"{what} of {family} | {sample}".encode()


def _place(data: Path, relative: str, content: bytes) -> None:
    path = data / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def _exercise(run: str, data: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.routers import family_qc_reports, signal_tracks
    from backend.tests.e2e import _harness

    f1, f2, f3 = f"RECPATH_{run}_F1", f"RECPATH_{run}_F2", f"RECPATH_{run}_F3"
    s1, s2, s3, s4, s5, s6 = (f"RECPATH_{run}_S{number}" for number in range(1, 7))

    _place(data, f"families/{f2}/cnv/{s2}.depth.bw", _content(f2, s2, "track"))
    _place(data, f"families/{f2}/qc/{s2}.html", _content(f2, s2, "report"))
    _place(data, f"families/{f1}/cnv/{s1}.depth.bw", _content(f1, s1, "track"))
    _place(data, f"families/{f1}/qc/{s1}.html", _content(f1, s1, "report"))
    (data / "families" / f1 / "linked").symlink_to(data / "families" / f2 / "cnv")
    (data / "families" / f3).symlink_to(data / "families" / f2)

    # What the samples' rows record, as an edited row could hold it for S3, S4, S5 and S6.
    recorded: dict[str, dict[str, Any]] = {
        s2: {"signal_tracks": {"hificnv": {KIND: f"cnv/{s2}.depth.bw"}}, "sequencing_qc": {"report": f"qc/{s2}.html"}},
        s1: {"signal_tracks": {"hificnv": {KIND: f"cnv/{s1}.depth.bw"}}, "sequencing_qc": {"report": f"qc/{s1}.html"}},
        s3: {
            "signal_tracks": {"hificnv": {KIND: f"../{f2}/cnv/{s2}.depth.bw"}},
            "sequencing_qc": {"report": f"../{f2}/qc/{s2}.html"},
        },
        s4: {"signal_tracks": {"hificnv": {KIND: str(data / "families" / f2 / "cnv" / f"{s2}.depth.bw")}}},
        s5: {"signal_tracks": {"hificnv": {KIND: f"linked/{s2}.depth.bw"}}},
        s6: {"sequencing_qc": {"report": f"qc/{s2}.html"}},
    }

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    out: dict[str, Any] = {}
    saved = signal_tracks.DATA_DIR, family_qc_reports.DATA_DIR, settings.storage_backend, settings.family_import_roots
    signal_tracks.DATA_DIR = family_qc_reports.DATA_DIR = data
    settings.storage_backend, settings.family_import_roots = "local", [str(data / "families")]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"
            out["created"] = [
                (
                    await admin.post(
                        "/api/ped/manual",
                        json={
                            "family_id": family_id,
                            "project_id": project_id,
                            "members": [
                                {"sample_id": sample_id, "sex": "female", "is_proband": index == 0}
                                for index, sample_id in enumerate(sample_ids)
                            ],
                        },
                    )
                ).status_code
                for family_id, sample_ids in ((f2, [s2]), (f1, [s1, s3, s4, s5]), (f3, [s6]))
            ]
            async with sessionmaker() as session:
                for sample_id, metadata in recorded.items():
                    await session.execute(
                        text("UPDATE samples SET metadata = metadata || CAST(:patch AS jsonb) WHERE sample_id = :sample_id"),
                        {"patch": json.dumps(metadata), "sample_id": sample_id},
                    )
                await session.commit()

            async def listed(family_id: str) -> tuple[int, list[str], list[bytes]]:
                """The manifest's status, the samples it lists and what its URLs fetch."""
                response = await admin.get(f"/api/signal-tracks/{family_id}/manifest")
                entries = response.json() if response.status_code == 200 else []
                fetched = [(await admin.get(f"/api{entry['url']}")).content for entry in entries]
                return response.status_code, [entry["sample_id"] for entry in entries], fetched

            out["f1_tracks"] = await listed(f1)
            out["f2_tracks"] = await listed(f2)
            out["refused_tracks"] = {}
            for sample_id in (s3, s4, s5):
                url = f"/api/signal-tracks/{f1}/{sample_id}/hificnv/{KIND}"
                got, head = await admin.get(url), await admin.head(url)
                out["refused_tracks"][sample_id] = (got.status_code, head.status_code, got.content == _content(f2, s2, "track"))

            async def report(family_id: str, sample_id: str) -> tuple[int, bytes | None]:
                """The link request's status, and what following the link fetches."""
                link = await admin.get(f"/api/families/{family_id}/qc-report/{sample_id}/link")
                if link.status_code != 200:
                    return link.status_code, None
                return link.status_code, (await admin.get(f"/api{link.json()['url']}")).content

            out["reports"] = {sample_id: await report(family_id, sample_id) for family_id, sample_id in ((f1, s1), (f1, s3), (f3, s6), (f2, s2))}
    finally:
        signal_tracks.DATA_DIR, family_qc_reports.DATA_DIR, settings.storage_backend, settings.family_import_roots = saved
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM families WHERE family_id IN (:f1, :f2, :f3)"), {"f1": f1, "f2": f2, "f3": f3}
            )
            await session.commit()
    out["ids"] = {"f1": f1, "f2": f2, "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "s6": s6}
    return out


@pytest.fixture(scope="module")
def looked_up(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    data = tmp_path_factory.mktemp("recorded-path-data")
    return _harness.run_async(lambda: _exercise(run, data))


def test_the_families_are_made(looked_up: dict[str, Any]) -> None:
    # Without this the answers below could come from families that do not exist.
    assert looked_up["created"] == [200, 200, 200]


def test_the_signal_track_manifest_lists_only_tracks_in_the_familys_own_folders(looked_up: dict[str, Any]) -> None:
    ids = looked_up["ids"]
    assert looked_up["f1_tracks"] == (200, [ids["s1"]], [_content(ids["f1"], ids["s1"], "track")])


def test_a_recorded_path_into_another_family_serves_none_of_its_tracks(looked_up: dict[str, Any]) -> None:
    # (GET status, HEAD status, whether the GET returned F2's track) for `..`, the absolute
    # path and the linked folder.
    assert looked_up["refused_tracks"] == {
        looked_up["ids"][sample]: (404, 404, False) for sample in ("s3", "s4", "s5")
    }


def test_a_qc_report_is_opened_only_from_the_familys_own_folders(looked_up: dict[str, Any]) -> None:
    ids = looked_up["ids"]
    assert looked_up["reports"] == {
        ids["s1"]: (200, _content(ids["f1"], ids["s1"], "report")),
        ids["s3"]: (404, None),
        ids["s6"]: (404, None),
        ids["s2"]: (200, _content(ids["f2"], ids["s2"], "report")),
    }


def test_the_other_family_still_reaches_its_own_track(looked_up: dict[str, Any]) -> None:
    ids = looked_up["ids"]
    assert looked_up["f2_tracks"] == (200, [ids["s2"]], [_content(ids["f2"], ids["s2"], "track")])
