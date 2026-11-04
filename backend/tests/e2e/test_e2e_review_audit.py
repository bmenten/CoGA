"""M3 end-to-end: clinical review round-trip, audit hash-chain, and report sign-out.

The IVDR-critical traceability path. Over the golden-trio data it:

* PUTs a clinical review on the P/LP variant (ACMG criteria + note + report tag)
  and asserts the server RECOMPUTES the classification (client-supplied totals are
  ignored) and persists note/tags;
* asserts the review produced chained ``clinical_audit_events`` and that
  ``/api/admin/integrity/verify`` re-walks the chain to ``verified=true``;
* signs the report out twice and asserts versioning + a verified sign-out chain;
* records a compound-het pair on the golden GENE_CH pair (``1-4000-T-C`` +
  ``1-5000-A-T``) from its first variant, gives the second variant tags and a note of its
  own, then drops the pair with a save of the first (``{"compound_het": null}``): the
  first variant's review, which held only the pair, is deleted, the second's keeps its own
  tags and note without the pair, and the removal is on the clinical audit trail under the
  variant whose review held it;
* records the pair again, then saves its first variant with a tag, a note and
  ``{"compound_het": null}``: the save stores the tag and note on that variant's review
  without the pair, the second variant's review, which held only the pair, is deleted, and
  the clinical audit trail records exactly that. The pair clear once deleted the saved
  variant's own review first: the save answered 500, stored nothing, and committed the tag,
  the note and the pair's removal to the trail.

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

from backend.tests.e2e._harness import capture

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
# The P/LP golden SNV (gene GENE_PATH); ACMG class is reviewer-driven, asserted here.
PLP_VARIANT_ID = "1-2000-C-T"
# The golden GENE_CH pair, both heterozygous in the affected proband.
CH_PAIR = ("1-4000-T-C", "1-5000-A-T")


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
        r["review"] = capture(
            await ac.put(f"/api/families/{FAMILY}/small-variants/{PLP_VARIANT_ID}/review", json=review_payload)
        )
        # The review as a variant list serves it: the list the tables, the report and the
        # ACMG dialog opened from a list read.
        r["list_after_review"] = capture(
            await ac.get(
                f"/api/families/{FAMILY}/small-variants",
                params={"review_tag": "report", "page_size": 50},
            )
        )
        r["audit"] = capture(await ac.get(f"/api/families/{FAMILY}/clinical-audit"))
        r["integrity_clinical"] = capture(
            await ac.get("/api/admin/integrity/verify", params={"table": "clinical_audit_events", "family_id": FAMILY})
        )
        r["integrity_badtable"] = capture(
            await ac.get("/api/admin/integrity/verify", params={"table": "not_a_table", "family_id": FAMILY})
        )

        signout_body = {
            "acknowledge_drift": True,
            "drift_acknowledgement_reason": "e2e validation",
            "acknowledge_qc": True,
            "qc_acknowledgement_reason": "e2e validation",
            # The abnormal repeat and Paraphase calls are not reviewed here (REQ-TRACE-016).
            "acknowledge_unreviewed_findings": True,
            "unreviewed_findings_acknowledgement_reason": "e2e: the repeat and Paraphase calls are not reviewed",
        }
        signout_url = f"/api/families/{FAMILY}/report/sign-out"
        r["signout1"] = capture(await _harness.sign_out(ac, signout_url, signout_body))
        r["signout2"] = capture(await _harness.sign_out(ac, signout_url, signout_body))
        r["integrity_signouts"] = capture(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": FAMILY})
        )
        # #508: the report page asks whether its live content still matches the latest
        # sign-out. Straight after signing it must — through the real JSONB round-trip —
        # and after a reported review is edited it must not.
        check_url = f"/api/families/{FAMILY}/report/sign-out-check"
        r["check_after_signout"] = capture(await ac.get(check_url))
        r["review_edit"] = capture(
            await ac.put(
                f"/api/families/{FAMILY}/small-variants/{PLP_VARIANT_ID}/review",
                json={**review_payload, "note": "E2E: note edited after sign-out"},
            )
        )
        r["check_after_edit"] = capture(await ac.get(check_url))
        # REQ-TRACE-007: the report page renders a signed version from this record alone, so
        # after the edit it must still hold what was signed.
        signed_version = (r["signout2"].get("json") or {}).get("version")
        if signed_version is not None:
            r["signed_after_edit"] = capture(
                await ac.get(f"/api/families/{FAMILY}/report/sign-outs/{signed_version}")
            )

        # A compound-het pair review removed by a later save: the pair is recorded on the
        # GENE_CH pair from its first variant, the second variant gets a review of its own,
        # and a save of the first that drops the pair rewrites each review of the pair.
        reviews = f"/api/families/{FAMILY}/small-variants"
        first, second = CH_PAIR
        try:
            r["pair_recorded"] = capture(
                await ac.put(
                    f"{reviews}/{first}/review",
                    json={"compound_het": {"partner_variant_id": second, "note": "E2E: in trans"}},
                )
            )
            r["second_own_review"] = capture(
                await ac.put(f"{reviews}/{second}/review", json={"tags": ["review"], "note": "E2E: own note"})
            )
            out["pair_rows_before"] = await _pair_review_rows(facts["family_uuid"])
            r["pair_removed"] = capture(await ac.put(f"{reviews}/{first}/review", json={"compound_het": None}))
            out["pair_rows_after"] = await _pair_review_rows(facts["family_uuid"])
            r["audit_after_pair"] = capture(
                await ac.get(f"/api/families/{FAMILY}/clinical-audit", params={"limit": 50})
            )
        finally:
            r["pair_cleared"] = capture(await ac.put(f"{reviews}/{second}/review", json={"tags": [], "note": None}))
            out["pair_rows_at_end"] = await _pair_review_rows(facts["family_uuid"])

        # A save that drops the pair and gives the variant a tag and a note of its own: the
        # pair is recorded from its first variant, whose review then holds only the pair.
        try:
            r["pair_for_drop"] = capture(
                await ac.put(f"{reviews}/{first}/review", json={"compound_het": {"partner_variant_id": second}})
            )
            out["drop_rows_before"] = await _pair_review_rows(facts["family_uuid"])
            r["pair_dropped_with_tags"] = capture(
                await ac.put(
                    f"{reviews}/{first}/review",
                    json={"tags": ["review"], "note": "E2E: own note", "compound_het": None},
                )
            )
            out["drop_rows_after"] = await _pair_review_rows(facts["family_uuid"])
            r["list_after_drop"] = capture(await ac.get(reviews, params={"review_tag": "review", "page_size": 50}))
            r["audit_after_drop"] = capture(
                await ac.get(f"/api/families/{FAMILY}/clinical-audit", params={"limit": 50})
            )
            r["integrity_after_drop"] = capture(
                await ac.get(
                    "/api/admin/integrity/verify", params={"table": "clinical_audit_events", "family_id": FAMILY}
                )
            )
        finally:
            r["drop_cleared"] = capture(await ac.put(f"{reviews}/{first}/review", json={"tags": [], "note": None}))
            out["drop_rows_at_end"] = await _pair_review_rows(facts["family_uuid"])

    out["tamper"] = await _tamper_checks()
    return out


async def _pair_review_rows(family_uuid: str) -> dict:
    """The GENE_CH pair's review rows as Postgres holds them, by variant."""
    from sqlalchemy import bindparam, text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT variant_id, tags, note, compound_het_group_id,
                           compound_het_partner_variant_ids, compound_het_note
                    FROM small_variant_reviews
                    WHERE family_id = CAST(:family AS uuid) AND variant_id IN :ids
                    """
                ).bindparams(bindparam("ids", expanding=True)),
                {"family": family_uuid, "ids": list(CH_PAIR)},
            )
        ).mappings().all()
    return {row["variant_id"]: dict(row) for row in rows}


@pytest.fixture(scope="module")
def snap(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_review") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
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
    # PVS1(very_strong=8) + PM2(supporting=1) = 9 -> likely pathogenic (class 4).
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


def test_signout_check_matches_right_after_signout(snap):
    # #508: identical content compares equal through the real JSONB round-trip.
    resp = _resp(snap, "check_after_signout")
    signout2 = _resp(snap, "signout2")
    assert resp["json"]["version"] == signout2["json"]["version"]
    assert resp["json"]["matches"] is True, resp["json"]
    assert resp["json"]["changed_sections"] == []


def test_signout_check_flags_a_review_edited_after_signout(snap):
    assert _resp(snap, "review_edit")["status"] == 200
    resp = _resp(snap, "check_after_edit")
    assert resp["json"]["matches"] is False, resp["json"]
    assert "reported_variants" in resp["json"]["changed_sections"]


def test_a_variant_list_serves_the_review_with_its_acmg_criteria(snap):
    # Through the real list endpoint, ClickHouse and Postgres: the saved criteria come back,
    # with the class the server recomputed, as the review save returned them.
    saved = _resp(snap, "review")
    resp = _resp(snap, "list_after_review")
    assert resp["status"] == 200, resp["text"]
    [variant] = [v for v in resp["json"]["variants"] if v["_id"] == PLP_VARIANT_ID]
    review = variant["review"]
    assert review["acmg"] is not None, review
    assert [(c["code"], c["strength"], c["accepted"]) for c in review["acmg"]["criteria"]] == [
        ("PVS1", "very_strong", True),
        ("PM2", "supporting", True),
        ("BA1", "stand_alone", False),
    ]
    assert review["acmg"] == saved["json"]["acmg"]
    assert review["acmg_unreadable"] is False


def test_a_signed_version_still_holds_what_was_signed_after_an_edit(snap):
    # REQ-TRACE-007: read back through the real JSONB round-trip, the latest signed version
    # verifies and holds the reported review as signed, not as edited since.
    signout2 = _resp(snap, "signout2")
    resp = _resp(snap, "signed_after_edit")
    assert resp["status"] == 200, resp["text"]
    body = resp["json"]
    assert body["version"] == signout2["json"]["version"]
    assert body["content_hash"] == signout2["json"]["content_hash"]
    assert body["verified"] is True, body
    reported = {entry["variant_id"]: entry for entry in body["snapshot"]["reported_variants"]}
    signed = {entry["variant_id"]: entry for entry in signout2["json"]["snapshot"]["reported_variants"]}
    assert reported[PLP_VARIANT_ID]["note"] == "E2E: likely pathogenic", reported
    assert reported[PLP_VARIANT_ID] == signed[PLP_VARIANT_ID]
    # What the record could not capture is named with it (a list, empty when complete).
    assert isinstance(body["not_captured"], list), body


# ------------------------------------------------------------ compound-het pair reviews


def test_a_save_that_drops_a_pair_rewrites_each_review_of_the_pair(snap):
    for key in ("pair_recorded", "second_own_review", "pair_removed", "pair_cleared"):
        assert _resp(snap, key)["status"] == 200, (key, _resp(snap, key)["text"])
    first, second = CH_PAIR
    # The pair recorded from its first variant is held by both variants' reviews; the
    # second variant's own review keeps it.
    before = snap["pair_rows_before"]
    assert before[first]["compound_het_group_id"] is not None
    assert before[first]["compound_het_group_id"] == before[second]["compound_het_group_id"]
    assert before[first]["compound_het_partner_variant_ids"] == [second]
    assert before[second]["compound_het_partner_variant_ids"] == [first]
    assert before[first]["compound_het_note"] == before[second]["compound_het_note"] == "E2E: in trans"
    assert (before[second]["tags"], before[second]["note"]) == (["review"], "E2E: own note")
    # Dropped from the first variant: its review held the pair alone and goes; the
    # second's is kept for its own tags and note, without the pair.
    after = snap["pair_rows_after"]
    assert set(after) == {second}, after
    kept = after[second]
    assert (kept["tags"], kept["note"]) == (["review"], "E2E: own note")
    assert kept["compound_het_group_id"] is None, kept
    assert not kept["compound_het_partner_variant_ids"], kept
    assert kept["compound_het_note"] is None, kept
    # Cleared at the end, the second variant's review goes too.
    assert snap["pair_rows_at_end"] == {}


def test_the_dropped_pair_is_on_the_clinical_audit_trail(snap):
    resp = _resp(snap, "audit_after_pair")
    assert resp["status"] == 200, resp["text"]
    first, second = CH_PAIR
    pair_events = [event for event in resp["json"]["events"] if event["action"] == "compound_het_pair"]
    # Most recent first: the removal, filed under the variant whose review held the pair,
    # then the pair as recorded.
    assert [(event["variant_id"], event["before"] is None, event["after"] is None) for event in pair_events[:2]] == [
        (first, False, True),
        (first, True, False),
    ], pair_events
    assert pair_events[0]["summary"] == f"Compound-het pair with {second} removed", pair_events[0]


def test_a_save_that_drops_the_pair_stores_the_tags_and_note_it_gives(snap):
    for key in ("pair_for_drop", "pair_dropped_with_tags", "list_after_drop", "drop_cleared"):
        assert _resp(snap, key)["status"] == 200, (key, _resp(snap, key)["text"])
    first, second = CH_PAIR
    before = snap["drop_rows_before"]
    assert before[first]["compound_het_group_id"] is not None
    assert before[first]["compound_het_group_id"] == before[second]["compound_het_group_id"]
    assert (before[first]["tags"], before[first]["note"]) == ([], None)  # the review held only the pair
    # The save stores the tag and note on the variant's review, without the pair; the
    # partner's review held only the pair and is deleted.
    after = snap["drop_rows_after"]
    assert set(after) == {first}, after
    kept = after[first]
    assert (kept["tags"], kept["note"]) == (["review"], "E2E: own note")
    assert kept["compound_het_group_id"] is None, kept
    assert not kept["compound_het_partner_variant_ids"], kept
    # Served so by the save and by the variant list.
    [listed] = [v for v in _resp(snap, "list_after_drop")["json"]["variants"] if v["_id"] == first]
    for served in (_resp(snap, "pair_dropped_with_tags")["json"], listed["review"]):
        assert (served["tags"], served["note"], served["compound_het"]) == (["review"], "E2E: own note", None)
    # Cleared at the end, the variant's review goes too.
    assert snap["drop_rows_at_end"] == {}


def test_the_trail_records_what_the_save_that_dropped_the_pair_stored(snap):
    resp = _resp(snap, "audit_after_drop")
    assert resp["status"] == 200, resp["text"]
    first, second = CH_PAIR
    # Most recent first: the drop save's three events, then the pair as recorded.
    latest = resp["json"]["events"][:4]
    events = [(event["variant_id"], event["action"], event["summary"]) for event in latest]
    assert sorted(events[:3]) == sorted(
        [
            (first, "compound_het_pair", f"Compound-het pair with {second} removed"),
            (first, "note", "Note added"),
            (first, "tags", "Tags added review"),
        ]
    ), events
    assert events[3][:2] == (first, "compound_het_pair") and "recorded" in events[3][2], events
    # Each value the trail records as saved is the one the review row stores.
    stored = snap["drop_rows_after"].get(first)
    assert stored is not None, snap["drop_rows_after"]
    for event in latest[:3]:
        if event["action"] in ("tags", "note"):
            assert event["after"] == {event["action"]: stored[event["action"]]}, (event, stored)
    integrity = _resp(snap, "integrity_after_drop")
    assert integrity["status"] == 200 and integrity["json"]["verified"] is True, integrity
