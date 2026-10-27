"""A database error in the Sample QC's NIPT step, during a NIPT family's sign-out (ENG-19).

The Sample QC runs a monogenic NIPT family's cfDNA checks (paternity, fetal sex, the category
distribution) from its NIPT analysis, which reads the family and the assay's artifact list
from Postgres. A database error there was caught and noted ("NIPT cfDNA analysis could not
run."), but it had aborted the request's transaction: the sign-out's next statement failed,
and the sign-out with it (a 500, nothing signed). The step now runs in a savepoint, so the
error is rolled back to it and reads like the step's other failures.

Over the synthetic demo NIPT package (``demo/nipt_family``, imported as
``test_e2e_nipt_signed_record.py`` imports it, with the maternal plasma on an assay panel of
the run's own), with the artifact list's read made to fail in Postgres (the statement reads a
table that does not exist), through the API, this:

* reads the Sample QC page: the fixed note, and no cfDNA check;
* signs out without acknowledging the Sample QC: the gate holds it, and lists the NIPT checks
  among those that could not run;
* signs out with the acknowledgement: the record holds the Sample QC with its note, and the
  NIPT section frozen as unavailable and named as not captured; it verifies, on a chain that
  verifies, and its audit event lists the concern the acknowledgement covered;
* with the list readable again, signs out once more: the record holds the NIPT results.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tests.e2e.test_e2e_nipt_signed_record import _DEMO, _cap, _import_demo, _prepare_copy

pytestmark = pytest.mark.integration

NOTE = "NIPT cfDNA analysis could not run."
COULD_NOT_RUN = (
    "NIPT sample integrity could not be assessed — the trio could not be resolved or the cfDNA "
    "analysis did not run (no paternity or maternal-lineage check)."
)
ACKNOWLEDGED = {
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the NIPT analysis could not read its artifact list",
}


async def _unreadable_artifact_list(session, *, assembly_id, assay_key):
    """The artifact list's read, failing in Postgres as a database error does: the statement
    aborts the transaction it runs in."""
    from sqlalchemy import text

    await session.execute(
        text(
            "SELECT variant_id FROM e2e_nipt_artifact_variants_missing "
            "WHERE assembly_id = CAST(:assembly_id AS uuid) AND assay_key = :assay_key"
        ),
        {"assembly_id": assembly_id, "assay_key": assay_key},
    )
    raise AssertionError("the statement read a table that does not exist")


async def _collect(root: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services import nipt_service
    from backend.tests.e2e import _harness

    out: dict = {"import": await _import_demo(root), "responses": {}}
    r = out["responses"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"
        mp = pytest.MonkeyPatch()
        mp.setattr(nipt_service, "load_nipt_artifact_ids", _unreadable_artifact_list)
        try:
            r["qc"] = _cap(await ac.get(f"{family}/qc/sample-integrity"))
            r["refused"] = _cap(await ac.post(f"{family}/report/sign-out", json={}))
            r["signout"] = _cap(await _harness.sign_out(ac, f"{family}/report/sign-out", ACKNOWLEDGED))
        finally:
            mp.undo()
        version = (r["signout"]["json"] or {}).get("version")
        r["detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["chain"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
        )
        r["audit"] = _cap(await ac.get(f"{family}/clinical-audit", params={"limit": 50}))
        # The list readable again: the NIPT analysis runs, and the record holds its results.
        r["readable_signout"] = _cap(await _harness.sign_out(ac, f"{family}/report/sign-out", ACKNOWLEDGED))
    return out


@pytest.fixture(scope="module")
def signed(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_DEMO / "manifest.yaml").exists():
        pytest.fail("the demo NIPT package is missing: it is committed (scripts/generate_nipt_demo.py rebuilds it)")
    copy = _prepare_copy(tmp_path_factory.mktemp("nipt_qc_database_error"))
    root = Path(copy["root"])
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root, copy["FAM_NIPT_DEMO"]))
    assert out["import"]["completed"] is True, out["import"]
    return out


def _ok(signed: dict, key: str) -> dict:
    resp = signed["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def test_the_sample_qc_page_notes_that_the_nipt_analysis_could_not_run(signed) -> None:
    qc = _ok(signed, "qc")
    assert qc["application"] == "nipt"
    assert NOTE in qc["notes"]
    assert (qc["paternity_check"], qc["category_qc_check"]) == (None, None)
    # The note, never the error's text.
    assert "e2e_nipt_artifact_variants_missing" not in signed["responses"]["qc"]["text"]


def test_the_gate_lists_the_nipt_checks_among_those_that_could_not_run(signed) -> None:
    # Before: the sign-out's next statement failed in the aborted transaction (a 500).
    refused = signed["responses"]["refused"]
    assert refused["status"] == 409, refused["text"][:2000]
    detail = refused["json"]["detail"]
    assert detail["gate"] == "sample_qc"
    assert detail["unverifiable_checks"] == [COULD_NOT_RUN]
    assert detail["failed_checks"] == [] and detail["nipt_failed_checks"] == []


def test_the_acknowledged_sign_out_freezes_what_could_not_run(signed) -> None:
    signout = _ok(signed, "signout")
    snapshot = signout["snapshot"]
    assert snapshot["acknowledged_qc"] is True
    assert NOTE in snapshot["sample_qc"]["notes"]
    assert snapshot["sample_qc"]["paternity_check"] is None
    # The record's NIPT section read the analysis that failed: frozen as unavailable, and named.
    assert snapshot["nipt"] == {"unavailable": "The NIPT results could not be read."}
    assert "nipt" in [gap["section"] for gap in signout["not_captured"]]
    # The rest of the sign-out ran in a clean transaction: the record, its chain and its
    # audit event were written and read back.
    detail = _ok(signed, "detail")
    assert (detail["version"], detail["verified"]) == (signout["version"], True)
    assert _ok(signed, "chain")["verified"] is True
    event = next(event for event in _ok(signed, "audit")["events"] if event["action"] == "sign_out")
    assert event["after"]["version"] == signout["version"]
    assert event["after"]["qc_unverifiable"] == [COULD_NOT_RUN]
    assert event["after"]["acknowledged_qc"] is True


def test_with_the_list_readable_again_the_record_holds_the_nipt_results(signed) -> None:
    readable = _ok(signed, "readable_signout")
    assert readable["version"] == _ok(signed, "signout")["version"] + 1
    snapshot = readable["snapshot"]
    assert NOTE not in snapshot["sample_qc"]["notes"]
    assert snapshot["sample_qc"]["paternity_check"] is not None
    assert "unavailable" not in snapshot["nipt"] and snapshot["nipt"]["samples"]["maternal_plasma"]
    assert "nipt" not in [gap["section"] for gap in readable["not_captured"]]
