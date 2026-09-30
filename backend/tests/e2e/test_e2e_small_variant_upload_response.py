"""A successful family small-variant upload answers 200 with its result (E2E).

``POST /families/{family_id}/small-variants/upload`` is what the **Family Small Variants**
form on the Upload page sends. The rows are written and committed before the response is
built, so a response that fails is worse than no answer: the page reports an error for an
upload that worked, and the user uploads again. The result holds a list
(``excluded_filters``), a map (``annotation_provenance``) and a null
(``annotation_source``), and the endpoint used to declare it as ``Dict[str, int | str]``:
FastAPI refused every successful upload's response with a 500. ``source_format`` was
also passed through unchecked, so an unknown value stored the rows under a made-up
callset before the same 500.

The family is created here through the PED upload under a new id each run, so the golden
trio is never touched. The upload runs over HTTP through an in-process
``httpx.ASGITransport`` client, on one event loop (see test_e2e_api_contract.py for why);
app exceptions come back as a 500 response rather than being raised in the test.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_VCF = (
    "##fileformat=VCFv4.2\n"
    "##DeepVariant_version=1.10.0\n"
    "##reference=GRCh38\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{mother}\t{proband}\n"
    "1\t1000\t.\tA\tG\t50\tPASS\t.\tGT:GQ:DP:AD\t0/1:40:30:15,15\t0/0:40:30:30,0\t0/1:40:30:14,16\n"
    "1\t2000\t.\tC\tT\t45\tPASS\t.\tGT:GQ:DP:AD\t0/0:40:30:30,0\t0/1:40:30:15,15\t0/1:40:30:16,14\n"
    "1\t3000\t.\tG\tGA\t12\tLowQual\t.\tGT:GQ:DP:AD\t0/0:20:30:30,0\t0/0:20:30:30,0\t0/1:20:30:20,10\n"
)


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


async def _exercise(family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness
    from sqlalchemy import text

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    ped = (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
    )
    vcf = _VCF.format(father=father, mother=mother, proband=proband).encode()

    out: dict = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        out["ped"] = _cap(
            await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id},
                files={"file": (f"{family_id}.ped", ped.encode(), "text/plain")},
            )
        )

        async def upload(*, overwrite: bool, source_format: str = "auto") -> dict:
            return _cap(
                await ac.post(
                    f"/api/families/{family_id}/small-variants/upload",
                    params={"overwrite": str(overwrite).lower(), "source_format": source_format},
                    files={"file": (f"{family_id}.vcf", vcf, "text/plain")},
                )
            )

        out["unknown_format"] = await upload(overwrite=False, source_format="not-a-callset")
        out["first"] = await upload(overwrite=False)
        out["repeat"] = await upload(overwrite=False)
        out["overwrite"] = await upload(overwrite=True)

    async with get_postgres_sessionmaker()() as session:
        family_uuid = (
            await session.execute(
                text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id}
            )
        ).scalar_one()
    out["stored"] = await count_family_small_variants(
        _harness.ASSEMBLY, family_uuid, project_ids=[project_id]
    )
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("small_variant_upload"))
    try:
        return _harness.run_async(lambda: _exercise(f"UPL_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


_EXPECTED_BODY = {
    "inserted": 3,
    "skipped_malformed": 0,
    "skipped_filtered": 0,
    "excluded_filters": [],
    "haplotypes_inserted": 0,
    "source_format": "clair3",
    "annotation_rows": 0,
    "annotation_source": None,
    "annotation_version": "vcf_info",
    "annotation_provenance": {
        "deepvariant": {"version": "1.10.0"},
        "assembly": {"version": "GRCh38", "detail": "from VCF ##reference"},
    },
    "insert_batch_size": 1000,
}


def test_family_is_created_by_the_ped_upload(run) -> None:
    assert run["ped"]["status"] == 200, run["ped"]["text"]


def test_successful_upload_answers_200_with_its_result(run) -> None:
    first = run["first"]
    # Before the fix: 500, raised while FastAPI validated the result against
    # Dict[str, int | str], after the rows had been committed.
    assert first["status"] == 200, first["text"]
    assert first["json"] == _EXPECTED_BODY


def test_rows_of_the_upload_are_stored(run) -> None:
    # Every callset of the family: the refused upload below stored nothing.
    assert run["stored"] == 3


def test_unknown_source_format_is_refused_before_anything_is_stored(run) -> None:
    assert run["unknown_format"]["status"] == 422, run["unknown_format"]["text"]


def test_repeat_upload_is_refused_until_overwrite(run) -> None:
    assert run["repeat"]["status"] == 409, run["repeat"]["text"]
    overwrite = run["overwrite"]
    assert overwrite["status"] == 200, overwrite["text"]
    assert overwrite["json"] == _EXPECTED_BODY
