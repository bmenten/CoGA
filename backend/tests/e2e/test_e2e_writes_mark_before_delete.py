"""Writes that fail or stop after their delete leave the family marked (SAFE-28), E2E.

ClickHouse has no transactions. Here a trio (a new family id each run) goes through the PED,
structural-variant, small-variant and BED upload routes, the admin delete of a sample's SVs
and a family-structure change into real Postgres and ClickHouse:

* a structural-variant upload whose rewrite fails after its delete (an insert error,
  simulated) puts every sample's calls of that caller back, and leaves the family unmarked;
* one whose rows cannot be put back either (ClickHouse failing on) leaves the family marked
  -- an ``upload-…`` entry in ``import_unfinished``, written before the delete on a
  connection of its own -- for each sample's calls; sign-out is refused (409) with a message
  that names the single-file upload, and the mark goes, sample by sample, as each sample's
  file is uploaded again with overwrite;
* the admin delete of a sample's SVs whose rewrite fails puts the family's SVs back;
* a small-variant upload whose process stops after its delete (simulated: nothing after the
  insert runs, and its transaction ends with the connection) leaves the family marked for
  the callset, and an upload of the callset again removes the mark;
* a BED upload with overwrite completes: its mark, written while the upload's transaction
  holds the family's write locks, does not wait on them, and goes with its commit;
* a structure change that clears the family's data and then fails leaves the family's
  variants in ClickHouse (before: deleted first, with the family rolled back).

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why); app exceptions come back as a 500 response.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from io import BytesIO
import json
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ACK = "e2e: the import gate is under test"
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}
_SNIFFLES_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "##contig=<ID=1,length=248956422>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)
_DEL_30000 = "1\t30000\tS.DEL.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t{gt}\n"
_DEL_80000 = "1\t80000\tS.DEL.2\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-800;END=80800;SUPPORT=8\tGT\t0/1\n"
_DUP_50000 = "1\t50000\tS.DUP.3\tN\t<DUP>\t35\tPASS\tSVTYPE=DUP;SVLEN=500;END=50500;SUPPORT=7\tGT\t0/1\n"
# Each member's Sniffles calls: the child's and the father's deletion is one SV.
_SNIFFLES = {
    "child": _SNIFFLES_HEADER + _DEL_30000.format(gt="0/1"),
    "father": _SNIFFLES_HEADER + _DEL_30000.format(gt="1/1") + _DEL_80000,
    "mother": _SNIFFLES_HEADER + _DUP_50000,
}
_VCF_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{mother}\t{child}\n"


def _calls(count: int) -> str:
    return "".join(
        f"1\t{10_000 + index * 100}\t.\tA\tG\t50\tPASS\t.\tGT:GQ:DP\t0/1:40:30\t0/0:40:31\t0/1:40:29\n"
        for index in range(count)
    )


class _ProcessStopped(BaseException):
    """The process running the upload stops there: nothing after it runs."""


async def _exercise(family_id: str) -> dict[str, Any]:
    from fastapi import UploadFile
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.routers.families_small_variants import _family_sample_contexts
    from backend.app.services import (
        clickhouse_variant_storage,
        family_structure_service,
        variant_upload_service,
    )
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
        fetch_family_structural_variant_rows,
    )
    from backend.app.services.family_metadata_context import build_family_metadata_context
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    names = {role: f"{role.upper()}_{family_id}" for role in ("father", "mother", "child")}
    ped = (
        f"{family_id}\t{names['father']}\t0\t0\t1\t1\n"
        f"{family_id}\t{names['mother']}\t0\t0\t2\t1\n"
        f"{family_id}\t{names['child']}\t{names['father']}\t{names['mother']}\t2\t2\n"
    )
    header = _VCF_HEADER.format(**names)
    roles = {name: role for role, name in names.items()}
    out: dict[str, Any] = {"steps": {}, "names": names}

    async def state() -> dict[str, Any]:
        """The family's Sniffles calls (by SV: role and genotype), its directly called
        small variants, and its import state."""
        async with sessionmaker() as session:
            family = (
                await session.execute(
                    text(
                        "SELECT id::text AS family_uuid, metadata -> 'import_unfinished' AS unfinished, "
                        "metadata -> 'import_incomplete' AS flag FROM families WHERE family_id = :f"
                    ),
                    {"f": family_id},
                )
            ).mappings().one()
        rows = await fetch_family_structural_variant_rows(
            _harness.ASSEMBLY, family["family_uuid"], source="sniffles"
        )
        return {
            "family_uuid": family["family_uuid"],
            "sniffles": {
                row.record.variant_id: {roles.get(call.sample, call.sample): call.gt for call in row.record.calls}
                for row in rows
            },
            "clair3": await count_family_small_variants(
                _harness.ASSEMBLY, family["family_uuid"], project_ids=[project_id], source="clair3"
            ),
            "unfinished": family["unfinished"],
            "flag": family["flag"],
        }

    real_insert = clickhouse_variant_storage.insert_structural_variant_records

    def failing_inserts(times: int):
        """The SV insert, failing its first ``times`` calls."""
        left = {"times": times}

        async def insert(*args: Any, **kwargs: Any) -> None:
            if left["times"] > 0:
                left["times"] -= 1
                raise RuntimeError("ClickHouse insert failed")
            await real_insert(*args, **kwargs)

        return insert

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

        async def step(name: str, response) -> None:
            out["steps"][name] = {"status": response.status_code, "text": response.text, **await state()}

        async def sv_upload(name: str, role: str, *, overwrite: bool, insert=None) -> None:
            mp = pytest.MonkeyPatch()
            if insert is not None:
                mp.setattr(clickhouse_variant_storage, "insert_structural_variant_records", insert)
            try:
                response = await ac.post(
                    f"/api/structural-variants/upload/{names[role]}",
                    params={"overwrite": str(overwrite).lower(), "source_format": "sniffles"},
                    files={"file": (f"{role}.sniffles.vcf", _SNIFFLES[role].encode(), "text/plain")},
                )
            finally:
                mp.undo()
            await step(name, response)

        response = await ac.post(
            "/api/ped/upload",
            params={"project_id": project_id, "overwrite": "false"},
            files={"file": (f"{family_id}.ped", ped.encode(), "text/plain")},
        )
        await step("ped", response)
        await sv_upload("sv_child", "child", overwrite=False)
        await sv_upload("sv_father", "father", overwrite=False)

        # The rewrite's insert fails once: its restore writes the stored rows back.
        await sv_upload("sv_restored", "mother", overwrite=False, insert=failing_inserts(1))
        # ClickHouse keeps failing: the rows cannot be put back.
        await sv_upload("sv_not_restored", "mother", overwrite=False, insert=failing_inserts(99))
        await step("signout_marked", await ac.post(f"/api/families/{family_id}/report/sign-out", json=_OTHER_GATES))
        page = await ac.get(f"/api/families/{family_id}")
        out["family_page"] = {"status": page.status_code, "json": page.json()}
        await sv_upload("sv_child_again", "child", overwrite=True)
        await sv_upload("sv_father_again", "father", overwrite=True)
        await sv_upload("sv_mother_again", "mother", overwrite=True)

        # The admin delete of the mother's SVs, whose rewrite of every source fails once.
        mp = pytest.MonkeyPatch()
        mp.setattr(clickhouse_variant_storage, "insert_structural_variant_records", failing_inserts(1))
        try:
            response = await ac.delete(
                f"/api/admin/data/samples/{names['mother']}/structural_variants", params={"confirm": "true"}
            )
        finally:
            mp.undo()
        await step("admin_delete_restored", response)

        response = await ac.post(
            f"/api/families/{family_id}/small-variants/upload",
            params={"overwrite": "false", "source_format": "clair3"},
            files={"file": (f"{family_id}.vcf", (header + _calls(4)).encode(), "text/plain")},
        )
        await step("clair3", response)

        # The process stops after the upload's delete: the upload is run in this process, and
        # nothing after its first insert runs; its transaction ends with its connection.
        async def stopped(*_args: Any, **_kwargs: Any) -> None:
            raise _ProcessStopped()

        mp = pytest.MonkeyPatch()
        mp.setattr(variant_upload_service, "insert_small_variant_records", stopped)
        try:
            async with sessionmaker() as session:
                context = await build_family_metadata_context(session, family_identifier=family_id, user=admin)
                try:
                    await variant_upload_service.upload_family_small_variant_file(
                        session,
                        context=context,
                        sample_contexts=_family_sample_contexts(context),
                        file=UploadFile(file=BytesIO((header + _calls(3)).encode()), filename="family.vcf"),
                        overwrite=True,
                        format_hint="clair3",
                        record_upload=True,
                    )
                except _ProcessStopped:
                    out["process_stopped"] = True
        finally:
            mp.undo()
        out["steps"]["clair3_stopped"] = {"status": None, "text": "", **await state()}
        await step("signout_stopped", await ac.post(f"/api/families/{family_id}/report/sign-out", json=_OTHER_GATES))
        response = await ac.post(
            f"/api/families/{family_id}/small-variants/upload",
            params={"overwrite": "true", "source_format": "clair3"},
            files={"file": (f"{family_id}.vcf", (header + _calls(5)).encode(), "text/plain")},
        )
        await step("clair3_again", response)

        response = await ac.post(
            f"/api/bed/upload/{names['child']}/coverage",
            # A coverage BED declares what it holds (TRACE-9).
            params={"overwrite": "true", "content": "read_depth"},
            files={"file": ("coverage.bed", b"1\t0\t1000\tbin1\t31.0\n1\t1000\t2000\tbin2\t29.5\n", "text/plain")},
        )
        await step("bed", response)

        # A structure change that clears the data, then fails writing its structure version.
        async def version_fails(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("structure version could not be written")

        mp = pytest.MonkeyPatch()
        mp.setattr(family_structure_service, "_record_family_structure_version", version_fails)
        try:
            response = await ac.put(
                f"/api/families/{family_id}/structure",
                json={"clear_existing_genomic_data": True, "change_reason": "e2e clear that fails"},
            )
        finally:
            mp.undo()
        await step("structure_clear_fails", response)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The uploads keep a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("writes_mark_before_delete"))
    try:
        return _harness.run_async(lambda: _exercise(f"MARK_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _ids(step: dict[str, Any]) -> list[str]:
    return sorted(step["sniffles"])


def _mark(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    [(key, entry)] = (step["unfinished"] or {}).items()
    return key, entry


def test_the_family_and_its_sv_calls_are_uploaded(run) -> None:
    for name in ("ped", "sv_child", "sv_father"):
        assert run["steps"][name]["status"] == 200, run["steps"][name]["text"]
    stored = run["steps"]["sv_father"]
    assert sorted(stored["sniffles"].values(), key=len) == [{"father": "0/1"}, {"child": "0/1", "father": "1/1"}]
    assert stored["unfinished"] is None


def test_an_sv_upload_whose_rewrite_fails_puts_every_samples_calls_back(run) -> None:
    restored = run["steps"]["sv_restored"]
    assert restored["status"] == 500
    # Before: the child's and the father's Sniffles calls were gone with the failed write.
    assert restored["sniffles"] == run["steps"]["sv_father"]["sniffles"]
    assert restored["unfinished"] is None and restored["flag"] is None


def test_an_sv_upload_whose_rows_cannot_be_put_back_marks_the_family_for_each_sample(run) -> None:
    failed = run["steps"]["sv_not_restored"]
    assert failed["status"] == 500
    assert failed["sniffles"] == {}
    key, mark = _mark(failed)
    assert key.startswith("upload-")
    assert (mark["job_id"], mark["datasets"], mark["finished_datasets"]) == (None, ["sv_sniffles"], [])
    names = run["names"]
    assert mark["whole_family"] == [
        {"dataset": "sv_sniffles", "scope": {"source": None, "samples": [names[role]]}}
        for role in sorted(("child", "father", "mother"), key=lambda role: names[role])
    ]
    # Every family page reads it.
    page = run["family_page"]
    assert page["status"] == 200 and page["json"]["metadata"]["import_unfinished"] == failed["unfinished"]


def test_the_mark_refuses_sign_out_and_names_the_upload(run) -> None:
    refused = run["steps"]["signout_marked"]
    assert refused["status"] == 409, refused["text"]
    detail = json.loads(refused["text"])["detail"]
    assert detail["gate"] == "import_incomplete"
    key, _mark_entry = _mark(run["steps"]["sv_not_restored"])
    assert list(detail["import_unfinished"]) == [key]
    assert detail["import_unfinished"][key]["datasets"] == ["sv_sniffles"]
    assert "a single-file upload (" in detail["message"]
    assert "did not finish, so sv_sniffles may be partly written or missing" in detail["message"]


def test_each_samples_upload_again_strikes_its_calls_off_the_mark(run) -> None:
    names = run["names"]
    after_child = run["steps"]["sv_child_again"]
    assert after_child["status"] == 200, after_child["text"]
    assert [item["scope"]["samples"] for item in _mark(after_child)[1]["whole_family"]] == sorted(
        [[names["father"]], [names["mother"]]]
    )
    after_father = run["steps"]["sv_father_again"]
    assert [item["scope"]["samples"] for item in _mark(after_father)[1]["whole_family"]] == [[names["mother"]]]
    after_mother = run["steps"]["sv_mother_again"]
    assert after_mother["status"] == 200, after_mother["text"]
    assert after_mother["unfinished"] is None
    assert len(after_mother["sniffles"]) == 3


def test_an_admin_delete_whose_rewrite_fails_puts_the_familys_svs_back(run) -> None:
    deleted = run["steps"]["admin_delete_restored"]
    assert deleted["status"] == 500
    # Before: every SV of the family was gone with the failed write.
    assert deleted["sniffles"] == run["steps"]["sv_mother_again"]["sniffles"]
    assert deleted["unfinished"] is None


def test_a_small_variant_upload_whose_process_stops_after_its_delete_leaves_the_family_marked(run) -> None:
    assert run["steps"]["clair3"]["status"] == 200 and run["steps"]["clair3"]["clair3"] == 4
    assert run.get("process_stopped") is True
    stopped = run["steps"]["clair3_stopped"]
    # The callset is gone; its upload's transaction never committed, its mark did.
    assert stopped["clair3"] == 0
    key, mark = _mark(stopped)
    assert key.startswith("upload-")
    assert mark["whole_family"] == [{"dataset": "snv", "scope": {"source": "clair3", "samples": None}}]
    refused = run["steps"]["signout_stopped"]
    assert refused["status"] == 409, refused["text"]
    assert "did not finish, so snv may be partly written or missing" in refused["text"]
    again = run["steps"]["clair3_again"]
    assert again["status"] == 200, again["text"]
    assert again["clair3"] == 5 and again["unfinished"] is None


def test_a_bed_upload_marks_and_unmarks_the_family_under_its_own_locks(run) -> None:
    bed = run["steps"]["bed"]
    assert bed["status"] == 200, bed["text"]
    assert bed["unfinished"] is None


def test_a_structure_change_that_fails_after_its_clear_leaves_the_familys_variants(run) -> None:
    failed = run["steps"]["structure_clear_fails"]
    assert failed["status"] == 500
    # Before: the variants were deleted before the structure change's own writes, and the
    # rollback brought the family back without them.
    assert failed["clair3"] == run["steps"]["bed"]["clair3"] == 5
    assert failed["sniffles"] == run["steps"]["bed"]["sniffles"]
