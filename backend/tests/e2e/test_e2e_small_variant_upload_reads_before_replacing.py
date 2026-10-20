"""A small-variant upload with overwrite reads the whole file before it replaces the callset
(SAFE-25), E2E.

``POST /families/{family_id}/small-variants/upload`` with ``overwrite`` deleted the family's
rows of its callset, and for imputed genotypes the family's haplotype blocks, before it read
the file: a file it refused answered 400 with them gone (a gzip file cut short even answered
500), while Postgres, whose deletes the error rolled back, still recorded the blocks. Now the
upload reads and checks the whole file first, then deletes and writes. A write that fails
after the delete leaves the callset without its rows, and the family flagged
import-incomplete for it until it is uploaded again. A PED upload with overwrite deleted the
family's variants before checks that could still refuse it; it now deletes them last.

Here a trio (a new family id each run) goes through the PED and small-variant upload routes
into real Postgres and ClickHouse:

* a directly called callset (clair3) and imputed genotypes (glimpse2), which make the
  haplotype blocks, are uploaded;
* refused with overwrite, each leaving both callsets, the blocks and their records as they
  were: a VCF naming a sample the family does not have, a gzip file cut short after its first
  records (400, before: 500), one with no record it can store, and imputed genotypes cut
  short;
* a valid VCF with overwrite replaces the callset, and imputed genotypes replace theirs and
  the blocks;
* a write that fails after the delete (ClickHouse failing after the first batch, simulated)
  answers 500 with the callset gone and the family flagged import-incomplete for it, as its
  page reads it; an upload of the callset again removes the flag;
* a PED upload with overwrite that is refused (a header line read as a family) leaves the
  family's variants; one that is not replaces the family, whose variants then go.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why); app exceptions come back as a 500 response.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import gzip
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{mother}\t{child}\n"


def _calls(count: int, *, start: int = 10_000) -> str:
    """``count`` records of the directly called callset on chr1."""
    return "".join(
        f"1\t{start + index * 100}\t.\tA\tG\t50\tPASS\t.\tGT:GQ:DP\t0/1:40:30\t0/0:40:31\t0/1:40:29\n"
        for index in range(count)
    )


def _imputed(count: int, *, start: int = 20_000) -> str:
    """``count`` phased records of imputed genotypes on chr1, in one phase set."""
    return "".join(
        f"1\t{start + index * 100}\t.\tC\tT\t.\tPASS\t.\tGT:GP:PS\t0|1:0,1,0:{start}\t0|0:1,0,0:{start}\t"
        f"1|0:0,1,0:{start}\n"
        for index in range(count)
    )


def _cut_short(text: str) -> bytes:
    """``text`` gzipped, its last bytes lost: an interrupted copy."""
    whole = gzip.compress(text.encode())
    return whole[: len(whole) - 12]


async def _exercise(family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import variant_upload_service
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_rows
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    father, mother, child = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "CHILD"))
    names = {"father": father, "mother": mother, "child": child}
    header = _HEADER.format(**names)
    ped = (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{child}\t{father}\t{mother}\t2\t2\n"
    )
    out: dict[str, Any] = {"steps": {}}

    async def family_rows() -> dict[str, Any]:
        async with sessionmaker() as session:
            family = (
                await session.execute(
                    text(
                        "SELECT id::text AS family_uuid, metadata -> 'import_incomplete' AS flag, "
                        "metadata -> 'imported_scopes' AS imported "
                        "FROM families WHERE family_id = :f"
                    ),
                    {"f": family_id},
                )
            ).mappings().one()
            samples = {
                str(row["sample_id"]): str(row["id"])
                for row in (
                    await session.execute(
                        text(
                            "SELECT s.id::text AS id, s.sample_id FROM samples s "
                            "WHERE s.family_id = CAST(:u AS uuid)"
                        ),
                        {"u": family["family_uuid"]},
                    )
                ).mappings()
            }
            records = {
                str(row["sample_id"]): int(row["row_count"])
                for row in (
                    await session.execute(
                        text(
                            "SELECT s.sample_id, t.row_count FROM sample_interval_track_sources t "
                            "JOIN samples s ON s.id = t.sample_id "
                            "WHERE s.family_id = CAST(:u AS uuid) AND t.track_type = 'haplotype' "
                            "AND t.source = 'glimpse2'"
                        ),
                        {"u": family["family_uuid"]},
                    )
                ).mappings()
            }
        return {**family, "samples": samples, "block_records": records}

    async def state(family_uuid: str | None = None) -> dict[str, Any]:
        """What the family holds: each callset's rows, each member's haplotype blocks (rows in
        ClickHouse, and what Postgres records of them), and its import flag."""
        family = await family_rows()
        uuid = family_uuid or family["family_uuid"]
        return {
            "family_uuid": family["family_uuid"],
            "clair3": await count_family_small_variants(
                _harness.ASSEMBLY, uuid, project_ids=[project_id], source="clair3"
            ),
            "glimpse2": await count_family_small_variants(
                _harness.ASSEMBLY, uuid, project_ids=[project_id], source="glimpse2"
            ),
            "blocks": {
                name: await count_interval_track_rows(
                    _harness.ASSEMBLY,
                    family_uuid=uuid,
                    sample_uuid=sample_uuid,
                    track_type="haplotype",
                    source="glimpse2",
                )
                for name, sample_uuid in sorted(family["samples"].items())
            },
            "block_records": family["block_records"],
            "flag": family["flag"],
            "imported": family["imported"],
        }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

        async def ped_upload(name: str, body: str, *, overwrite: bool) -> None:
            response = await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id, "overwrite": str(overwrite).lower()},
                files={"file": (f"{family_id}.ped", body.encode(), "text/plain")},
            )
            out["steps"][name] = {"status": response.status_code, "text": response.text}

        async def upload(name: str, body: str | bytes, *, callset: str, overwrite: bool) -> None:
            data = body.encode() if isinstance(body, str) else body
            filename = f"{family_id}.vcf.gz" if isinstance(body, bytes) else f"{family_id}.vcf"
            response = await ac.post(
                f"/api/families/{family_id}/small-variants/upload",
                params={"overwrite": str(overwrite).lower(), "source_format": callset},
                files={"file": (filename, data, "application/octet-stream")},
            )
            out["steps"][name] = {
                "status": response.status_code,
                "text": response.text,
                **await state(),
            }

        await ped_upload("ped", ped, overwrite=False)
        await upload("clair3", header + _calls(3), callset="clair3", overwrite=False)
        await upload("glimpse2", header + _imputed(4), callset="glimpse2", overwrite=False)

        await upload(
            "unknown_sample",
            header.replace(f"\t{child}\n", "\tNOT_IN_THIS_FAMILY\n") + _calls(5),
            callset="clair3",
            overwrite=True,
        )
        await upload("cut_short", _cut_short(header + _calls(2_000)), callset="clair3", overwrite=True)
        # Each record's POS unreadable: none can be stored.
        await upload(
            "no_record", header + _calls(3).replace("1\t10", "1\tx10"), callset="clair3", overwrite=True
        )
        await upload("imputed_cut_short", _cut_short(header + _imputed(2_000)), callset="glimpse2", overwrite=True)

        await upload("clair3_again", header + _calls(5), callset="clair3", overwrite=True)
        await upload("glimpse2_again", header + _imputed(6, start=30_000), callset="glimpse2", overwrite=True)

        # ClickHouse failing after the first batch of the write (simulated): the callset's
        # rows were deleted before it, and the write's own are removed again.
        real_insert = variant_upload_service.insert_small_variant_records
        inserts = {"count": 0}

        async def failing_insert(*args: Any, **kwargs: Any) -> None:
            inserts["count"] += 1
            if inserts["count"] > 1:
                raise RuntimeError("ClickHouse insert failed")
            await real_insert(*args, **kwargs)

        mp = pytest.MonkeyPatch()
        mp.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", 1)
        mp.setattr(variant_upload_service, "insert_small_variant_records", failing_insert)
        try:
            await upload("write_fails", header + _calls(4), callset="clair3", overwrite=True)
        finally:
            mp.undo()
        response = await ac.get(f"/api/families/{family_id}")
        out["family_page"] = {"status": response.status_code, "json": response.json()}
        await upload("uploaded_again", header + _calls(4), callset="clair3", overwrite=False)

        before_ped = await state()
        await ped_upload("ped_with_header", "#FID IID PAT MAT SEX PHENOTYPE\n" + ped, overwrite=True)
        out["after_refused_ped"] = await state()
        await ped_upload("ped_replaced", ped, overwrite=True)
        out["after_ped"] = await state()
        # What the replaced family's uuid still holds.
        out["replaced_family"] = {
            "clair3": await count_family_small_variants(
                _harness.ASSEMBLY, before_ped["family_uuid"], project_ids=[project_id], source="clair3"
            ),
            "glimpse2": await count_family_small_variants(
                _harness.ASSEMBLY, before_ped["family_uuid"], project_ids=[project_id], source="glimpse2"
            ),
        }
        out["before_ped"] = before_ped
    out["names"] = names
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("small_variant_replace"))
    try:
        return _harness.run_async(lambda: _exercise(f"REPL_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _held(step: dict[str, Any]) -> dict[str, Any]:
    """What the family holds after a step, its uuid left out."""
    return {key: step[key] for key in ("clair3", "glimpse2", "blocks", "block_records", "flag", "imported")}


def test_the_family_and_both_callsets_are_uploaded(run) -> None:
    assert run["steps"]["ped"]["status"] == 200, run["steps"]["ped"]["text"]
    for name in ("clair3", "glimpse2"):
        assert run["steps"][name]["status"] == 200, run["steps"][name]["text"]
    stored = run["steps"]["glimpse2"]
    assert (stored["clair3"], stored["glimpse2"]) == (3, 4)
    # The haplotype blocks, and Postgres's record of each member's.
    assert sum(stored["blocks"].values()) > 0
    assert stored["block_records"] == {name: count for name, count in stored["blocks"].items() if count}


@pytest.mark.parametrize(
    "step, detail",
    [
        ("unknown_sample", "Sample 'NOT_IN_THIS_FAMILY' not found in family"),
        ("cut_short", "VCF file must be plain text or gzipped"),
        ("no_record", "No valid small-variant records found"),
        ("imputed_cut_short", "VCF file must be plain text or gzipped"),
    ],
)
def test_a_refused_upload_with_overwrite_leaves_the_callsets_and_the_blocks(run, step, detail) -> None:
    refused = run["steps"][step]
    # Before: 400 with the callset deleted (500 for a gzip file cut short), and the blocks'
    # rows gone while Postgres still recorded them.
    assert refused["status"] == 400, refused["text"]
    assert detail in refused["text"]
    assert _held(refused) == _held(run["steps"]["glimpse2"])


def test_a_valid_upload_with_overwrite_replaces_its_callset(run) -> None:
    again = run["steps"]["clair3_again"]
    assert again["status"] == 200, again["text"]
    assert (again["clair3"], again["glimpse2"]) == (5, 4)
    assert again["blocks"] == run["steps"]["glimpse2"]["blocks"]
    imputed = run["steps"]["glimpse2_again"]
    assert imputed["status"] == 200, imputed["text"]
    assert (imputed["clair3"], imputed["glimpse2"]) == (5, 6)
    assert sum(imputed["blocks"].values()) > 0
    assert imputed["block_records"] == {name: count for name, count in imputed["blocks"].items() if count}
    assert imputed["flag"] is None


def test_a_write_that_fails_after_the_delete_flags_the_family_until_it_is_uploaded_again(run) -> None:
    failed = run["steps"]["write_fails"]
    assert failed["status"] == 500
    # The callset is gone, its own first batch removed again; the imputed callset stays.
    assert (failed["clair3"], failed["glimpse2"]) == (0, 6)
    # Flagged import-incomplete for the callset, under no import job: every family page
    # warns, and sign-out needs it acknowledged (before: nothing marked the family).
    flag = failed["flag"]
    assert (flag["failed_datasets"], flag["imported_datasets"], flag["job_id"]) == (["snv"], [], None)
    assert flag["failed_scopes"] == {"snv": [{"scope": {"source": "clair3", "samples": None}, "job_id": None}]}
    page = run["family_page"]
    assert page["status"] == 200
    assert page["json"]["metadata"]["import_incomplete"] == flag
    # Uploaded again (nothing of it is stored, so without overwrite): the flag goes.
    again = run["steps"]["uploaded_again"]
    assert again["status"] == 200, again["text"]
    assert again["clair3"] == 4 and again["flag"] is None


def test_a_refused_ped_upload_with_overwrite_leaves_the_familys_variants(run) -> None:
    refused = run["steps"]["ped_with_header"]
    # The header line is read as a family whose phenotype is refused, after the replacement
    # began. Before: the family came back without its variants.
    assert refused["status"] == 400, refused["text"]
    assert _held(run["after_refused_ped"]) == _held(run["before_ped"])
    assert run["after_refused_ped"]["family_uuid"] == run["before_ped"]["family_uuid"]


def test_a_ped_upload_with_overwrite_still_replaces_the_family_and_its_variants(run) -> None:
    assert run["steps"]["ped_replaced"]["status"] == 200, run["steps"]["ped_replaced"]["text"]
    after = run["after_ped"]
    assert after["family_uuid"] != run["before_ped"]["family_uuid"]
    assert (after["clair3"], after["glimpse2"]) == (0, 0)
    assert run["replaced_family"] == {"clair3": 0, "glimpse2": 0}
