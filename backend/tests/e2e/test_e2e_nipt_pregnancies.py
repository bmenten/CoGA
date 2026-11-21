"""Two pregnancies of one woman in one family, each pregnancy's NIPT an analysis of its own, E2E.

The owner's decisions of 2026-11-21: a second monogenic or conventional NIPT of the same woman can
be of a later pregnancy, with another fetus and a very different result. Each package names its
pregnancy (``pregnancy_id``); CoGA keys each NIPT by its type and its pregnancy, and keeps each
pregnancy's data, readings, reviews and sign-out its own. A synthetic family is imported, into
real Postgres and ClickHouse, a new family each run, from four package folders:

1. the monogenic NIPT of pregnancy ZW1: the father, the first plasma and its fetus, with their
   callset (the father heterozygous at 7-1000) and the plasma's coverage BED;
2. the monogenic NIPT of pregnancy ZW2, a twin pregnancy, imported with update: the same father,
   the second plasma (linked as the same woman as the first) and two fetuses, with their own
   callset of the father (homozygous reference at 7-1000) and the second plasma (7-2000);
3. the conventional NIPT of pregnancy ZW1, of the first plasma, imported with update;
4. a monogenic NIPT of a pregnancy ZW3 that lists the first plasma: refused before anything is
   written, a plasma being one pregnancy's.

Then:

* the family holds three analyses, one package record each, each pregnancy's data under sources of
  its own: the second pregnancy's import replaced nothing of the first's, the father's calls
  among them;
* a reading of the monogenic NIPT names its pregnancy (422 without one): each reads its own trio,
  the twin pregnancy both fetuses with the multiple-pregnancy note, and each variant list its own
  callset (the father heterozygous at 7-1000 in the first, homozygous reference in the second);
* a review is its pregnancy's: the first pregnancy's variant tagged for the report is not the
  second's;
* the conventional NIPT reads the first plasma alone, and the Sample QC of each pregnancy names it;
* the second pregnancy is signed out on its own: its record holds its trio, both fetuses and the
  note, and no reported variant (the first pregnancy's tag is not its own);
* an overwrite of the first pregnancy's monogenic NIPT replaces its own calls and leaves the
  second's as they were.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from backend.app.services.family_package_records import suffixed_source
from backend.tests._ccnv_output import write_ccnv_output
from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration

MNIPT1, MNIPT2, CNIPT1 = "monogenic_nipt:ZW1", "monogenic_nipt:ZW2", "conventional_nipt:ZW1"
# Each pregnancy's callset: its own source of the caller its package names.
CALLS1, CALLS2 = suffixed_source("mutect2", "ZW1"), suffixed_source("mutect2", "ZW2")
FIRST, SECOND = "7-1000-A-G", "7-2000-C-T"
MNIPT2_SIGN_OUT = {
    "analysis_type": MNIPT2,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: two variants are too few for the NIPT checks",
}


def _vcf(father: str, plasma: str, rows: list[tuple[int, str, str, str, str]]) -> str:
    """A Mutect2-style joint VCF of the father and a plasma: (position, ref, alt, father GT:AD:DP,
    plasma GT:AD:DP) per row, on chr7."""
    lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=chr7>",
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{plasma}",
    ]
    lines += [
        f"chr7\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\tTLOD=300\tGT:AD:DP\t{father_call}\t{plasma_call}"
        for pos, ref, alt, father_call, plasma_call in rows
    ]
    return "\n".join(lines) + "\n"


def _mnipt_package(
    root: Path,
    family_id: str,
    pregnancy: str,
    *,
    father: str,
    plasma: str,
    fetuses: list[str],
    rows: list[tuple[int, str, str, str, str]],
    same_as: str | None = None,
) -> Path:
    root.mkdir(parents=True)
    (root / "nipt_combined.vcf").write_text(_vcf(father, plasma, rows), encoding="utf-8")
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{plasma}\t0\t0\t2\t1\n"
        + "".join(f"{family_id}\t{fetus}\t{father}\t{plasma}\t0\t2\trole=fetus\n" for fetus in fetuses),
        encoding="utf-8",
    )
    (root / "coverage").mkdir()
    (root / "coverage" / f"{plasma}.bed").write_text("1\t10000\t18000\t80\n", encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "family_id": family_id,
        "analysis_type": "monogenic_nipt",
        "pregnancy_id": pregnancy,
        "ped": "family.ped",
        "samples": {plasma: {"assay": "nipt_cfdna"}},
        "datasets": {
            "snv": {"family_vcf": "nipt_combined.vcf", "source_format": "mutect2"},
            "coverage": {"per_sample": {plasma: {"bed": f"coverage/{plasma}.bed", "content": "read_depth"}}},
        },
    }
    if same_as is not None:
        manifest["family"] = {"relationships": {"same_individual": [{"samples": [plasma, same_as]}]}}
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _cnipt_package(root: Path, family_id: str, plasma: str) -> Path:
    root.mkdir(parents=True)
    entry = write_ccnv_output(root, plasma)
    (root / "family.ped").write_text(f"{family_id}\t{plasma}\t0\t0\t2\t1\trole=mother\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": "family.ped",
        "analysis_type": "conventional_nipt",
        "pregnancy_id": "ZW1",
        "samples": {plasma: {"assay": "cnipt_cfdna"}},
        "datasets": {"ccnv": {"per_sample": {plasma: entry}}},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


async def _import(sessionmaker: Any, root: Path, *, project_id: str, admin: Any, mode: str) -> dict[str, Any]:
    from backend.app.services import family_package_import as package_import

    async with sessionmaker() as session:
        try:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                manifest_text=kept_manifest(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 - a refusal is what a test reads
            await session.rollback()
            return {"completed": False, "refused": type(exc).__name__, "message": str(exc)}
    return {
        "completed": result.completed,
        "error": result.error,
        "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
    }


async def _calls(family_uuid: str, source: str) -> list[tuple[str, dict[str, str]]]:
    """The family's rows of the callset ``source``: each variant with each sample's GT."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_storage import _small_table_name
    from backend.tests.e2e import _harness

    rows = await execute_clickhouse(
        "SELECT variantId, `calls.sampleId`, `calls.gt` "
        f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND source = %(source)s AND sign = 1 ORDER BY variantId",
        {"family_guid": family_uuid, "source": source},
    )
    return [(str(variant_id), dict(zip(map(str, samples), map(str, gts)))) for variant_id, samples, gts in rows]


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Sample IDs are unique across families.
    father, plasma1, plasma2, fetus1, twin1, twin2 = (
        f"{role}_{family_id}" for role in ("FATHER", "CFDNA1", "CFDNA2", "FETUS1", "TWIN1", "TWIN2")
    )
    out: dict[str, Any] = {
        "samples": {
            "father": father,
            "plasma1": plasma1,
            "plasma2": plasma2,
            "fetus1": fetus1,
            "twins": [twin1, twin2],
        }
    }
    first = _mnipt_package(
        base / family_id / "zw1",
        family_id,
        "ZW1",
        father=father,
        plasma=plasma1,
        fetuses=[fetus1],
        rows=[(1000, "A", "G", "0/1:500,500:1000", "0/1:900,100:1000")],
    )
    second = _mnipt_package(
        base / family_id / "zw2",
        family_id,
        "ZW2",
        father=father,
        plasma=plasma2,
        fetuses=[twin1, twin2],
        rows=[
            (1000, "A", "G", "0/0:1000,0:1000", "0/0:1000,0:1000"),
            (2000, "C", "T", "0/1:500,500:1000", "0/1:880,120:1000"),
        ],
        same_as=plasma1,
    )
    cnipt = _cnipt_package(base / family_id / "cnipt_zw1", family_id, plasma1)
    reused = _mnipt_package(
        base / family_id / "zw3",
        family_id,
        "ZW3",
        father=father,
        plasma=plasma1,
        fetuses=[f"FETUS3_{family_id}"],
        rows=[(3000, "G", "A", "0/1:500,500:1000", "0/1:900,100:1000")],
    )

    out["imports"] = {"zw1": await _import(sessionmaker, first, project_id=project_id, admin=admin, mode="cancel")}
    async with sessionmaker() as session:
        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
    out["calls1_before"] = await _calls(family_uuid, CALLS1)
    out["imports"]["zw2"] = await _import(sessionmaker, second, project_id=project_id, admin=admin, mode="update")
    out["calls1_after_zw2"] = await _calls(family_uuid, CALLS1)
    out["calls2"] = await _calls(family_uuid, CALLS2)
    out["imports"]["cnipt"] = await _import(sessionmaker, cnipt, project_id=project_id, admin=admin, mode="update")
    async with sessionmaker() as session:
        before = (
            await session.execute(text("SELECT metadata FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
    out["imports"]["zw3"] = await _import(sessionmaker, reused, project_id=project_id, admin=admin, mode="update")
    async with sessionmaker() as session:
        out["family_metadata"] = (
            await session.execute(text("SELECT metadata FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
        out["zw3_wrote_nothing"] = out["family_metadata"] == before
        out["links"] = sorted(
            tuple(sorted((str(row["a"]), str(row["b"]))))
            for row in (
                await session.execute(
                    text(
                        "SELECT sa.sample_id AS a, sb.sample_id AS b FROM family_relationships fr "
                        "JOIN samples sa ON sa.id = fr.sample_id_a JOIN samples sb ON sb.id = fr.sample_id_b "
                        "WHERE fr.family_id = CAST(:f AS uuid) AND fr.relationship_type = 'same_individual' "
                        "AND fr.active"
                    ),
                    {"f": family_uuid},
                )
            ).mappings()
        )

    def captured(response: Any) -> dict[str, Any]:
        return {"status": response.status_code, "json": response.json()}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        api = f"/api/families/{family_id}"
        out["summary_unnamed"] = captured(await ac.get(f"{api}/nipt/summary"))
        out["summaries"] = {
            analysis: captured(await ac.get(f"{api}/nipt/summary", params={"analysis": analysis}))
            for analysis in (MNIPT1, MNIPT2)
        }
        out["lists"] = {}
        for analysis in (MNIPT1, MNIPT2):
            listed = await ac.get(f"{api}/small-variants", params={"analysis": analysis, "page_size": 50})
            out["lists"][analysis] = {
                "status": listed.status_code,
                "rows": {
                    str(row["_id"]): {call["sample"]: call["gt"] for call in row["genotypes"]}
                    for row in listed.json().get("variants", [])
                },
            }
        out["tagged"] = captured(
            await ac.put(f"{api}/small-variants/{FIRST}/review", params={"analysis": MNIPT1}, json={"tags": ["report"]})
        )
        out["review_summaries"] = {
            analysis: captured(await ac.get(f"{api}/small-variant-review-summary", params={"analysis": analysis}))
            for analysis in (MNIPT1, MNIPT2)
        }
        out["cnipt"] = captured(await ac.get(f"{api}/cnipt"))
        out["qc_unnamed"] = captured(await ac.get(f"{api}/qc/sample-integrity"))
        out["qc"] = {
            analysis: captured(await ac.get(f"{api}/qc/sample-integrity", params={"analysis": analysis}))
            for analysis in (MNIPT1, MNIPT2)
        }
        report = f"{api}/report"
        out["signout_unnamed"] = captured(await ac.post(f"{report}/sign-out", json={}))
        signed = await _harness.sign_out(ac, f"{report}/sign-out", MNIPT2_SIGN_OUT)
        out["signout"] = captured(signed)
        version = (out["signout"]["json"] or {}).get("version")
        if version is not None:
            out["record"] = captured(await ac.get(f"{report}/sign-outs/{version}"))

    # The first pregnancy's monogenic NIPT again, with overwrite: the father now homozygous at
    # 7-1000 in its callset. The second pregnancy's calls stay as they were.
    again = _mnipt_package(
        base / family_id / "zw1_again",
        family_id,
        "ZW1",
        father=father,
        plasma=plasma1,
        fetuses=[fetus1],
        rows=[(1000, "A", "G", "1/1:0,1000:1000", "0/1:850,150:1000")],
    )
    out["imports"]["zw1_again"] = await _import(sessionmaker, again, project_id=project_id, admin=admin, mode="overwrite")
    out["calls1_after_overwrite"] = await _calls(family_uuid, CALLS1)
    out["calls2_after_overwrite"] = await _calls(family_uuid, CALLS2)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("nipt_pregnancies")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"PREG_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_each_pregnancys_packages_are_imported_into_the_one_family(run) -> None:
    for name in ("zw1", "zw2", "cnipt", "zw1_again"):
        assert run["imports"][name]["completed"] is True, (name, run["imports"][name])


def test_the_family_holds_each_pregnancys_analyses_with_a_record_each(run) -> None:
    metadata = run["family_metadata"]
    assert metadata["analyses"] == [CNIPT1, MNIPT1, MNIPT2]
    records = metadata["package_imports"]
    assert sorted(records) == [CNIPT1, MNIPT1, MNIPT2]
    samples = run["samples"]
    assert records[MNIPT1]["members"] == sorted([samples["father"], samples["plasma1"], samples["fetus1"]])
    assert records[MNIPT2]["members"] == sorted([samples["father"], samples["plasma2"], *samples["twins"]])
    # Each pregnancy's sources are its own.
    assert records[MNIPT1]["sources"]["small_variants"] == [CALLS1]
    assert records[MNIPT2]["sources"]["small_variants"] == [CALLS2]
    assert suffixed_source("web", "ZW2") in records[MNIPT2]["sources"]["tracks"]
    # The two plasmas are one woman, linked as the same individual.
    assert run["links"] == [tuple(sorted((samples["plasma1"], samples["plasma2"])))]


def test_the_second_pregnancys_import_replaced_nothing_of_the_firsts(run) -> None:
    samples = run["samples"]
    assert run["calls1_before"] == [(FIRST, {samples["father"]: "0/1", samples["plasma1"]: "0/1"})]
    assert run["calls1_after_zw2"] == run["calls1_before"]
    # The second pregnancy's own copy of the father's calls.
    assert dict(run["calls2"]) == {
        FIRST: {samples["father"]: "0/0", samples["plasma2"]: "0/0"},
        SECOND: {samples["father"]: "0/1", samples["plasma2"]: "0/1"},
    }


def test_a_plasma_of_another_pregnancy_is_refused_before_anything_is_written(run) -> None:
    refused = run["imports"]["zw3"]
    assert refused.get("refused") == "ImportRefused", refused
    assert f"maternal plasma {run['samples']['plasma1']}" in refused["message"]
    assert "monogenic NIPT of pregnancy ZW1" in refused["message"]
    assert run["zw3_wrote_nothing"] is True


def test_a_reading_of_the_monogenic_nipt_names_its_pregnancy(run) -> None:
    unnamed = run["summary_unnamed"]
    assert unnamed["status"] == 422, unnamed
    assert "name the one to read" in str(unnamed["json"]["detail"])
    samples = run["samples"]
    first = run["summaries"][MNIPT1]
    assert first["status"] == 200, first
    assert (first["json"]["analysis"], first["json"]["fetuses"], first["json"]["multiple_pregnancy_note"]) == (
        MNIPT1,
        [samples["fetus1"]],
        None,
    )
    second = run["summaries"][MNIPT2]
    assert second["status"] == 200, second
    assert second["json"]["fetuses"] == samples["twins"]
    assert "Multiple pregnancy (2 fetuses)" in second["json"]["multiple_pregnancy_note"]


def test_each_pregnancys_variant_list_reads_its_own_callset(run) -> None:
    samples = run["samples"]
    first, second = run["lists"][MNIPT1], run["lists"][MNIPT2]
    assert (first["status"], second["status"]) == (200, 200), (first, second)
    assert set(first["rows"]) == {FIRST}
    assert set(second["rows"]) == {FIRST, SECOND}
    # The father's call at 7-1000 is each pregnancy's own: heterozygous in the first's callset,
    # homozygous reference in the second's.
    assert first["rows"][FIRST][samples["father"]] == "0/1"
    assert second["rows"][FIRST][samples["father"]] == "0/0"
    # Another pregnancy's plasma is no member of a reading.
    assert samples["plasma2"] not in first["rows"][FIRST]


def test_a_review_is_its_pregnancys(run) -> None:
    assert run["tagged"]["status"] == 200, run["tagged"]
    counts = {analysis: response["json"]["tag_counts"] for analysis, response in run["review_summaries"].items()}
    assert counts[MNIPT1].get("report") == 1
    assert counts[MNIPT2].get("report", 0) == 0


def test_the_conventional_nipt_and_each_pregnancys_sample_qc_are_their_own(run) -> None:
    samples = run["samples"]
    cnipt = run["cnipt"]
    assert cnipt["status"] == 200, cnipt
    assert cnipt["json"]["analysis"] == CNIPT1
    assert [sample["sample_id"] for sample in cnipt["json"]["samples"]] == [samples["plasma1"]]
    assert run["qc_unnamed"]["status"] == 422, run["qc_unnamed"]
    for analysis in (MNIPT1, MNIPT2):
        qc = run["qc"][analysis]
        assert qc["status"] == 200, (analysis, qc)
        assert qc["json"]["application"] == "nipt"
    assert run["qc"][MNIPT2]["json"]["paternity_check"]["father"] == samples["father"]


def test_the_second_pregnancy_is_signed_out_on_its_own(run) -> None:
    assert run["signout_unnamed"]["status"] == 422, run["signout_unnamed"]
    assert run["signout"]["status"] == 200, run["signout"]
    snapshot = run["record"]["json"]["snapshot"]
    samples = run["samples"]
    assert snapshot["analysis_type"] == MNIPT2
    assert snapshot["nipt"]["samples"] == {
        "father": samples["father"],
        "maternal_plasma": samples["plasma2"],
        "fetuses": samples["twins"],
    }
    assert snapshot["nipt"]["analysis"] == MNIPT2
    assert "Multiple pregnancy (2 fetuses)" in snapshot["nipt"]["multiple_pregnancy_note"]
    # The first pregnancy's report tag is not the second's.
    assert snapshot["reported_variants"] == []
    assert "cnipt" not in snapshot


def test_an_overwrite_of_the_first_pregnancy_leaves_the_seconds_calls_as_they_were(run) -> None:
    samples = run["samples"]
    assert run["calls1_after_overwrite"] == [(FIRST, {samples["father"]: "1/1", samples["plasma1"]: "0/1"})]
    assert run["calls2_after_overwrite"] == run["calls2"]
