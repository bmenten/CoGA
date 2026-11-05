"""The import watcher finds, checks and records the packages pipelines deliver (E2E, real Postgres).

A pipeline writes a package folder into a watched import folder and, last, its ready marker
``coga-ready.json``. The watcher (``import_watcher.run_watch_round``) makes each marked folder a
delivery per package fingerprint, checks a new one once (marker, sizes, checksums, project,
manifest, validation, samples) and records its state in ``import_deliveries``, each change an
event on the clinical audit chain ``system:import-inbox``.

In a watched folder of synthetic packages, as rounds go:

- round 1: a complete package of the e2e project is ``ready``; one whose PED differs from its
  checksum, and one of a project CoGA lacks, ``needs_attention``; one whose marker lists a file not
  copied yet ``incomplete``; one without a manifest is drafted one by Discover, which the delivery
  keeps; a folder without a marker is no delivery;
- round 2, nothing changed: no new delivery, each seen again;
- the ready package changes (a file added, its marker rewritten): a new delivery, the one before
  ``superseded``;
- the copying package's folder goes: after two rounds its delivery is ``gone``;
- a watched folder that is empty while it had an open delivery is skipped, its delivery kept open;
- the list (``GET /family-imports/deliveries``) shows them, by state when asked, and the chain
  verifies; one watcher holds the leadership at a time.

The values are synthetic. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import one_sample_package

pytestmark = pytest.mark.integration


def write_marker(folder: Path, *, project: str, family_id: str, extra_files: tuple[dict[str, Any], ...] = (), **fields: Any) -> None:
    """The ready marker of ``folder``, listing every file in it as it is now."""
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name != "coga-ready.json":
            content = path.read_bytes()
            files.append(
                {"path": path.relative_to(folder).as_posix(), "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            )
    body = {
        "schema": "coga-ready/1",
        "written_at": "2026-11-05T14:02:11Z",
        "project": project,
        "family_id": family_id,
        "analysis_type": "wgs",
        "assembly": "GRCh38",
        "pipeline": {"name": "nf-core/example", "version": "1.0.0"},
        "run": {"id": f"run-{uuid4().hex[:8]}"},
        "samples": [{"sample_id": f"{family_id}_S1", "role": "proband", "sex": "male"}],
        "files": [*files, *extra_files],
        **fields,
    }
    tmp = folder / ".coga-ready.json.tmp"
    tmp.write_text(json.dumps(body), encoding="utf-8")
    tmp.replace(folder / "coga-ready.json")


async def _exercise(base: Path, second: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import import_watcher
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project = (
            await session.execute(text("SELECT name FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_id})
        ).scalar_one()

    names = {key: f"E2E_WATCH_{key}_{run}" for key in ("READY", "CORRUPT", "COPYING", "NOMARKER", "NOPROJECT", "NOMANIFEST")}
    folders = {key: one_sample_package(base / name, name, variants=2, datasets=("snv",)) for key, name in names.items()}
    write_marker(folders["READY"], project=project, family_id=names["READY"])
    write_marker(folders["CORRUPT"], project=project, family_id=names["CORRUPT"])
    # Its PED changed after the marker was written: affected became unaffected.
    ped = folders["CORRUPT"] / "family.ped"
    ped.write_text(ped.read_text().replace("\t1\t2\n", "\t1\t1\n"), encoding="utf-8")
    write_marker(
        folders["COPYING"],
        project=project,
        family_id=names["COPYING"],
        extra_files=({"path": "snv/family.vcf.tbi", "size": 10, "sha256": "0" * 64},),
    )
    write_marker(folders["NOPROJECT"], project=f"NO_SUCH_PROJECT_{run}", family_id=names["NOPROJECT"])
    (folders["NOMANIFEST"] / "manifest.yaml").unlink()
    write_marker(folders["NOMANIFEST"], project=project, family_id=names["NOMANIFEST"])

    async def deliveries() -> list[dict[str, Any]]:
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, folder_name, state, issues, warnings, package_fingerprint, "
                        "manifest_text IS NOT NULL AS kept_manifest, missing_rounds, last_seen_at, project_id::text AS project_id "
                        "FROM import_deliveries WHERE folder_name LIKE :run ORDER BY first_seen_at, folder_name"
                    ),
                    {"run": f"%{run}"},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    out: dict[str, Any] = {"names": names, "project_id": project_id}
    roots = [base.resolve()]
    out["round1"] = await import_watcher.run_watch_round(sessionmaker, roots)
    out["after1"] = await deliveries()
    out["round2"] = await import_watcher.run_watch_round(sessionmaker, roots)
    out["after2"] = await deliveries()

    (folders["READY"] / "notes.txt").write_text("a later output\n", encoding="utf-8")
    write_marker(folders["READY"], project=project, family_id=names["READY"])
    out["round3"] = await import_watcher.run_watch_round(sessionmaker, roots)
    out["after3"] = await deliveries()

    shutil.rmtree(folders["COPYING"])
    await import_watcher.run_watch_round(sessionmaker, roots)
    out["after4"] = await deliveries()
    out["round5"] = await import_watcher.run_watch_round(sessionmaker, roots)
    out["after5"] = await deliveries()

    # A second watched folder whose one delivery's folder is moved away: empty, it is skipped.
    lone = f"E2E_WATCH_LONE_{run}"
    lone_folder = one_sample_package(second / lone, lone, variants=2, datasets=("snv",))
    write_marker(lone_folder, project=project, family_id=lone)
    await import_watcher.run_watch_round(sessionmaker, [second.resolve()])
    shutil.move(str(lone_folder), str(base / "moved-away"))
    out["empty_rounds"] = [await import_watcher.run_watch_round(sessionmaker, [second.resolve()]) for _ in range(3)]
    async with sessionmaker() as session:
        out["lone"] = (
            await session.execute(
                text("SELECT state, missing_rounds FROM import_deliveries WHERE folder_name = :n"), {"n": lone}
            )
        ).one()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"
        out["listed"] = capture(await client.get("/api/family-imports/deliveries", params={"limit": 200}))
        out["listed_ready"] = capture(
            await client.get("/api/family-imports/deliveries", params=[("state", "ready"), ("state", "gone")])
        )
        out["listed_bad_state"] = capture(await client.get("/api/family-imports/deliveries", params={"state": "nonsense"}))
        out["verified"] = capture(
            await client.get(
                "/api/admin/integrity/verify",
                params={"table": "clinical_audit_events", "family_id": import_watcher.IMPORT_INBOX_CHAIN},
            )
        )
    async with sessionmaker() as session:
        out["events"] = [
            tuple(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT action, actor, after ->> 'folder' FROM clinical_audit_events "
                        "WHERE family_identifier = :chain AND after ->> 'folder' LIKE :run ORDER BY created_at, id"
                    ),
                    {"chain": import_watcher.IMPORT_INBOX_CHAIN, "run": f"%{run}"},
                )
            ).all()
        ]

    first, other = import_watcher.WatcherLeadership(), import_watcher.WatcherLeadership()
    try:
        out["leadership"] = [await first.hold(), await other.hold(), await first.hold()]
        await first.release()
        out["leadership"].append(await other.hold())
    finally:
        await first.release()
        await other.release()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_watcher")
    second = tmp_path_factory.mktemp("import_watcher_second")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base), str(second)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, second, run))


def _by_name(ran: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    keys = {name: key for key, name in ran["names"].items()}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(keys.get(row["folder_name"], row["folder_name"]), []).append(row)
    return grouped


def _codes(row: dict[str, Any], key: str = "issues") -> list[str]:
    return [issue["code"] for issue in row[key]]


def test_the_first_round_checks_each_marked_folder_once(ran) -> None:
    rows = _by_name(ran, ran["after1"])
    assert "NOMARKER" not in rows
    assert [row["state"] for row in rows["READY"]] == ["ready"], rows["READY"]
    assert rows["READY"][0]["project_id"] == ran["project_id"]
    [corrupt] = rows["CORRUPT"]
    assert (corrupt["state"], _codes(corrupt)) == ("needs_attention", ["file_checksum_differs"])
    [copying] = rows["COPYING"]
    assert (copying["state"], _codes(copying)) == ("incomplete", ["file_missing"])
    [no_project] = rows["NOPROJECT"]
    assert (no_project["state"], _codes(no_project)) == ("needs_attention", ["marker_project_unknown"])
    [drafted] = rows["NOMANIFEST"]
    assert (drafted["state"], drafted["kept_manifest"]) == ("ready", True)
    assert ran["round1"].deliveries == {"ready": 2, "needs_attention": 2, "incomplete": 1}


def test_a_round_that_finds_nothing_new_checks_nothing_again(ran) -> None:
    assert ran["round2"].deliveries == {}
    before = {row["id"]: row["last_seen_at"] for row in ran["after1"]}
    after = {row["id"]: row["last_seen_at"] for row in ran["after2"]}
    assert set(after) == set(before)
    assert all(after[key] > before[key] for key in before)


def test_a_changed_folder_is_a_new_delivery_and_supersedes_the_one_before(ran) -> None:
    ready = _by_name(ran, ran["after3"])["READY"]
    assert [row["state"] for row in ready] == ["superseded", "ready"]
    assert ready[0]["package_fingerprint"] != ready[1]["package_fingerprint"]
    assert ran["round3"].deliveries == {"ready": 1}


def test_a_delivery_whose_folder_went_is_gone_after_two_rounds(ran) -> None:
    [after4] = _by_name(ran, ran["after4"])["COPYING"]
    assert (after4["state"], after4["missing_rounds"]) == ("incomplete", 1)
    [after5] = _by_name(ran, ran["after5"])["COPYING"]
    assert after5["state"] == "gone"
    assert ran["round5"].gone == 1


def test_an_empty_watched_folder_with_open_deliveries_is_skipped(ran) -> None:
    assert [(done.roots_watched, done.roots_skipped) for done in ran["empty_rounds"]] == [(0, 1)] * 3
    state, missing = ran["lone"]
    assert state in ("ready", "needs_attention") and missing == 0


def test_the_list_shows_the_deliveries_by_state_when_asked(ran) -> None:
    listed = ran["listed"]
    assert listed["status"] == 200, listed
    names = set(ran["names"].values())
    mine = [item for item in listed["json"] if item["folder_name"] in names]
    assert sorted((item["folder_name"], item["state"]) for item in mine) == sorted(
        (row["folder_name"], row["state"]) for row in ran["after5"]
    )
    ready = next(item for item in mine if item["state"] == "ready" and item["folder_name"] == ran["names"]["READY"])
    assert (ready["analysis_type"], ready["pipeline_name"], ready["pipeline_version"], ready["project_id"]) == (
        "wgs",
        "nf-core/example",
        "1.0.0",
        ran["project_id"],
    )
    assert ready["run_id"].startswith("run-") and len(ready["marker_sha256"]) == 64
    filtered = [item for item in ran["listed_ready"]["json"] if item["folder_name"] in names]
    assert sorted(item["state"] for item in filtered) == ["gone", "ready", "ready"]
    assert ran["listed_bad_state"]["status"] == 422


def test_each_change_of_state_is_on_the_import_inbox_chain_which_verifies(ran) -> None:
    actions = [action for action, actor, _folder in ran["events"]]
    assert {actor for _action, actor, _folder in ran["events"]} == {"system:import-watcher"}
    assert {"import_delivery_ready", "import_delivery_needs_attention", "import_delivery_incomplete"} <= set(actions)
    assert {"import_delivery_superseded", "import_delivery_gone"} <= set(actions)
    verified = ran["verified"]
    assert verified["status"] == 200, verified
    assert verified["json"]["verified"] is True and verified["json"]["first_bad_row"] is None


def test_one_watcher_holds_the_leadership_at_a_time(ran) -> None:
    assert ran["leadership"] == [True, False, True, True]
