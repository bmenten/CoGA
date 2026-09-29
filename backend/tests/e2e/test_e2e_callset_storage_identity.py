"""Two callsets' calls of one variant are two stored rows, and both survive part merges (E2E).

A small variant's storage key is the variant, and a family's ``entries`` rows were sorted
by ``(project, family, position, key)`` only. A clair3 row and a GLIMPSE2 row for one
variant then had one sort key, and when ClickHouse merged the parts the CollapsingMergeTree
kept the later row, the imputed one: the direct call was gone, and with it the variant
from the diagnostic lists, which hide imputed callsets. SVs collided the same way: a
per-sample upload's id (``chrom-start-end-type---``) names no caller, so a Sniffles and a
Spectre call at the same coordinates had one key, and a merge kept one of them.

Over the golden trio (clair3 SNVs, NeedlR SVs) this stores:

* a GLIMPSE2 callset, through the loader the package import's ``haplotypes`` dataset uses,
  with imputed genotypes at two clair3 sites that differ from the direct calls (the
  PROBAND de novo 1:3000 G>A, and 1:4000 T>C, half of the GENE_CH compound het), plus one
  site no direct callset has;
* a Sniffles and a Spectre call for PROBAND at the same deletion, with different genotypes,
  through ``POST /structural-variants/upload/{sample_id}``;

then forces the part merges (``OPTIMIZE … FINAL``) and asserts that every row is still
stored; that the diagnostic list, the de novo and the compound-het views show the direct
calls and still hide the imputed-only site; that a compound-het review, which needs both
variants heterozygous in the proband, reads the direct calls; and that the SV page shows the
deletion once per caller, each with its own call. A second test creates a table with the
older sort key and asserts that startup and the table ensure refuse it.

Afterwards the added rows and reviews are removed and the golden trio re-imported, so later
modules see the usual state. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"

_DE_NOVO = "1-3000-G-A"
_CH_LEFT = "1-4000-T-C"
_CH_RIGHT = "1-5000-A-T"
_IMPUTED_ONLY = "1-9500-C-T"
_DELETION = "1-30000-31000-DEL---"

_GLIMPSE2_FORMAT = "GT:DP:AD:AF:GP:PS"


def _imputed(gt: str) -> str:
    # A phased GT with genotype posteriors, as GLIMPSE2 writes it.
    return f"{gt}:30:15,15:0.5:0.05,0.05,0.9:1"


_GLIMPSE2_VCF = (
    "\n".join(
        [
            "##fileformat=VCFv4.2",
            "##source=GLIMPSE2",
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
            '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
            '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">',
            '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">',
            '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype posteriors">',
            '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tFATHER\tMOTHER\tPROBAND",
            # clair3: FATHER 0/0, MOTHER 0/0, PROBAND 0/1 (de novo).
            "\t".join(["1", "3000", ".", "G", "A", ".", "PASS", ".", _GLIMPSE2_FORMAT,
                       _imputed("0|0"), _imputed("0|1"), _imputed("1|1")]),
            # clair3: FATHER 0/0, MOTHER 0/1, PROBAND 0/1 (one half of the GENE_CH pair).
            "\t".join(["1", "4000", ".", "T", "C", ".", "PASS", ".", _GLIMPSE2_FORMAT,
                       _imputed("0|1"), _imputed("0|1"), _imputed("1|1")]),
            # Imputed only: no direct callset has it.
            "\t".join(["1", "9500", ".", "C", "T", ".", "PASS", ".", _GLIMPSE2_FORMAT,
                       _imputed("0|1"), _imputed("0|0"), _imputed("0|1")]),
        ]
    )
    + "\n"
).encode()


def _sv_vcf(source_line: str, record: str) -> bytes:
    return (
        "##fileformat=VCFv4.2\n"
        f"{source_line}\n"
        "##contig=<ID=1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
        f"{record}\n"
    ).encode()


_SNIFFLES_VCF = _sv_vcf(
    "##source=Sniffles2_2.2",
    "1\t30000\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=14\tGT\t0/1",
)
_SPECTRE_VCF = _sv_vcf(
    "##source=Spectre",
    "1\t30000\tSpectre.DEL.1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000\tGT\t1/1",
)


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


async def _small_rows(family_uuid: str) -> list[dict]:
    """The family's live small-variant entry rows at the sites under test, as stored."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.tests.e2e import _harness

    rows = await execute_clickhouse(
        f"SELECT source, variantId, key, `calls.sampleId`, `calls.gt` "
        f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND sign = 1 AND variantId IN %(variant_ids)s "
        "ORDER BY variantId, source",
        {"family_guid": family_uuid, "variant_ids": (_DE_NOVO, _CH_LEFT, _CH_RIGHT, _IMPUTED_ONLY)},
    )
    return [
        {"source": str(source), "variant_id": str(vid), "key": int(key), "calls": dict(zip(ids, gts))}
        for source, vid, key, ids, gts in rows
    ]


async def _sv_rows(family_uuid: str) -> dict[str, list[dict]]:
    """The family's live SV entry, details and key-lookup rows, as stored."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.tests.e2e import _harness

    params = {"family_guid": family_uuid}
    entries = await execute_clickhouse(
        f"SELECT source, variantId, key, `calls.sampleId`, `calls.gt` "
        f"FROM {_structural_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND sign = 1 ORDER BY variantId, source",
        params,
    )
    details = await execute_clickhouse(
        f"SELECT source, variantId, key FROM {_structural_table_name(_harness.ASSEMBLY, 'variants/details')} "
        "FINAL WHERE family_guid = %(family_guid)s ORDER BY variantId, source",
        params,
    )
    # Read without naming the source column, which the older table does not have.
    lookups = await execute_clickhouse(
        f"SELECT variantId, key FROM {_structural_table_name(_harness.ASSEMBLY, 'key_lookup')} "
        "FINAL WHERE family_guid = %(family_guid)s ORDER BY variantId, key",
        params,
    )
    return {
        "entries": [
            {"source": str(s), "variant_id": str(v), "key": int(k), "calls": dict(zip(ids, gts))}
            for s, v, k, ids, gts in entries
        ],
        "details": [{"source": str(s), "variant_id": str(v), "key": int(k)} for s, v, k in details],
        "lookups": [{"variant_id": str(v), "key": int(k)} for v, k in lookups],
    }


async def _sort_keys() -> dict[str, str]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    names = tuple(
        f"{_harness.ASSEMBLY}/{suffix}" for suffix in ("SNV_INDEL/entries", "SV/entries", "SV/key_lookup")
    )
    rows = await execute_clickhouse(
        "SELECT name, sorting_key FROM system.tables WHERE database = %(database)s AND name IN %(names)s",
        {"database": settings.clickhouse_database, "names": names},
    )
    return {str(name).split("/", 1)[1]: str(key) for name, key in rows}


async def _load_glimpse2(project_id: str) -> dict:
    """Store the GLIMPSE2 callset the way the package import's ``haplotypes`` dataset does."""
    from io import BytesIO

    from fastapi import UploadFile

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.routers.families_small_variants import _family_sample_contexts
    from backend.app.services.family_metadata_context import build_family_metadata_context
    from backend.app.services.variant_upload_service import upload_family_small_variant_file
    from backend.tests.e2e import _harness

    async with get_postgres_sessionmaker()() as session:
        admin, _project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        context = await build_family_metadata_context(
            session, family_identifier=FAMILY, user=admin, project_id=project_id
        )
        return await upload_family_small_variant_file(
            session,
            context=context,
            sample_contexts=_family_sample_contexts(context),
            file=UploadFile(file=BytesIO(_GLIMPSE2_VCF), filename=f"{FAMILY}.glimpse2.vcf"),
            overwrite=True,
            format_hint="glimpse2",
        )


async def _remove_added_rows(family_uuid: str) -> None:
    """Remove what this module adds: the GLIMPSE2 callset and its haplotype blocks, the
    Sniffles and Spectre rows (with their details and lookup rows, which a source-scoped
    SV delete leaves behind) and the compound-het review. Idempotent."""
    from sqlalchemy import text

    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_interval_tracks import (
        delete_interval_track_sources,
        delete_interval_tracks,
    )
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_small_variants,
        delete_family_structural_variants,
    )
    from backend.tests.e2e import _harness

    await delete_family_small_variants(_harness.ASSEMBLY, family_uuid, source="glimpse2")
    await delete_interval_tracks(_harness.ASSEMBLY, family_uuid=family_uuid, track_type="haplotype")
    for source in ("sniffles", "spectre"):
        await delete_family_structural_variants(_harness.ASSEMBLY, family_uuid, source=source)
    await execute_clickhouse(
        f"ALTER TABLE {_structural_table_name(_harness.ASSEMBLY, 'variants/details')} "
        "DELETE WHERE family_guid = %(family_guid)s AND source IN %(sources)s "
        "SETTINGS mutations_sync = 1",
        {"family_guid": family_uuid, "sources": ("sniffles", "spectre")},
    )
    await execute_clickhouse(
        f"ALTER TABLE {_structural_table_name(_harness.ASSEMBLY, 'key_lookup')} "
        "DELETE WHERE family_guid = %(family_guid)s AND variantId = %(variant_id)s "
        "SETTINGS mutations_sync = 1",
        {"family_guid": family_uuid, "variant_id": _DELETION},
    )
    async with get_postgres_sessionmaker()() as session:
        await delete_interval_track_sources(session, family_uuid=family_uuid, track_type="haplotype")
        await session.execute(
            text(
                "DELETE FROM small_variant_reviews "
                "WHERE family_id = CAST(:family AS uuid) AND variant_id IN (:left, :right)"
            ),
            {"family": family_uuid, "left": _CH_LEFT, "right": _CH_RIGHT},
        )
        await session.commit()


def _genotypes(variant: dict) -> dict[str, str]:
    return {genotype["sample"]: genotype["gt"] for genotype in variant["genotypes"]}


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_small_variants import get_small_variant_family_record
    from backend.app.services.clickhouse_variant_storage import optimize_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    # A run that failed half-way may have left its rows behind.
    await _remove_added_rows(family_uuid)
    out: dict = {"facts": facts, "sort_keys": await _sort_keys()}
    out["small_before"] = await _small_rows(family_uuid)
    out["sv_before"] = await _sv_rows(family_uuid)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        out["glimpse2_upload"] = await _load_glimpse2(facts["project_id"])
        for source_format, body in (("sniffles", _SNIFFLES_VCF), ("spectre", _SPECTRE_VCF)):
            out[f"{source_format}_upload"] = _cap(
                await ac.post(
                    "/api/structural-variants/upload/PROBAND",
                    params={"source_format": source_format},
                    files={"file": (f"PROBAND.{source_format}.vcf", body, "text/plain")},
                )
            )

        # The merges ClickHouse runs in the background, forced: this is where one row of
        # each colliding pair used to disappear.
        await optimize_clickhouse_variant_tables(_harness.ASSEMBLY, final=True)
        out["small_after"] = await _small_rows(family_uuid)
        out["sv_after"] = await _sv_rows(family_uuid)

        async def page(**params) -> dict:
            return _cap(
                await ac.get(
                    f"/api/families/{FAMILY}/small-variants", params={"page_size": 100, **params}
                )
            )

        out["list"] = await page()
        out["de_novo"] = await page(inheritance="de_novo")
        out["compound_het"] = await page(inheritance="compound_het")
        out["imputed_list"] = await page(source="glimpse2")
        out["sv_page"] = _cap(
            await ac.get(f"/api/families/{FAMILY}/structural-variants", params={"page_size": 100})
        )
        record = await get_small_variant_family_record(
            assembly_name=_harness.ASSEMBLY, family_guid=family_uuid, variant_id=_CH_LEFT
        )
        out["record_calls"] = dict(record.sample_calls) if record else None
        # A compound-het review is refused unless both variants are heterozygous in an
        # affected member, read through the per-variant record: the imputed row has the
        # proband homozygous at 1:4000.
        out["compound_het_review"] = _cap(
            await ac.put(
                f"/api/families/{FAMILY}/small-variants/{_CH_LEFT}/review",
                json={"compound_het": {"partner_variant_id": _CH_RIGHT, "note": "e2e callset identity"}},
            )
        )

    # Restore the fixture state for later modules: the package re-import replaces the
    # clair3 and NeedlR rows only, so the added callsets, blocks and reviews go explicitly.
    await _remove_added_rows(family_uuid)
    out["restored"] = await _harness.import_golden_trio(root)
    out["small_restored"] = await _small_rows(family_uuid)
    out["sv_restored"] = await _sv_rows(family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    base = tmp_path_factory.mktemp("golden_callset_identity")
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


def _by(rows: list[dict], variant_id: str) -> dict[str, dict[str, str]]:
    return {row["source"]: row["calls"] for row in rows if row["variant_id"] == variant_id}


def test_entries_tables_identify_a_row_by_its_callset(run) -> None:
    assert run["sort_keys"] == {
        "SNV_INDEL/entries": "project_guid, family_guid, xpos, key, source",
        "SV/entries": "project_guid, family_guid, svType, chrom, start, key, source",
        "SV/key_lookup": "family_guid, variantId, source",
    }


def test_the_uploads_are_accepted(run) -> None:
    for name in ("sniffles_upload", "spectre_upload"):
        assert run[name]["status"] == 200, (name, run[name]["text"])
    assert run["glimpse2_upload"]["source_format"] == "glimpse2"
    assert run["glimpse2_upload"]["inserted"] == 3


def test_a_direct_and_an_imputed_call_of_one_variant_survive_the_merge(run) -> None:
    rows = run["small_after"]
    # Before the fix the merge kept only the GLIMPSE2 row at 1:3000 and 1:4000.
    assert _by(rows, _DE_NOVO) == {
        "clair3": {"FATHER": "0/0", "MOTHER": "0/0", "PROBAND": "0/1"},
        "glimpse2": {"FATHER": "0|0", "MOTHER": "0|1", "PROBAND": "1|1"},
    }
    assert _by(rows, _CH_LEFT) == {
        "clair3": {"FATHER": "0/0", "MOTHER": "0/1", "PROBAND": "0/1"},
        "glimpse2": {"FATHER": "0|1", "MOTHER": "0|1", "PROBAND": "1|1"},
    }
    assert set(_by(rows, _IMPUTED_ONLY)) == {"glimpse2"}
    # One key per variant, whatever the callset: the annotations stay shared.
    assert len({row["key"] for row in rows if row["variant_id"] == _DE_NOVO}) == 1


def test_the_diagnostic_list_shows_the_direct_calls(run) -> None:
    listed = run["list"]
    assert listed["status"] == 200, listed["text"]
    by_id = {variant["_id"]: variant for variant in listed["json"]["variants"]}
    for variant_id in (_DE_NOVO, _CH_LEFT):
        assert by_id[variant_id]["source"] == "clair3"
    assert _genotypes(by_id[_DE_NOVO]) == {"FATHER": "0/0", "MOTHER": "0/0", "PROBAND": "0/1"}
    assert _genotypes(by_id[_CH_LEFT]) == {"FATHER": "0/0", "MOTHER": "0/1", "PROBAND": "0/1"}
    # Imputed callsets stay hidden from the diagnostic list.
    assert _IMPUTED_ONLY not in by_id
    assert {variant["source"] for variant in listed["json"]["variants"]} == {"clair3"}


def test_the_de_novo_and_compound_het_views_keep_the_direct_calls(run) -> None:
    de_novo = run["de_novo"]
    assert de_novo["status"] == 200, de_novo["text"]
    assert _DE_NOVO in {variant["_id"] for variant in de_novo["json"]["variants"]}

    compound_het = run["compound_het"]
    assert compound_het["status"] == 200, compound_het["text"]
    groups = {
        group["gene"]: sorted(variant["_id"] for variant in group["variants"])
        for group in compound_het["json"]["variant_groups"]
    }
    assert groups.get("GENE_CH") == [_CH_LEFT, _CH_RIGHT]


def test_an_explicit_imputed_view_still_reads_the_imputed_rows(run) -> None:
    imputed = run["imputed_list"]
    assert imputed["status"] == 200, imputed["text"]
    by_id = {variant["_id"]: variant for variant in imputed["json"]["variants"]}
    assert set(by_id) == {_DE_NOVO, _CH_LEFT, _IMPUTED_ONLY}
    assert _genotypes(by_id[_DE_NOVO])["PROBAND"] == "1|1"


def test_reviews_read_the_direct_call(run) -> None:
    assert run["record_calls"] == {"FATHER": "0/0", "MOTHER": "0/1", "PROBAND": "0/1"}
    review = run["compound_het_review"]
    assert review["status"] == 200, review["text"]


def test_two_callers_of_one_sv_survive_the_merge(run) -> None:
    sv = run["sv_after"]
    entries = _by(sv["entries"], _DELETION)
    # Before the fix both uploads shared one key and the merge kept one of them.
    assert entries == {"sniffles": {"PROBAND": "0/1"}, "spectre": {"PROBAND": "1/1"}}
    keys = {row["source"]: row["key"] for row in sv["entries"] if row["variant_id"] == _DELETION}
    assert keys["sniffles"] != keys["spectre"]
    # Each caller keeps its own details and lookup row, under its own key.
    assert {
        row["source"]: row["key"] for row in sv["details"] if row["variant_id"] == _DELETION
    } == keys
    assert sorted(row["key"] for row in sv["lookups"] if row["variant_id"] == _DELETION) == sorted(
        keys.values()
    )
    # The NeedlR rows are untouched.
    needlr = [row for row in sv["entries"] if row["source"] == "needlr"]
    assert needlr == [row for row in run["sv_before"]["entries"] if row["source"] == "needlr"]


def test_the_sv_page_shows_each_caller_with_its_own_call(run) -> None:
    page = run["sv_page"]
    assert page["status"] == 200, page["text"]
    calls = sorted(
        (variant["source"], _genotypes(variant)["PROBAND"])
        for variant in page["json"]["variants"]
        if variant["_id"] == _DELETION
    )
    assert calls == [("sniffles", "0/1"), ("spectre", "1/1")]


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    assert run["small_restored"] == run["small_before"]
    assert run["sv_restored"] == run["sv_before"]


async def _refusal_checks() -> dict:
    from backend.app.core.clickhouse import execute_clickhouse, init_clickhouse_schema
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs

    await init_clickhouse_schema()
    database = settings.clickhouse_database
    table = f"{database}.`E2E_OLDKEY/SNV_INDEL/entries`"
    out: dict = {}
    await execute_clickhouse(f"DROP TABLE IF EXISTS {table} SYNC")
    # The sort key a table created before the callset joined the row identity has.
    await execute_clickhouse(
        f"CREATE TABLE {table} (`key` UInt64, `project_guid` LowCardinality(String), "
        "`family_guid` String, `xpos` UInt64, `source` LowCardinality(String), `sign` Int8) "
        "ENGINE = CollapsingMergeTree(sign) PARTITION BY project_guid "
        "ORDER BY (project_guid, family_guid, xpos, key)"
    )
    try:
        for name, check in (
            ("startup", cvs.verify_clickhouse_variant_storage_identity),
            ("ensure", lambda: cvs.ensure_clickhouse_variant_tables("E2E_OLDKEY")),
        ):
            try:
                await check()
                out[name] = None
            except cvs.ClickHouseStorageIdentityError as exc:
                out[name] = str(exc)
        rows = await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
            {"database": database, "prefix": "E2E_OLDKEY/"},
        )
        out["tables"] = sorted(str(name) for (name,) in rows)
    finally:
        await execute_clickhouse(f"DROP TABLE IF EXISTS {table} SYNC")
    await cvs.verify_clickhouse_variant_storage_identity()
    out["after_drop"] = "ok"
    return out


def test_startup_and_the_table_ensure_refuse_an_older_row_identity() -> None:
    from backend.tests.e2e import _harness

    out = _harness.run_async(_refusal_checks)
    for name in ("startup", "ensure"):
        message = out[name]
        assert message is not None, f"{name} accepted the older table"
        assert "`E2E_OLDKEY/SNV_INDEL/entries`" in message
        assert "(project_guid, family_guid, xpos, key)" in message
        assert "(project_guid, family_guid, xpos, key, source)" in message
    # The refused ensure created nothing next to the older table.
    assert out["tables"] == ["E2E_OLDKEY/SNV_INDEL/entries"]
    assert out["after_drop"] == "ok"
