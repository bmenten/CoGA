"""M3 end-to-end: clinical review round-trip, audit hash-chain, and report sign-out.

The IVDR-critical traceability path. Over the golden-trio data it:

* PUTs a clinical review on the P/LP variant (ACMG criteria + note + report tag)
  and asserts the server RECOMPUTES the classification (client-supplied totals are
  ignored) and persists note/tags;
* asserts the review produced chained ``clinical_audit_events`` and that
  ``/api/admin/integrity/verify`` re-walks the chain to ``verified=true``;
* signs the report out twice and asserts versioning + a verified sign-out chain.

Chain tamper-evidence + append-only immutability are exercised on a THROWAWAY
family via the direct audit service (``record_clinical_event``), so the
irreversible chain break never corrupts FAM_TRIO and the suite stays re-runnable.

Per the design verification, this asserts the chain is tamper-EVIDENT (a
trigger-bypass edit flips ``verified`` to false) — NOT that it detects a
determined owner who re-chains (that residual is the external signed anchor).

Everything runs on one event loop via an in-process ``httpx.ASGITransport``
client (no lifespan/bootstrap; schema + admin + data come from the harness).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
# The P/LP golden SNV (gene GENE_PATH); ACMG class is reviewer-driven, asserted here.
PLP_VARIANT_ID = "1-2000-C-T"


def _cap(resp, *, as_json: bool = True) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if as_json and resp.status_code < 400:
        try:
            out["json"] = resp.json()
        except Exception:  # pragma: no cover
            out["json"] = None
    return out


async def _tamper_checks() -> dict:
    """Seed a throwaway family's audit chain, verify it, then bypass the
    append-only trigger to edit an interior row and confirm the chain detects it;
    finally confirm a normal UPDATE is rejected by the trigger."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clinical_audit_service import (
        record_clinical_event,
        verify_clinical_audit_chain,
    )

    fam_id = f"E2E_TAMPER_{uuid4().hex[:10]}"
    sm = get_postgres_sessionmaker()
    result: dict = {"family_identifier": fam_id}
    async with sm() as session:
        for i, action in enumerate(["classification", "tags", "note"]):
            await record_clinical_event(
                session,
                family_uuid=None,
                family_identifier=fam_id,
                variant_id="1-1-A-G",
                actor="e2e",
                actor_id=None,
                action=action,
                summary=f"seed {action}",
                before={},
                after={"i": i},
            )
        await session.commit()

        clean = await verify_clinical_audit_chain(session, fam_id)
        result["clean_verified"] = clean.verified
        result["clean_rows"] = clean.rows_checked

        # Bypass the append-only trigger to edit an interior row WITHOUT re-hashing.
        try:
            await session.execute(text("ALTER TABLE clinical_audit_events DISABLE TRIGGER USER"))
            await session.execute(
                text(
                    "UPDATE clinical_audit_events SET summary = 'tampered' "
                    "WHERE family_identifier = :f AND action = 'tags'"
                ),
                {"f": fam_id},
            )
            await session.commit()
        finally:
            await session.execute(text("ALTER TABLE clinical_audit_events ENABLE TRIGGER USER"))
            await session.commit()

        tampered = await verify_clinical_audit_chain(session, fam_id)
        result["tampered_verified"] = tampered.verified
        result["tampered_reason"] = tampered.reason
        result["tampered_first_bad_row"] = tampered.first_bad_row

        # With the trigger live, a direct UPDATE must be rejected (append-only).
        try:
            await session.execute(
                text("UPDATE clinical_audit_events SET summary = 'x' WHERE family_identifier = :f"),
                {"f": fam_id},
            )
            await session.commit()
            result["append_only_enforced"] = False
        except Exception as exc:  # noqa: BLE001 - asserting the DB rejects the write
            await session.rollback()
            result["append_only_enforced"] = "append-only" in str(exc).lower()
    return result


async def _collect(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    out: dict = {"facts": facts, "responses": {}}
    r = out["responses"]
    transport = ASGITransport(app=app)
    base = "http://e2e"

    async with AsyncClient(transport=transport, base_url=base) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        review_payload = {
            "tags": ["report", "acmg_class_4"],
            "note": "E2E: likely pathogenic",
            "acmg": {
                "criteria": [
                    {"code": "PVS1", "strength": "very_strong", "accepted": True},
                    {"code": "PM2", "strength": "supporting", "accepted": True},
                    # client-supplied derived fields must be ignored (server recomputes):
                    {"code": "BA1", "strength": "stand_alone", "accepted": False},
                ],
                "point_total": 999,
                "classification": "Benign - class 1",
            },
        }
        r["review"] = _cap(
            await ac.put(f"/api/families/{FAMILY}/small-variants/{PLP_VARIANT_ID}/review", json=review_payload)
        )
        r["audit"] = _cap(await ac.get(f"/api/families/{FAMILY}/clinical-audit"))
        r["integrity_clinical"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "clinical_audit_events", "family_id": FAMILY})
        )
        r["integrity_badtable"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "not_a_table", "family_id": FAMILY}),
            as_json=False,
        )

        signout_body = {"acknowledge_drift": True, "acknowledge_qc": True, "qc_acknowledgement_reason": "e2e validation"}
        r["signout1"] = _cap(await ac.post(f"/api/families/{FAMILY}/report/sign-out", json=signout_body))
        r["signout2"] = _cap(await ac.post(f"/api/families/{FAMILY}/report/sign-out", json=signout_body))
        r["integrity_signouts"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": FAMILY})
        )

    out["tamper"] = await _tamper_checks()
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory, request) -> dict:
    from backend.app.services import family_package_import as package_import
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    root = tmp_path_factory.mktemp("golden_review") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(package_import.settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def _resp(snap: dict, key: str) -> dict:
    return snap["responses"][key]


# -------------------------------------------------------------------- review round-trip


def test_review_recomputes_acmg_and_persists(snap):
    resp = _resp(snap, "review")
    assert resp["status"] == 200, resp["text"]
    body = resp["json"]
    acmg = body["acmg"]
    # PVS1(very_strong=8) + PM2(supporting=1) = 9 -> Likely Pathogenic (class 4).
    # The bogus client point_total/classification (999 / Benign) must be ignored.
    assert acmg["point_total"] == 9, acmg
    assert "4" in acmg["classification"], acmg
    assert "report" in body["tags"], body["tags"]
    assert body["note"] == "E2E: likely pathogenic"


# ------------------------------------------------------------------------------ audit


def test_review_emits_chained_audit_events(snap):
    resp = _resp(snap, "audit")
    assert resp["status"] == 200, resp["text"]
    actions = {e["action"] for e in resp["json"]["events"]}
    assert {"classification", "tags", "note"} <= actions, actions


def test_clinical_audit_chain_verifies(snap):
    resp = _resp(snap, "integrity_clinical")
    assert resp["status"] == 200, resp["text"]
    body = resp["json"]
    assert body["verified"] is True, body
    assert body["rows_checked"] >= 3, body


def test_integrity_verify_rejects_unknown_table(snap):
    assert _resp(snap, "integrity_badtable")["status"] == 400, _resp(snap, "integrity_badtable")["text"]


# --------------------------------------------------------------------------- sign-out


def test_report_signout_versions_and_hash(snap):
    s1, s2 = _resp(snap, "signout1"), _resp(snap, "signout2")
    assert s1["status"] == 200, s1["text"]
    assert s2["status"] == 200, s2["text"]
    assert len(s1["json"]["content_hash"]) == 64, s1["json"]["content_hash"]
    assert s2["json"]["version"] == s1["json"]["version"] + 1, (s1["json"]["version"], s2["json"]["version"])


def test_report_signout_chain_verifies(snap):
    resp = _resp(snap, "integrity_signouts")
    assert resp["status"] == 200, resp["text"]
    assert resp["json"]["verified"] is True, resp["json"]


# ------------------------------------------------------------ tamper-evidence / append-only


def test_audit_chain_is_tamper_evident(snap):
    t = snap["tamper"]
    assert t["clean_verified"] is True, t
    assert t["clean_rows"] == 3, t
    # a trigger-bypass edit of an interior row must flip the chain to not-verified
    assert t["tampered_verified"] is False, t
    assert t["tampered_first_bad_row"] is not None, t


def test_audit_table_is_append_only(snap):
    assert snap["tamper"]["append_only_enforced"] is True, snap["tamper"]
