"""The review of a repeat locus and of a Paraphase region, from save to sign-out, E2E.

docs/repeat-paraphase-review-design.md: one review per family and assembly per repeat locus and
Paraphase region, with tags, a note and a classification on its type's scale beside CoGA's
automatic status; the calls a classification or the Report tag rests on are frozen, and the
sign-out compares them with the calls then (REQ-TRACE-015); an abnormal call without a review is
acknowledged with a reason (REQ-TRACE-016); the record holds the reviews (REQ-TRACE-017).

A one-member family (a woman, a new id each run) goes through the package import into real
Postgres and ClickHouse with a TRGT call at FXS_FMR1 of 30 and 53 repeats (grey zone, near the
premutation's 55 at a locus whose counting units QA has not validated) and a Paraphase result at
GBA that reads ``review``. Through the API, this:

* signs out without a review: refused by the gate of unreviewed findings, which lists FMR1 (near a
  boundary) and GBA; an acknowledgement without a reason is refused (422);
* saves an FMR1 review (a classification, the Report tag, a note): 200 with its frozen calls; the
  same save against the review it replaced is refused (409); another scale's value (422), a tag
  the family may not use (400) and a locus the family holds no call at (404) are refused;
* saves a GBA review, reads both on the repeat and Paraphase tables, and signs out: no gate of
  unreviewed findings; the record holds both reviews with calls unchanged, nothing unreviewed,
  and a content hash that verifies; the check matches;
* validates FMR1's counting units as an admin: what the FMR1 review froze is no longer the calls
  (the warning goes, the locus reads validated); the table says so, and the sign-out's drift gate
  lists the review with what changed until it is acknowledged; the record freezes the drift;
* deletes the GBA review with an empty save; the clinical audit trail holds each change of both
  reviews, named ``repeat:FXS_FMR1`` and ``paraphase:GBA`` with their modality and assembly, and
  the family's chain verifies.

FXS_FMR1's QA record is put back as it was. Everything runs in ONE event loop, with an in-process
``httpx.ASGITransport`` client for the API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. All samples
and values are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

LOCUS = "FXS_FMR1"
GENE = "GBA"
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: the FMR1 calls changed since the review",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the synthetic family has no genotypes to check",
}
VALIDATION_REASON = "e2e: validation report of TRGT's counting units at FMR1"

_TRGT = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
    "chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID=FXS_FMR1;END=147912111;MOTIFS=CGG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:90,159:30,53\n"
)
_PARAPHASE = Path(__file__).parent / "fixtures" / "golden_trio" / "paraphase" / "PROBAND.paraphase.json"


def _write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _package(root: Path, family_id: str, sample: str) -> Path:
    """The one-member family: a woman, her TRGT VCF and her Paraphase result at GBA."""
    root.mkdir(parents=True)
    _write(root, "family.ped", f"{family_id}\t{sample}\t0\t0\t2\t1\n")
    trgt = _write(root, f"trgt/{sample}.vcf", _TRGT.format(column=f"{sample}_sort"))
    result = json.loads(_PARAPHASE.read_text(encoding="utf-8"))[GENE]
    paraphase = _write(root, f"paraphase/{sample}.json", json.dumps({GENE: result}))
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  repeats_trgt:",
        "    per_sample:",
        f"      {sample}: {{file: {trgt}}}",
        "  paraphase:",
        "    per_sample:",
        f"      {sample}: {{json: {paraphase}}}",
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


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.repeat_expansion_pg import seed_builtin_repeat_catalog
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)
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

    sample = f"{family_id}_S1"
    out: dict[str, Any] = {"sample": sample, "assembly_id": str(assembly_id), "responses": {}}
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
            return out

        r = out["responses"]
        family = f"/api/families/{family_id}"
        repeat_review = f"{family}/repeat-expansions/{LOCUS}/review"
        paraphase_review = f"{family}/paraphase/{GENE}/review"
        sign_out_url = f"{family}/report/sign-out"
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            # Unreviewed: the gate lists both findings.
            r["gate"] = _cap(await _harness.sign_out(ac, sign_out_url, SIGN_OUT_BODY))
            acknowledged = {**SIGN_OUT_BODY, "acknowledge_unreviewed_findings": True}
            r["gate_no_reason"] = _cap(
                await _harness.sign_out(
                    ac, sign_out_url, {**acknowledged, "unreviewed_findings_acknowledgement_reason": " "}
                )
            )

            # The FMR1 review, and what is refused.
            r["save"] = _cap(
                await ac.put(
                    repeat_review,
                    json={
                        "classification": "intermediate",
                        "tags": ["report"],
                        "note": "Grey zone; PCR sizing requested",
                        "expected_updated_at": None,
                    },
                )
            )
            r["stale"] = _cap(
                await ac.put(repeat_review, json={"classification": "normal", "tags": [], "expected_updated_at": None})
            )
            r["other_scale"] = _cap(await ac.put(repeat_review, json={"classification": "carrier"}))
            r["unknown_tag"] = _cap(
                await ac.put(
                    repeat_review,
                    json={
                        "classification": "intermediate",
                        "tags": ["report", "no_such_tag"],
                        "expected_updated_at": (r["save"]["json"] or {}).get("updated_at"),
                    },
                )
            )
            r["no_call"] = _cap(await ac.put(f"{family}/repeat-expansions/HD_HTT/review", json={"note": "x"}))
            r["paraphase_save"] = _cap(
                await ac.put(paraphase_review, json={"classification": "carrier", "tags": ["review"], "expected_updated_at": None})
            )
            r["repeat_table"] = _cap(await ac.get(f"{family}/repeat-expansions"))
            r["paraphase_table"] = _cap(await ac.get(f"{family}/paraphase"))

            # Reviewed: the sign-out passes the gate; the record holds both reviews.
            r["signout"] = _cap(await _harness.sign_out(ac, sign_out_url, SIGN_OUT_BODY))
            version = (r["signout"]["json"] or {}).get("version")
            r["signout_detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
            r["check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))

            # QA validates FMR1's counting units: the FMR1 review's calls are not the calls now.
            r["validate"] = _cap(
                await ac.put(
                    f"/api/admin/repeat-loci/{LOCUS}/count-units",
                    json={"validated": True, "reason": VALIDATION_REASON},
                )
            )
            r["repeat_table_drifted"] = _cap(await ac.get(f"{family}/repeat-expansions"))
            r["drift_gate"] = _cap(
                await _harness.sign_out(
                    ac, sign_out_url, {key: value for key, value in SIGN_OUT_BODY.items() if "drift" not in key}
                )
            )
            r["signout_drifted"] = _cap(await _harness.sign_out(ac, sign_out_url, SIGN_OUT_BODY))
            version = (r["signout_drifted"]["json"] or {}).get("version")
            r["signout_drifted_detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))

            # The GBA review cleared; the audit trail and its chain.
            current_gba = (r["paraphase_save"]["json"] or {}).get("updated_at")
            r["clear"] = _cap(await ac.put(paraphase_review, json={"tags": [], "expected_updated_at": current_gba}))
            r["audit"] = _cap(await ac.get(f"{family}/clinical-audit"))
            r["chain"] = _cap(
                await ac.get(
                    "/api/admin/integrity/verify",
                    params={"table": "clinical_audit_events", "family_id": family_id},
                )
            )
            r["signout_chain"] = _cap(
                await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
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

    base = tmp_path_factory.mktemp("repeat_paraphase_reviews")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    mp.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    try:
        return _harness.run_async(lambda: _exercise(base, f"FINDREV_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _ok(run: dict[str, Any], key: str) -> Any:
    resp = run["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _detail(run: dict[str, Any], key: str, status: int) -> Any:
    resp = run["responses"][key]
    assert resp["status"] == status, (key, resp["status"], resp["text"][:2000])
    return resp["json"]["detail"]


def test_the_family_imports(run: dict[str, Any]) -> None:
    assert run["import"]["completed"], run["import"]
    datasets = run["import"]["datasets"]
    assert (datasets["repeats_trgt"], datasets["paraphase"]) == ("imported", "imported")


def test_an_abnormal_call_without_a_review_is_acknowledged_with_a_reason(run: dict[str, Any]) -> None:
    detail = _detail(run, "gate", 409)
    assert detail["gate"] == "unreviewed_findings"
    findings = {(finding["kind"], finding["key"]): finding for finding in detail["unreviewed_findings"]}
    assert set(findings) == {("paraphase", GENE), ("repeat", LOCUS)}
    # The 53 is grey zone, listed because it lies near the premutation at a locus not validated.
    [fmr1] = findings[("repeat", LOCUS)]["samples"]
    assert fmr1 == {"sample": run["sample"], "status": "intermediate", "near_boundary": True}
    assert "FMR1 (repeat locus)" in detail["message"]
    assert _detail(run, "gate_no_reason", 422) == "A reason is required to acknowledge findings without a review."


def test_a_review_saves_its_calls_and_refuses_what_it_must(run: dict[str, Any]) -> None:
    saved = _ok(run, "save")
    assert saved["key"] == LOCUS and saved["classification"] == "intermediate"
    assert saved["tags"] == ["report"] and saved["note"] == "Grey zone; PCR sizing requested"
    assert saved["evidence_captured_at"] is not None
    assert saved["drift"] == {"status": "unchanged", "changes": []}
    assert _detail(run, "stale", 409)["code"] == "review_conflict"
    assert run["responses"]["other_scale"]["status"] == 422
    assert "no_such_tag" in _detail(run, "unknown_tag", 400)
    assert _detail(run, "no_call", 404) == "This family holds no call at that repeat locus"
    gba = _ok(run, "paraphase_save")
    assert (gba["classification"], gba["tags"]) == ("carrier", ["review"])


def test_the_tables_carry_each_review(run: dict[str, Any]) -> None:
    [locus] = [locus for locus in _ok(run, "repeat_table")["loci"] if locus["locus_id"] == LOCUS]
    assert locus["review"]["classification"] == "intermediate"
    assert locus["review"]["drift"]["status"] == "unchanged"
    # The automatic status is the classifier's, whatever the review says.
    assert locus["status"] == "intermediate"
    [gene] = [gene for gene in _ok(run, "paraphase_table")["genes"] if gene["gene_symbol"] == GENE]
    assert gene["review"]["classification"] == "carrier"


def test_the_record_holds_the_reviews_and_nothing_unreviewed(run: dict[str, Any]) -> None:
    signed = _ok(run, "signout")
    detail = _ok(run, "signout_detail")
    assert detail["verified"] is True and detail["content_hash"] == signed["content_hash"]
    snapshot = detail["snapshot"]
    [repeat] = snapshot["repeat_expansion_reviews"]
    assert repeat["key"] == LOCUS and repeat["classification_label"] == "Intermediate (grey zone)"
    assert repeat["reported"] is True and repeat["drift"] == {"status": "unchanged", "changes": []}
    assert repeat["evidence_snapshot"]["evidence"]["locus_id"] == LOCUS
    [paraphase] = snapshot["paraphase_reviews"]
    assert paraphase["key"] == GENE and paraphase["reported"] is False
    assert snapshot["unreviewed_findings"] == []
    assert snapshot["finding_review_drift"] == {"checked": 2, "drifted_count": 0, "drifted": []}
    assert snapshot["acknowledged_unreviewed_findings"] is False
    check = _ok(run, "check")
    assert (check["matches"], check["changed_sections"], check["not_compared"]) == (True, [], [])


def test_a_change_in_the_calls_under_a_review_gates_the_sign_out(run: dict[str, Any]) -> None:
    assert _ok(run, "validate")["validated"] is True
    [locus] = [locus for locus in _ok(run, "repeat_table_drifted")["loci"] if locus["locus_id"] == LOCUS]
    drift = locus["review"]["drift"]
    assert drift["status"] == "changed"
    assert "count_units.validated: False → True" in drift["changes"]
    gate = _detail(run, "drift_gate", 409)
    assert gate["gate"] == "evidence_drift"
    [listed] = gate["finding_drifted"]
    assert (listed["kind"], listed["key"], listed["status"]) == ("repeat", LOCUS, "changed")
    assert "repeat or Paraphase reviews" in gate["message"]
    snapshot = _ok(run, "signout_drifted_detail")["snapshot"]
    assert snapshot["acknowledged_drift"] is True
    assert snapshot["finding_review_drift"]["drifted_count"] == 1
    assert snapshot["repeat_expansion_reviews"][0]["drift"]["status"] == "changed"


def test_each_change_is_on_the_familys_audit_chain(run: dict[str, Any]) -> None:
    assert _ok(run, "clear") == {
        "key": GENE,
        "classification": None,
        "tags": [],
        "tag_metadata": {},
        "note": None,
        "updated_by": None,
        "updated_at": None,
        "evidence_captured_at": None,
        "drift": None,
    }
    events = _ok(run, "audit")["events"]
    by_finding: dict[str, list[str]] = {}
    for event in events:
        if event.get("variant_id") in (f"repeat:{LOCUS}", f"paraphase:{GENE}"):
            by_finding.setdefault(event["variant_id"], []).append(event["action"])
    assert sorted(by_finding[f"repeat:{LOCUS}"]) == ["classification", "note", "tags"]
    # Saved, then cleared: two classification and two tag events.
    assert sorted(by_finding[f"paraphase:{GENE}"]) == ["classification", "classification", "tags", "tags"]
    assert _ok(run, "chain")["verified"] is True
    assert _ok(run, "signout_chain")["verified"] is True
