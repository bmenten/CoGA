"""Importing one sample's mitochondrial calls replaces only that sample's calls (E2E).

A package's ``mito`` dataset holds one chrM VCF per sample. The calls are stored as small
variants under the ``mito`` source, one row per variant holding every sample's call:
the mtDNA workspace reads the mother's and the siblings' calls from those rows for the
maternal transmission, and the mitochondrial ACMG evaluator reads that transmission
(PP1, BS4). Each sample's file used to be imported as an overwrite of the family's whole
``mito`` source, so every sample deleted the calls of the samples imported before it and
only the last one's remained.

The family here is its own (a new id each run), so the golden trio is never touched:

* the first import brings in the mother's, the proband's and the father's calls; every
  sample's calls must remain, one live row per variant, also after ClickHouse has merged
  the parts, and the mtDNA workspace must show the variant the mother and the proband
  share as maternally shared, and each sample's haplogroup from its mutserve annotation
  (the maternal-lineage check of the Sample QC review compares them; the import used to
  close the annotation before it read the haplogroup, so none was ever shown);
* a second import in ``overwrite`` mode brings a new proband file and an empty father
  file: the proband's calls are replaced, the father's are removed (his new callset has
  none) and the mother's stay exactly as stored.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for
the API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##DeepVariant_version=1.10.0\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant allele fraction">\n'
    # The long-read pipeline names the column after its input file, not the sample; a
    # single-column file under a per-sample entry belongs to that sample.
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)


def _record(pos: int, ref: str, alt: str, gt: str, depth: int, alt_depth: int, filt: str) -> str:
    vaf = round(alt_depth / depth, 4)
    return (
        f"chrM\t{pos}\t.\t{ref}\t{alt}\t30\t{filt}\t.\tGT:GQ:DP:AD:VAF\t"
        f"{gt}:30:{depth}:{depth - alt_depth},{alt_depth}:{vaf}\n"
    )


_HOMOPLASMIC_73 = _record(73, "A", "G", "1/1", 500, 500, "GERMLINE")

_MOTHER = _HEADER + (
    _HOMOPLASMIC_73
    + _record(3243, "A", "G", "0/1", 500, 75, "PASS")
    + _record(16519, "T", "C", "1/1", 500, 500, "GERMLINE")
)
_PROBAND = _HEADER + (
    _HOMOPLASMIC_73
    + _record(3243, "A", "G", "0/1", 500, 225, "PASS")
    + _record(9000, "C", "T", "0/1", 500, 150, "PASS")
)
_FATHER = _HEADER + (
    _HOMOPLASMIC_73
    + _record(263, "A", "G", "1/1", 500, 500, "GERMLINE")
)
# The re-import: the 3243 heteroplasmy is now 50%, the 9000 call is gone, 10000 is new.
_PROBAND_V2 = _HEADER + (
    _HOMOPLASMIC_73
    + _record(3243, "A", "G", "0/1", 500, 250, "PASS")
    + _record(10000, "G", "A", "0/1", 500, 100, "PASS")
)
_EMPTY = _HEADER


def _annotation(*rows: tuple[int, str, str, str]) -> str:
    """A mutserve annotation TSV of ``(pos, ref, alt, Phylotree17 haplogroups)`` rows."""
    return "ID\tFilter\tPos\tRef\tVariant\tVariantLevel\tMaplocus\tPhylotree17_haplogroups\n" + "".join(
        f"sample\tPASS\t{pos}\t{ref}\t{alt}\t1.0\tMT\t{haplogroups}\n" for pos, ref, alt, haplogroups in rows
    )


# Each sample's haplogroup is the one most of its annotated variants name: U5a1 for the
# mother and the proband (one maternal line), H1 for the father.
_MOTHER_ANNOTATION = _annotation((73, "A", "G", "U5a1,H1"), (3243, "A", "G", "."), (16519, "T", "C", "U5a1"))
_PROBAND_ANNOTATION = _annotation((73, "A", "G", "U5a1,H1"), (3243, "A", "G", "."), (9000, "C", "T", "U5a1"))
_FATHER_ANNOTATION = _annotation((73, "A", "G", "U5a1,H1"), (263, "A", "G", "H1"))


_SV_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.7.3\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)


def _sv_record(start: int, end: int, vaf: float, support: int) -> str:
    return (
        f"chrM\t{start}\tSniffles2.DEL.1M0\tN\t<DEL>\t60\tPASS\t"
        f"PRECISE;SVTYPE=DEL;SVLEN=-{end - start};END={end};SUPPORT={support};VAF={vaf}\t"
        f"GT:GQ:DR:DV\t0/1:60:{400 - support}:{support}\n"
    )


# The common 4977 bp deletion: 5% in the mother, 35% in the proband; none in the father.
_MOTHER_SV = _SV_HEADER + _sv_record(8470, 13447, 0.05, 20)
_PROBAND_SV = _SV_HEADER + _sv_record(8470, 13447, 0.35, 140)
_FATHER_SV = _SV_HEADER
# The re-import: the proband's file now holds another deletion only.
_PROBAND_SV_V2 = _SV_HEADER + _sv_record(10000, 12000, 0.4, 160)


def _write_package(
    root: Path,
    family_id: str,
    files: dict[str, str],
    annotations: dict[str, str] | None = None,
    sv_files: dict[str, str] | None = None,
) -> None:
    """A package with the trio's PED and a ``mito`` dataset of ``files`` (sample -> VCF),
    with the mutserve ``annotations`` given (sample -> TSV)."""
    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n",
        encoding="utf-8",
    )
    (root / "mito").mkdir()
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  mito:",
        "    per_sample:",
    ]
    for sample_id, vcf in files.items():
        (root / "mito" / f"{sample_id}.vcf").write_text(vcf, encoding="utf-8")
        entry = f"vcf: mito/{sample_id}.vcf"
        if annotations and sample_id in annotations:
            (root / "mito" / f"{sample_id}_snv_annot.txt").write_text(annotations[sample_id], encoding="utf-8")
            entry += f", annotation_tsv: mito/{sample_id}_snv_annot.txt"
        if sv_files and sample_id in sv_files:
            (root / "mito" / f"{sample_id}_sv.vcf").write_text(sv_files[sample_id], encoding="utf-8")
            entry += f", sv_vcf: mito/{sample_id}_sv.vcf"
        lines.append(f"      {sample_id}: {{{entry}}}")
    (root / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _mito_rows(family_uuid: str) -> list[dict]:
    """The family's live ``mito`` entry rows as stored, one dict per row (not grouped)."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.tests.e2e import _harness

    rows = await execute_clickhouse(
        f"SELECT variantId, key, `calls.sampleId`, `calls.gt`, `calls.af` "
        f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND source = 'mito' AND sign = 1 "
        "ORDER BY variantId",
        {"family_guid": family_uuid},
    )
    return [
        {
            "variant_id": str(variant_id),
            "key": int(key),
            "calls": {
                str(sample): (str(gt), round(float(af[0]), 2) if af else None)
                for sample, gt, af in zip(sample_ids, gts, afs)
            },
        }
        for variant_id, key, sample_ids, gts, afs in rows
    ]


async def _mito_sv_calls(family_uuid: str) -> dict[tuple[int, int], set[str]]:
    """The family's live ``mito_sv`` SVs as stored: (start, end) -> the samples called."""
    from backend.app.services.clickhouse_variant_storage import fetch_family_structural_variant_rows
    from backend.tests.e2e import _harness

    rows = await fetch_family_structural_variant_rows(_harness.ASSEMBLY, family_uuid, source="mito_sv")
    calls: dict[tuple[int, int], set[str]] = {}
    for row in rows:
        calls.setdefault((row.record.start, row.record.end), set()).update(
            call.sample for call in row.record.calls
        )
    return calls


async def _merge_parts() -> None:
    """Force the part merges ClickHouse would otherwise run in the background."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.tests.e2e import _harness

    await execute_clickhouse(
        f"OPTIMIZE TABLE {_small_table_name(_harness.ASSEMBLY, 'entries')} FINAL"
    )


async def _exercise(base: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness
    from sqlalchemy import text

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))

    async def run_import(folder: Path, conflict_mode: str) -> dict:
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(folder),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=conflict_mode,
            )
        mito = next((d for d in result.datasets if d.dataset_type == "mito"), None)
        return {
            "completed": result.completed,
            "error": result.error,
            "mito_status": mito.status if mito else None,
            "mito_summary": mito.summary if mito else None,
        }

    out: dict = {"samples": {"father": father, "mother": mother, "proband": proband}}

    first = base / "first" / family_id
    _write_package(
        first,
        family_id,
        {mother: _MOTHER, proband: _PROBAND, father: _FATHER},
        {mother: _MOTHER_ANNOTATION, proband: _PROBAND_ANNOTATION, father: _FATHER_ANNOTATION},
        {mother: _MOTHER_SV, proband: _PROBAND_SV, father: _FATHER_SV},
    )
    out["first"] = await run_import(first, "cancel")

    async with sessionmaker() as session:
        family_uuid = (
            await session.execute(
                text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id}
            )
        ).scalar_one()
    out["rows_after_first"] = await _mito_rows(family_uuid)
    out["sv_after_first"] = await _mito_sv_calls(family_uuid)
    await _merge_parts()
    out["rows_after_first_merge"] = await _mito_rows(family_uuid)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        response = await ac.get(f"/api/families/{family_id}/mitochondrial-dna")
        out["mtdna"] = {"status": response.status_code, "text": response.text}
        if response.status_code == 200:
            out["mtdna"]["json"] = response.json()

    second = base / "second" / family_id
    _write_package(
        second, family_id, {proband: _PROBAND_V2, father: _EMPTY}, sv_files={proband: _PROBAND_SV_V2}
    )
    out["second"] = await run_import(second, "overwrite")
    out["rows_after_second"] = await _mito_rows(family_uuid)
    out["sv_after_second"] = await _mito_sv_calls(family_uuid)
    await _merge_parts()
    out["rows_after_second_merge"] = await _mito_rows(family_uuid)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("mito_import")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base / "first"), str(base / "second")])
    try:
        return _harness.run_async(lambda: _exercise(base, f"MITO_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _calls(rows: list[dict]) -> dict[str, dict[str, tuple[str, float | None]]]:
    return {row["variant_id"]: row["calls"] for row in rows}


def _duplicate_keys(rows: list[dict]) -> list[int]:
    return [key for key, count in Counter(row["key"] for row in rows).items() if count > 1]


def _after_first(samples: dict[str, str]) -> dict[str, dict[str, tuple[str, float | None]]]:
    mother, proband, father = samples["mother"], samples["proband"], samples["father"]
    return {
        "M-73-A-G": {mother: ("1/1", 1.0), proband: ("1/1", 1.0), father: ("1/1", 1.0)},
        "M-263-A-G": {father: ("1/1", 1.0)},
        "M-3243-A-G": {mother: ("0/1", 0.15), proband: ("0/1", 0.45)},
        "M-9000-C-T": {proband: ("0/1", 0.3)},
        "M-16519-T-C": {mother: ("1/1", 1.0)},
    }


def test_first_import_completes(run) -> None:
    first = run["first"]
    assert first["completed"] is True, first
    assert first["mito_status"] == "imported", first


def test_every_samples_calls_remain_after_the_first_import(run) -> None:
    rows = run["rows_after_first"]
    # Before the fix only the father's calls (the last file imported) were left.
    assert _duplicate_keys(rows) == []
    assert _calls(rows) == _after_first(run["samples"])


def test_every_samples_calls_survive_part_merges(run) -> None:
    rows = run["rows_after_first_merge"]
    assert _duplicate_keys(rows) == []
    assert _calls(rows) == _after_first(run["samples"])


def test_mtdna_workspace_shows_the_maternally_shared_heteroplasmy(run) -> None:
    mtdna = run["mtdna"]
    assert mtdna["status"] == 200, mtdna["text"]
    mother, proband = run["samples"]["mother"], run["samples"]["proband"]
    variants = {variant["variant_id"]: variant for variant in mtdna["json"]["variants"]}
    shared = variants["M-3243-A-G"]
    # Stored as Float32, so read back to two decimals.
    assert {sample: round(call["allele_fraction"], 2) for sample, call in shared["calls"].items()} == {
        mother: 0.15,
        proband: 0.45,
    }
    assert shared["maternal_transmission"] == "maternal_shared"
    assert variants["M-16519-T-C"]["maternal_transmission"] == "maternal_only"
    assert variants["M-9000-C-T"]["maternal_transmission"] == "maternal_not_observed"


def test_mtdna_workspace_shows_each_samples_haplogroup(run) -> None:
    mtdna = run["mtdna"]
    assert mtdna["status"] == 200, mtdna["text"]
    mother, proband, father = (run["samples"][role] for role in ("mother", "proband", "father"))
    haplogroups = {sample["sample_id"]: sample["haplogroup"] for sample in mtdna["json"]["samples"]}
    assert haplogroups == {mother: "U5a1", proband: "U5a1", father: "H1"}
    assert "No per-sample mtDNA haplogroup metadata is available." not in mtdna["json"]["qc_notes"]


def test_reimport_replaces_only_the_imported_samples_calls(run) -> None:
    second = run["second"]
    assert second["completed"] is True, second
    assert second["mito_status"] == "imported", second
    mother, proband, father = (run["samples"][role] for role in ("mother", "proband", "father"))
    # The father's new file holds no call: his two stored calls are removed.
    assert second["mito_summary"][father] == {
        "inserted": 0,
        "removed": 2,
        "message": "No chrM variants called",
    }
    expected = {
        "M-73-A-G": {mother: ("1/1", 1.0), proband: ("1/1", 1.0)},
        "M-3243-A-G": {mother: ("0/1", 0.15), proband: ("0/1", 0.5)},
        "M-10000-G-A": {proband: ("0/1", 0.2)},
        "M-16519-T-C": {mother: ("1/1", 1.0)},
    }
    for rows in (run["rows_after_second"], run["rows_after_second_merge"]):
        assert _duplicate_keys(rows) == []
        assert _calls(rows) == expected


# The chrM SV files were found and validated but never read: a large heteroplasmic mtDNA
# deletion was dropped without a word.
def test_the_chrm_deletion_is_stored_once_with_both_carriers(run) -> None:
    mother, proband = run["samples"]["mother"], run["samples"]["proband"]
    assert run["sv_after_first"] == {(8470, 13447): {mother, proband}}
    assert run["first"]["mito_summary"]["structural_variants"]["processed"] == 1


def test_mtdna_workspace_shows_the_chrm_deletion_with_each_heteroplasmy(run) -> None:
    mtdna = run["mtdna"]
    assert mtdna["status"] == 200, mtdna["text"]
    mother, proband = run["samples"]["mother"], run["samples"]["proband"]
    [deletion] = mtdna["json"]["structural_variants"]
    assert (deletion["sv_type"], deletion["start"], deletion["end"], deletion["length"]) == ("DEL", 8470, 13447, 4977)
    assert {"MT-ATP8", "MT-ND5"} <= set(deletion["genes"])
    assert {sample: call["heteroplasmy"] for sample, call in deletion["calls"].items()} == {
        mother: 0.05,
        proband: 0.35,
    }


def test_a_chrm_sv_reimport_replaces_only_the_samples_it_brings(run) -> None:
    mother, proband = run["samples"]["mother"], run["samples"]["proband"]
    assert run["sv_after_second"] == {(8470, 13447): {mother}, (10000, 12000): {proband}}
