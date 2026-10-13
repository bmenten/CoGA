"""A CNV classification freezes the evidence of the caller's row it was made on (E2E, TRACE-11).

A package's NeedlR call of a deletion and an uploaded Sniffles call of it have one SV id since
CLIN-43 (two uploads' calls of one event already did), and share its review. Each caller's call
is a row of its own on the SV page, with its own evidence: NeedlR annotates the genes, their pLI
and the inheritance; the upload's genes come from the gene loci, and it carries neither. The save
froze the row with the lowest storage key, whatever row the classification was made on, so a
classification made on the NeedlR row could freeze the upload's evidence; and the drift check
read that row again.

Here a trio is imported through the front door (``execute_family_package_import``) with a NeedlR
VCF holding two deletions of the proband, one of which the proband's Sniffles VCF, uploaded
through ``POST /structural-variants/upload/{sample_id}``, calls too; a gene row is seeded on that
deletion for the upload, and removed afterwards. Then, over HTTP:

* the SV page lists the shared deletion twice, a row per caller, with their own genes, pLI and
  inheritance;
* a CNV classification saved through ``PUT …/structural-variants/{variant_id}/review`` naming
  the NeedlR row's caller freezes the NeedlR row's evidence, as the page served it, and the
  drift check (``GET …/classification-drift``) reads it as current; a re-upload that changes the
  Sniffles call does not drift it;
* saved again naming the Sniffles row's caller, it freezes the upload's evidence instead, and a
  re-upload that changes the Sniffles call drifts it (its annotation), compared with that row;
* a save that names no caller (a client from before ``source``) is refused with 422 for the shared
  deletion, writing nothing, and takes the one row of the deletion NeedlR alone calls; one naming
  a caller that does not call the SV is refused with 404.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the API
(see test_e2e_api_contract.py for why). All samples, genes and values are synthetic.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

pytestmark = pytest.mark.integration

_SHARED = "1-7000-12000-DEL---"
_NEEDLR_ONLY = "1-50000-56000-DEL---"
_SEEDED_GENE = "E2E_TRACE11_LOCUS"
_SEEDED_GENE_ID = "E2E_TRACE11_LOCUS_ID"
_SCORING = {
    "classification": "Pathogenic - class 5",
    "tags": [],
    "cnv_acmg": {"kind": "loss", "criteria": [{"code": "2A", "points": 1.0, "accepted": True}]},
}


def _needlr_vcf(proband: str) -> str:
    parents = "Maternal_GT=0/0;Maternal_Alt_Reads=0;Paternal_GT=0/0;Paternal_Alt_Reads=0"
    return (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t7000\tSniffles2.DEL.0S0\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;SVLEN=-5000;END=12000;"
        "Genes=E2E_TRACE_A,E2E_TRACE_B;pLI=0.99,0.41;Inheritance=de_novo;"
        f"Query_ID={proband};Genotype=0/1;Alt_Reads=18;{parents}\n"
        "chr1\t50000\tSniffles2.DEL.1S0\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;SVLEN=-6000;END=56000;"
        "Genes=E2E_TRACE_C;pLI=0.12;Inheritance=maternal;"
        f"Query_ID={proband};Genotype=0/1;Alt_Reads=9;{parents}\n"
    )


def _sniffles_vcf(proband: str, support: int) -> bytes:
    return (
        "##fileformat=VCFv4.2\n"
        "##source=Sniffles2_2.6\n"
        "##contig=<ID=1,length=248956422>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{proband}\n"
        f"1\t7000\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVTYPE=DEL;SVLEN=-5000;END=12000;SUPPORT={support}\tGT\t0/1\n"
    ).encode()


def _write_package(root: Path, family_id: str, samples: dict[str, str]) -> None:
    proband, mother, father = samples["proband"], samples["mother"], samples["father"]
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n",
        encoding="utf-8",
    )
    (root / "sv_needlr").mkdir()
    (root / "sv_needlr" / "family.needlr.vcf").write_text(_needlr_vcf(proband), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": "family.ped",
        "datasets": {"sv_needlr": {"family_vcf": "sv_needlr/family.needlr.vcf"}},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


async def _set_seeded_gene(assembly_id: str | None) -> None:
    """Seed (or, with None, remove) the gene the upload lists its deletion with: an uploaded
    SV takes its genes from the gene table, and the e2e database holds none there."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("DELETE FROM genes WHERE gene_id = :gene_id"), {"gene_id": _SEEDED_GENE_ID})
        if assembly_id is not None:
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, :symbol, '1', 8000, 9000,
                            1, 'protein_coding', 'E2E CNV evidence of its own caller', 'e2e')
                    """
                ),
                {"assembly_id": assembly_id, "gene_id": _SEEDED_GENE_ID, "symbol": _SEEDED_GENE},
            )
        await session.commit()


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:
        pass
    return out


async def _exercise(base: Path, suffix: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    family_id = f"TRACE11_{suffix}"
    samples = {role: f"{role.upper()}_{suffix}" for role in ("proband", "mother", "father")}
    proband = samples["proband"]
    _write_package(base / family_id, family_id, samples)
    out: dict = {"samples": samples}

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(base / family_id),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
        out["import"] = {"completed": result.completed, "error": result.error}
        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()

    async def _snapshot(variant_id: str):
        async with sessionmaker() as session:
            return (
                await session.execute(
                    text(
                        "SELECT cnv_evidence_snapshot FROM structural_variant_reviews "
                        "WHERE family_id = CAST(:f AS uuid) AND variant_id = :v"
                    ),
                    {"f": family_uuid, "v": variant_id},
                )
            ).scalar_one_or_none()

    async def _audit_events() -> int:
        async with sessionmaker() as session:
            return (
                await session.execute(
                    text("SELECT count(*) FROM clinical_audit_events WHERE family_identifier = :f"),
                    {"f": family_id},
                )
            ).scalar_one()

    await _set_seeded_gene(assembly_id)
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e", timeout=120) as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            svs = f"/api/families/{family_id}/structural-variants"
            drift = f"/api/families/{family_id}/classification-drift"

            async def _upload(support: int, *, overwrite: bool) -> dict:
                return _cap(
                    await ac.post(
                        f"/api/structural-variants/upload/{proband}",
                        params={"overwrite": str(overwrite).lower(), "source_format": "sniffles"},
                        files={"file": (f"{proband}.sniffles.vcf", _sniffles_vcf(proband, support), "text/plain")},
                    )
                )

            async def _classify(variant_id: str, **extra) -> dict:
                return _cap(await ac.put(f"{svs}/{variant_id}/review", json={**_SCORING, **extra}))

            async def _drift() -> dict:
                return _cap(await ac.get(drift))

            out["upload"] = await _upload(14, overwrite=False)
            out["svs"] = _cap(await ac.get(svs, params={"page_size": 50}))

            # Made on the NeedlR row; then the Sniffles call changes.
            out["needlr_save"] = await _classify(_SHARED, source="needlr")
            out["needlr_snapshot"] = await _snapshot(_SHARED)
            out["needlr_drift"] = await _drift()
            out["reupload_1"] = await _upload(20, overwrite=True)
            out["needlr_drift_after_upload_change"] = await _drift()

            # Made on the Sniffles row; then the Sniffles call changes again.
            out["sniffles_svs"] = _cap(await ac.get(svs, params={"page_size": 50}))
            out["sniffles_save"] = await _classify(_SHARED, source="sniffles")
            out["sniffles_snapshot"] = await _snapshot(_SHARED)
            out["sniffles_drift"] = await _drift()
            out["reupload_2"] = await _upload(30, overwrite=True)
            out["sniffles_drift_after_upload_change"] = await _drift()

            # Requests that name no caller, or one that does not call the SV.
            audit_before = await _audit_events()
            out["nameless_save"] = await _classify(_SHARED)
            out["nameless_audit_events"] = (audit_before, await _audit_events())
            out["snapshot_after_refusals"] = await _snapshot(_SHARED)
            out["absent_caller_save"] = await _classify(_SHARED, source="spectre")
            out["snapshot_after_absent_caller"] = await _snapshot(_SHARED)
            out["one_caller_save"] = await _classify(_NEEDLR_ONLY)
            out["one_caller_snapshot"] = await _snapshot(_NEEDLR_ONLY)
    finally:
        await _set_seeded_gene(None)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("cnv_evidence_own_caller")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", base / "data")
    try:
        return _harness.run_async(lambda: _exercise(base, uuid4().hex[:8].upper()))
    finally:
        mp.undo()


def _rows(listing: dict) -> dict[tuple[str, str], dict]:
    assert listing["status"] == 200, listing["text"]
    return {(variant["_id"], variant["source"]): variant for variant in listing["json"]["variants"]}


def _served_evidence(variant: dict) -> tuple:
    """What the page gives the classification dialog of a row, as the evidence names it."""
    return (
        variant["source"],
        variant["gene_symbols"],
        variant["gene_pli"],
        (variant.get("annotation_extra") or {}).get("inheritance"),
    )


def _frozen_evidence(snapshot: dict | None) -> tuple:
    assert snapshot is not None
    evidence = snapshot["evidence"]
    return evidence["source"], evidence["gene_symbols"], evidence["pli"], evidence["inheritance"]


def _structural(drift: dict) -> dict:
    assert drift["status"] == 200, drift["text"]
    return drift["json"]["structural"]


def test_needlr_and_the_upload_list_the_shared_deletion_as_a_row_each(run) -> None:
    assert run["import"]["completed"] is True, run["import"]
    assert run["upload"]["status"] == 200, run["upload"]["text"]
    rows = _rows(run["svs"])
    assert set(rows) == {(_SHARED, "needlr"), (_SHARED, "sniffles"), (_NEEDLR_ONLY, "needlr")}
    assert _served_evidence(rows[(_SHARED, "needlr")]) == (
        "needlr",
        ["E2E_TRACE_A", "E2E_TRACE_B"],
        0.99,
        "de_novo",
    )
    source, genes, pli, inheritance = _served_evidence(rows[(_SHARED, "sniffles")])
    assert (source, pli, inheritance) == ("sniffles", None, None)
    assert _SEEDED_GENE in genes and "E2E_TRACE_A" not in genes


def test_a_classification_on_the_needlr_row_freezes_the_needlr_rows_evidence(run) -> None:
    assert run["needlr_save"]["status"] == 200, run["needlr_save"]["text"]
    # Before: the row with the lowest storage key, the upload's half the time, without
    # NeedlR's genes, pLI and inheritance.
    served = _rows(run["svs"])[(_SHARED, "needlr")]
    assert _frozen_evidence(run["needlr_snapshot"]) == _served_evidence(served)
    assert _structural(run["needlr_drift"]) == {"checked": 1, "drifted_count": 0, "drifted": []}


def test_a_change_to_the_other_callers_row_does_not_drift_it(run) -> None:
    assert run["reupload_1"]["status"] == 200, run["reupload_1"]["text"]
    structural = _structural(run["needlr_drift_after_upload_change"])
    assert (structural["checked"], structural["drifted_count"]) == (1, 0), structural


def test_a_classification_on_the_upload_row_freezes_the_upload_rows_evidence(run) -> None:
    assert run["sniffles_save"]["status"] == 200, run["sniffles_save"]["text"]
    served = _rows(run["sniffles_svs"])[(_SHARED, "sniffles")]
    assert _frozen_evidence(run["sniffles_snapshot"]) == _served_evidence(served)
    source, genes, pli, inheritance = _frozen_evidence(run["sniffles_snapshot"])
    assert (source, pli, inheritance) == ("sniffles", None, None) and _SEEDED_GENE in genes
    assert _structural(run["sniffles_drift"])["drifted_count"] == 0


def test_a_change_to_its_own_callers_row_drifts_it(run) -> None:
    assert run["reupload_2"]["status"] == 200, run["reupload_2"]["text"]
    [entry] = _structural(run["sniffles_drift_after_upload_change"])["drifted"]
    assert (entry["variant_id"], entry["status"], entry["changed"]) == (_SHARED, "drifted", ["annotations"])
    assert (entry["evidence_from"]["source"], entry["evidence_to"]["source"]) == ("sniffles", "sniffles")


def test_a_save_naming_no_caller_of_the_shared_deletion_is_refused_and_writes_nothing(run) -> None:
    refused = run["nameless_save"]
    assert refused["status"] == 422, refused["text"]
    assert "(needlr, sniffles)" in refused["json"]["detail"]
    before, after = run["nameless_audit_events"]
    assert after == before
    assert run["snapshot_after_refusals"] == run["sniffles_snapshot"]


def test_a_save_naming_a_caller_that_does_not_call_the_sv_is_refused(run) -> None:
    assert run["absent_caller_save"]["status"] == 404, run["absent_caller_save"]["text"]
    assert run["snapshot_after_absent_caller"] == run["sniffles_snapshot"]


def test_a_save_naming_no_caller_takes_the_one_row_of_an_sv_one_caller_calls(run) -> None:
    assert run["one_caller_save"]["status"] == 200, run["one_caller_save"]["text"]
    served = _rows(run["svs"])[(_NEEDLR_ONLY, "needlr")]
    assert _frozen_evidence(run["one_caller_snapshot"]) == _served_evidence(served)
    assert _frozen_evidence(run["one_caller_snapshot"]) == ("needlr", ["E2E_TRACE_C"], 0.12, "maternal")
