"""The SV length filters measure each call's SVLEN, read back from ClickHouse (E2E, CLIN-25).

The length filters measured END minus POS. Sniffles2 and pbsv write an insertion with its
END at its POS and its length in SVLEN, so the list showed a 5 kb insertion as 5.0 kb while
a minimum length dropped it and an exact length never found it; a breakend, whose END is
its POS, was dropped by any minimum. The filters now measure a call's SVLEN, as a positive
number, and END minus POS only for a call without one. A translocation (BND, TRA) has no
length: it passes any minimum and matches no exact length (the owner's decision,
2026-10-13).

The golden trio has two NeedlR SVs: a 5 kb deletion (SVLEN -5000) and a breakend with
neither SVLEN nor END. PROBAND's Sniffles2 calls are uploaded through
``POST /structural-variants/upload/{sample_id}`` as Sniffles2 writes them: a 5 kb and a
30 bp insertion with END at POS, a 5 kb deletion with SVLEN -5000, and a breakend. The
family SV search (``GET /families/{family_id}/structural-variants``) then reads them back,
SVLEN from the stored details row, with a minimum or an exact length: unranked and ranked,
with a type, as PROBAND's genome track, and as a CSV export. The export names each
breakend's mate beside its locus, in *Remote chr* and *Remote start*, and leaves both empty
for every other SV (DATA-5).

The Sniffles rows are removed and the golden trio re-imported afterwards, so any module
that runs later sees the usual state. Everything runs in ONE event loop via an in-process
``httpx.ASGITransport`` client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"

_SNIFFLES2_VCF = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "##contig=<ID=1,length=248956422>\n"
    "##contig=<ID=5,length=181538259>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
    "1\t40000\tSniffles2.INS.1\tN\t<INS>\t60\tPASS\tPRECISE;SVTYPE=INS;SVLEN=5000;END=40000;SUPPORT=12\tGT\t0/1\n"
    "1\t45000\tSniffles2.INS.2\tN\t<INS>\t60\tPASS\tPRECISE;SVTYPE=INS;SVLEN=30;END=45000;SUPPORT=10\tGT\t0/1\n"
    "1\t50000\tSniffles2.DEL.3\tN\t<DEL>\t55\tPASS\tPRECISE;SVTYPE=DEL;SVLEN=-5000;END=55000;SUPPORT=11\tGT\t0/1\n"
    "1\t60000\tSniffles2.BND.4\tN\tN]5:3000000]\t50\tPASS\tPRECISE;SVTYPE=BND;SUPPORT=8\tGT\t0/1\n"
).encode()

# Each SV of the family once PROBAND's calls are in, as (type, POS).
_NEEDLR_DEL = ("DEL", 7000)  # SVLEN -5000, END 12000
_NEEDLR_BND = ("BND", 20000)  # neither SVLEN nor END
_INS_5KB = ("INS", 40000)  # SVLEN 5000, END at POS
_INS_30 = ("INS", 45000)  # SVLEN 30, END at POS
_DEL_5KB = ("DEL", 50000)  # SVLEN -5000, END 55000
_BND = ("BND", 60000)  # neither SVLEN nor END

_SEARCHES: dict[str, dict[str, object]] = {
    "all": {},
    "min_50": {"min_length": 50},
    "min_50_ranked": {"min_length": 50, "prioritize": "true"},
    "exact_5000": {"length": 5000},
    "exact_0": {"length": 0},
    "min_10mb": {"min_length": 10_000_000},
    "ins_min_50": {"type": "INS", "min_length": 50},
    "track_min_50": {"min_length": 50, "track_mode": "true", "sample": "PROBAND"},
}


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import delete_family_structural_variants
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    # Only the golden trio's NeedlR calls, whatever an earlier module left.
    await delete_family_structural_variants(_harness.ASSEMBLY, family_uuid, source="sniffles")
    out: dict = {"facts": facts, "searches": {}}
    svs = f"/api/families/{FAMILY}/structural-variants"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        out["upload"] = _cap(
            await ac.post(
                "/api/structural-variants/upload/PROBAND",
                params={"overwrite": "false", "source_format": "sniffles"},
                files={"file": ("PROBAND.sniffles.vcf", _SNIFFLES2_VCF, "text/plain")},
            )
        )
        for name, params in _SEARCHES.items():
            out["searches"][name] = _cap(await ac.get(svs, params={"page_size": 100, **params}))
        export = await ac.get(f"{svs}/export", params={"min_length": 50})
        out["export"] = {"status": export.status_code, "text": export.text}

    await delete_family_structural_variants(_harness.ASSEMBLY, family_uuid, source="sniffles")
    out["restored"] = await _harness.import_golden_trio(root)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    base = tmp_path_factory.mktemp("golden_sv_length_filter")
    root = base / FAMILY
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", base / "data")
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _page(run: dict, name: str) -> dict:
    resp = run["searches"][name]
    assert resp["status"] == 200, resp["text"]
    return resp["json"]


def _found(run: dict, name: str) -> set[tuple[str, int]]:
    return {(variant["type"], variant["start"]) for variant in _page(run, name)["variants"]}


def test_the_sniffles2_calls_are_uploaded(run) -> None:
    upload = run["upload"]
    assert upload["status"] == 200, upload["text"]
    assert upload["json"]["created"] == 4
    assert _found(run, "all") == {_NEEDLR_DEL, _NEEDLR_BND, _INS_5KB, _INS_30, _DEL_5KB, _BND}


def test_the_list_shows_each_calls_svlen(run) -> None:
    shown = {(variant["type"], variant["start"]): variant["length"] for variant in _page(run, "all")["variants"]}
    assert shown[_INS_5KB] == 5000
    assert shown[_INS_30] == 30
    assert shown[_DEL_5KB] == -5000
    assert shown[_NEEDLR_DEL] == -5000


@pytest.mark.parametrize("name", ["min_50", "min_50_ranked", "track_min_50"])
def test_a_minimum_keeps_the_insertion_by_its_svlen_and_every_breakend(run, name) -> None:
    # Before the fix: the deletions alone. The 5 kb insertion was 0 bp, the breakends 0 or 1.
    assert _found(run, name) == {_NEEDLR_DEL, _NEEDLR_BND, _INS_5KB, _DEL_5KB, _BND}
    assert _page(run, name)["total"] == 5


def test_an_exact_length_finds_the_insertion_by_its_svlen_and_no_breakend(run) -> None:
    assert _found(run, "exact_5000") == {_NEEDLR_DEL, _INS_5KB, _DEL_5KB}
    # A breakend is no 0 bp event either.
    assert _found(run, "exact_0") == set()


def test_only_the_breakends_pass_a_10_mb_minimum(run) -> None:
    assert _found(run, "min_10mb") == {_NEEDLR_BND, _BND}
    assert _page(run, "min_10mb")["summary"] == {"BND": {"needlr": 1, "sniffles": 1}}


def test_a_type_and_a_minimum_find_the_long_insertion(run) -> None:
    assert _found(run, "ins_min_50") == {_INS_5KB}


def test_the_export_holds_the_svs_the_list_holds(run) -> None:
    export = run["export"]
    assert export["status"] == 200, export["text"]
    rows = list(csv.DictReader(io.StringIO(export["text"])))
    assert {(row["Type"], int(row["Start"])) for row in rows} == _found(run, "min_50")
    assert [row["Length"] for row in rows if (row["Type"], int(row["Start"])) == _INS_5KB] == ["5000"]


def test_the_export_names_each_breakends_mate_beside_its_locus(run) -> None:
    # DATA-5. Before: no mate column, so an exported translocation named no partner.
    export = run["export"]
    assert export["status"] == 200, export["text"]
    header = next(csv.reader(io.StringIO(export["text"])))
    assert header[:6] == ["Chromosome", "Start", "End", "Remote chr", "Remote start", "Type"]
    rows = csv.DictReader(io.StringIO(export["text"]))
    mates = {
        (row["Source"], row["Type"], int(row["Start"])): (row["Remote chr"], row["Remote start"]) for row in rows
    }
    # The NeedlR breakend and the uploaded one with the mate their ALT names (N]chr5:3000000],
    # N]5:3000000]), as the list serves it; the deletions and the insertion with none.
    assert mates == {
        ("needlr", *_NEEDLR_BND): ("5", "3000000"),
        ("sniffles", *_BND): ("5", "3000000"),
        ("needlr", *_NEEDLR_DEL): ("", ""),
        ("sniffles", *_DEL_5KB): ("", ""),
        ("sniffles", *_INS_5KB): ("", ""),
    }
    listed = {
        (variant["source"], variant["type"], variant["start"]): (variant["remote_chr"], variant["remote_start"])
        for variant in _page(run, "min_50")["variants"]
    }
    assert listed[("needlr", *_NEEDLR_BND)] == listed[("sniffles", *_BND)] == ("5", 3_000_000)


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    assert run["restored"]["n_structural_variants"] == run["facts"]["n_structural_variants"]
