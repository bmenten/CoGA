"""The per-sample SV upload judges and rewrites only the source it uploads (E2E).

The golden trio's structural variants come from the NeedlR package import (source
``needlr``; every trio member has a call on both SVs). A Sniffles VCF uploaded for one
sample through ``POST /structural-variants/upload/{sample_id}`` is a different callset:

* its "already exists" check must look at that sample's Sniffles calls only. It used to
  count any call of the sample, so the first Sniffles upload for PROBAND was refused (409)
  because of PROBAND's NeedlR calls;
* an overwrite must rewrite the Sniffles rows only. It used to merge every source's
  records, strip the sample's calls from them and re-insert them all under a delete that
  only covered the Sniffles rows, so each NeedlR SV was stored twice (same key, both live)
  and, once ClickHouse merged the parts, the copy without the sample's calls was the one
  kept: PROBAND's NeedlR deletion in BRCA2 was gone.

The admin per-sample SV delete rewrites the whole family (every source) and must stay
that way; it is exercised here across both sources too. Afterwards the Sniffles rows are
removed and the golden trio re-imported, so any module that runs later sees the usual
state. Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client
(see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from collections import Counter
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"

_VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "##contig=<ID=1,length=248956422>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)


def _vcf(*records: str) -> bytes:
    return (_VCF_HEADER + "".join(f"{record}\n" for record in records)).encode()


# First PROBAND upload: one deletion near NeedlR's SVDEL1 (a different call, different
# breakpoints, so a different id) and one elsewhere.
_PROBAND_V1 = _vcf(
    "1\t7010\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVTYPE=DEL;SVLEN=-4980;END=11990;SUPPORT=14\tGT\t0/1",
    "1\t30000\tSniffles2.DEL.2\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t0/1",
)
# The overwrite: the 7010 call is gone, the 30000 call is now 1/1, and a duplication is new.
_PROBAND_V2 = _vcf(
    "1\t30000\tSniffles2.DEL.2\tN\t<DEL>\t42\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=11\tGT\t1/1",
    "1\t50000\tSniffles2.DUP.3\tN\t<DUP>\t35\tPASS\tSVTYPE=DUP;SVLEN=500;END=50500;SUPPORT=7\tGT\t0/1",
)
# MOTHER's Sniffles call on the same deletion merges into PROBAND's Sniffles record.
_MOTHER = _vcf(
    "1\t30000\tSniffles2.DEL.9\tN\t<DEL>\t38\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=8\tGT\t0/1",
)

_DEL_30000 = "1-30000-31000-DEL---"
_DUP_50000 = "1-50000-50500-DUP---"


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
        f"SELECT source, variantId, key, `calls.sampleId`, `calls.gt` FROM {table} "
        "WHERE family_guid = %(family_guid)s AND sign = 1 ORDER BY source, variantId, key",
        {"family_guid": family_uuid},
    )
    return [
        {
            "source": str(source),
            "variant_id": str(variant_id),
            "key": int(key),
            "calls": dict(zip(sample_ids, gts)),
        }
        for source, variant_id, key, sample_ids, gts in rows
    ]


async def _merge_parts() -> None:
    """Force the part merges ClickHouse would otherwise run in the background."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.tests.e2e import _harness

    await execute_clickhouse(
        f"OPTIMIZE TABLE {_structural_table_name(_harness.ASSEMBLY, 'entries')} FINAL"
    )


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import delete_family_structural_variants
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    out: dict = {"facts": facts, "rows_before": await _entry_rows(family_uuid)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        async def upload(sample: str, body: bytes, *, overwrite: bool = False) -> dict:
            return _cap(
                await ac.post(
                    f"/api/structural-variants/upload/{sample}",
                    params={"overwrite": str(overwrite).lower(), "source_format": "sniffles"},
                    files={"file": (f"{sample}.sniffles.vcf", body, "text/plain")},
                )
            )

        out["first"] = await upload("PROBAND", _PROBAND_V1)
        out["repeat"] = await upload("PROBAND", _PROBAND_V1)
        out["overwrite"] = await upload("PROBAND", _PROBAND_V2, overwrite=True)
        out["rows_after_overwrite"] = await _entry_rows(family_uuid)
        out["mother"] = await upload("MOTHER", _MOTHER)
        await _merge_parts()
        out["rows_after_merge"] = await _entry_rows(family_uuid)
        out["family_page"] = _cap(
            await ac.get(f"/api/families/{FAMILY}/structural-variants", params={"page_size": 100})
        )

        out["admin_delete"] = _cap(
            await ac.delete(
                "/api/admin/data/samples/MOTHER/structural_variants", params={"confirm": "true"}
            )
        )
        await _merge_parts()
        out["rows_after_admin_delete"] = await _entry_rows(family_uuid)

    # Restore the fixture state for any module that runs later: the package re-import
    # replaces the NeedlR rows only, so the Sniffles rows are removed explicitly.
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

    base = tmp_path_factory.mktemp("golden_sv_upload")
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


def _rows(rows: list[dict], source: str) -> dict[str, dict[str, str]]:
    return {row["variant_id"]: row["calls"] for row in rows if row["source"] == source}


def _duplicate_keys(rows: list[dict]) -> list[int]:
    return [key for key, count in Counter(row["key"] for row in rows).items() if count > 1]


def test_golden_trio_starts_with_needlr_calls_for_every_member(run) -> None:
    needlr = _rows(run["rows_before"], "needlr")
    assert set(needlr) == {"SVDEL1", "SVBND1"}
    assert needlr["SVDEL1"] == {"FATHER": "0/1", "MOTHER": "0/0", "PROBAND": "0/1"}
    assert {row["source"] for row in run["rows_before"]} == {"needlr"}


def test_first_sniffles_upload_is_not_blocked_by_the_samples_needlr_calls(run) -> None:
    first = run["first"]
    # Before the fix: 409 "already exist for this sample and source" — PROBAND's NeedlR
    # calls counted as Sniffles ones.
    assert first["status"] == 200, first["text"]
    assert first["json"]["source_format"] == "sniffles"
    assert first["json"]["created"] == 2


def test_repeat_upload_conflicts_on_the_samples_own_sniffles_calls(run) -> None:
    assert run["repeat"]["status"] == 409, run["repeat"]["text"]


def test_overwrite_rewrites_the_sniffles_rows_and_leaves_needlr_untouched(run) -> None:
    assert run["overwrite"]["status"] == 200, run["overwrite"]["text"]
    rows = run["rows_after_overwrite"]
    # Before the fix every NeedlR SV was inserted a second time, without PROBAND's call.
    assert _duplicate_keys(rows) == []
    assert _rows(rows, "needlr") == _rows(run["rows_before"], "needlr")
    assert _rows(rows, "sniffles") == {
        _DEL_30000: {"PROBAND": "1/1"},
        _DUP_50000: {"PROBAND": "0/1"},
    }
    assert {row["source"] for row in rows} == {"needlr", "sniffles"}


def test_needlr_calls_survive_part_merges(run) -> None:
    rows = run["rows_after_merge"]
    assert _duplicate_keys(rows) == []
    # A merge keeps one live row per key. Before the fix it kept the copy the overwrite
    # had inserted without PROBAND's call, so PROBAND lost its NeedlR deletion.
    assert _rows(rows, "needlr") == _rows(run["rows_before"], "needlr")


def test_second_sample_merges_into_the_same_source(run) -> None:
    # Before the fix MOTHER's first Sniffles upload was refused too (her NeedlR calls).
    assert run["mother"]["status"] == 200, run["mother"]["text"]
    assert run["mother"]["json"]["merged"] == 1
    assert _rows(run["rows_after_merge"], "sniffles") == {
        _DEL_30000: {"MOTHER": "0/1", "PROBAND": "1/1"},
        _DUP_50000: {"PROBAND": "0/1"},
    }


def test_family_page_shows_each_source_with_its_own_calls(run) -> None:
    page = run["family_page"]
    assert page["status"] == 200, page["text"]
    by_id = {variant["_id"]: variant for variant in page["json"]["variants"]}
    assert {by_id[vid]["source"] for vid in ("SVDEL1", "SVBND1")} == {"needlr"}
    assert {g["sample"]: g["gt"] for g in by_id["SVDEL1"]["genotypes"]} == {
        "FATHER": "0/1",
        "MOTHER": "0/0",
        "PROBAND": "0/1",
    }
    assert by_id[_DEL_30000]["source"] == "sniffles"
    assert {g["sample"]: g["gt"] for g in by_id[_DEL_30000]["genotypes"]} == {
        "MOTHER": "0/1",
        "PROBAND": "1/1",
    }


def test_admin_sample_delete_removes_that_sample_from_every_source(run) -> None:
    assert run["admin_delete"]["status"] == 200, run["admin_delete"]["text"]
    rows = run["rows_after_admin_delete"]
    assert _duplicate_keys(rows) == []
    assert _rows(rows, "needlr") == {
        "SVDEL1": {"FATHER": "0/1", "PROBAND": "0/1"},
        "SVBND1": {"FATHER": "0/0", "PROBAND": "0/1"},
    }
    assert _rows(rows, "sniffles") == {
        _DEL_30000: {"PROBAND": "1/1"},
        _DUP_50000: {"PROBAND": "0/1"},
    }


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    assert run["restored"]["n_structural_variants"] == run["facts"]["n_structural_variants"]
    assert run["rows_restored"] == run["rows_before"]
