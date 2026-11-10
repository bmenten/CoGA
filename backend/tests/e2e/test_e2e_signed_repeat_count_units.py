"""The signed record freezes each repeat call's near-boundary warning, QA's counting-units record and
QA's count correction, E2E.

TRGT's count need not be the clinical repeat number (at FMR1 it leaves out the AGG
interruptions: a 55-repeat premutation can read 53), so until QA validates a catalogue locus the
repeat page marks a count within 3 repeats of a classification boundary (#889). The signed record
now freezes that warning beside each allele, and per locus QA's record of TRGT's counting units
as ``repeat_locus_count_units`` holds it at sign-out.

Here a one-member family (a woman, a new id each run) goes through the package import into real
Postgres and ClickHouse with a TRGT call at FXS_FMR1 of 30 and 53 repeats and one at HD_HTT of 18
and 36, at loci without a QA record. Through the API, this:

* reads the repeat page: the 53 reads grey zone and is marked with the premutation at 55;
* signs the family out and reads the record back: the warning beside the 53, none beside the 30,
  the locus not validated with no record, the margin; a verified content hash and chain; a check
  that matches, with nothing of the section not captured;
* validates the locus as an admin (``PUT /admin/repeat-loci/FXS_FMR1/count-units``): the repeat
  page no longer marks the 53, and the check names the repeat expansions as changed since sign-out;
* signs out again: the record holds the locus validated, with the reason, by whom and when, and no
  warning; then withdraws the validation: the check names the repeat expansions as changed again;
* records QA's count correction of -1 at HD_HTT (CLIN-57), which TRGT reads one above the usual
  count: the repeat page reads the 36 as 35, grey zone, with TRGT's 36 beside it; the check names
  the repeat expansions as changed; a sign-out freezes both counts and the correction. The
  record's chain holds the correction before and after.

FXS_FMR1's and HD_HTT's records are put back as they were. Everything runs in ONE event loop, with an in-process
``httpx.ASGITransport`` client for the API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. All samples
and values are synthetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import capture, ensure_postgres_schema, ok_json, write_file

pytestmark = pytest.mark.integration

LOCUS = "FXS_FMR1"
HTT_LOCUS = "HD_HTT"
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: nothing is reported",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the synthetic family has no genotypes to check",
    # The abnormal repeat and Paraphase calls are not reviewed here (REQ-TRACE-016).
    "acknowledge_unreviewed_findings": True,
    "unreviewed_findings_acknowledgement_reason": "e2e: the repeat and Paraphase calls are not reviewed",
}
VALIDATION_REASON = "e2e: validation report of TRGT's counting units at FMR1"
WITHDRAWAL_REASON = "e2e: a new TRGT catalogue"
CORRECTION_REASON = "e2e: TRGT counts HTT one above the usual count"
WARNING_53 = {"repeat_count": 53, "margin": 3, "boundaries": [{"repeat_count": 55, "status": "premutation"}]}

_TRGT = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
    "chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID=FXS_FMR1;END=147912111;MOTIFS=CGG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:90,159:30,53\n"
    "chr4\t3074876\t.\tCAG\t<TR>\t.\t.\tTRID=HD_HTT;END=3074933;MOTIFS=CAG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:54,108:18,36\n"
)


def _package(root: Path, family_id: str, sample: str) -> Path:
    """The one-member family: a woman, her TRGT VCF with an FMR1 call of 30 and 53 repeats and an
    HTT call of 18 and 36."""
    root.mkdir(parents=True)
    write_file(root, "family.ped", f"{family_id}\t{sample}\t0\t0\t2\t1\n")
    trgt = write_file(root, f"trgt/{sample}.vcf", _TRGT.format(column=f"{sample}_sort"))
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  repeats_trgt:",
        "    per_sample:",
        f"      {sample}: {{file: {trgt}}}",
    ]
    write_file(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.repeat_expansion_pg import seed_builtin_repeat_catalog
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        await seed_builtin_repeat_catalog(session)
        # The loci start without a QA record (no correction, not validated); their records are
        # put back below.
        priors = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT locus_id, validated, count_offset, reason, updated_by, updated_by_name, updated_at "
                        "FROM repeat_locus_count_units WHERE locus_id IN (:l, :h)"
                    ),
                    {"l": LOCUS, "h": HTT_LOCUS},
                )
            ).mappings().all()
        ]
        await session.execute(
            text("DELETE FROM repeat_locus_count_units WHERE locus_id IN (:l, :h)"), {"l": LOCUS, "h": HTT_LOCUS}
        )
        await session.commit()

    sample = f"{family_id}_S1"  # sample ids are unique across families
    out: dict[str, Any] = {"sample": sample, "responses": {}}
    try:
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(_package(base / family_id, family_id, sample)),
                # Evaluated after the package is written.
                manifest_text=kept_manifest(base / family_id),
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
                r[key] = capture(await _harness.sign_out(ac, f"{family}/report/sign-out", SIGN_OUT_BODY))
                version = (r[key]["json"] or {}).get("version")
                r[f"{key}_detail"] = capture(await ac.get(f"{family}/report/sign-outs/{version}"))

            r["table"] = capture(await ac.get(f"{family}/repeat-expansions"))
            await sign_out("signout")
            r["check"] = capture(await ac.get(f"{family}/report/sign-out-check"))

            # QA validates TRGT's counting units at FMR1 after sign-out.
            r["validate"] = capture(
                await ac.put(units, json={"validated": True, "count_offset": 0, "reason": VALIDATION_REASON})
            )
            r["table_validated"] = capture(await ac.get(f"{family}/repeat-expansions"))
            r["check_validated"] = capture(await ac.get(f"{family}/report/sign-out-check"))
            await sign_out("signout_validated")
            r["check_signed_validated"] = capture(await ac.get(f"{family}/report/sign-out-check"))

            # And withdraws it.
            r["withdraw"] = capture(
                await ac.put(units, json={"validated": False, "count_offset": 0, "reason": WITHDRAWAL_REASON})
            )
            r["check_withdrawn"] = capture(await ac.get(f"{family}/report/sign-out-check"))

            # QA's count correction at HTT (CLIN-57), after a sign-out.
            await sign_out("signout_before_correction")
            r["correct"] = capture(
                await ac.put(
                    f"/api/admin/repeat-loci/{HTT_LOCUS}/count-units",
                    json={"validated": False, "count_offset": -1, "reason": CORRECTION_REASON},
                )
            )
            r["table_corrected"] = capture(await ac.get(f"{family}/repeat-expansions"))
            r["check_corrected"] = capture(await ac.get(f"{family}/report/sign-out-check"))
            await sign_out("signout_corrected")
            r["check_signed_corrected"] = capture(await ac.get(f"{family}/report/sign-out-check"))

            r["listed"] = capture(await ac.get("/api/admin/repeat-loci"))
            r["chain"] = capture(
                await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
            )
            r["units_chain"] = capture(
                await ac.get(
                    "/api/admin/integrity/verify",
                    params={"table": "clinical_audit_events", "family_id": "system:repeat-loci"},
                )
            )
        return out
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM repeat_locus_count_units WHERE locus_id IN (:l, :h)"), {"l": LOCUS, "h": HTT_LOCUS}
            )
            for prior in priors:
                await session.execute(
                    text(
                        "INSERT INTO repeat_locus_count_units "
                        "(locus_id, validated, count_offset, reason, updated_by, updated_by_name, updated_at) "
                        "VALUES (:locus_id, :validated, :count_offset, :reason, :updated_by, :updated_by_name, "
                        ":updated_at)"
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


def _fmr1(section: dict[str, Any]) -> dict[str, Any]:
    [locus] = [locus for locus in section["loci"] if locus["locus_id"] == LOCUS]
    return locus


def _htt(section: dict[str, Any]) -> dict[str, Any]:
    [locus] = [locus for locus in section["loci"] if locus["locus_id"] == HTT_LOCUS]
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
    [locus] = [row for row in ok_json(run, "table")["loci"] if row["locus_id"] == LOCUS]
    assert locus["count_units_validated"] is False
    alleles = locus["calls"][run["sample"]]["alleles"]
    assert [(allele["repeat_count"], allele["status"]) for allele in alleles] == [(30, "normal"), (53, "intermediate")]
    assert [allele["count_unit_warning"] for allele in alleles] == [None, WARNING_53]


def test_the_record_freezes_the_warning_beside_the_call_and_the_locus_not_validated(run) -> None:
    detail = ok_json(run, "signout_detail")
    section = detail["snapshot"]["repeat_expansions"]
    assert section["count_unit_margin"] == 3
    locus = _fmr1(section)
    assert locus["count_units"] == {
        "count_offset": 0,
        "validated": False,
        "reason": None,
        "updated_by": None,
        "updated_at": None,
    }
    alleles = _alleles(locus, run["sample"])
    assert [(allele["repeat_count"], allele["trgt_repeat_count"], allele["status"]) for allele in alleles] == [
        (30, 30, "normal"),
        (53, 53, "intermediate"),
    ]
    # The warning the repeat page showed, as it served it.
    assert [allele["count_unit_warning"] for allele in alleles] == [None, WARNING_53]
    # Bound into the content hash, which the stored record still matches.
    assert detail["verified"] is True
    assert _section_gaps(detail["not_captured"]) == []


def test_the_check_matches_the_record_right_after_it_was_signed(run) -> None:
    check = ok_json(run, "check")
    # Through the JSONB round trip the section compares equal to the one built again.
    assert check["matches"] is True, check
    assert _section_gaps(check["not_captured"]) == []


def test_a_validation_since_sign_out_reads_as_a_change(run) -> None:
    validated = ok_json(run, "validate")
    assert (validated["locus_id"], validated["validated"], validated["reason"]) == (LOCUS, True, VALIDATION_REASON)
    # The repeat page no longer marks the 53; its status is the same.
    [locus] = [row for row in ok_json(run, "table_validated")["loci"] if row["locus_id"] == LOCUS]
    assert locus["count_units_validated"] is True
    alleles = locus["calls"][run["sample"]]["alleles"]
    assert [allele["count_unit_warning"] for allele in alleles] == [None, None]
    assert [allele["status"] for allele in alleles] == ["normal", "intermediate"]
    # The record held the 53 marked at a locus not validated: the check says the repeat
    # expansions changed since sign-out, and so did the findings without a review, which
    # listed the 53 near a boundary (REQ-TRACE-016).
    check = ok_json(run, "check_validated")
    assert check["matches"] is False
    assert check["changed_sections"] == ["unreviewed_findings", "repeat_expansions"]


def test_the_next_record_freezes_the_validation_with_its_reason_by_whom_and_when(run) -> None:
    detail = ok_json(run, "signout_validated_detail")
    locus = _fmr1(detail["snapshot"]["repeat_expansions"])
    units = locus["count_units"]
    assert (units["validated"], units["reason"]) == (True, VALIDATION_REASON)
    assert units["updated_by"] == ok_json(run, "validate")["updated_by"] and units["updated_by"]
    assert units["updated_at"]
    assert [allele["count_unit_warning"] for allele in _alleles(locus, run["sample"])] == [None, None]
    assert detail["verified"] is True
    assert ok_json(run, "check_signed_validated")["matches"] is True


def test_a_withdrawal_since_sign_out_reads_as_a_change(run) -> None:
    withdrawn = ok_json(run, "withdraw")
    assert (withdrawn["validated"], withdrawn["reason"]) == (False, WITHDRAWAL_REASON)
    check = ok_json(run, "check_withdrawn")
    assert check["matches"] is False
    # The 53 is near a boundary again, so it is again a finding without a review.
    assert check["changed_sections"] == ["unreviewed_findings", "repeat_expansions"]


def test_a_count_correction_reclassifies_the_call_with_trgts_count_beside_it(run) -> None:
    corrected = ok_json(run, "correct")
    assert (corrected["locus_id"], corrected["validated"], corrected["count_offset"]) == (HTT_LOCUS, False, -1)
    [locus] = [row for row in ok_json(run, "table_corrected")["loci"] if row["locus_id"] == HTT_LOCUS]
    assert (locus["count_offset"], locus["count_units_validated"], locus["count_units_reason"]) == (
        -1,
        False,
        CORRECTION_REASON,
    )
    alleles = locus["calls"][run["sample"]]["alleles"]
    # TRGT's 36 (pathogenic) is 35: grey zone, still marked while HTT is not validated.
    assert [(allele["repeat_count"], allele["trgt_repeat_count"], allele["status"]) for allele in alleles] == [
        (17, 18, "normal"),
        (35, 36, "intermediate"),
    ]
    assert alleles[1]["count_unit_warning"] == {
        "repeat_count": 35,
        "margin": 3,
        "boundaries": [{"repeat_count": 36, "status": "pathogenic"}],
    }
    assert locus["status"] == "intermediate"
    # The record signed just before held the 36 pathogenic: the check says the repeat expansions
    # changed, and so did the findings without a review that listed it.
    check = ok_json(run, "check_corrected")
    assert check["matches"] is False
    assert "repeat_expansions" in check["changed_sections"]


def test_the_record_freezes_the_corrected_count_trgts_count_and_the_correction(run) -> None:
    detail = ok_json(run, "signout_corrected_detail")
    locus = _htt(detail["snapshot"]["repeat_expansions"])
    assert locus["status"] == "intermediate"
    units = locus["count_units"]
    assert (units["count_offset"], units["validated"], units["reason"]) == (-1, False, CORRECTION_REASON)
    assert units["updated_by"] and units["updated_at"]
    alleles = _alleles(locus, run["sample"])
    assert [(allele["repeat_count"], allele["trgt_repeat_count"], allele["status"]) for allele in alleles] == [
        (17, 18, "normal"),
        (35, 36, "intermediate"),
    ]
    assert detail["verified"] is True
    assert _section_gaps(detail["not_captured"]) == []
    # Through the JSONB round trip the section compares equal to the one built again.
    check = ok_json(run, "check_signed_corrected")
    assert check["matches"] is True, check


def test_the_admin_list_names_the_correction(run) -> None:
    [htt] = [locus for locus in ok_json(run, "listed")["loci"] if locus["locus_id"] == HTT_LOCUS]
    assert (htt["count_offset"], htt["validated"], htt["offset_note"]) == (-1, False, None)
    [fmr1] = [locus for locus in ok_json(run, "listed")["loci"] if locus["locus_id"] == LOCUS]
    assert fmr1["count_offset"] == 0 and "AGG" in fmr1["offset_note"]
    assert ok_json(run, "listed")["offset_limit"] == 20


def test_the_sign_out_chain_and_the_records_chain_verify(run) -> None:
    assert ok_json(run, "chain")["verified"] is True
    assert ok_json(run, "units_chain")["verified"] is True
