"""A family's variant writes run one at a time (E2E).

Each write of a family's variants reads the rows it rewrites, deletes them and inserts them
again changed, in ClickHouse, which has no transactions: the per-sample SV upload merges its
file's calls into its source's stored rows, the admin per-sample SV delete writes the
family's rows back without the sample's calls, the per-sample small-variant upload (the
package import's mitochondrial path) merges its file into the callset's rows. Two such writes
of one family ran at once, each from what it had read before the other wrote, and both
answered 200: one writer's calls were lost, or a variant was stored twice with different
calls and a part merge then kept only one of the rows.

Over the golden trio (NeedlR SVs) this starts writes of the family together, each on a
request or session of its own, so on a Postgres connection of its own, as two workers or
two processes run them:

* PROBAND's, MOTHER's and FATHER's Sniffles uploads of one deletion, through
  ``POST /structural-variants/upload/{sample_id}``;
* the admin delete of FATHER's SVs (``DELETE /admin/data/samples/FATHER/structural_variants``)
  and MOTHER's Sniffles upload of the deletion and a duplication;
* PROBAND's and MOTHER's per-sample mitochondrial uploads (``upload_family_small_variant_file``
  with ``overwrite_scope="samples"``).

Each storage read is wrapped so that it waits, for a moment at most, until the other writes
have read too: the interleaving writes started together can take, made certain. Without a
lock every write then wrote from what it had read. With one, a write reads only once the
write before it has committed, and the earlier write's wait ends by its timeout.

After each round every write answered 200, each variant is one live row holding every call,
also after a forced part merge (``OPTIMIZE … FINAL``), and each SV entry has its details and
lookup row. Then the lock itself is checked on Postgres: a writer holding it is refused (404)
when its sample or family is gone, and a second writer waits for the first one's commit.
Afterwards the added rows are removed, the annotation manifest is put back and
the golden trio is re-imported. Everything runs in ONE event loop via an in-process
``httpx.ASGITransport`` client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"

_DEL = "1-30000-31000-DEL---"
_DUP = "1-50000-50500-DUP---"
# How long a write that has read waits for the others to read. Long enough for writes
# started together to reach their read; with the lock it is what the first write loses.
_READ_WAIT_SECONDS = 1.5


def _sv_vcf(*records: str) -> bytes:
    return (
        "##fileformat=VCFv4.2\n"
        "##source=Sniffles2_2.2\n"
        "##contig=<ID=1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
        + "".join(f"{record}\n" for record in records)
    ).encode()


def _del(gt: str) -> str:
    return f"1\t30000\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=14\tGT\t{gt}"


def _dup(gt: str) -> str:
    return f"1\t50000\tSniffles2.DUP.2\tN\t<DUP>\t35\tPASS\tSVTYPE=DUP;SVLEN=500;END=50500;SUPPORT=7\tGT\t{gt}"


def _mito_vcf(sample: str, *positions: int) -> bytes:
    return (
        "##fileformat=VCFv4.2\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(
            f"chrM\t{pos}\t.\tA\tG\t50\tPASS\t.\tGT:DP:AD:VAF\t1/1:500:0,500:1\n" for pos in positions
        )
    ).encode()


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


class _Reads:
    """Holds each write after its storage read until ``writers`` writes have read, or
    ``_READ_WAIT_SECONDS`` have passed."""

    def __init__(self) -> None:
        self.writers = 0
        self.reads = 0
        self._all_read = asyncio.Event()

    def expect(self, writers: int) -> None:
        self.writers, self.reads, self._all_read = writers, 0, asyncio.Event()

    def wrap(self, read):
        async def read_then_wait(*args, **kwargs):
            rows = await read(*args, **kwargs)
            self.reads += 1
            if self.reads >= self.writers:
                self._all_read.set()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._all_read.wait(), timeout=_READ_WAIT_SECONDS)
            return rows

        return read_then_wait


async def _sv_rows(family_uuid: str, *, final: bool = False) -> dict[str, list[tuple]]:
    """The family's rows in the three SV tables: as stored (every live row a merge has not
    yet collapsed), or with ``final`` as a merge leaves them."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.tests.e2e import _harness

    modifier = " FINAL" if final else ""
    out: dict[str, list[tuple]] = {}
    entries = await execute_clickhouse(
        "SELECT source, variantId, key, `calls.sampleId`, `calls.gt` "
        f"FROM {_structural_table_name(_harness.ASSEMBLY, 'entries')}{modifier} "
        "WHERE family_guid = %(family_guid)s AND sign = 1 ORDER BY source, variantId, key",
        {"family_guid": family_uuid},
    )
    out["entries"] = [
        (str(source), str(variant_id), int(key), dict(zip(map(str, samples), map(str, gts))))
        for source, variant_id, key, samples, gts in entries
    ]
    for suffix in ("variants/details", "key_lookup"):
        rows = await execute_clickhouse(
            f"SELECT source, variantId, key FROM {_structural_table_name(_harness.ASSEMBLY, suffix)}"
            f"{modifier} WHERE family_guid = %(family_guid)s ORDER BY source, variantId, key",
            {"family_guid": family_uuid},
        )
        out[suffix] = [(str(source), str(variant_id), int(key)) for source, variant_id, key in rows]
    return out


async def _mito_rows(family_uuid: str, *, final: bool = False) -> list[tuple]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.tests.e2e import _harness

    modifier = " FINAL" if final else ""
    rows = await execute_clickhouse(
        "SELECT variantId, `calls.sampleId`, `calls.gt` "
        f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')}{modifier} "
        "WHERE family_guid = %(family_guid)s AND source = 'mito' AND sign = 1 ORDER BY variantId",
        {"family_guid": family_uuid},
    )
    return [
        (str(variant_id), dict(zip(map(str, samples), map(str, gts))))
        for variant_id, samples, gts in rows
    ]


async def _merge_parts() -> None:
    """Force the part merges ClickHouse would otherwise run in the background."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name, _structural_table_name
    from backend.tests.e2e import _harness

    for table in (
        _structural_table_name(_harness.ASSEMBLY, "entries"),
        _structural_table_name(_harness.ASSEMBLY, "variants/details"),
        _structural_table_name(_harness.ASSEMBLY, "key_lookup"),
        _small_table_name(_harness.ASSEMBLY, "entries"),
    ):
        await execute_clickhouse(f"OPTIMIZE TABLE {table} FINAL")


async def _manifest_modules(family_uuid: str) -> str | None:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        return (
            await session.execute(
                text(
                    "SELECT modules::text FROM family_annotation_manifest "
                    "WHERE family_id = CAST(:family_uuid AS uuid)"
                ),
                {"family_uuid": family_uuid},
            )
        ).scalar_one_or_none()


async def _restore_manifest_modules(family_uuid: str, modules: str | None) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        if modules is None:
            await session.execute(
                text("DELETE FROM family_annotation_manifest WHERE family_id = CAST(:f AS uuid)"),
                {"f": family_uuid},
            )
        else:
            await session.execute(
                text(
                    "UPDATE family_annotation_manifest SET modules = CAST(:modules AS jsonb) "
                    "WHERE family_id = CAST(:f AS uuid)"
                ),
                {"modules": modules, "f": family_uuid},
            )
        await session.commit()


async def _remove_added_rows(family_uuid: str) -> None:
    """Remove what this module adds. Idempotent."""
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_small_variants,
        delete_family_structural_variants,
    )
    from backend.tests.e2e import _harness

    await delete_family_structural_variants(_harness.ASSEMBLY, family_uuid, source="sniffles")
    await delete_family_small_variants(_harness.ASSEMBLY, family_uuid, source="mito")


async def _lock_checks(family_uuid: str) -> dict:
    """The lock itself, on Postgres: what a writer finds once it holds it, and a second
    writer's wait for the first's commit."""
    from fastapi import HTTPException

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_variant_write_lock import VARIANT_TYPES, lock_family_variant_writes

    maker = get_postgres_sessionmaker()
    async with maker() as session:
        samples = list(
            (
                await session.execute(
                    text("SELECT id::text FROM samples WHERE family_id = CAST(:f AS uuid)"),
                    {"f": family_uuid},
                )
            ).scalars().all()
        )
    gone = "00000000-0000-4000-8000-0000000e2e70"
    out: dict = {}
    for label, family, named in (
        ("every sample", family_uuid, samples),
        ("no sample named", family_uuid, []),
        ("a sample gone", family_uuid, [*samples, gone]),
        ("the family gone", gone, samples),
    ):
        async with maker() as session:
            try:
                await lock_family_variant_writes(session, family, VARIANT_TYPES, samples=named)
                out[label] = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
                        )
                    )
                ).scalar_one()
            except HTTPException as exc:
                out[label] = (exc.status_code, exc.detail)
            await session.rollback()
    async with maker() as first, maker() as second:
        await lock_family_variant_writes(first, family_uuid, VARIANT_TYPES)
        waiter = asyncio.create_task(lock_family_variant_writes(second, family_uuid, VARIANT_TYPES))
        await asyncio.sleep(0.5)
        out["second waits"] = not waiter.done()
        await first.commit()
        await asyncio.wait_for(waiter, timeout=5)
        out["second granted after the commit"] = waiter.done()
        await second.rollback()
    return out


async def _mito_upload(admin, sample: str, *positions: int) -> dict:
    """One sample's mitochondrial calls, merged into the family's ``mito`` rows as the
    package import's ``mito`` dataset does it, on a session of its own."""
    from fastapi import UploadFile
    from io import BytesIO

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_metadata_context import build_family_metadata_context
    from backend.app.services.family_package_registration import _family_sample_contexts
    from backend.app.services.variant_upload_service import upload_family_small_variant_file

    async with get_postgres_sessionmaker()() as session:
        context = await build_family_metadata_context(session, family_identifier=FAMILY, user=admin)
        return await upload_family_small_variant_file(
            session,
            context=context,
            sample_contexts=_family_sample_contexts(context),
            file=UploadFile(file=BytesIO(_mito_vcf(sample, *positions)), filename=f"{sample}.mito.vcf"),
            overwrite=True,
            format_hint="mito",
            overwrite_scope="samples",
        )


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import admin_service, variant_upload_service
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    # A run that failed half-way may have left its rows behind.
    await _remove_added_rows(family_uuid)
    manifest_before = await _manifest_modules(family_uuid)
    async with get_postgres_sessionmaker()() as session:
        admin, _project_id, _assembly_id = await _harness.ensure_e2e_project(session)
    out: dict = {"facts": facts, "golden": await _sv_rows(family_uuid)}

    reads = _Reads()
    mp = pytest.MonkeyPatch()
    for module, name in (
        (variant_upload_service, "fetch_family_structural_variant_rows"),
        (admin_service, "fetch_family_structural_variant_rows"),
        (variant_upload_service, "fetch_family_small_variant_entries"),
    ):
        mp.setattr(module, name, reads.wrap(getattr(module, name)))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"

            async def upload(sample: str, body: bytes) -> dict:
                return _cap(
                    await ac.post(
                        f"/api/structural-variants/upload/{sample}",
                        params={"overwrite": "true", "source_format": "sniffles"},
                        files={"file": (f"{sample}.sniffles.vcf", body, "text/plain")},
                    )
                )

            async def delete_svs(sample: str) -> dict:
                return _cap(
                    await ac.delete(
                        f"/api/admin/data/samples/{sample}/structural_variants",
                        params={"confirm": "true"},
                    )
                )

            reads.expect(3)
            out["uploads"] = await asyncio.gather(
                upload("PROBAND", _sv_vcf(_del("0/1"))),
                upload("MOTHER", _sv_vcf(_del("1/1"))),
                upload("FATHER", _sv_vcf(_del("0/1"))),
            )
            out["after_uploads"] = await _sv_rows(family_uuid)

            reads.expect(2)
            out["delete_and_upload"] = await asyncio.gather(
                delete_svs("FATHER"),
                upload("MOTHER", _sv_vcf(_del("1/1"), _dup("0/1"))),
            )
            out["after_delete_and_upload"] = await _sv_rows(family_uuid)

            reads.expect(2)
            out["mito_uploads"] = await asyncio.gather(
                _mito_upload(admin, "PROBAND", 73, 3243),
                _mito_upload(admin, "MOTHER", 73, 16519),
            )
            out["mito"] = await _mito_rows(family_uuid)

            await _merge_parts()
            out["merged"] = await _sv_rows(family_uuid)
            out["final"] = await _sv_rows(family_uuid, final=True)
            out["mito_merged"] = await _mito_rows(family_uuid)
            out["page"] = _cap(
                await ac.get(f"/api/families/{FAMILY}/structural-variants", params={"page_size": 100})
            )
        out["lock"] = await _lock_checks(family_uuid)
    finally:
        mp.undo()
        # Restore the fixture state for any module that runs later.
        await _remove_added_rows(family_uuid)
        await _restore_manifest_modules(family_uuid, manifest_before)
        out["restored"] = await _harness.import_golden_trio(root)
        out["rows_restored"] = await _sv_rows(family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    base = tmp_path_factory.mktemp("golden_variant_writes")
    root = base / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    # The uploads keep a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", base / "data")
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _calls(rows: dict[str, list[tuple]], source: str) -> dict[str, list[dict[str, str]]]:
    """Each live entries row's calls, by SV: one dict per row stored."""
    out: dict[str, list[dict[str, str]]] = {}
    for row_source, variant_id, _key, calls in rows["entries"]:
        if row_source == source:
            out.setdefault(variant_id, []).append(calls)
    return out


def _without_father(rows: dict[str, list[tuple]]) -> dict[str, list[dict[str, str]]]:
    kept: dict[str, list[dict[str, str]]] = {}
    for variant_id, calls in _calls(rows, "needlr").items():
        remaining = [
            {sample: gt for sample, gt in row.items() if sample != "FATHER"} for row in calls
        ]
        remaining = [row for row in remaining if row]
        if remaining:
            kept[variant_id] = remaining
    return kept


def test_every_write_is_answered(run) -> None:
    for step in run["uploads"] + run["delete_and_upload"]:
        assert step["status"] == 200, step["text"]
    assert [result["inserted"] for result in run["mito_uploads"]] == [2, 2]


def test_three_uploads_of_one_sv_leave_one_row_with_every_call(run) -> None:
    # Before: each upload wrote back what it had read before the others wrote, so the
    # deletion was stored once per upload, each row with one sample's call, or only the
    # last upload's call was left.
    assert _calls(run["after_uploads"], "sniffles") == {
        _DEL: [{"FATHER": "0/1", "MOTHER": "1/1", "PROBAND": "0/1"}]
    }
    assert _calls(run["after_uploads"], "needlr") == _calls(run["golden"], "needlr")


def test_an_admin_delete_and_an_upload_keep_each_others_change(run) -> None:
    rows = run["after_delete_and_upload"]
    # Before: the upload wrote the father's call back, or the admin delete wrote the
    # family's rows back without the mother's duplication.
    assert _calls(rows, "sniffles") == {
        _DEL: [{"MOTHER": "1/1", "PROBAND": "0/1"}],
        _DUP: [{"MOTHER": "0/1"}],
    }
    assert _calls(rows, "needlr") == _without_father(run["golden"])


def test_two_mitochondrial_uploads_keep_both_samples_calls(run) -> None:
    expected = [
        ("M-16519-A-G", {"MOTHER": "1/1"}),
        ("M-3243-A-G", {"PROBAND": "1/1"}),
        ("M-73-A-G", {"MOTHER": "1/1", "PROBAND": "1/1"}),
    ]
    # Before: the second rewrite removed the first upload's calls, or stored chrM 73 twice.
    assert run["mito"] == expected
    assert run["mito_merged"] == expected


def test_a_part_merge_keeps_every_call(run) -> None:
    for rows in (run["merged"], run["final"]):
        assert _calls(rows, "sniffles") == _calls(run["after_delete_and_upload"], "sniffles")
        assert _calls(rows, "needlr") == _without_father(run["golden"])


def test_every_sv_entry_keeps_its_details_and_lookup_row(run) -> None:
    rows = run["final"]
    entry_keys = {(source, variant_id, key) for source, variant_id, key, _calls in rows["entries"]}
    for suffix in ("variants/details", "key_lookup"):
        assert entry_keys <= set(rows[suffix]), suffix


def test_the_family_page_shows_each_call_once(run) -> None:
    page = run["page"]
    assert page["status"] == 200, page["text"]
    sniffles = {
        variant["_id"]: {g["sample"]: g["gt"] for g in variant["genotypes"]}
        for variant in page["json"]["variants"]
        if variant["source"] == "sniffles"
    }
    assert sniffles == {_DEL: {"MOTHER": "1/1", "PROBAND": "0/1"}, _DUP: {"MOTHER": "0/1"}}


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    assert _calls(run["rows_restored"], "needlr") == _calls(run["golden"], "needlr")
    assert _calls(run["rows_restored"], "sniffles") == {}


def test_the_lock_waits_for_a_commit_and_refuses_a_gone_family_or_sample(run) -> None:
    lock = run["lock"]
    # Both of the family's locks, held by the writer's own connection.
    assert lock["every sample"] == 2
    assert lock["no sample named"] == 2
    assert lock["a sample gone"] == (404, "Sample not found")
    assert lock["the family gone"] == (404, "Family not found")
    assert lock["second waits"] is True
    assert lock["second granted after the commit"] is True
