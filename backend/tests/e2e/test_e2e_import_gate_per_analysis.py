"""The import gate of a family of two analyses holds each analysis by its own imports' marks, E2E.

A family may hold short-read and then long-read WGS of the same DNA, each imported from a package
of its own and signed out on its own. Both packages carry an ``snv`` dataset (each its own
callset), so the dataset a failure names cannot say whose it is: each import mark records the
analysis of the import that left it. Over real Postgres and ClickHouse, a new family each run:

1. a short-read WGS package (``analysis_type: short_read_wgs``): the trio's HaplotypeCaller callset;
2. a long-read WGS package (``long_read_wgs``) of the same samples, imported with update, whose
   DeepVariant callset has no readable record: its ``snv`` dataset fails, and the family is
   flagged import-incomplete for it.

Then:

* the flag's failure records the long-read analysis;
* the short-read analysis signs out without an import acknowledgement, and its record holds no
  import mark;
* the long-read analysis's sign-out is refused by the import gate, naming its ``snv`` failure, and
  signs out once that is acknowledged, its record holding the failure;
* the long-read import fails again: the short-read analysis's check still finds its import
  sections as signed.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from backend.tests._kept_manifest import kept_manifest

pytestmark = pytest.mark.integration

_COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{}\t{}\t{}\n"
_HEADERS = {
    "short_read_wgs": '##GATKCommandLine=<ID=HaplotypeCaller,CommandLine="HaplotypeCaller -R ref.fa",Version="4.5.0.0">\n',
    "long_read_wgs": "##DeepVariant_version=1.6.1\n",
}
# The short-read callset's records; the long-read one's have no readable position, so its
# loader imports no variant and the dataset fails.
_POSITIONS = {"short_read_wgs": ["1000", "2000"], "long_read_wgs": ["NOT_A_NUMBER", "NOT_A_NUMBER"]}
SIGN_OUT = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: no reported variant holds an ACMG classification",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: two variants are too few for the trio's QC",
}
IMPORT_ACKNOWLEDGED = {
    **SIGN_OUT,
    "acknowledge_import_incomplete": True,
    "import_incomplete_acknowledgement_reason": "e2e: the long-read callset is imported again later",
}
_IMPORT_SECTIONS = {"import_incomplete", "import_unfinished"}


def _package(root: Path, family_id: str, analysis: str, trio: tuple[str, str, str]) -> Path:
    proband, mother, father = trio
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{father}\t0\t0\t1\t1\n",
        encoding="utf-8",
    )
    lines = ["##fileformat=VCFv4.2\n", "##contig=<ID=chr1>\n", _HEADERS[analysis], _COLUMNS.format(proband, mother, father)]
    lines += [f"chr1\t{pos}\t.\tA\tG\t50\tPASS\t.\tGT:DP\t0/1:30\t0/1:30\t0/0:30\n" for pos in _POSITIONS[analysis]]
    (root / "family.vcf").write_text("".join(lines), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "analysis_type": analysis,
        "ped": "family.ped",
        "datasets": {"snv": {"family_vcf": "family.vcf"}},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness
    from backend.tests.e2e._harness import capture, ensure_postgres_schema

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    trio = tuple(f"{role}_{family_id}" for role in ("PROBAND", "MOTHER", "FATHER"))
    out: dict[str, Any] = {"imports": {}}

    async def run_import(name: str, analysis: str, mode: str) -> None:
        root = _package(base / family_id / name, family_id, analysis, trio)  # type: ignore[arg-type]
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                manifest_text=kept_manifest(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=mode,
            )
        out["imports"][name] = {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: d.status for d in result.datasets},
        }

    async def flag() -> Any:
        async with sessionmaker() as session:
            return (
                await session.execute(
                    text("SELECT metadata -> 'import_incomplete' FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).scalar_one()

    await run_import("short_read", "short_read_wgs", "cancel")
    await run_import("long_read", "long_read_wgs", "update")
    out["flag"] = await flag()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        api = f"/api/families/{family_id}"

        async def sign_out(name: str, body: dict[str, Any]) -> None:
            signed = capture(await _harness.sign_out(ac, f"{api}/report/sign-out", body))
            version = (signed["json"] or {}).get("version") if signed["status"] == 200 else None
            if version is not None:
                signed["snapshot"] = (await ac.get(f"{api}/report/sign-outs/{version}")).json().get("snapshot")
            out[name] = signed

        await sign_out("short_read_signed", {**SIGN_OUT, "analysis_type": "short_read_wgs"})
        await sign_out("long_read_refused", {**SIGN_OUT, "analysis_type": "long_read_wgs"})
        await sign_out("long_read_signed", {**IMPORT_ACKNOWLEDGED, "analysis_type": "long_read_wgs"})

        # The long-read callset fails again: a later failure of the other analysis.
        await run_import("long_read_again", "long_read_wgs", "update")
        out["flag_again"] = await flag()
        check = await ac.get(f"{api}/report/sign-out-check", params={"analysis_type": "short_read_wgs"})
        out["short_read_check"] = capture(check)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_gate_per_analysis")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"GATE_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_the_long_read_import_fails_for_its_callset_and_its_failure_records_its_analysis(run) -> None:
    assert run["imports"]["short_read"]["completed"] is True, run["imports"]["short_read"]
    long_read = run["imports"]["long_read"]
    assert long_read["completed"] is False and long_read["datasets"]["snv"] == "failed", long_read
    flag = run["flag"]
    assert flag["failed_datasets"] == ["snv"]
    assert flag["failed_analysis_types"] == {"snv": ["long_read_wgs"]}
    [failure] = flag["failed_scopes"]["snv"]
    assert (failure["analysis_type"], failure["scope"]) == ("long_read_wgs", {"source": "deepvariant", "samples": None})


def test_the_short_read_analysis_signs_out_unheld_by_the_long_read_failure(run) -> None:
    signed = run["short_read_signed"]
    assert signed["status"] == 200, signed["text"]
    snapshot = signed["snapshot"]
    assert snapshot["analysis_type"] == "short_read_wgs"
    # Its record holds no import mark: the long-read import's failure is not its own.
    assert snapshot["import_incomplete"] is None
    assert snapshot["import_unfinished"] == {}
    assert snapshot["acknowledged_import_incomplete"] is False


def test_the_long_read_analysis_s_sign_out_is_held_by_its_own_failure(run) -> None:
    refused = run["long_read_refused"]
    assert refused["status"] == 409, refused["text"]
    detail = refused["json"]["detail"]
    assert detail["gate"] == "import_incomplete"
    assert detail["import_incomplete"]["failed_datasets"] == ["snv"]
    assert detail["import_incomplete"]["failed_analysis_types"] == {"snv": ["long_read_wgs"]}
    assert "failed for snv (long-read WGS)" in detail["message"]

    signed = run["long_read_signed"]
    assert signed["status"] == 200, signed["text"]
    snapshot = signed["snapshot"]
    assert snapshot["analysis_type"] == "long_read_wgs"
    assert snapshot["import_incomplete"]["failed_datasets"] == ["snv"]
    assert snapshot["import_incomplete"]["failed_analysis_types"] == {"snv": ["long_read_wgs"]}
    assert snapshot["acknowledged_import_incomplete"] is True


def test_a_later_failure_of_the_other_analysis_leaves_the_short_read_record_s_import_sections(run) -> None:
    again = run["imports"]["long_read_again"]
    assert again["completed"] is False and again["datasets"]["snv"] == "failed", again
    # The flag now holds the later failure, with its own time.
    assert run["flag_again"]["at"] != run["flag"]["at"]
    check = run["short_read_check"]
    assert check["status"] == 200, check["text"]
    assert check["json"]["analysis_type"] == "short_read_wgs"
    assert not _IMPORT_SECTIONS & set(check["json"]["changed_sections"]), check["json"]
