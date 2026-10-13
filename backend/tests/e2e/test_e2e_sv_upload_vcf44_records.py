"""An uploaded SV record without END or SVTYPE is read as VCF 4.4 defines it (E2E, CLIN-33, CLIN-37).

VCF 4.4 lets a structural-variant record leave END and SVTYPE out. The Sniffles and Spectre
uploads read a record without END as ending at its POS, and took a record without SVTYPE to
be of the type its ALT spells: a deletion written with SVLEN alone lay on one base, so a
search for a gene it deletes downstream of its POS, or for a region inside it, missed it; a
breakend was stored as of type ``N]5:3000000]``, which the type filter and the
translocation rule of the length filters do not know.

The family here is its own, created through the PED upload under a new id each run, so the
golden trio is never touched; a gene row, E2E_SPANNED on 1:30500-30800, is seeded for the
search and removed afterwards. The child's Sniffles VCF, written as VCF 4.4 allows (neither
END nor SVTYPE), is uploaded through ``POST /structural-variants/upload/{sample_id}``, and
the family SV search (``GET /families/{family_id}/structural-variants``) reads it back:

* a 1 kb deletion at 1:30000 (``<DEL>``, SVLEN 1000) is stored on 1:30000-31000, listed with
  E2E_SPANNED, and found by a search for that gene and for the SVs overlapping a region
  inside it, but no longer by a search for the SVs within a region it reaches beyond;
* a breakend at 1:60000 (``N]5:3000000]``) is a BND with its mate on 5:3000000, found by a
  BND search and, a translocation, by a 10 Mb minimum length;
* a 5 kb insertion at 1:80000 (``<INS>``, SVLEN 5000) stays on its POS: it spans no
  reference base;
* a sequence-resolved 100 bp deletion at 1:90000 (REF 101 bases, ALT its first) is a DEL
  on 1:90000-90100.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why); app exceptions come back as a 500 response rather
than being raised in the test.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_GENE = "E2E_SPANNED"
_SEEDED_GENE_ID = "E2E_SV_UPLOAD_VCF44"
_SEQUENCE_DELETION_REF = "A" + "C" * 100

# Each SV of the child's upload as stored: (type, start, end).
_DEL = ("DEL", 30000, 31000)
_BND = ("BND", 60000, 60000)
_INS = ("INS", 80000, 80000)
_SEQUENCE_DEL = ("DEL", 90000, 90100)

_SEARCHES: dict[str, dict[str, object]] = {
    "all": {},
    "gene": {"gene": _GENE},
    "overlapping": {"chr": "1", "start": 30500, "end": 30800, "overlap": "true"},
    "within": {"chr": "1", "start": 29000, "end": 30500},
    "bnd": {"type": "BND"},
    "del": {"type": "DEL"},
    "min_10mb": {"min_length": 10_000_000},
}


def _vcf(child: str) -> bytes:
    return (
        "##fileformat=VCFv4.4\n"
        "##source=Sniffles2_2.6\n"
        "##contig=<ID=1,length=248956422>\n"
        "##contig=<ID=5,length=181538259>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{child}\n"
        "1\t30000\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVLEN=1000;SUPPORT=14\tGT\t0/1\n"
        "1\t60000\tSniffles2.BND.2\tN\tN]5:3000000]\t50\tPASS\tSUPPORT=8\tGT\t0/1\n"
        "1\t80000\tSniffles2.INS.3\tN\t<INS>\t45\tPASS\tSVLEN=5000;SUPPORT=9\tGT\t0/1\n"
        f"1\t90000\tSniffles2.DEL.4\t{_SEQUENCE_DELETION_REF}\tA\t40\tPASS\tSUPPORT=7\tGT\t0/1\n"
    ).encode()


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:
        pass
    return out


async def _set_seeded_gene(assembly_id: str | None) -> None:
    """Seed (or, with None, remove) the gene row of E2E_SPANNED: the upload lists an SV
    with the genes its span overlaps, from the gene table, and the fixture ships none."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text("DELETE FROM genes WHERE gene_id = :gene_id"), {"gene_id": _SEEDED_GENE_ID}
        )
        if assembly_id is not None:
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, :symbol, '1', 30500, 30800,
                            1, 'protein_coding', 'E2E SV upload of VCF 4.4 records', 'e2e')
                    """
                ),
                {"assembly_id": assembly_id, "gene_id": _SEEDED_GENE_ID, "symbol": _GENE},
            )
        await session.commit()


async def _exercise(family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)

    father, mother, child = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    ped = (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{child}\t{father}\t{mother}\t1\t2\n"
    )
    out: dict = {"searches": {}}

    await _set_seeded_gene(assembly_id)
    try:
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
            out["upload"] = _cap(
                await ac.post(
                    f"/api/structural-variants/upload/{child}",
                    params={"overwrite": "false", "source_format": "sniffles"},
                    files={"file": (f"{child}.sniffles.vcf", _vcf(child), "text/plain")},
                )
            )
            svs = f"/api/families/{family_id}/structural-variants"
            for name, params in _SEARCHES.items():
                out["searches"][name] = _cap(await ac.get(svs, params={"page_size": 100, **params}))
    finally:
        await _set_seeded_gene(None)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("sv_upload_vcf44_records"))
    try:
        return _harness.run_async(lambda: _exercise(f"SVV_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _variants(run: dict, name: str) -> list[dict]:
    resp = run["searches"][name]
    assert resp["status"] == 200, resp["text"]
    return resp["json"]["variants"]


def _found(run: dict, name: str) -> set[tuple[str, int, int]]:
    return {(variant["type"], variant["start"], variant["end"]) for variant in _variants(run, name)}


def test_the_family_is_created_and_the_childs_vcf_uploaded(run) -> None:
    assert run["ped"]["status"] == 200, run["ped"]["text"]
    upload = run["upload"]
    assert upload["status"] == 200, upload["text"]
    assert (upload["json"]["processed"], upload["json"]["created"]) == (4, 4)


def test_each_record_is_stored_where_vcf_4_4_puts_it(run) -> None:
    # Before: every record on its POS alone, the breakend of type "N]5:3000000]" and the
    # sequence-resolved deletion of type "ACCC…" (its REF's bases).
    assert _found(run, "all") == {_DEL, _BND, _INS, _SEQUENCE_DEL}


def test_the_deletion_is_listed_with_the_gene_it_spans(run) -> None:
    (deletion,) = [variant for variant in _variants(run, "all") if variant["start"] == 30000]
    assert deletion["gene_symbols"] == [_GENE]


@pytest.mark.parametrize("name", ["gene", "overlapping"])
def test_a_search_for_the_gene_or_a_region_inside_it_finds_the_deletion(run, name) -> None:
    # Before: the deletion lay on 1:30000, outside both, and neither search found it.
    assert _found(run, name) == {_DEL}


def test_a_search_within_a_region_the_deletion_reaches_beyond_does_not_find_it(run) -> None:
    # Before: a point on 1:30000, within 1:29000-30500.
    assert _found(run, "within") == set()


def test_the_breakend_is_a_bnd_with_its_mate(run) -> None:
    assert _found(run, "bnd") == {_BND}
    (breakend,) = _variants(run, "bnd")
    assert (breakend["remote_chr"], breakend["remote_start"]) == ("5", 3_000_000)


def test_a_type_search_finds_both_deletions(run) -> None:
    assert _found(run, "del") == {_DEL, _SEQUENCE_DEL}


def test_the_breakend_as_a_translocation_passes_any_minimum_length(run) -> None:
    assert _found(run, "min_10mb") == {_BND}
