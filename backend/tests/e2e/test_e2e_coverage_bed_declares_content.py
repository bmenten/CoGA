"""Each coverage BED declares what it holds, read depth or a log2 ratio (TRACE-9), E2E.

A coverage BED recorded nothing of what its values are: the depth readers read it as depth and the
coverage chart drew it on its log2-ratio axis. Each now declares it (the upload's ``content``, the
manifest entry's ``content`` key), its record keeps it, and an undeclared one is ``unknown``, never
read as depth.

Here a two-member family (a new id each run), with chrM calls for both members at 500 reads, goes
through the package import and the BED upload route into real Postgres and ClickHouse:

* the package (cancel): S1's coverage BED declares ``read_depth`` (300x over chrM); S2's declares
  nothing (0.2 over chrM). The validation warns of S2's; each record keeps what was declared, S2's
  as ``unknown``, and so does what the family holds (``imported_scopes``); the track availability
  serves ``read_depth`` and ``unknown``; the mtDNA coverage reads S1's BED, and S2's calls' depth,
  with a note naming S2's BED and why (before: S2's 0.2 was read as its mean depth);
* S2's BED uploaded again without ``content``: refused (422), its record as it was;
* uploaded with ``content=log2_ratio``: stored as declared, the raw file's record and what the
  family holds say so, and the mtDNA note says it holds log2 ratios;
* uploaded with ``content=read_depth`` (450x): the mtDNA coverage reads it.

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

_MITO_HEADER = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant allele fraction">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)
_MITO_CALLS = (
    "chrM\t73\t.\tA\tG\t30\tPASS\t.\tGT:DP:AD:VAF\t1/1:500:0,500:1\n"
    "chrM\t263\t.\tA\tG\t30\tPASS\t.\tGT:DP:AD:VAF\t1/1:500:0,500:1\n"
)


def _write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _package(root: Path, family_id: str, s1: str, s2: str) -> Path:
    root.mkdir(parents=True)
    _write(root, "family.ped", f"{family_id}\t{s1}\t0\t0\t2\t1\n{family_id}\t{s2}\t0\t0\t1\t1\n")
    vcfs = {sample: _write(root, f"mito/{sample}.vcf", _MITO_HEADER + _MITO_CALLS) for sample in (s1, s2)}
    # S1's BED holds 300x over chrM, S2's a value of no stated kind.
    beds = {
        s1: _write(root, f"coverage/{s1}.bed", f"chrM\t0\t{MT_LENGTH}\t300\n"),
        s2: _write(root, f"coverage/{s2}.bed", f"chrM\t0\t{MT_LENGTH}\t0.2\n"),
    }
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  mito:",
        "    per_sample:",
        *(f"      {sample}: {{vcf: {path}}}" for sample, path in vcfs.items()),
        "  coverage:",
        "    per_sample:",
        f"      {s1}: {{bed: {beds[s1]}, content: read_depth}}",
        # No `content`: imported as unknown.
        f"      {s2}: {{bed: {beds[s2]}}}",
    ]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    s1, s2 = f"{family_id}_S1", f"{family_id}_S2"
    out: dict[str, Any] = {"samples": {"S1": s1, "S2": s2}, "steps": {}}

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
        "datasets": {d.dataset_type: (d.status, d.summary) for d in result.datasets},
        "warnings": [(w.code, w.sample_id) for w in result.validation.warnings],
    }
    if not result.completed:
        return out  # the first test names the dataset that failed

    async def stored() -> dict[str, Any]:
        """Each member's coverage BED record, what the family holds of the coverage BEDs, and
        the content the raw files of the uploads record."""
        async with sessionmaker() as session:
            records = {
                str(row["sample_id"]): row["content"]
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT s.sample_id, t.metadata ->> 'content' AS content
                            FROM sample_interval_track_sources t
                            JOIN samples s ON s.id = t.sample_id
                            JOIN families f ON f.id = t.family_id
                            WHERE f.family_id = :f AND t.track_type = 'coverage' AND t.source = 'web'
                            """
                        ),
                        {"f": family_id},
                    )
                ).mappings()
            }
            held = (
                await session.execute(
                    text("SELECT metadata -> 'imported_scopes' FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).scalar_one()
            raw = [
                row["content"]
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT r.metadata ->> 'content' AS content
                            FROM raw_import_files r
                            JOIN families f ON f.id = r.family_id
                            WHERE f.family_id = :f AND r.dataset = 'coverage' AND r.source = 'web'
                            ORDER BY r.created_at
                            """
                        ),
                        {"f": family_id},
                    )
                ).mappings()
            ]
        return {
            "records": records,
            "held": {dataset: (held or {}).get(dataset) for dataset in ("coverage", "bed_coverage")},
            "raw": raw,
        }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

        async def views() -> dict[str, Any]:
            availability = await ac.get(f"/api/families/{family_id}/track-availability", params={"chrom": "MT"})
            mtdna = await ac.get(f"/api/families/{family_id}/mitochondrial-dna")
            return {
                "availability": {
                    "status": availability.status_code,
                    "contents": {
                        name: sample["coverage_contents"]
                        for name, sample in availability.json().get("samples", {}).items()
                    },
                },
                "mtdna": {
                    "status": mtdna.status_code,
                    "coverage": {
                        sample["sample_id"]: (sample["coverage"]["mean_depth"], sample["coverage"]["source"])
                        for sample in mtdna.json().get("samples", [])
                    },
                    "notes": mtdna.json().get("qc_notes", []),
                },
            }

        out["steps"]["package"] = {**await stored(), **await views()}

        async def upload(name: str, body: str, params: dict[str, str]) -> None:
            response = await ac.post(
                f"/api/bed/upload/{s2}/coverage",
                params={"overwrite": "true", **params},
                files={"file": (f"{s2}.bed", body.encode(), "text/plain")},
            )
            out["steps"][name] = {
                "status": response.status_code,
                "json": response.json(),
                **await stored(),
                **await views(),
            }

        await upload("undeclared", f"chrM\t0\t{MT_LENGTH}\t0.3\n", {})
        await upload("log2_ratio", f"chrM\t0\t{MT_LENGTH}\t0.3\n", {"content": "log2_ratio"})
        await upload("read_depth", f"chrM\t0\t{MT_LENGTH}\t450\n", {"content": "read_depth"})
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("coverage_bed_content")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"BEDCONTENT_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _undeclared_note(sample: str) -> str:
    return (
        f"{sample}: the coverage BED does not declare whether it holds read depth or a log2 ratio; "
        "it is not read as depth."
    )


def test_the_package_records_what_each_coverage_bed_declares(run) -> None:
    s1, s2 = run["samples"]["S1"], run["samples"]["S2"]
    imported = run["import"]
    assert imported["completed"] is True, imported
    status, summary = imported["datasets"]["coverage"]
    assert status == "imported"
    assert {sample: result["content"] for sample, result in summary.items()} == {s1: "read_depth", s2: "unknown"}
    # The validation said S2's BED declares nothing.
    assert ("coverage_content_undeclared", s2) in imported["warnings"]
    step = run["steps"]["package"]
    # Before: neither record said what its BED held.
    assert step["records"] == {s1: "read_depth", s2: "unknown"}
    assert step["held"]["coverage"] == [
        {"source": None, "samples": [s1], "content": "read_depth"},
        {"source": None, "samples": [s2], "content": "unknown"},
    ]


def test_the_views_and_the_mtdna_coverage_read_what_was_declared(run) -> None:
    s1, s2 = run["samples"]["S1"], run["samples"]["S2"]
    step = run["steps"]["package"]
    assert step["availability"]["status"] == 200, step["availability"]
    assert step["availability"]["contents"] == {s1: {"web": "read_depth"}, s2: {"web": "unknown"}}
    assert step["mtdna"]["status"] == 200, step["mtdna"]
    # Before: S2's 0.2 was read as its mean chrM depth. Now its calls' depth, and a note says why.
    assert step["mtdna"]["coverage"] == {s1: (300.0, "coverage BED"), s2: (500.0, "variant calls")}
    assert _undeclared_note(s2) in step["mtdna"]["notes"]
    assert not any(note.startswith(f"{s1}:") for note in step["mtdna"]["notes"])


def test_an_upload_that_declares_nothing_is_refused_and_replaces_nothing(run) -> None:
    s2 = run["samples"]["S2"]
    step = run["steps"]["undeclared"]
    # Before: 200, the BED read as depth.
    assert step["status"] == 422, step["json"]
    assert step["json"]["detail"] == "Declare what the coverage BED holds: content=read_depth or content=log2_ratio"
    assert step["records"][s2] == "unknown"
    assert step["held"]["bed_coverage"] is None
    assert step["raw"] == []


def test_an_upload_declared_as_a_log2_ratio_is_kept_off_the_depth_readers(run) -> None:
    s2 = run["samples"]["S2"]
    step = run["steps"]["log2_ratio"]
    assert step["status"] == 200, step["json"]
    assert step["records"][s2] == "log2_ratio"
    assert step["held"]["bed_coverage"] == [{"source": None, "samples": [s2], "content": "log2_ratio"}]
    assert step["raw"] == ["log2_ratio"]
    assert step["availability"]["contents"][s2] == {"web": "log2_ratio"}
    assert step["mtdna"]["coverage"][s2] == (500.0, "variant calls")
    assert (
        f"{s2}: the coverage BED holds log2 ratios, not read depth; it is not read as depth."
        in step["mtdna"]["notes"]
    )


def test_an_upload_declared_as_read_depth_is_read_as_depth(run) -> None:
    s2 = run["samples"]["S2"]
    step = run["steps"]["read_depth"]
    assert step["status"] == 200, step["json"]
    assert step["records"][s2] == "read_depth"
    # What the family holds of S2's BED is its latest declaration, not both.
    assert step["held"]["bed_coverage"] == [{"source": None, "samples": [s2], "content": "read_depth"}]
    assert step["raw"] == ["log2_ratio", "read_depth"]
    assert step["availability"]["contents"][s2] == {"web": "read_depth"}
    assert step["mtdna"]["coverage"][s2] == (450.0, "coverage BED")
    assert not any(note.startswith(f"{s2}:") for note in step["mtdna"]["notes"])
