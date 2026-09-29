"""Deleting a sample rewrites the family's small variants without losing anyone else's (E2E).

``DELETE /admin/samples/{sample_id}`` removes the sample's small-variant calls by rewriting
the family's ``SNV_INDEL/entries`` rows: it deletes them all and writes them back without
that sample's calls. Everything else must come back as it was stored. The rows used to be
read back through the family view, which drops what the view does not show:

* the imputed callset (GLIMPSE2), which the view never reads, so the delete wiped it for
  every member;
* an inactive member's calls (FATHER, marked inactive the way a member deletion leaves
  him, with his data kept; set directly so the golden trio's pedigree stays as it is);
* rows written under a project the family has since left;
* the stored annotation version, the genotype as stored (``HET``) and the allele fraction
  fields, which the view normalises.

Over the golden trio (clair3 SNVs for all three members) a sibling, SIB, is added with
calls in both callsets, and a few rows are written through the normal writer to cover the
cases above. SIB is then deleted through the admin endpoint. Afterwards FATHER is made
active again, the imputed rows are removed and the golden trio is re-imported. Everything
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
_LEFT_PROJECT = "project-the-family-left"

_IMPUTED = "1-1500-C-T"
_ANNOTATED = "1-9000-G-A"
_RAW = "1-9500-T-C"
_SIB_ONLY = "1-9700-A-C"
_LEFT = "1-9900-C-G"


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


def _call(sample: str, gt: str, *, ps: int | None = None, af=None, ad=None):
    from backend.app.services.clickhouse_variant_records import SmallVariantCall

    return SmallVariantCall(
        sample=sample,
        gt=gt,
        gq=99.0,
        dp=20,
        af=[0.5] if af is None else af,
        ad=[10, 10] if ad is None else ad,
        ps=ps,
    )


def _record(variant_id: str, source: str, calls: list):
    from backend.app.services.clickhouse_variant_records import SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source=source,
        rsid=None,
        filters=["PASS"],
        gene_symbols=["GENE_X"],
        annotations=[{"gene": "GENE_X", "consequence": "missense_variant"}],
        calls=calls,
        qual=50.0,
    )


async def _entry_rows(family_uuid: str) -> dict[tuple[str, str, str], dict]:
    """The family's live small-variant entry rows as stored, keyed by (source, id, project)."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.tests.e2e import _harness

    rows = await execute_clickhouse(
        "SELECT source, variantId, project_guid, annotation_version, annotationSetHash, "
        "`calls.sampleId`, `calls.gt`, `calls.ps`, `calls.ab`, `calls.af`, `calls.ad` "
        f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND sign = 1",
        {"family_guid": family_uuid},
    )
    out: dict[tuple[str, str, str], dict] = {}
    for source, variant_id, project, version, set_hash, samples, gts, pss, abs_, afs, ads in rows:
        out[(str(source), str(variant_id), str(project))] = {
            "annotation_version": str(version),
            "annotation_set_hash": int(set_hash),
            "calls": {
                str(sample): {
                    "gt": gt,
                    "ps": ps,
                    "ab": None if ab is None else round(float(ab), 4),
                    "af": [round(float(value), 4) for value in af],
                    "ad": list(ad),
                }
                for sample, gt, ps, ab, af, ad in zip(samples, gts, pss, abs_, afs, ads)
            },
        }
    return out


async def _entry_columns() -> list[str]:
    from backend.app.core.clickhouse import clickhouse_dataset_key, execute_clickhouse
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    table = f"{clickhouse_dataset_key(_harness.ASSEMBLY)}/SNV_INDEL/entries"
    rows = await execute_clickhouse(
        "SELECT name FROM system.columns WHERE database = %(database)s AND table = %(table)s",
        {"database": settings.clickhouse_database, "table": table},
    )
    return sorted(str(name) for (name,) in rows)


async def _sql(statement: str, params: dict) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(text(statement), params)
        await session.commit()


async def _add_sibling(family_uuid: str, project_id: str) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        sib_uuid = (
            await session.execute(
                text(
                    "INSERT INTO samples (sample_id, family_id, sex, metadata) "
                    "VALUES ('SIB', CAST(:f AS uuid), 'female', '{}'::jsonb) RETURNING id::text"
                ),
                {"f": family_uuid},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO family_members (family_id, sample_id, role, clinical_status, affected) "
                "VALUES (CAST(:f AS uuid), CAST(:s AS uuid), 'sibling', 'unaffected', false)"
            ),
            {"f": family_uuid, "s": sib_uuid},
        )
        await session.execute(
            text(
                "INSERT INTO sample_projects (sample_id, project_id) "
                "VALUES (CAST(:s AS uuid), CAST(:p AS uuid))"
            ),
            {"s": sib_uuid, "p": project_id},
        )
        await session.commit()


async def _set_member_active(family_uuid: str, sample_id: str, active: bool) -> None:
    await _sql(
        """
        UPDATE family_members SET active = :active
        WHERE family_id = CAST(:f AS uuid)
          AND sample_id = (SELECT id FROM samples WHERE sample_id = :s AND family_id = CAST(:f AS uuid))
        """,
        {"active": active, "f": family_uuid, "s": sample_id},
    )


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_small_variants,
        insert_small_variant_records,
        refresh_family_small_variant_summaries,
    )
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid, project_id = facts["family_uuid"], facts["project_id"]
    out: dict = {"facts": facts, "golden_rows": await _entry_rows(family_uuid)}
    assembly = _harness.ASSEMBLY

    try:
        await _add_sibling(family_uuid, project_id)
        await insert_small_variant_records(
            assembly,
            family_uuid,
            [project_id],
            [
                _record(
                    _IMPUTED,
                    "glimpse2",
                    [
                        _call("FATHER", "0|1", ps=5001),
                        _call("MOTHER", "1|0", ps=5001),
                        _call("PROBAND", "0|1", ps=5001),
                        _call("SIB", "1|1", ps=5001),
                    ],
                )
            ],
        )
        await insert_small_variant_records(
            assembly,
            family_uuid,
            [project_id],
            [_record(_ANNOTATED, "clair3", [_call("PROBAND", "0/1"), _call("SIB", "0/1")])],
            annotation_version="vep-e2e",
        )
        await insert_small_variant_records(
            assembly,
            family_uuid,
            [project_id],
            [
                # A caller token and an allele balance from AD with no AF, as stored.
                _record(_RAW, "clair3", [_call("PROBAND", "HET", af=[], ad=[12, 8]), _call("SIB", "0/1")]),
                _record(_SIB_ONLY, "clair3", [_call("SIB", "0/1")]),
            ],
        )
        await insert_small_variant_records(
            assembly,
            family_uuid,
            [_LEFT_PROJECT],
            [_record(_LEFT, "clair3", [_call("PROBAND", "0/1"), _call("SIB", "0/1")])],
        )
        await refresh_family_small_variant_summaries(assembly, family_uuid)
        await _set_member_active(family_uuid, "FATHER", False)
        out["rows_before"] = await _entry_rows(family_uuid)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            out["delete"] = _cap(await ac.delete("/api/admin/samples/SIB", params={"confirm": "true"}))
        out["rows_after"] = await _entry_rows(family_uuid)
        out["entry_columns"] = await _entry_columns()
    finally:
        # Restore the fixture state for any module that runs later.
        await _set_member_active(family_uuid, "FATHER", True)
        await _sql(
            "DELETE FROM samples WHERE sample_id = 'SIB' AND family_id = CAST(:f AS uuid)",
            {"f": family_uuid},
        )
        await delete_family_small_variants(assembly, family_uuid, source="glimpse2")
        out["restored"] = await _harness.import_golden_trio(root)
        out["rows_restored"] = await _entry_rows(family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    base = tmp_path_factory.mktemp("golden_small_delete")
    root = base / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    # Deleting a sample purges its managed files; keep every managed copy out of data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", base / "data")
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _without_sib(rows: dict) -> dict:
    out = {}
    for identity, row in rows.items():
        calls = {sample: call for sample, call in row["calls"].items() if sample != "SIB"}
        if calls:
            out[identity] = {**row, "calls": calls}
    return out


def test_the_delete_succeeds_and_counts_the_one_variant_only_sib_had(run) -> None:
    delete = run["delete"]
    assert delete["status"] == 200, delete["text"]
    # Counted in the family's current project: SIB's own variant only. Before, the wiped
    # imputed row was counted as deleted too.
    assert delete["json"]["deleted"]["small_variants"] == 1


def test_every_other_row_comes_back_exactly_as_stored(run) -> None:
    # The whole family, row for row, minus SIB's calls: the golden rows with the inactive
    # FATHER's calls, the imputed row with its phase sets, the non-default annotation
    # version, the raw HET token and its AD-derived allele balance with no AF, and the row
    # under the project the family left. Before, all of those came back changed or not at all.
    assert run["rows_after"] == _without_sib(run["rows_before"])


def test_the_imputed_callset_survives(run) -> None:
    rows = run["rows_after"]
    project = run["facts"]["project_id"]
    imputed = rows.get(("glimpse2", _IMPUTED, project))
    assert imputed is not None, "the delete wiped the family's imputed callset"
    assert {sample: (call["gt"], call["ps"]) for sample, call in imputed["calls"].items()} == {
        "FATHER": ("0|1", 5001),
        "MOTHER": ("1|0", 5001),
        "PROBAND": ("0|1", 5001),
    }


def test_the_inactive_members_calls_survive(run) -> None:
    # FATHER is inactive during the delete; the golden rows keep his calls.
    assert len(run["golden_rows"]) == run["facts"]["n_small_variants"]
    for identity, row in run["golden_rows"].items():
        after = run["rows_after"][identity]
        assert after["calls"] == row["calls"], identity
        assert "FATHER" in after["calls"], identity


def test_stored_fields_are_not_normalised(run) -> None:
    rows = run["rows_after"]
    project = run["facts"]["project_id"]
    assert rows[("clair3", _ANNOTATED, project)]["annotation_version"] == "vep-e2e"
    raw = rows[("clair3", _RAW, project)]["calls"]["PROBAND"]
    assert (raw["gt"], raw["ab"], raw["af"], raw["ad"]) == ("HET", 0.4, [], [12, 8])
    assert ("clair3", _LEFT, _LEFT_PROJECT) in rows
    assert ("clair3", _SIB_ONLY, project) not in rows


def test_the_rewrite_copies_every_entries_column(run) -> None:
    from backend.app.services.clickhouse_variant_storage import SMALL_VARIANT_ENTRY_COLUMNS

    assert run["entry_columns"] == sorted(SMALL_VARIANT_ENTRY_COLUMNS)


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["restored"]["completed"] is True
    assert run["rows_restored"] == run["golden_rows"]
