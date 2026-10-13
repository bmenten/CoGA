"""A trio's per-sample NeedlR files, imported: each SV named by its coordinates (E2E, CLIN-43).

The long-read pipeline writes one NeedlR VCF per sample, and each file's records are numbered by
its own Sniffles2 run, so the files give one ID to different calls. The package's NeedlR reader
took a record's ID as its SV id and merged the records by it: a call was attached to another
sample's SV of the same ID, wherever that lay, and its own deletion was lost. Now an SV's id is
its coordinates and type (and a breakend's mate), as an uploaded SV's is, and the record ID stays
in its annotation.

Here a trio package with one NeedlR VCF per sample is imported through the front door
(``execute_family_package_import``) into Postgres and ClickHouse, the mother's file first, and the
SVs are read back through ``GET /families/{family_id}/structural-variants`` and from the stored
details:

* the father's deletion, which shares its ID with the mother's first deletion, and the mother's
  second deletion, which shares hers with the proband's, are SVs of their own, at their own
  positions, each with its own sample's call;
* the deletion the mother and the proband both carry, under other IDs, is one SV with both calls:
  the mother's own, not the no-call the proband's line gives her;
* each SV's annotations keep the record IDs of its lines.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the API
(see test_e2e_api_contract.py for why). All samples and values are synthetic.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

pytestmark = pytest.mark.integration

_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"


def _deletion(pos: int, end: int, record_id: str, sample: str, calls: str) -> str:
    return (
        f"chr1\t{pos}\t{record_id}\tN\t<DEL>\t60\tPASS\t"
        f"SVTYPE=DEL;SVLEN=-{end - pos};END={end};Query_ID={sample}_sv_phased;{calls}\n"
    )


def _write_package(root: Path, family_id: str, samples: dict[str, str]) -> None:
    proband, mother, father = samples["proband"], samples["mother"], samples["father"]
    # Each file numbered by its own run: the IDs repeat across the files.
    files = {
        mother: [
            _deletion(7000, 12000, "Sniffles2.DEL.0S0", mother, "Genotype=0/1;Alt_Reads=12"),
            _deletion(50000, 56000, "Sniffles2.DEL.1S0", mother, "Genotype=0/1;Alt_Reads=9"),
        ],
        # Called without the parents: the proband's line names no parent's genotype.
        proband: [_deletion(7000, 12000, "Sniffles2.DEL.1S0", proband, "Genotype=0/1;Alt_Reads=18")],
        father: [_deletion(90000, 96000, "Sniffles2.DEL.0S0", father, "Genotype=1/1;Alt_Reads=20")],
    }
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n",
        encoding="utf-8",
    )
    per_sample: dict[str, dict[str, str]] = {}
    for sample, lines in files.items():  # the mother's file first
        path = Path("sv") / sample / "needlr" / f"{sample}_sv_phased.needLR.4.0.vcf"
        (root / path).parent.mkdir(parents=True)
        (root / path).write_text(_HEADER + "".join(lines), encoding="utf-8")
        per_sample[sample] = {"vcf": path.as_posix()}
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": "family.ped",
        "datasets": {"sv_needlr": {"per_sample": per_sample}},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


async def _exercise(base: Path, suffix: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    family_id = f"NEEDLR_IDS_{suffix}"
    samples = {role: f"{role.upper()}_{suffix}" for role in ("proband", "mother", "father")}
    root = base / family_id
    _write_package(root, family_id, samples)
    out: dict = {"samples": samples}

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
        out["import"] = {
            "completed": result.completed,
            "error": result.error,
            "datasets": {
                dataset.dataset_type: {"status": dataset.status, "summary": dataset.summary}
                for dataset in result.datasets
                if dataset.enabled
            },
        }
        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
    out["record_ids"] = await _harness.structural_variant_record_ids(family_uuid)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e", timeout=120) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        svs = await ac.get(f"/api/families/{family_id}/structural-variants", params={"page_size": 50})
        out["svs"] = {"status": svs.status_code, "json": svs.json()}
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("needlr_coordinate_ids")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, uuid4().hex[:8].upper()))
    finally:
        mp.undo()


def test_the_per_sample_files_are_imported(run) -> None:
    imported = run["import"]
    assert imported["completed"] is True, imported
    needlr = imported["datasets"]["sv_needlr"]
    assert needlr["status"] == "imported", needlr
    # Before: two SVs, under the files' IDs.
    assert needlr["summary"]["processed"] == 3, needlr


def test_each_call_is_an_sv_of_its_own_position_or_merged_with_the_same_event(run) -> None:
    svs = run["svs"]
    assert svs["status"] == 200, svs["json"]
    proband, mother, father = (run["samples"][role] for role in ("proband", "mother", "father"))
    listed = {
        variant["_id"]: (
            variant["source"],
            variant["start"],
            variant["end"],
            {genotype["sample"]: genotype["gt"] for genotype in variant["genotypes"]},
        )
        for variant in svs["json"]["variants"]
    }
    # Before: the father's deletion at 90000 was listed at 7000 on the mother's
    # Sniffles2.DEL.0S0, and the proband's at 7000 on the mother's Sniffles2.DEL.1S0 at
    # 50000, where the no-call the proband's line gives her replaced the mother's own call.
    assert listed == {
        "1-7000-12000-DEL---": ("needlr", 7000, 12000, {father: "./.", mother: "0/1", proband: "0/1"}),
        "1-50000-56000-DEL---": ("needlr", 50000, 56000, {mother: "0/1"}),
        "1-90000-96000-DEL---": ("needlr", 90000, 96000, {father: "1/1"}),
    }


def test_each_svs_annotations_keep_its_lines_record_ids(run) -> None:
    # Before: the SVs were named by the record IDs, which no annotation kept.
    assert {variant_id: sorted(ids, key=str) for variant_id, ids in run["record_ids"].items()} == {
        "1-7000-12000-DEL---": ["Sniffles2.DEL.0S0", "Sniffles2.DEL.1S0"],
        "1-50000-56000-DEL---": ["Sniffles2.DEL.1S0"],
        "1-90000-96000-DEL---": ["Sniffles2.DEL.0S0"],
    }
