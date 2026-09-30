"""A source-scoped SV delete leaves none of that source's rows behind (E2E).

Since #658 an SV's storage key hashes its source, so each caller's call of an SV has its own
``SV/variants/details`` row (span, length, filters, annotation) and its own ``SV/key_lookup``
row beside its ``SV/entries`` rows. The source-scoped delete, the delete half of a per-sample
upload's rewrite and of a package dataset's re-import, deleted the entries only. The details
and lookup rows of every SV it removed stayed behind, and no entry reached them; a rewritten
SV's replaced rows stayed beside its new ones until ClickHouse merged the parts.

Over the golden trio (NeedlR SVs) this uploads, through ``POST
/structural-variants/upload/{sample_id}``, a Sniffles VCF for PROBAND with a deletion and a
duplication, and a Spectre call of the same deletion (same id, its own key). Another family's
Sniffles rows of the same two SVs are written through the storage writer: the SV tables are
keyed by the family's uuid, so no Postgres family is needed. Then:

* PROBAND's Sniffles upload is overwritten without the duplication. The duplication's
  details and lookup rows must go, and the deletion must keep one details and one lookup
  row, under its stored key;
* the family's Sniffles rows are deleted with ``delete_family_structural_variants(source=…)``.
  No Sniffles row of the family may be left in the three tables, while the Spectre and
  NeedlR rows and the other family's rows stay exactly as stored, and the family's SV page
  still reads the Spectre deletion's length from its details row.

The tables are read as stored (no ``FINAL``), and after each step every details and lookup
row of the family must have the key of one of its entries. Afterwards the added rows are
removed with the same deletes, the annotation manifest is put back and the golden trio is
re-imported: the three tables then hold exactly what the golden import wrote. Everything
runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
# The uuid the other family's SV rows are stored under. It names no Postgres family.
_OTHER_FAMILY = "00000000-0000-4000-8000-0000000e2e58"

_DEL = "1-30000-31000-DEL---"
_DUP = "1-50000-50500-DUP---"
_TABLES = ("entries", "variants/details", "key_lookup")


def _sv_vcf(source_line: str, *records: str) -> bytes:
    return (
        "##fileformat=VCFv4.2\n"
        f"{source_line}\n"
        "##contig=<ID=1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
        + "".join(f"{record}\n" for record in records)
    ).encode()


_SNIFFLES_DEL = (
    "1\t30000\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=14\tGT\t0/1"
)
_SNIFFLES_DUP = (
    "1\t50000\tSniffles2.DUP.2\tN\t<DUP>\t35\tPASS\tSVTYPE=DUP;SVLEN=500;END=50500;SUPPORT=7\tGT\t0/1"
)
_SNIFFLES = _sv_vcf("##source=Sniffles2_2.2", _SNIFFLES_DEL, _SNIFFLES_DUP)
_SNIFFLES_WITHOUT_DUP = _sv_vcf("##source=Sniffles2_2.2", _SNIFFLES_DEL)
_SPECTRE = _sv_vcf(
    "##source=Spectre",
    "1\t30000\tSpectre.DEL.1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000\tGT\t1/1",
)


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


def _other_family_records() -> list:
    from backend.app.services.clickhouse_variant_records import (
        StructuralVariantCall,
        StructuralVariantRecord,
    )

    records = []
    for variant_id, sv_type, start, end, sv_len in (
        (_DEL, "DEL", 30000, 31000, -1000),
        (_DUP, "DUP", 50000, 50500, 500),
    ):
        records.append(
            StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr="1",
                start=start,
                end=end,
                sv_type=sv_type,
                source="sniffles",
                remote_chr=None,
                remote_start=None,
                remote_end=None,
                sv_len=sv_len,
                filters=["PASS"],
                gene_symbols=[],
                annotations=[{"info": {"SVTYPE": sv_type}}],
                calls=[
                    StructuralVariantCall(
                        sample="OTHER_PROBAND", gt="0/1", qual=50.0, read_support=12, filter="PASS"
                    )
                ],
            )
        )
    return records


async def _sv_rows(family_uuid: str) -> dict[str, list[tuple]]:
    """The family's rows in the three SV tables as stored: no ``FINAL``, so a row the
    storage left beside a newer one is counted. Each row as ``(source, variant id, key, …)``,
    with the entry's calls and the details' span, length, filters and annotation."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.tests.e2e import _harness

    extra = {
        "entries": ", project_guid, `calls.sampleId`, `calls.gt`",
        "variants/details": ", start, end, svLen, filters, annotationsJson",
        "key_lookup": "",
    }
    out: dict[str, list[tuple]] = {}
    for suffix in _TABLES:
        rows = await execute_clickhouse(
            f"SELECT source, variantId, key{extra[suffix]} "
            f"FROM {_structural_table_name(_harness.ASSEMBLY, suffix)} "
            "WHERE family_guid = %(family_guid)s ORDER BY source, variantId, key",
            {"family_guid": family_uuid},
        )
        out[suffix] = [
            (str(row[0]), str(row[1]), int(row[2]), *(_plain(value) for value in row[3:]))
            for row in rows
        ]
    return out


def _plain(value):
    return tuple(value) if isinstance(value, list) else value


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
    """Remove what this module adds, with the deletes under test only. Idempotent."""
    from backend.app.services.clickhouse_variant_storage import delete_family_structural_variants
    from backend.tests.e2e import _harness

    for source in ("sniffles", "spectre"):
        await delete_family_structural_variants(_harness.ASSEMBLY, family_uuid, source=source)
    await delete_family_structural_variants(_harness.ASSEMBLY, _OTHER_FAMILY)


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_structural_variants,
        insert_structural_variant_records,
    )
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    # A run that failed half-way may have left its rows behind.
    await _remove_added_rows(family_uuid)
    manifest_before = await _manifest_modules(family_uuid)
    out: dict = {"facts": facts, "golden": await _sv_rows(family_uuid)}

    try:
        await insert_structural_variant_records(
            _harness.ASSEMBLY, _OTHER_FAMILY, [facts["project_id"]], _other_family_records()
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"

            async def upload(source_format: str, body: bytes, *, overwrite: bool = False) -> dict:
                return _cap(
                    await ac.post(
                        "/api/structural-variants/upload/PROBAND",
                        params={"overwrite": str(overwrite).lower(), "source_format": source_format},
                        files={"file": (f"PROBAND.{source_format}.vcf", body, "text/plain")},
                    )
                )

            out["sniffles"] = await upload("sniffles", _SNIFFLES)
            out["spectre"] = await upload("spectre", _SPECTRE)
            out["stored"] = await _sv_rows(family_uuid)
            out["other_stored"] = await _sv_rows(_OTHER_FAMILY)

            out["overwrite"] = await upload("sniffles", _SNIFFLES_WITHOUT_DUP, overwrite=True)
            out["after_overwrite"] = await _sv_rows(family_uuid)

            await delete_family_structural_variants(
                _harness.ASSEMBLY, family_uuid, source="sniffles"
            )
            out["after_delete"] = await _sv_rows(family_uuid)
            out["other_after_delete"] = await _sv_rows(_OTHER_FAMILY)
            out["page_after_delete"] = _cap(
                await ac.get(
                    f"/api/families/{FAMILY}/structural-variants", params={"page_size": 100}
                )
            )
    finally:
        # Restore the fixture state for any module that runs later.
        await _remove_added_rows(family_uuid)
        await _restore_manifest_modules(family_uuid, manifest_before)
        out["restored"] = await _harness.import_golden_trio(root)
        out["rows_restored"] = await _sv_rows(family_uuid)
        out["other_restored"] = await _sv_rows(_OTHER_FAMILY)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    base = tmp_path_factory.mktemp("golden_sv_source_delete")
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


def _of(rows: dict[str, list[tuple]], source: str) -> dict[str, list[tuple]]:
    return {suffix: [row for row in rows[suffix] if row[0] == source] for suffix in _TABLES}


def _identities(rows: dict[str, list[tuple]], source: str) -> dict[str, list[tuple[str, int]]]:
    return {suffix: [(row[1], row[2]) for row in table] for suffix, table in _of(rows, source).items()}


def _unreached(rows: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    """The details and lookup rows whose key none of the family's entries has."""
    keys = {row[2] for row in rows["entries"]}
    return {
        suffix: [row[:3] for row in rows[suffix] if row[2] not in keys]
        for suffix in ("variants/details", "key_lookup")
    }


def test_the_uploads_are_accepted(run) -> None:
    for step in ("sniffles", "spectre", "overwrite"):
        assert run[step]["status"] == 200, (step, run[step]["text"])


def test_each_caller_starts_with_its_own_row_in_each_table(run) -> None:
    stored = run["stored"]
    sniffles, spectre = _identities(stored, "sniffles"), _identities(stored, "spectre")
    keys = dict(sniffles["entries"])
    assert set(keys) == {_DEL, _DUP}
    for suffix in _TABLES:
        assert sniffles[suffix] == [(_DEL, keys[_DEL]), (_DUP, keys[_DUP])], suffix
        assert spectre[suffix] == spectre["entries"], suffix
    # One id, two callers, two keys.
    assert [variant_id for variant_id, _key in spectre["entries"]] == [_DEL]
    assert spectre["entries"][0][1] != keys[_DEL]
    assert _unreached(stored) == {"variants/details": [], "key_lookup": []}


def test_an_overwrite_that_drops_an_sv_leaves_no_row_for_it(run) -> None:
    stored_key = dict(_identities(run["stored"], "sniffles")["entries"])[_DEL]
    after = _identities(run["after_overwrite"], "sniffles")
    for suffix in _TABLES:
        # Before: the duplication kept its details and lookup row, and until ClickHouse
        # merged the parts the deletion's replaced rows stayed beside the new ones.
        assert after[suffix] == [(_DEL, stored_key)], suffix
    assert _unreached(run["after_overwrite"]) == {"variants/details": [], "key_lookup": []}
    for source in ("spectre", "needlr"):
        assert _of(run["after_overwrite"], source) == _of(run["stored"], source), source


def test_a_source_delete_leaves_no_row_of_that_source(run) -> None:
    # Before: the entries went, and the deletion's details and lookup rows stayed.
    assert _of(run["after_delete"], "sniffles") == {suffix: [] for suffix in _TABLES}
    assert _unreached(run["after_delete"]) == {"variants/details": [], "key_lookup": []}


def test_other_sources_and_the_other_family_keep_every_row_as_stored(run) -> None:
    for source in ("spectre", "needlr"):
        assert _of(run["after_delete"], source) == _of(run["stored"], source), source
        assert all(_of(run["stored"], source).values()), source
    # The other family has the same two Sniffles SVs, under its own keys.
    assert run["other_after_delete"] == run["other_stored"]
    assert [row[1] for row in run["other_stored"]["variants/details"]] == [_DEL, _DUP]


def test_the_other_callers_deletion_keeps_its_details_on_the_page(run) -> None:
    page = run["page_after_delete"]
    assert page["status"] == 200, page["text"]
    by_source = {
        (variant["_id"], variant["source"]): variant for variant in page["json"]["variants"]
    }
    assert {source for _id, source in by_source} == {"needlr", "spectre"}
    spectre = by_source[(_DEL, "spectre")]
    # The length comes from the details row (SVLEN=-1000); without it the page falls back
    # to end - start (1000).
    assert spectre["length"] == -1000
    assert {g["sample"]: g["gt"] for g in spectre["genotypes"]} == {"PROBAND": "1/1"}


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    # The NeedlR rows only, as the golden import wrote them, with no clean-up of the details
    # or lookup table by hand.
    assert run["rows_restored"] == run["golden"]
    assert {row[0] for rows in run["golden"].values() for row in rows} == {"needlr"}
    assert _unreached(run["rows_restored"]) == {"variants/details": [], "key_lookup": []}
    assert run["other_restored"] == {suffix: [] for suffix in _TABLES}
