"""A monogenic NIPT family's signed record, end to end (REG-2; owner decision of 2026-10-14).

The NIPT analysis is signed out through the family sign-out: one signed record per analysis,
which holds the NIPT results. Over the synthetic demo NIPT package (``demo/nipt_family``: a
joint VCF of a father and a maternal plasma at a fetal fraction of 12%, with a de novo
candidate and the eight categories), imported through the real package import, through the
API, this:

* reads the NIPT page's summary and variant list, which serve the verdicts and the
  classifications the record freezes, and the variant summary's NIPT-M statistics, read from
  the same analysis and left out of the record;
* tags a de novo candidate, a paternal allele and the listed artifact for the report, and
  reads the NIPT page's reported view before signing out (UI-4: the review-tag filter
  ``review_tag=report`` with no other filter, and ``exclude_review_tag``);
* signs the family out, the drift and Sample QC gates acknowledged (the demo's paternity rests
  on too few sites, so the Sample QC gate holds; no NIPT quality check fails, and the 409 says
  so);
* reads the signed record back: the NIPT results as the page served them (the fetal fraction,
  the quality checks with their verdicts, each reported variant as the variant list
  classifies it, the artifact as not classified, the de novo candidate's triage, the inputs),
  the variants the reported view listed and named, a verified content hash and chain, and a
  check that matches;
* asks the sign-out as a reader in the family's project (SEC-1), the account made for the run
  and removed at its end: the family reads as read-only to them, which both pages that sign it
  out (the NIPT page and the report) disable the sign-out by, and the server refuses theirs
  with 403, signing nothing.

A second copy gives the fetus the status a NIPT PED usually has, unknown (TRACE-19). The import
reads it as an embryo, and the run:

* tags the demo's compound heterozygote in GENE_RECESS (a paternal and a maternal allele) for
  the report on its pair review alone, through the API (no page sets a pair's tags);
* reads the NIPT page's "Show reported", its recessive view and its count, and the live
  report's list, and signs out;
* reads the record back: both variants frozen as "Show reported" classified them, among the
  reported small variants with the pair review that reports them, and described; the recessive
  fetal risk of GENE_RECESS the reported view computes; and no PGT section for the fetus.

CI runs every e2e module in one process on one database. The artifact list and the de novo
recurrence are shared by every cfDNA sample of an assay, and other modules import the same
demo package (``test_e2e_demo_smoke.py``, which also lists an artifact on the default assay).
So each run gives its maternal plasma an assay panel of its own (``assay_panel``, the scope of
both), lists an artifact in that scope through the admin API, compares the record with the
list it read in the same run, and removes the artifact when it is done: the run sees no other
module's samples or artifacts, and leaves no sample or artifact in a scope another module reads.

The golden trio's records, a family that is no NIPT family, are checked by
``test_e2e_signed_analysis_record.py``: its check names nothing it does not compare.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tests.e2e._harness import capture, ok_json
from backend.tests.e2e._nipt_demo import DEMO, import_demo, prepare_copy

pytestmark = pytest.mark.integration

DE_NOVO = "1-1001-A-G"  # GENE_DENOVO, category 1, in the fetal window
# The demo's region of interest, which its README sets by hand: the span of its coverage BED.
ROI = "1:1000-20000"
PATERNAL = "1-10000-A-G"  # a paternal allele the fetus inherited, category 7
ARTIFACT = "1-9501-A-G"  # the demo's recurrent artifact (EXP_CAT=ARTIFACT), in the plasma only
# What the server answers a reader's change (SEC-1, access_control.require_change_access).
READ_ONLY = (
    "Read-only access: you are a reader in this project, so you can view and export its records "
    "but not change them."
)
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: the reported variants hold no ACMG classification",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the synthetic demo has too few paternal-informative sites",
}


def _shown_fingerprint(refusal: dict) -> str | None:
    """The fingerprint of the concerns a captured refusal listed: what its dialog sends back."""
    detail = (refusal.get("json") or {}).get("detail")
    return detail.get("concerns_fingerprint") if isinstance(detail, dict) else None


async def _add_reader(project_id: str, email: str) -> tuple[str, str]:
    """An account that reads the family's project only (SEC-1): its id, and a bearer the app
    issues for it. It signs in with no password: the bearer is made as a sign-in makes one."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import create_access_token

    async with get_postgres_sessionmaker()() as session:
        user_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, role, email)
                    VALUES (:email, :marker, 'analyst', :email)
                    RETURNING id::text
                    """
                ),
                # No bcrypt hash: no password signs this account in.
                {"email": email, "marker": "!e2e-no-local-sign-in"},
            )
        ).scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO project_users (project_id, user_id, access_level)
                VALUES (CAST(:project_id AS uuid), CAST(:user_id AS uuid), 'reader')
                """
            ),
            {"project_id": project_id, "user_id": user_id},
        )
        await session.commit()
    return user_id, create_access_token({"sub": email})


async def _remove_user(user_id: str) -> None:
    """The run's reader removed, with their membership (ON DELETE CASCADE)."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": user_id})
        await session.commit()


async def _signed_versions(family_id: str) -> int:
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        return (
            await session.execute(
                text(
                    """
                    SELECT count(*) FROM report_signouts s
                    JOIN families f ON f.id = s.family_id
                    WHERE f.family_id = :family_id
                    """
                ),
                {"family_id": family_id},
            )
        ).scalar_one()


async def _collect(root: Path, family_id: str, panel: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    out: dict = {"import": await import_demo(root), "family": family_id, "responses": {}}
    r = out["responses"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"
        # The artifact-list scope: the assembly and the run's assay panel.
        scope = {"assembly_id": out["import"]["assembly_id"], "assay_key": panel}

        # The run's own artifact list: the demo's recurrent artifact, listed through the admin
        # API, and the list as it reads back, which the record is compared with.
        r["artifact_added"] = capture(
            await ac.post("/api/admin/nipt/artifacts", json={**scope, "variant_id": ARTIFACT, "label": "e2e recurrent artifact"})
        )
        artifact_id = (r["artifact_added"]["json"] or {}).get("id")
        reader_id = None
        try:
            r["artifacts"] = capture(await ac.get("/api/admin/nipt/artifacts", params=scope))
            await _sign_out_and_read(ac, r, family, family_id)

            # A reader in the family's project: the same sign-out, the one the NIPT page sends.
            reader_id, bearer = await _add_reader(out["import"]["project_id"], f"{panel}-reader@example.org")
            as_reader = {"Authorization": f"Bearer {bearer}"}
            r["reader_family"] = capture(await ac.get(family, headers=as_reader))
            r["reader_signout"] = capture(
                await _harness.sign_out(ac, f"{family}/report/sign-out", SIGN_OUT_BODY, headers=as_reader)
            )
            out["versions_after_reader"] = await _signed_versions(family_id)
        finally:
            # Nothing left in a scope another module could read, and no account.
            if artifact_id:
                r["artifact_deleted"] = capture(await ac.delete(f"/api/admin/nipt/artifacts/{artifact_id}"))
            r["artifacts_after"] = capture(await ac.get("/api/admin/nipt/artifacts", params=scope))
            if reader_id:
                await _remove_user(reader_id)
    return out


async def _sign_out_and_read(ac, r: dict, family: str, family_id: str) -> None:
    """Read the NIPT page, tag two variants for the report, sign out through the gates, and
    read the record, the check and the chain back."""
    # What the NIPT page shows: its coverage card asks for no gene or panel when it opens.
    r["summary"] = capture(await ac.get(f"{family}/nipt/summary"))
    r["coverage"] = capture(await ac.get(f"{family}/nipt/coverage"))
    r["variants"] = capture(await ac.get(f"{family}/nipt/variants", params={"page_size": 500}))
    r["de_novo_view"] = capture(await ac.get(f"{family}/nipt/variants", params={"inheritance": "de_novo"}))
    # The variant summary's NIPT-M tab and its small-variant tab.
    r["statistics"] = capture(await ac.get(f"{family}/nipt/statistics"))
    r["small_variant_statistics"] = capture(await ac.get(f"{family}/small-variant-statistics"))

    # Two variants for the report, from the NIPT page's list, and the artifact the analysis
    # leaves out (tagged from the family's small-variant list: the NIPT list never shows it).
    for variant_id in (DE_NOVO, PATERNAL, ARTIFACT):
        r[f"review_{variant_id}"] = capture(
            await ac.put(f"{family}/small-variants/{variant_id}/review", json={"tags": ["report"]})
        )

    # What the NIPT page lists before the sign-out (UI-4): "Show reported", the review tag
    # report with no other filter; within a view; the list without them; a tag none holds.
    nipt_variants = f"{family}/nipt/variants"
    r["reported_view"] = capture(await ac.get(nipt_variants, params={"review_tag": "report", "page_size": 500}))
    r["reported_de_novo_view"] = capture(
        await ac.get(nipt_variants, params={"review_tag": "report", "inheritance": "de_novo"})
    )
    r["without_reported"] = capture(await ac.get(nipt_variants, params={"exclude_review_tag": "report", "page_size": 500}))
    r["unheld_tag_view"] = capture(await ac.get(nipt_variants, params={"review_tag": "e2e-no-such-tag"}))

    # The gates, one by one: the drift of the unclassified reported variants, then the
    # Sample QC (the demo's paternity cannot be verified). Each acknowledgement carries the
    # fingerprint of the concerns its refusal listed, as its dialog sends it (TRACE-19).
    r["refused_drift"] = capture(await ac.post(f"{family}/report/sign-out", json={}))
    drift_acknowledged = {
        **{key: SIGN_OUT_BODY[key] for key in ("acknowledge_drift", "drift_acknowledgement_reason")},
        "drift_concerns_fingerprint": _shown_fingerprint(r["refused_drift"]),
    }
    r["refused_qc"] = capture(await ac.post(f"{family}/report/sign-out", json=drift_acknowledged))
    r["signout"] = capture(
        await ac.post(
            f"{family}/report/sign-out",
            json={
                **SIGN_OUT_BODY,
                **drift_acknowledged,
                "qc_concerns_fingerprint": _shown_fingerprint(r["refused_qc"]),
            },
        )
    )
    version = (r["signout"]["json"] or {}).get("version")
    r["detail"] = capture(await ac.get(f"{family}/report/sign-outs/{version}"))
    r["check"] = capture(await ac.get(f"{family}/report/sign-out-check"))
    r["chain"] = capture(
        await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
    )
    r["audit"] = capture(await ac.get(f"{family}/clinical-audit", params={"limit": 50}))


@pytest.fixture(scope="module")
def signed(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (DEMO / "manifest.yaml").exists():
        pytest.fail("the demo NIPT package is missing: it is committed (scripts/generate_nipt_demo.py rebuilds it)")
    # A region of interest for the coverage check, and a coverage BED that declares nothing: the
    # check names it as not read as depth (TRACE-9), the note the record freezes (TRACE-17).
    copy = prepare_copy(tmp_path_factory.mktemp("nipt_signed_record"), roi=ROI, declare_coverage=False)
    root = Path(copy["root"])
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root, copy["FAM_NIPT_DEMO"], copy["panel"]))
    assert out["import"]["completed"] is True, out["import"]
    out["ids"] = copy
    return out


def _nipt(signed: dict) -> dict:
    return ok_json(signed, "detail")["snapshot"]["nipt"]


# ------------------------------------------------------------------- the NIPT page


def test_the_nipt_page_serves_the_verdicts_the_record_freezes(signed) -> None:
    summary = ok_json(signed, "summary")
    assert summary["fetal_fraction"]["ff_computed"] == pytest.approx(0.12, abs=0.01)
    qc = summary["qc"]
    assert qc["fetal_fraction_status"] == "pass"
    # No chrX site and no target table: neither signal tells the fetal sex, a warning.
    assert (qc["fetal_sex"]["call"], qc["fetal_sex"]["status"]) == ("indeterminate", "warn")
    assert qc["plasma_profile_status"] == "unknown"
    [candidate] = ok_json(signed, "de_novo_view")["variants"]
    assert candidate["_id"] == DE_NOVO and candidate["nipt"]["de_novo"]["label"] == "high"


def test_the_variant_summary_reads_the_analysis_the_nipt_page_shows(signed) -> None:
    summary = ok_json(signed, "summary")
    statistics = ok_json(signed, "statistics")
    assert statistics["fetal_fraction"] == summary["fetal_fraction"]
    assert statistics["filter_counts"] == summary["filter_counts"]
    assert statistics["qc"]["paternity"] == summary["qc"]["paternity"]
    groups = {group["key"]: group for group in statistics["groups"]}
    for category, count in summary["category_counts"].items():
        assert groups[str(category)]["sites"] == count, category
    # The demo's joint VCF writes PASS on every record: every plasma call that went in passed.
    assert statistics["caller_filters"] == {"PASS": summary["filter_counts"]["total_in"]}
    # What the run's artifact list did: it lists one allele, the plasma carries it, and the
    # analysis removed it, as the record freezes it.
    assert statistics["artifacts"] == {"listed": 1, "in_family": 1, "removed": 1, "protected": {}}
    # Its small-variant tab names the plasma and the father, whose GT is not their genotype.
    small = ok_json(signed, "small_variant_statistics")
    assert set(small["nipt_samples"]) == {signed["ids"]["CFDNA_NIPT"], signed["ids"]["FATHER_NIPT"]}


# ------------------------------------------------------------------------ the gates


def test_the_sample_qc_gate_holds_and_no_nipt_check_failed(signed) -> None:
    for variant_id in (DE_NOVO, PATERNAL, ARTIFACT):
        ok_json(signed, f"review_{variant_id}")
    refused_drift = signed["responses"]["refused_drift"]
    assert refused_drift["status"] == 409, refused_drift["text"]
    drift = refused_drift["json"]["detail"]
    # The drift refusal lists the classifications it counts, as its dialog shows them: the
    # reported variants, which hold no classification and so no frozen evidence (TRACE-19).
    assert drift["gate"] == "evidence_drift"
    assert {(entry["variant_id"], entry["status"]) for entry in drift["drifted"]} >= {
        (DE_NOVO, "no_snapshot"),
        (PATERNAL, "no_snapshot"),
    }
    refused = signed["responses"]["refused_qc"]
    assert refused["status"] == 409, refused["text"]
    detail = refused["json"]["detail"]
    assert detail["gate"] == "sample_qc"
    assert any("paternity" in reason.lower() for reason in detail["unverifiable_checks"])
    # The NIPT page's checks passed, warned or could not run: none failed.
    assert detail["nipt_failed_checks"] == []
    # The refusal lists each group of concerns once, as the sign-out dialog shows them, and
    # the acknowledged sign-out's audit event records the same groups: what the
    # acknowledgement covered (CLIN-58).
    assert not set(detail["warning_checks"]) & set(detail["unverifiable_checks"])
    event = next(event for event in ok_json(signed, "audit")["events"] if event["action"] == "sign_out")
    assert event["after"]["qc_failed"] == detail["failed_checks"]
    assert event["after"]["qc_unverifiable"] == detail["unverifiable_checks"]
    assert event["after"]["nipt_failed_checks"] == detail["nipt_failed_checks"]
    assert event["after"]["qc_warnings"] == detail["warning_checks"]
    # And the fingerprint of the concerns each dialog showed, which its acknowledgement
    # carried and covered (TRACE-19).
    assert event["after"]["qc_concerns_fingerprint"] == detail["concerns_fingerprint"]
    assert event["after"]["drift_concerns_fingerprint"] == drift["concerns_fingerprint"]


# ------------------------------------------------------------------ the signed record


def test_the_record_holds_the_nipt_results_the_page_showed(signed) -> None:
    nipt = _nipt(signed)
    summary = ok_json(signed, "summary")
    assert nipt["samples"]["father"] == signed["ids"]["FATHER_NIPT"]
    assert nipt["samples"]["maternal_plasma"] == signed["ids"]["CFDNA_NIPT"]
    for field in ("ff", "ff_computed", "ff_median", "ci_low", "ci_high", "n_sites", "low_confidence"):
        assert nipt["fetal_fraction"][field] == summary["fetal_fraction"][field], field
    assert nipt["fetal_fraction"]["status"] == summary["qc"]["fetal_fraction_status"]
    assert nipt["category_counts"] == {str(key): value for key, value in summary["category_counts"].items()}
    assert nipt["filter_counts"] == summary["filter_counts"]
    checks = nipt["checks"]
    assert (checks["paternity"]["status"], checks["paternity"]["message"]) == (
        summary["qc"]["paternity"]["status"],
        summary["qc"]["paternity"]["message"],
    )
    assert (checks["fetal_sex"]["call"], checks["fetal_sex"]["status"]) == ("indeterminate", "warn")
    assert (checks["plasma_sample"]["status"], checks["plasma_sample"]["message"]) == (
        summary["qc"]["plasma_profile_status"],
        summary["qc"]["plasma_profile_message"],
    )
    # No per-target coverage table in the demo.
    assert nipt["target_coverage"] is None and nipt["inputs"]["target_tables"] == []
    assert nipt["model"]["reference"].startswith("R NIPT-M v0.5.1")
    # The variant summary's NIPT-M statistics are descriptive: the record does not hold them.
    assert not {"groups", "chromosomes", "caller_filters", "father_genotypes"} & set(nipt)


def test_the_record_holds_the_artifact_list_the_analysis_applied(signed) -> None:
    from backend.app.services.hash_chain import canonical_hash

    ok_json(signed, "artifact_added")
    # The list of the run's own assay panel, as the admin API read it back in the same run.
    listed = sorted(entry["variant_id"] for entry in ok_json(signed, "artifacts"))
    assert listed == [ARTIFACT]
    frozen = _nipt(signed)["inputs"]["artifact_list"]
    assert frozen["assay_key"] == signed["ids"]["panel"]
    assert (frozen["entries"], frozen["digest"]) == (len(listed), canonical_hash(listed))
    # The plasma carries the listed allele: the analysis left it out, as its funnel counts it,
    # and the family's annotation protects none of it.
    assert frozen["removed"] == ok_json(signed, "summary")["filter_counts"]["failed_artifact"] == 1
    assert frozen["kept"] == []


def test_a_reader_is_refused_the_nipt_sign_out(signed) -> None:
    # The family reads as read-only to them: what the NIPT page and the report disable the
    # sign-out by (SEC-1).
    assert ok_json(signed, "reader_family")["read_only"] is True
    refused = signed["responses"]["reader_signout"]
    assert (refused["status"], refused["json"]) == (403, {"detail": READ_ONLY}), refused["text"]
    # Nothing signed: the family holds the analyst's versions only.
    assert signed["versions_after_reader"] == ok_json(signed, "signout")["version"]


def test_the_run_leaves_no_artifact_behind(signed) -> None:
    assert ok_json(signed, "artifact_deleted") == {"deleted": True}
    assert ok_json(signed, "artifacts_after") == []


def test_each_reported_variant_is_frozen_as_the_nipt_variant_list_classifies_it(signed) -> None:
    frozen = {entry["variant_id"]: entry for entry in _nipt(signed)["variants"]}
    assert set(frozen) == {DE_NOVO, PATERNAL}
    listed = {variant["_id"]: variant["nipt"] for variant in ok_json(signed, "variants")["variants"]}
    for variant_id, entry in frozen.items():
        for field in (
            "category", "maternal_state", "fetal_inheritance", "confidence", "observed_vaf", "expected_vaf",
            "paternal_transmission_probability", "maternal_allele_probability", "fetal_hom_alt_probability",
        ):
            assert entry[field] == listed[variant_id][field], (variant_id, field)
        assert entry["flags"] == listed[variant_id]["flags"]
    assert frozen[DE_NOVO]["category"] == 1 and frozen[PATERNAL]["category"] == 7
    # The de novo candidate with its triage, as the de novo view gives it; the paternal allele
    # is no candidate.
    [candidate] = ok_json(signed, "de_novo_view")["variants"]
    assert frozen[DE_NOVO]["de_novo"]["label"] == candidate["nipt"]["de_novo"]["label"] == "high"
    assert frozen[DE_NOVO]["de_novo"]["score"] == candidate["nipt"]["de_novo"]["score"]
    assert frozen[PATERNAL]["de_novo"] is None
    # The reported artifact: on the run's artifact list, so not classified, and said why.
    assert _nipt(signed)["not_classified"] == [{"variant_id": ARTIFACT, "reason": "artifact_list"}]


# ------------------------------------------------- the NIPT page's reported view (UI-4)


def test_the_nipt_page_listed_what_the_sign_out_froze(signed) -> None:
    # "Show reported": the review tag report and no other filter, read before the sign-out.
    reported = ok_json(signed, "reported_view")
    nipt = _nipt(signed)
    listed = {variant["_id"]: variant for variant in reported["variants"]}
    frozen = {entry["variant_id"]: entry for entry in nipt["variants"]}
    assert set(listed) == set(frozen) == {DE_NOVO, PATERNAL}
    assert reported["total"] == len(frozen)
    for variant_id, entry in frozen.items():
        assert "report" in listed[variant_id]["review"]["tags"]
        for field in ("category", "maternal_state", "fetal_inheritance", "confidence", "observed_vaf", "expected_vaf"):
            assert entry[field] == listed[variant_id]["nipt"][field], (variant_id, field)
    # The one the analysis does not classify: named with the record's reason, not left out.
    assert reported["not_classified"] == nipt["not_classified"] == [{"variant_id": ARTIFACT, "reason": "artifact_list"}]


def test_the_review_tag_filter_keeps_and_leaves_out_exactly_the_tagged_variants(signed) -> None:
    # Within a view: the reported de novo candidate alone.
    [candidate] = ok_json(signed, "reported_de_novo_view")["variants"]
    assert candidate["_id"] == DE_NOVO
    # Without the reported variants: every other variant the list held before they were tagged.
    every = {variant["_id"] for variant in ok_json(signed, "variants")["variants"]}
    without = ok_json(signed, "without_reported")
    assert {variant["_id"] for variant in without["variants"]} == every - {DE_NOVO, PATERNAL}
    assert without["total"] == len(every) - 2
    assert without["not_classified"] == []
    # A tag no review holds selects nothing.
    unheld = ok_json(signed, "unheld_tag_view")
    assert (unheld["total"], unheld["variants"], unheld["not_classified"]) == (0, [], [])


def test_the_record_verifies_and_still_matches_right_after_it_was_signed(signed) -> None:
    detail = ok_json(signed, "detail")
    assert detail["verified"] is True
    assert detail["report_code"] == ok_json(signed, "signout")["report_code"]
    assert ok_json(signed, "chain")["verified"] is True
    check = ok_json(signed, "check")
    assert check["matches"] is True and check["changed_sections"] == [], check
    assert [gap for gap in check["not_captured"] if gap["section"] == "nipt"] == []
    # The sign-out's audit event names the NIPT checks the acknowledgement covered: none.
    # Most recent first: the sign-out just made.
    event = next(event for event in ok_json(signed, "audit")["events"] if event["action"] == "sign_out")
    assert event["after"]["version"] == detail["version"]
    assert event["after"]["nipt_failed_checks"] == []


def test_the_record_freezes_the_coverage_notes_the_nipt_page_showed(signed) -> None:
    # Without a per-target table the NIPT page's coverage card, which asks for no gene or panel
    # when it opens, reads the plasma's coverage tracks over the family's ROI and names its
    # coverage BED, which declares nothing, as not read as depth. The record freezes the notes
    # word for word, with what the check ran for (the owner's decision of 2026-10-28).
    coverage = ok_json(signed, "coverage")
    plasma = signed["ids"]["CFDNA_NIPT"]
    assert coverage["coverage_notes"] == [
        f"{plasma}: the coverage BED does not declare whether it holds read depth or a log2 ratio; it is not "
        "read as depth."
    ]
    assert coverage["target_region_count"] == 1
    frozen = _nipt(signed)["coverage_notes"]
    assert frozen == {
        "gene": None,
        "panel": None,
        "regions": [
            {"label": region["label"], "chrom": region["chr"], "start": region["start"], "end": region["end"]}
            for region in coverage["per_region"]
        ],
        "notes": coverage["coverage_notes"],
    }
    assert [(region["chrom"], region["start"], region["end"]) for region in frozen["regions"]] == [("1", 1000, 20000)]


MATERNAL = "1-3001-A-G"  # GENE_RECESS, category 3: the maternal allele of the demo's compound het


async def _collect_pair(root: Path, family_id: str) -> dict:
    """Import the copy, tag the demo's compound heterozygote for the report on its pair review
    alone, read what the NIPT page and the live report list, sign out and read the record."""
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    out: dict = {"import": await import_demo(root), "family": family_id, "responses": {}}
    r = out["responses"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"
        r["family"] = capture(await ac.get(family))
        # GENE_RECESS's paternal and maternal alleles as a compound-het pair, the pair review
        # tagged report and neither variant's own review: only the API sets a pair's tags.
        r["pair_review"] = capture(
            await ac.put(
                f"{family}/small-variants/{PATERNAL}/review",
                json={"compound_het": {"partner_variant_id": MATERNAL, "tags": ["report"]}},
            )
        )
        nipt_variants = f"{family}/nipt/variants"
        # "Show reported", its recessive view, and the count beside the button.
        r["reported_view"] = capture(await ac.get(nipt_variants, params={"review_tag": "report", "page_size": 500}))
        r["reported_recessive_view"] = capture(
            await ac.get(nipt_variants, params={"review_tag": "report", "inheritance": "recessive_at_risk"})
        )
        r["review_summary"] = capture(await ac.get(f"{family}/small-variant-review-summary"))
        # The live report's list of the reported small variants.
        r["live_report"] = capture(await ac.get(f"{family}/small-variants", params={"review_tag": "report"}))
        # Through the dialogs, each acknowledgement with the fingerprint of what its refusal
        # listed (TRACE-19).
        r["signout"] = capture(await _harness.sign_out(ac, f"{family}/report/sign-out", SIGN_OUT_BODY))
        version = (r["signout"]["json"] or {}).get("version")
        r["detail"] = capture(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["check"] = capture(await ac.get(f"{family}/report/sign-out-check"))
    return out


@pytest.fixture(scope="module")
def pair_signed(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    # As a NIPT PED usually has it: the fetus's sex and status unknown.
    copy = prepare_copy(tmp_path_factory.mktemp("nipt_pair_record"), fetus_status="0")
    root = Path(copy["root"])
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect_pair(root, copy["FAM_NIPT_DEMO"]))
    assert out["import"]["completed"] is True, out["import"]
    out["ids"] = copy
    return out


def _pair_snapshot(pair_signed: dict) -> dict:
    return ok_json(pair_signed, "detail")["snapshot"]


def test_a_nipt_fetus_of_unknown_status_is_imported_as_an_embryo(pair_signed) -> None:
    # The import reads a child of unknown sex whose status is unknown as an embryo, and the NIPT
    # trio takes that embryo for the fetus. No member is affected.
    members = {member["sample_id"]: member for member in ok_json(pair_signed, "family")["members"]}
    fetus = members[pair_signed["ids"]["FETUS_NIPT"]]
    assert (fetus["role"], fetus["affected"]) == ("embryo", False)
    assert not any(member["affected"] for member in members.values())


def test_a_nipt_record_holds_no_pgt_section_for_its_fetus(pair_signed) -> None:
    # The fetus has no sequencing column, so no haplotype blocks: the record holds no PGT
    # analysis of it, a section of another application.
    snapshot = _pair_snapshot(pair_signed)
    assert snapshot["sample_qc"]["application"] == "nipt"
    assert "nipt" in snapshot
    assert "pgt_analysis" not in snapshot
    check = ok_json(pair_signed, "check")
    assert (check["matches"], check["changed_sections"]) == (True, []), check


def test_a_pair_tagged_for_the_report_is_frozen_as_the_nipt_page_listed_it(pair_signed) -> None:
    ok_json(pair_signed, "pair_review")
    reported = ok_json(pair_signed, "reported_view")
    listed = {variant["_id"]: variant for variant in reported["variants"]}
    assert set(listed) == {PATERNAL, MATERNAL}
    # The report tag is the pair's: neither variant's own review holds one.
    for variant in listed.values():
        assert variant["review"]["tags"] == []
        assert variant["review"]["compound_het"]["tags"] == ["report"]
    snapshot = _pair_snapshot(pair_signed)
    # The NIPT section freezes each as "Show reported" classified it.
    frozen = {entry["variant_id"]: entry for entry in snapshot["nipt"]["variants"]}
    assert set(frozen) == set(listed)
    for variant_id, entry in frozen.items():
        for field in ("category", "maternal_state", "fetal_inheritance", "confidence", "observed_vaf", "expected_vaf"):
            assert entry[field] == listed[variant_id]["nipt"][field], (variant_id, field)
    assert (frozen[PATERNAL]["category"], frozen[MATERNAL]["category"]) == (7, 3)
    assert snapshot["nipt"]["not_classified"] == []
    # And so do the reported small variants, each with the pair review that reports it, and
    # their descriptions.
    reported_variants = {entry["variant_id"]: entry for entry in snapshot["reported_variants"]}
    assert set(reported_variants) == {PATERNAL, MATERNAL}
    assert reported_variants[PATERNAL]["tags"] == []
    assert reported_variants[PATERNAL]["compound_het"] == {
        "partner_variant_ids": [MATERNAL],
        "gene": "GENE_RECESS",
        "classification": None,
        "tags": ["report"],
        "note": None,
        "phase_status": "unknown",
    }
    assert reported_variants[MATERNAL]["compound_het"]["partner_variant_ids"] == [PATERNAL]
    assert {entry["variant_id"] for entry in snapshot["variant_descriptions"]["small"]} == {PATERNAL, MATERNAL}


def test_the_recessive_risk_frozen_is_the_one_the_reported_view_computes(pair_signed) -> None:
    # Both parents carry an allele of GENE_RECESS among the reported variants: the record's
    # recessive fetal risk is the one the NIPT page computes over "Show reported".
    [gene] = ok_json(pair_signed, "reported_recessive_view")["recessive_genes"]
    assert (gene["gene"], gene["maternal_variant_id"], gene["paternal_variant_id"]) == ("GENE_RECESS", MATERNAL, PATERNAL)
    [frozen] = _pair_snapshot(pair_signed)["nipt"]["recessive_genes"]
    assert (frozen["gene"], frozen["maternal_variant_id"], frozen["paternal_variant_id"]) == (
        "GENE_RECESS",
        MATERNAL,
        PATERNAL,
    )
    assert frozen["risk"] == pytest.approx(gene["risk"])
    # About 86%, as the demo's README works it out.
    assert frozen["risk"] == pytest.approx(0.86, abs=0.05)


def test_the_live_report_and_the_reported_count_name_what_was_frozen(pair_signed) -> None:
    live = {variant["_id"] for variant in ok_json(pair_signed, "live_report")["variants"]}
    frozen = {entry["variant_id"] for entry in _pair_snapshot(pair_signed)["reported_variants"]}
    assert live == frozen == {PATERNAL, MATERNAL}
    assert ok_json(pair_signed, "review_summary")["tag_counts"]["report"] == 2
