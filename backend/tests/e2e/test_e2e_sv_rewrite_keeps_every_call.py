"""A per-sample SV rewrite writes back every other call exactly as stored (E2E).

Two write paths rewrite a family's structural-variant rows to change one sample's calls:
the per-sample SV upload (it merges the uploaded sample's calls into that source's rows)
and the admin per-sample SV delete (it removes the sample's calls from every source). Both
must write back everything else as it was stored: each call's phase set (``calls.ps``),
the breakend's remote end, and the calls of samples the family view does not show. Here
that is FATHER once he is an inactive member, the state a member deletion leaves: the
member is marked inactive and his imported data is kept. It is set directly so that the
golden trio's pedigree stays as it is for later modules.

Over the golden trio (NeedlR SVs for all three members):

* PROBAND uploads a phased Sniffles deletion and a breakend; FATHER a phased call on the
  same deletion and a deletion of his own. FATHER is then made inactive.
* MOTHER's upload merges into the shared Sniffles deletion. The rewrite used to read the
  rows through the family view: PROBAND's phase set and the breakend's remote end came back
  empty, and FATHER's calls, and his own deletion, were not read at all and were deleted.
* The admin delete of MOTHER's SVs then used to drop FATHER's calls from every source.

The upload also records its caller (``##source=Sniffles2_2.2``) in the family's annotation
manifest. Afterwards FATHER is made active again, the manifest is put back, the Sniffles
rows are removed and the golden trio is re-imported. Everything runs in ONE event loop via
an in-process ``httpx.ASGITransport`` client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"

_VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "##contig=<ID=1,length=248956422>\n"
    "##contig=<ID=5,length=181538259>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)


def _vcf(*records: str) -> bytes:
    return (_VCF_HEADER + "".join(f"{record}\n" for record in records)).encode()


_PROBAND = _vcf(
    "1\t30000\tS.DEL.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT:PS\t0|1:7001",
    "1\t60000\tS.BND.2\tN\tN]5:3000000]\t30\tPASS\tSVTYPE=BND;SUPPORT=6\tGT\t0/1",
)
_FATHER = _vcf(
    "1\t30000\tS.DEL.7\tN\t<DEL>\t41\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=10\tGT:PS\t1|0:9001",
    "1\t80000\tS.DEL.8\tN\t<DEL>\t33\tPASS\tSVTYPE=DEL;SVLEN=-800;END=80800;SUPPORT=5\tGT\t0/1",
)
_MOTHER = _vcf(
    "1\t30000\tS.DEL.9\tN\t<DEL>\t38\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=8\tGT\t0/1",
)

_DEL_30000 = "1-30000-31000-DEL---"
_DEL_80000 = "1-80000-80800-DEL---"
_BND_60000 = "1-60000-60000-BND-5-3000000-3000000"


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


async def _entry_rows(family_uuid: str) -> list[dict]:
    """The family's live SV entry rows as stored, one dict per row (not grouped)."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.tests.e2e import _harness

    table = _structural_table_name(_harness.ASSEMBLY, "entries")
    rows = await execute_clickhouse(
        f"SELECT source, variantId, project_guid, `calls.sampleId`, `calls.gt`, `calls.ps` "
        f"FROM {table} WHERE family_guid = %(family_guid)s AND sign = 1 "
        "ORDER BY source, variantId, project_guid",
        {"family_guid": family_uuid},
    )
    return [
        {
            "source": str(source),
            "variant_id": str(variant_id),
            "project": str(project),
            "calls": {
                str(sample): (str(gt), None if ps is None else int(ps))
                for sample, gt, ps in zip(sample_ids, gts, phase_sets)
            },
        }
        for source, variant_id, project, sample_ids, gts, phase_sets in rows
    ]


async def _remote_ends(family_uuid: str) -> dict[str, int | None]:
    """Each SV's remote end as its latest details row holds it."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.tests.e2e import _harness

    table = _structural_table_name(_harness.ASSEMBLY, "variants/details")
    rows = await execute_clickhouse(
        f"SELECT variantId, remoteEnd FROM {table} FINAL WHERE family_guid = %(family_guid)s",
        {"family_guid": family_uuid},
    )
    return {str(variant_id): None if end is None else int(end) for variant_id, end in rows}


async def _set_member_active(family_uuid: str, sample_id: str, active: bool) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                """
                UPDATE family_members SET active = :active
                WHERE family_id = CAST(:family_uuid AS uuid)
                  AND sample_id = (
                      SELECT id FROM samples
                      WHERE sample_id = :sample_id AND family_id = CAST(:family_uuid AS uuid)
                  )
                """
            ),
            {"active": active, "family_uuid": family_uuid, "sample_id": sample_id},
        )
        await session.commit()


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


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import delete_family_structural_variants
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    manifest_before = await _manifest_modules(family_uuid)
    out: dict = {
        "facts": facts,
        "manifest_before": manifest_before,
        "rows_before": await _entry_rows(family_uuid),
    }

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"

            async def upload(sample: str, body: bytes) -> dict:
                return _cap(
                    await ac.post(
                        f"/api/structural-variants/upload/{sample}",
                        params={"source_format": "sniffles"},
                        files={"file": (f"{sample}.sniffles.vcf", body, "text/plain")},
                    )
                )

            out["proband"] = await upload("PROBAND", _PROBAND)
            out["manifest"] = _cap(await ac.get(f"/api/families/{FAMILY}/annotation-manifest"))
            out["father"] = await upload("FATHER", _FATHER)
            out["rows_before_rewrite"] = await _entry_rows(family_uuid)
            out["remote_ends_before_rewrite"] = await _remote_ends(family_uuid)

            await _set_member_active(family_uuid, "FATHER", False)
            out["mother"] = await upload("MOTHER", _MOTHER)
            out["rows_after_upload"] = await _entry_rows(family_uuid)
            out["remote_ends_after_upload"] = await _remote_ends(family_uuid)

            out["admin_delete"] = _cap(
                await ac.delete(
                    "/api/admin/data/samples/MOTHER/structural_variants", params={"confirm": "true"}
                )
            )
            out["rows_after_admin_delete"] = await _entry_rows(family_uuid)
            out["remote_ends_after_admin_delete"] = await _remote_ends(family_uuid)

            await _set_member_active(family_uuid, "FATHER", True)
            out["family_page"] = _cap(
                await ac.get(f"/api/families/{FAMILY}/structural-variants", params={"page_size": 100})
            )
    finally:
        # Restore the fixture state for any module that runs later.
        await _set_member_active(family_uuid, "FATHER", True)
        await _restore_manifest_modules(family_uuid, manifest_before)
        await delete_family_structural_variants(_harness.ASSEMBLY, family_uuid, source="sniffles")
        out["restored"] = await _harness.import_golden_trio(root)
        out["rows_restored"] = await _entry_rows(family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    base = tmp_path_factory.mktemp("golden_sv_rewrite")
    root = base / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", base / "data")
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _rows(rows: list[dict], source: str) -> dict[str, dict[str, tuple[str, int | None]]]:
    return {row["variant_id"]: row["calls"] for row in rows if row["source"] == source}


def test_uploads_store_phase_sets_and_the_breakend_remote_end(run) -> None:
    for step in ("proband", "father"):
        assert run[step]["status"] == 200, run[step]["text"]
    assert _rows(run["rows_before_rewrite"], "sniffles") == {
        _DEL_30000: {"PROBAND": ("0|1", 7001), "FATHER": ("1|0", 9001)},
        _BND_60000: {"PROBAND": ("0/1", None)},
        _DEL_80000: {"FATHER": ("0/1", None)},
    }
    assert run["remote_ends_before_rewrite"][_BND_60000] == 3000000


def test_upload_rewrite_keeps_phase_sets_remote_end_and_inactive_members_calls(run) -> None:
    assert run["mother"]["status"] == 200, run["mother"]["text"]
    assert run["mother"]["json"]["merged"] == 1
    # Before: PROBAND's phase set came back empty, FATHER (inactive) lost his call on the
    # shared deletion, and his own deletion was deleted.
    assert _rows(run["rows_after_upload"], "sniffles") == {
        _DEL_30000: {
            "FATHER": ("1|0", 9001),
            "MOTHER": ("0/1", None),
            "PROBAND": ("0|1", 7001),
        },
        _BND_60000: {"PROBAND": ("0/1", None)},
        _DEL_80000: {"FATHER": ("0/1", None)},
    }
    # Before: rewritten with an empty remote end.
    assert run["remote_ends_after_upload"][_BND_60000] == 3000000
    # Another source is not part of the upload's rewrite at all.
    assert _rows(run["rows_after_upload"], "needlr") == _rows(run["rows_before"], "needlr")


def test_admin_delete_removes_only_that_sample_and_keeps_every_other_call(run) -> None:
    assert run["admin_delete"]["status"] == 200, run["admin_delete"]["text"]
    rows = run["rows_after_admin_delete"]
    # Before: FATHER (inactive) lost every call, in both sources, along with the phase sets.
    assert _rows(rows, "needlr") == {
        "SVDEL1": {"FATHER": ("0/1", None), "PROBAND": ("0/1", None)},
        "SVBND1": {"FATHER": ("0/0", None), "PROBAND": ("0/1", None)},
    }
    assert _rows(rows, "sniffles") == {
        _DEL_30000: {"FATHER": ("1|0", 9001), "PROBAND": ("0|1", 7001)},
        _BND_60000: {"PROBAND": ("0/1", None)},
        _DEL_80000: {"FATHER": ("0/1", None)},
    }
    assert run["remote_ends_after_admin_delete"][_BND_60000] == 3000000
    # One row per SV and project, each in the project it was written under.
    assert len({(row["variant_id"], row["project"]) for row in rows}) == len(rows)
    assert {row["project"] for row in rows} == {run["facts"]["project_id"]}


def test_reactivated_member_sees_his_calls_again(run) -> None:
    page = run["family_page"]
    assert page["status"] == 200, page["text"]
    by_id = {variant["_id"]: variant for variant in page["json"]["variants"]}
    assert {g["sample"]: g["gt"] for g in by_id[_DEL_30000]["genotypes"]} == {
        "FATHER": "1|0",
        "PROBAND": "0|1",
    }
    assert _DEL_80000 in by_id


def test_upload_records_its_caller_in_the_annotation_manifest(run) -> None:
    manifest = run["manifest"]
    assert manifest["status"] == 200, manifest["text"]
    modules = {module["key"]: module for module in manifest["json"]["modules"]}
    # Before: a per-sample SV upload recorded no provenance at all.
    assert modules["sniffles"]["version"] == "2.2"
    assert modules["sniffles"]["detail"] == "sv caller"
    assert modules["sniffles"]["by_modality"] == {"sv": "2.2"}
    # What the package import recorded is kept beside it.
    before = json.loads(run["manifest_before"] or "{}")
    assert set(before) <= set(modules)


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    assert run["rows_restored"] == run["rows_before"]
