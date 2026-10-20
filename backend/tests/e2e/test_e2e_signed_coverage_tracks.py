"""The signed record freezes what each coverage track holds, end to end (owner decision of 2026-10-20).

Each coverage BED declares whether it holds read depth or a log2 ratio (TRACE-9), and the depth
readers read only a track of read depth; the signed record now freezes, per sample and caller, what
each coverage track holds and where it came from (``coverage_tracks``), so it stays traceable which
kind of coverage a depth check rested on.

Here a two-member family (a new id each run) goes through the package import into real Postgres and
ClickHouse: S1's coverage BED declares ``read_depth`` (300x over chrM); S2's declares nothing (0.2),
and S2 has WisecondorX bins too (log2 ratios). Through the API, this:

* reads the mtDNA coverage, the depth check that rests on these tracks: S1's from its BED, S2's
  left out with a note for each of its two tracks;
* signs the family out and reads the record back: S1's BED as read depth from a package, S2's as
  unknown from a package, S2's WisecondorX bins as a log2 ratio, each with its file, rows and load
  time; a verified content hash and chain; a check that matches, with nothing it does not compare
  and nothing of the section not captured;
* uploads S2's BED again, declared as read depth: the check names the coverage tracks as changed
  since sign-out, and a new sign-out freezes S2's BED as read depth from a single-file upload;
* signs out once more as a build from before the section (its key left out): that record still
  verifies and reads, nothing it predates is called not captured, and the check names the section
  as not compared.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the API
(see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

MT_LENGTH = 16_569
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: nothing is reported",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the synthetic family has no genotypes to check",
}


def _write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _package(root: Path, family_id: str, s1: str, s2: str) -> Path:
    root.mkdir(parents=True)
    _write(root, "family.ped", f"{family_id}\t{s1}\t0\t0\t2\t1\n{family_id}\t{s2}\t0\t0\t1\t1\n")
    beds = {
        s1: _write(root, f"coverage/{s1}.bed", f"chrM\t0\t{MT_LENGTH}\t300\n"),
        s2: _write(root, f"coverage/{s2}.bed", f"chrM\t0\t{MT_LENGTH}\t0.2\n"),
    }
    bins = _write(root, f"wisecondorx/{s2}_bins.bed", f"chrM\t0\t{MT_LENGTH}\t0.1\n")
    segments = _write(root, f"wisecondorx/{s2}_segments.bed", f"chrM\t0\t{MT_LENGTH}\t0.1\n")
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  wisecondorx:",
        "    per_sample:",
        f"      {s2}: {{bins: {bins}, segments: {segments}}}",
        "  coverage:",
        "    per_sample:",
        f"      {s1}: {{bed: {beds[s1]}, content: read_depth}}",
        # No `content`: imported as unknown, never read as depth.
        f"      {s2}: {{bed: {beds[s2]}}}",
    ]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


def _cap(resp) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import report_signout_service as rss
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    s1, s2 = f"{family_id}_S1", f"{family_id}_S2"
    out: dict[str, Any] = {"samples": {"S1": s1, "S2": s2}, "responses": {}}
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(_package(base / family_id, family_id, s1, s2)),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    out["import"] = {
        "completed": result.completed,
        "error": result.error,
        "datasets": {d.dataset_type: d.status for d in result.datasets},
    }
    if not result.completed:
        return out  # the first test names the dataset that failed

    r = out["responses"]
    family = f"/api/families/{family_id}"
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

        async def chain() -> dict[str, Any]:
            return _cap(
                await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
            )

        # The depth check these tracks feed.
        r["mtdna"] = _cap(await ac.get(f"{family}/mitochondrial-dna"))

        r["signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        version = (r["signout"]["json"] or {}).get("version")
        r["detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["chain"] = await chain()

        # S2's BED uploaded again, declared as read depth.
        r["upload"] = _cap(
            await ac.post(
                f"/api/bed/upload/{s2}/coverage",
                params={"overwrite": "true", "content": "read_depth"},
                files={"file": (f"{s2}.depth.bed", f"chrM\t0\t{MT_LENGTH}\t450\n".encode(), "text/plain")},
            )
        )
        r["check_after_upload"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["signout_after_upload"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        version = (r["signout_after_upload"]["json"] or {}).get("version")
        r["detail_after_upload"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))

        # A record signed by a build from before the section: its key left out.
        real_build = rss.build_report_snapshot

        async def _old_build(*args: Any, **kwargs: Any) -> dict[str, Any]:
            body = await real_build(*args, **kwargs)
            body.pop("coverage_tracks", None)
            return body

        mp = pytest.MonkeyPatch()
        mp.setattr(rss, "build_report_snapshot", _old_build)
        try:
            r["old_signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        finally:
            mp.undo()
        version = (r["old_signout"]["json"] or {}).get("version")
        r["old_detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["old_check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["old_chain"] = await chain()
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("signed_coverage_tracks")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"SIGNEDCOV_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _ok(run: dict[str, Any], key: str) -> Any:
    resp = run["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _tracks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return snapshot["coverage_tracks"]["tracks"]


def _section_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [gap for gap in gaps if gap["section"] == "coverage_tracks"]


def test_the_family_imports_with_a_declared_and_an_undeclared_coverage_bed(run) -> None:
    imported = run["import"]
    assert imported["completed"] is True, imported
    assert imported["datasets"]["coverage"] == "imported"
    assert imported["datasets"]["wisecondorx"] == "imported"


def test_the_depth_check_reads_the_track_of_read_depth_and_names_the_others(run) -> None:
    s1, s2 = run["samples"]["S1"], run["samples"]["S2"]
    mtdna = _ok(run, "mtdna")
    coverage = {sample["sample_id"]: sample["coverage"] for sample in mtdna["samples"]}
    assert (coverage[s1]["mean_depth"], coverage[s1]["source"]) == (300.0, "coverage")
    assert coverage[s2]["mean_depth"] is None
    assert (
        f"{s2}: the coverage BED does not declare whether it holds read depth or a log2 ratio; "
        "it is not read as depth."
    ) in mtdna["qc_notes"]
    assert (
        f"{s2}: the WisecondorX coverage holds log2 ratios, not read depth; it is not read as depth."
        in mtdna["qc_notes"]
    )


def test_the_record_freezes_what_each_coverage_track_holds_and_where_it_came_from(run) -> None:
    s1, s2 = run["samples"]["S1"], run["samples"]["S2"]
    detail = _ok(run, "detail")
    tracks = _tracks(detail["snapshot"])
    # Per sample and caller, in a fixed order: what the depth check above read each as.
    assert [(track["sample"], track["source"], track["content"]) for track in tracks] == [
        (s1, "web", "read_depth"),
        (s2, "web", "unknown"),
        (s2, "wisecondorx", "log2_ratio"),
    ]
    files = {(track["sample"], track["source"]): track["files"] for track in tracks}
    [s1_bed] = files[(s1, "web")]
    assert (s1_bed["file"], s1_bed["rows"], s1_bed["loaded_from"], s1_bed["declared"]) == (
        f"{s1}.bed",
        1,
        "package",
        "read_depth",
    )
    assert s1_bed["loaded_at"]
    [s2_bed] = files[(s2, "web")]
    assert (s2_bed["file"], s2_bed["loaded_from"], s2_bed["declared"]) == (f"{s2}.bed", "package", "unknown")
    [s2_bins] = files[(s2, "wisecondorx")]
    assert (s2_bins["file"], s2_bins["loaded_from"], s2_bins["declared"]) == (f"{s2}_bins.bed", "package", None)
    # Bound into the content hash, which the stored record still matches, and the chain.
    assert detail["verified"] is True
    assert _ok(run, "chain")["verified"] is True
    assert _section_gaps(detail["not_captured"]) == []


def test_the_check_matches_the_record_right_after_it_was_signed(run) -> None:
    check = _ok(run, "check")
    # Through the JSONB round trip the section compares equal to the one built again.
    assert check["matches"] is True, check
    assert check["not_compared"] == []
    assert _section_gaps(check["not_captured"]) == []


def test_a_coverage_bed_declared_again_since_sign_out_is_named_and_frozen_by_the_next(run) -> None:
    s2 = run["samples"]["S2"]
    _ok(run, "upload")
    check = _ok(run, "check_after_upload")
    assert check["matches"] is False
    assert check["changed_sections"] == ["coverage_tracks"]
    tracks = _tracks(_ok(run, "detail_after_upload")["snapshot"])
    [s2_bed] = [track for track in tracks if (track["sample"], track["source"]) == (s2, "web")]
    assert s2_bed["content"] == "read_depth"
    [upload] = s2_bed["files"]
    assert (upload["file"], upload["loaded_from"], upload["declared"]) == (f"{s2}.depth.bed", "upload", "read_depth")


def test_a_record_signed_before_the_section_still_verifies_reads_and_is_not_called_not_captured(run) -> None:
    old = _ok(run, "old_signout")
    detail = _ok(run, "old_detail")
    assert detail["version"] == old["version"]
    assert "coverage_tracks" not in detail["snapshot"]
    assert detail["verified"] is True
    assert _ok(run, "old_chain")["verified"] is True
    # Nothing it predates is called "not captured": the signed view says it is not held.
    assert _section_gaps(detail["not_captured"]) == []
    # The check compares what it holds, and names the section it predates as not compared.
    check = _ok(run, "old_check")
    assert check["version"] == old["version"]
    assert check["not_compared"] == ["coverage_tracks"]
    assert check["matches"] is True, check
