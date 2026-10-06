"""A per-sample TRGT file is read from the target sample's own column (E2E).

A package's per-sample TRGT file and the admin upload (``POST
/repeat-expansions/upload/{sample_id}``) both store one sample's repeat calls. They used to
read the file's first sample column whatever it was named: a family TRGT VCF uploaded for
the child stored the mother's repeat sizes as the child's, and another member's file
uploaded by mistake replaced the child's calls with that member's.

The family here is its own (a new id each run):

* a package import with one ``<sample>_sort`` file per member stores each member's own
  repeat size;
* uploading the mother's file for the child, with ``overwrite=true``, is refused (400) and
  the child's stored calls stay -- the overwrite's delete is rolled back with the refusal;
* uploading the family TRGT VCF for the child stores the child's column only, and leaves
  the mother's and the father's calls as they were.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for
the API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def _trgt_vcf(columns: list[str], counts: list[int]) -> str:
    """A TRGT VCF with one HTT record, each column's repeat count in ``counts``."""
    calls = "\t".join(f"0/0:{count}_0" for count in counts)
    return (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(columns) + "\n"
        f"chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG,CAA\tGT:MC\t{calls}\n"
    )


def _write_package(root: Path, family_id: str, counts: dict[str, int]) -> None:
    """A trio package with one single-column ``<sample>_sort`` TRGT file per member."""
    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n",
        encoding="utf-8",
    )
    (root / "repeats").mkdir()
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  repeats_trgt:",
        "    per_sample:",
    ]
    for sample_id, count in counts.items():
        (root / "repeats" / f"{sample_id}_tr.vcf").write_text(
            _trgt_vcf([f"{sample_id}_sort"], [count]), encoding="utf-8"
        )
        lines.append(f"      {sample_id}: {{file: repeats/{sample_id}_tr.vcf}}")
    (root / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _stored_counts(session, family_uuid: str) -> dict[str, tuple[list[int], str | None]]:
    """Each member's stored HTT repeat counts and the VCF column they were read from."""
    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                """
                SELECT s.sample_id, re.alleles, re.metadata ->> 'vcf_sample' AS vcf_sample
                FROM repeat_expansions re
                JOIN samples s ON s.id = re.sample_id
                WHERE re.family_id = CAST(:family_uuid AS uuid) AND re.source = 'trgt'
                """
            ),
            {"family_uuid": family_uuid},
        )
    ).mappings().all()
    return {
        str(row["sample_id"]): (
            [int(allele["repeat_count"]) for allele in row["alleles"] if allele.get("repeat_count") is not None],
            row["vcf_sample"],
        )
        for row in rows
    }


async def _exercise(base: Path, family_id: str) -> dict:
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

    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    out: dict = {"samples": {"father": father, "mother": mother, "proband": proband}}

    package = base / family_id
    _write_package(package, family_id, {mother: 17, proband: 45, father: 20})
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(package),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    repeats = next((d for d in result.datasets if d.dataset_type == "repeats_trgt"), None)
    out["import"] = {
        "completed": result.completed,
        "error": result.error,
        "status": repeats.status if repeats else None,
    }
    async with sessionmaker() as session:
        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
        out["after_import"] = await _stored_counts(session, family_uuid)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        async def upload(text_value: str, filename: str) -> dict:
            response = await ac.post(
                f"/api/repeat-expansions/upload/{proband}",
                params={"overwrite": "true"},
                files={"file": (filename, text_value.encode("utf-8"), "text/plain")},
            )
            return {"status": response.status_code, "json": response.json()}

        out["wrong_member"] = await upload(_trgt_vcf([f"{mother}_sort"], [17]), "mother_tr.vcf")
        async with sessionmaker() as session:
            out["after_wrong_member"] = await _stored_counts(session, family_uuid)

        out["family_vcf"] = await upload(
            _trgt_vcf([f"{mother}_sort", f"{proband}_sort", f"{father}_sort"], [18, 50, 21]),
            "family.trgt.vcf",
        )
        async with sessionmaker() as session:
            out["after_family_vcf"] = await _stored_counts(session, family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("trgt_sample_column")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"TRGT_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_each_members_own_file_is_read_from_its_own_column(run) -> None:
    assert run["import"]["completed"] is True, run["import"]
    assert run["import"]["status"] == "imported", run["import"]
    mother, proband, father = (run["samples"][role] for role in ("mother", "proband", "father"))
    assert run["after_import"] == {
        mother: ([17], f"{mother}_sort"),
        proband: ([45], f"{proband}_sort"),
        father: ([20], f"{father}_sort"),
    }


def test_another_members_file_is_refused_and_the_stored_calls_stay(run) -> None:
    mother = run["samples"]["mother"]
    assert run["wrong_member"]["status"] == 400, run["wrong_member"]
    assert f"'{mother}_sort' is {mother}" in run["wrong_member"]["json"]["detail"]
    # The overwrite deleted the child's calls before the file was read; the refusal
    # rolls that delete back.
    assert run["after_wrong_member"] == run["after_import"]


def test_a_family_vcf_uploaded_for_the_child_stores_the_childs_column_only(run) -> None:
    mother, proband, father = (run["samples"][role] for role in ("mother", "proband", "father"))
    assert run["family_vcf"]["status"] == 200, run["family_vcf"]
    assert run["after_family_vcf"] == {
        mother: ([17], f"{mother}_sort"),
        proband: ([50], f"{proband}_sort"),
        father: ([20], f"{father}_sort"),
    }
