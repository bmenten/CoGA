"""The signed record freezes each repeat call's near-boundary warning and QA's counting-units record, E2E.

TRGT's count need not be the clinical repeat number (at FMR1 it leaves out the AGG
interruptions: a 55-repeat premutation can read 53), so until QA validates a catalogue locus the
repeat page marks a count within 3 repeats of a classification boundary (#889). The signed record
now freezes that warning beside each allele, and per locus QA's record of TRGT's counting units
as ``repeat_locus_count_units`` holds it at sign-out.

Here a one-member family (a woman, a new id each run) goes through the package import into real
Postgres and ClickHouse with a TRGT call at FXS_FMR1 of 30 and 53 repeats, at a locus without a
QA record. Through the API, this:

* reads the repeat page: the 53 reads grey zone and is marked with the premutation at 55;
* signs the family out and reads the record back: the warning beside the 53, none beside the 30,
  the locus not validated with no record, the margin; a verified content hash and chain; a check
  that matches, with nothing it does not compare and nothing of the section not captured;
* validates the locus as an admin (``PUT /admin/repeat-loci/FXS_FMR1/count-units``): the repeat
  page no longer marks the 53, and the check names the repeat expansions as changed since sign-out;
* signs out again: the record holds the locus validated, with the reason, by whom and when, and no
  warning; then withdraws the validation: the check names the repeat expansions as changed again;
* signs out once more as a build from before the warnings (the section without them): that record
  still verifies and reads, nothing it predates is called not captured, and the check compares it
  on what it holds, naming the warnings and QA's records as not compared.

FXS_FMR1's record is put back as it was. Everything runs in ONE event loop, with an in-process
``httpx.ASGITransport`` client for the API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. All samples
and values are synthetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

LOCUS = "FXS_FMR1"
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: nothing is reported",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the synthetic family has no genotypes to check",
}
VALIDATION_REASON = "e2e: validation report of TRGT's counting units at FMR1"
WITHDRAWAL_REASON = "e2e: a new TRGT catalogue"
WARNING_53 = {"repeat_count": 53, "margin": 3, "boundaries": [{"repeat_count": 55, "status": "premutation"}]}

_TRGT = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
    "chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID=FXS_FMR1;END=147912111;MOTIFS=CGG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:90,159:30,53\n"
)


def _write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _package(root: Path, family_id: str, sample: str) -> Path:
    """The one-member family: a woman, her TRGT VCF with an FMR1 call of 30 and 53 repeats."""
    root.mkdir(parents=True)
    _write(root, "family.ped", f"{family_id}\t{sample}\t0\t0\t2\t1\n")
    trgt = _write(root, f"trgt/{sample}.vcf", _TRGT.format(column=f"{sample}_sort"))
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  repeats_trgt:",
        "    per_sample:",
        f"      {sample}: {{file: {trgt}}}",
    ]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


def _cap(resp) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _before_count_units(section: Any) -> None:
    """The section as a build from before the warnings froze it: no margin, no QA record per
    locus, no warning beside an allele (in place)."""
    if not isinstance(section, dict):
        return
    section.pop("count_unit_margin", None)
    for locus in section.get("loci") or []:
        locus.pop("count_units", None)
        for call in locus.get("calls") or []:
            for allele in call.get("alleles") or []:
                allele.pop("count_unit_warning", None)


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import report_signout_service as rss
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.repeat_expansion_pg import seed_builtin_repeat_catalog
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        await seed_builtin_repeat_catalog(session)
        # The locus starts without a QA record (not validated); its record is put back below.
        prior = (
            await session.execute(
                text(
                    "SELECT locus_id, validated, reason, updated_by, updated_by_name, updated_at "
                    "FROM repeat_locus_count_units WHERE locus_id = :l"
                ),
                {"l": LOCUS},
            )
        ).mappings().first()
        prior = dict(prior) if prior is not None else None
        await session.execute(text("DELETE FROM repeat_locus_count_units WHERE locus_id = :l"), {"l": LOCUS})
        await session.commit()

    sample = f"{family_id}_S1"  # sample ids are unique across families
    out: dict[str, Any] = {"sample": sample, "responses": {}}
    try:
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(_package(base / family_id, family_id, sample)),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode="cancel",
            )
        out["import"] = {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: d.status for d in result.datasets},
        }
        if not result.completed:
            return out  # the first test names the dataset that failed

        r = out["responses"]
        family = f"/api/families/{family_id}"
        units = f"/api/admin/repeat-loci/{LOCUS}/count-units"
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def sign_out(key: str) -> None:
                r[key] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
                version = (r[key]["json"] or {}).get("version")
                r[f"{key}_detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))

            r["table"] = _cap(await ac.get(f"{family}/repeat-expansions"))
            await sign_out("signout")
            r["check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))

            # QA validates TRGT's counting units at FMR1 after sign-out.
            r["validate"] = _cap(await ac.put(units, json={"validated": True, "reason": VALIDATION_REASON}))
            r["table_validated"] = _cap(await ac.get(f"{family}/repeat-expansions"))
            r["check_validated"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
            await sign_out("signout_validated")
            r["check_signed_validated"] = _cap(await ac.get(f"{family}/report/sign-out-check"))

            # And withdraws it.
            r["withdraw"] = _cap(await ac.put(units, json={"validated": False, "reason": WITHDRAWAL_REASON}))
            r["check_withdrawn"] = _cap(await ac.get(f"{family}/report/sign-out-check"))

            # A record signed by a build from before the warnings: the section without them.
            real_build = rss.build_report_snapshot

            async def _old_build(*args: Any, **kwargs: Any) -> dict[str, Any]:
                body = await real_build(*args, **kwargs)
                _before_count_units(body.get("repeat_expansions"))
                return body

            mp = pytest.MonkeyPatch()
            mp.setattr(rss, "build_report_snapshot", _old_build)
            try:
                await sign_out("old_signout")
            finally:
                mp.undo()
            r["old_check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
            r["chain"] = _cap(
                await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
            )
            r["units_chain"] = _cap(
                await ac.get(
                    "/api/admin/integrity/verify",
                    params={"table": "clinical_audit_events", "family_id": "system:repeat-loci"},
                )
            )
        return out
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM repeat_locus_count_units WHERE locus_id = :l"), {"l": LOCUS})
            if prior is not None:
                await session.execute(
                    text(
                        "INSERT INTO repeat_locus_count_units "
                        "(locus_id, validated, reason, updated_by, updated_by_name, updated_at) "
                        "VALUES (:locus_id, :validated, :reason, :updated_by, :updated_by_name, :updated_at)"
                    ),
                    prior,
                )
            await session.commit()


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.app.services import repeat_expansion_pg
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("signed_repeat_count_units")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    # The shipped STRchive file, not one an environment variable may point at.
    mp.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    try:
        return _harness.run_async(lambda: _exercise(base, f"SIGNEDREP_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _ok(run: dict[str, Any], key: str) -> Any:
    resp = run["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _fmr1(section: dict[str, Any]) -> dict[str, Any]:
    [locus] = [locus for locus in section["loci"] if locus["locus_id"] == LOCUS]
    return locus


def _alleles(locus: dict[str, Any], sample: str) -> list[dict[str, Any]]:
    [call] = [call for call in locus["calls"] if call["sample"] == sample]
    return call["alleles"]


def _section_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [gap for gap in gaps if gap["section"].split(".")[0] == "repeat_expansions"]


def test_the_family_imports_with_a_grey_zone_fmr1_count(run) -> None:
    imported = run["import"]
    assert imported["completed"] is True, imported
    assert imported["datasets"]["repeats_trgt"] == "imported"


def test_the_repeat_page_marks_the_53_while_the_locus_is_not_validated(run) -> None:
    [locus] = [row for row in _ok(run, "table")["loci"] if row["locus_id"] == LOCUS]
    assert locus["count_units_validated"] is False
    alleles = locus["calls"][run["sample"]]["alleles"]
    assert [(allele["repeat_count"], allele["status"]) for allele in alleles] == [(30, "normal"), (53, "intermediate")]
    assert [allele["count_unit_warning"] for allele in alleles] == [None, WARNING_53]


def test_the_record_freezes_the_warning_beside_the_call_and_the_locus_not_validated(run) -> None:
    detail = _ok(run, "signout_detail")
    section = detail["snapshot"]["repeat_expansions"]
    assert section["count_unit_margin"] == 3
    locus = _fmr1(section)
    assert locus["count_units"] == {"validated": False, "reason": None, "updated_by": None, "updated_at": None}
    alleles = _alleles(locus, run["sample"])
    assert [(allele["repeat_count"], allele["status"]) for allele in alleles] == [(30, "normal"), (53, "intermediate")]
    # The warning the repeat page showed, as it served it.
    assert [allele["count_unit_warning"] for allele in alleles] == [None, WARNING_53]
    # Bound into the content hash, which the stored record still matches.
    assert detail["verified"] is True
    assert _section_gaps(detail["not_captured"]) == []


def test_the_check_matches_the_record_right_after_it_was_signed(run) -> None:
    check = _ok(run, "check")
    # Through the JSONB round trip the section compares equal to the one built again.
    assert check["matches"] is True, check
    assert check["not_compared"] == []
    assert _section_gaps(check["not_captured"]) == []


def test_a_validation_since_sign_out_reads_as_a_change(run) -> None:
    validated = _ok(run, "validate")
    assert (validated["locus_id"], validated["validated"], validated["reason"]) == (LOCUS, True, VALIDATION_REASON)
    # The repeat page no longer marks the 53; its status is the same.
    [locus] = [row for row in _ok(run, "table_validated")["loci"] if row["locus_id"] == LOCUS]
    assert locus["count_units_validated"] is True
    alleles = locus["calls"][run["sample"]]["alleles"]
    assert [allele["count_unit_warning"] for allele in alleles] == [None, None]
    assert [allele["status"] for allele in alleles] == ["normal", "intermediate"]
    # The record held the 53 marked at a locus not validated: the check says the repeat
    # expansions changed since sign-out.
    check = _ok(run, "check_validated")
    assert check["matches"] is False
    assert check["changed_sections"] == ["repeat_expansions"]
    assert check["not_compared"] == []


def test_the_next_record_freezes_the_validation_with_its_reason_by_whom_and_when(run) -> None:
    detail = _ok(run, "signout_validated_detail")
    locus = _fmr1(detail["snapshot"]["repeat_expansions"])
    units = locus["count_units"]
    assert (units["validated"], units["reason"]) == (True, VALIDATION_REASON)
    assert units["updated_by"] == _ok(run, "validate")["updated_by"] and units["updated_by"]
    assert units["updated_at"]
    assert [allele["count_unit_warning"] for allele in _alleles(locus, run["sample"])] == [None, None]
    assert detail["verified"] is True
    assert _ok(run, "check_signed_validated")["matches"] is True


def test_a_withdrawal_since_sign_out_reads_as_a_change(run) -> None:
    withdrawn = _ok(run, "withdraw")
    assert (withdrawn["validated"], withdrawn["reason"]) == (False, WITHDRAWAL_REASON)
    check = _ok(run, "check_withdrawn")
    assert check["matches"] is False
    assert check["changed_sections"] == ["repeat_expansions"]
    assert check["not_compared"] == []


def test_a_record_signed_before_the_warnings_still_verifies_reads_and_is_compared_on_what_it_holds(run) -> None:
    old = _ok(run, "old_signout")
    detail = _ok(run, "old_signout_detail")
    assert detail["version"] == old["version"]
    section = detail["snapshot"]["repeat_expansions"]
    assert "count_unit_margin" not in section
    locus = _fmr1(section)
    assert "count_units" not in locus
    assert all("count_unit_warning" not in allele for allele in _alleles(locus, run["sample"]))
    assert detail["verified"] is True
    # Nothing it predates is called "not captured".
    assert _section_gaps(detail["not_captured"]) == []
    # Compared on what it holds: the calls match, and the warnings and QA's records are named
    # as not compared (the locus is withdrawn now, so the 53 is marked again).
    check = _ok(run, "old_check")
    assert check["version"] == old["version"]
    assert check["not_compared"] == ["repeat_expansions.count_units"]
    assert check["matches"] is True, check
    assert _section_gaps(check["not_captured"]) == []


def test_the_sign_out_chain_and_the_records_chain_verify(run) -> None:
    assert _ok(run, "chain")["verified"] is True
    assert _ok(run, "units_chain")["verified"] is True
