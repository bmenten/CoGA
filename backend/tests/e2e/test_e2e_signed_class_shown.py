"""The class a reported variant's review shows, frozen into the signed record (E2E, CLIN-80).

The owner's decision of 2026-11-10: the signed record freezes the class each reported variant's
review shows, by the family page's one rule (``review_class_label``: a bare class word in the
text, else the most severe ACMG class tag, else the text), beside its classification text; the
live report and the signed version show that class, with the ACMG (or CNV) score beside it when
the two differ. Over the golden trio, through the API, this:

* classifies four small variants as the owner's cases have it (an ACMG classification of class 4
  whose class the review dialog then set to class 3; a class tag alone; "VUS" beside the tag of
  class 4; "Pathogenic" alone) and two SVs (a class tag alone; a ClinGen CNV scoring with the
  text the CNV dialog writes), tags them for the report and signs the family out;
* reads the signed record back: each entry holds the class its review shows and, for a small
  variant, its text, beside the ACMG record and the CNV scoring as frozen before; the record
  verifies and the check matches;
* changes a class by its text alone ("VUS" to "Likely benign") and clears the CNV-scored SV's
  text: the check names both sections as changed, and the next sign-out freezes the new classes,
  the SV with none (its CNV scoring is the score, never the class).

It leaves nothing tagged for the report: the golden trio's database is shared by the modules of
a run. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.tests.e2e._harness import capture, ok_json

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
# The four small variants, each classified as one of the owner's cases.
OVERRIDDEN = "1-2000-C-T"  # ACMG class 4, then the review dialog's class 3
TAG_ONLY = "1-3000-G-A"  # the tag of class 4 alone
WORD_BEFORE_TAG = "1-4000-T-C"  # "VUS" beside the tag of class 4
WORD_ONLY = "1-5000-A-T"  # "Pathogenic" alone
SMALL_VARIANTS = (OVERRIDDEN, TAG_ONLY, WORD_BEFORE_TAG, WORD_ONLY)
# The two SVs: a class tag alone, and a ClinGen CNV scoring with its dialog's text.
SV_TAG_ONLY = "1-20000-20000-BND-5-3000000-3000000"
SV_SCORED = "1-7000-12000-DEL---"
STRUCTURAL_VARIANTS = (SV_TAG_ONLY, SV_SCORED)

# PVS1 alone scores 8 points: Likely pathogenic, class 4.
_ACMG_CLASS_4 = {"criteria": [{"code": "PVS1", "strength": "very_strong", "accepted": True}]}
_CNV_SCORING = {"kind": "loss", "criteria": [{"code": "2A", "points": 1.0, "accepted": True}]}
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: classified through the review dialog, no evidence frozen",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: synthetic genotypes",
    "acknowledge_unreviewed_findings": True,
    "unreviewed_findings_acknowledgement_reason": "e2e: the repeat and Paraphase calls are not reviewed",
}
_REPORTED = {"review_tag": "report", "page": 1, "page_size": 100}


async def _collect(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    out: dict = {"facts": facts, "responses": {}}
    r = out["responses"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{FAMILY}"

        def small(variant_id: str) -> str:
            return f"{family}/small-variants/{variant_id}/review"

        def structural(variant_id: str) -> str:
            return f"{family}/structural-variants/{variant_id}/review"

        # ACMG classify: class 4, its text and its class tag. Then the review dialog's class 3:
        # the text and class tag it writes, the ACMG classification kept.
        r["acmg_classify"] = capture(
            await ac.put(
                small(OVERRIDDEN),
                json={
                    "classification": "Likely pathogenic - class 4",
                    "tags": ["acmg_class_4", "report"],
                    "acmg": _ACMG_CLASS_4,
                },
            )
        )
        r["dialog_class_3"] = capture(
            await ac.put(small(OVERRIDDEN), json={"classification": "VUS - class 3", "tags": ["acmg_class_3", "report"]})
        )
        r["tag_only"] = capture(await ac.put(small(TAG_ONLY), json={"tags": ["acmg_class_4", "report"]}))
        r["word_before_tag"] = capture(
            await ac.put(small(WORD_BEFORE_TAG), json={"classification": "VUS", "tags": ["acmg_class_4", "report"]})
        )
        r["word_only"] = capture(await ac.put(small(WORD_ONLY), json={"classification": "Pathogenic", "tags": ["report"]}))
        r["sv_tag_only"] = capture(await ac.put(structural(SV_TAG_ONLY), json={"tags": ["acmg_class_4", "report"]}))
        r["sv_scored"] = capture(
            await ac.put(
                structural(SV_SCORED),
                json={
                    "classification": "Pathogenic - class 5",
                    "tags": ["report"],
                    "cnv_acmg": _CNV_SCORING,
                    "source": "needlr",
                },
            )
        )
        # The live report's lists, as the report page reads them.
        r["live_small"] = capture(await ac.get(f"{family}/small-variants", params=_REPORTED))
        r["live_sv"] = capture(await ac.get(f"{family}/structural-variants", params=_REPORTED))

        r["signout"] = capture(await _harness.sign_out(ac, f"{family}/report/sign-out", SIGN_OUT_BODY))
        first = (r["signout"]["json"] or {}).get("version")
        r["detail"] = capture(await ac.get(f"{family}/report/sign-outs/{first}"))
        r["check"] = capture(await ac.get(f"{family}/report/sign-out-check"))

        # After sign-out: a class changed by its text alone, the tags as they were; and the
        # CNV-scored SV's text cleared, its scoring kept.
        r["word_changed"] = capture(
            await ac.put(
                small(WORD_BEFORE_TAG), json={"classification": "Likely benign", "tags": ["acmg_class_4", "report"]}
            )
        )
        r["sv_text_cleared"] = capture(
            await ac.put(structural(SV_SCORED), json={"classification": None, "tags": ["report"]})
        )
        r["check_changed"] = capture(await ac.get(f"{family}/report/sign-out-check"))
        r["live_small_changed"] = capture(await ac.get(f"{family}/small-variants", params=_REPORTED))
        r["live_sv_changed"] = capture(await ac.get(f"{family}/structural-variants", params=_REPORTED))
        r["signout_changed"] = capture(await _harness.sign_out(ac, f"{family}/report/sign-out", SIGN_OUT_BODY))
        second = (r["signout_changed"]["json"] or {}).get("version")
        r["detail_changed"] = capture(await ac.get(f"{family}/report/sign-outs/{second}"))
        r["chain"] = capture(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": FAMILY})
        )

        # The family ends on a record of today's build, with nothing tagged for the report.
        r["final_signout"] = capture(await _harness.sign_out(ac, f"{family}/report/sign-out", SIGN_OUT_BODY))
        cleared = {"classification": None, "tags": [], "note": None}
        for variant_id in SMALL_VARIANTS:
            r[f"untag_{variant_id}"] = capture(await ac.put(small(variant_id), json={**cleared, "acmg": None}))
        for variant_id in STRUCTURAL_VARIANTS:
            r[f"untag_{variant_id}"] = capture(
                await ac.put(structural(variant_id), json={**cleared, "cnv_acmg": None})
            )
        r["left_tagged_small"] = capture(await ac.get(f"{family}/small-variants", params=_REPORTED))
        r["left_tagged_sv"] = capture(await ac.get(f"{family}/structural-variants", params=_REPORTED))
    return out


@pytest.fixture(scope="module")
def signed(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_signed_class_shown") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def _entries(signed: dict, key: str, section: str) -> dict:
    return {entry["variant_id"]: entry for entry in ok_json(signed, key)["snapshot"][section]}


def test_each_review_is_saved_as_the_case_has_it(signed) -> None:
    for key in ("acmg_classify", "dialog_class_3", "tag_only", "word_before_tag", "word_only", "sv_tag_only", "sv_scored"):
        ok_json(signed, key)
    # The live lists the report reads hold the four small variants and the two SVs.
    assert {variant["_id"] for variant in ok_json(signed, "live_small")["variants"]} == set(SMALL_VARIANTS)
    assert {variant["_id"] for variant in ok_json(signed, "live_sv")["variants"]} == set(STRUCTURAL_VARIANTS)


def test_the_record_freezes_the_class_each_review_shows_beside_its_scoring(signed) -> None:
    small = _entries(signed, "detail", "reported_variants")
    assert {variant_id: small[variant_id]["class_shown"] for variant_id in SMALL_VARIANTS} == {
        OVERRIDDEN: "VUS - class 3",
        TAG_ONLY: "Likely pathogenic - class 4",
        WORD_BEFORE_TAG: "VUS - class 3",
        WORD_ONLY: "Pathogenic - class 5",
    }
    # The text each class was read from, as stored.
    assert {variant_id: small[variant_id]["classification"] for variant_id in SMALL_VARIANTS} == {
        OVERRIDDEN: "VUS - class 3",
        TAG_ONLY: None,
        WORD_BEFORE_TAG: "VUS",
        WORD_ONLY: "Pathogenic",
    }
    # The ACMG classification the dialog's class overrode is kept, as the score beside it.
    assert small[OVERRIDDEN]["acmg"]["classification"] == "Likely pathogenic - class 4"
    assert small[OVERRIDDEN]["acmg"]["point_total"] == 8

    structural = _entries(signed, "detail", "reported_structural_variants")
    assert structural[SV_TAG_ONLY]["class_shown"] == "Likely pathogenic - class 4"
    assert structural[SV_SCORED]["class_shown"] == "Pathogenic - class 5"
    assert structural[SV_SCORED]["cnv_acmg"]["classification"] == "Pathogenic - class 5"


def test_the_record_verifies_and_matches_right_after_it_was_signed(signed) -> None:
    assert ok_json(signed, "detail")["verified"] is True
    check = ok_json(signed, "check")
    assert check["matches"] is True, check
    assert check["not_compared"] == []


def test_a_class_changed_since_sign_out_reads_as_a_change(signed) -> None:
    ok_json(signed, "word_changed")
    ok_json(signed, "sv_text_cleared")
    check = ok_json(signed, "check_changed")
    assert check["matches"] is False
    # The small variant's text alone changed, and the SV's.
    assert {"reported_variants", "reported_structural_variants"} <= set(check["changed_sections"]), check
    assert check["not_compared"] == []


def test_the_next_sign_out_freezes_the_classes_shown_now(signed) -> None:
    small = _entries(signed, "detail_changed", "reported_variants")
    assert (small[WORD_BEFORE_TAG]["classification"], small[WORD_BEFORE_TAG]["class_shown"]) == (
        "Likely benign",
        "Likely benign - class 2",
    )
    structural = _entries(signed, "detail_changed", "reported_structural_variants")
    # Its text cleared, the SV shows no class: its CNV scoring stays, as the score.
    assert structural[SV_SCORED]["class_shown"] is None
    assert structural[SV_SCORED]["classification"] is None
    assert structural[SV_SCORED]["cnv_acmg"]["classification"] == "Pathogenic - class 5"
    assert ok_json(signed, "chain")["verified"] is True


def test_the_module_leaves_nothing_tagged_for_the_report(signed) -> None:
    ok_json(signed, "final_signout")
    for variant_id in (*SMALL_VARIANTS, *STRUCTURAL_VARIANTS):
        assert signed["responses"][f"untag_{variant_id}"]["status"] == 200, signed["responses"][f"untag_{variant_id}"]
    assert ok_json(signed, "left_tagged_small")["variants"] == []
    assert ok_json(signed, "left_tagged_sv")["variants"] == []
