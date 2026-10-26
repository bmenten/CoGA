"""The signed analysis record, end to end (owner decisions of 2026-10-14).

A sign-out is the analyst's analytical sign-off; the EPD's report of the medical sign-off
refers to the frozen record by its report code. Over the golden trio, through the API, this:

* applies a gene panel to the family's searches, edits it to a second version and applies
  that too: each version goes on the family's clinical audit trail once;
* tags a small variant and a structural variant for the report and signs the family out;
* reads the signed record back: each reported variant's description as reviewed (gene,
  transcript, consequence, each sample's genotype), both panel versions, the reference-data
  releases, the TRGT calls with their status and thresholds, the Paraphase results, the
  sentence that says what the record is, a verified content hash and a verified chain;
* finds the record by its report code (as typed back, lower case, spaced), refuses a code
  with two symbols swapped (422), and answers an unknown code and a code whose family is
  outside the caller's projects with the same 404;
* signs out once more as a build from before these sections (their keys left out) and shows
  that record still verifies, reads and is found by its code, and that the check names the
  sections it predates as not compared.

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
SMALL_VARIANT = "1-2000-C-T"  # GENE_PATH, stop_gained, de novo in the proband
STRUCTURAL_VARIANT = "1-7000-12000-DEL---"  # the NeedlR deletion of BRCA2
NEW_SECTIONS = ("variant_descriptions", "gene_panels", "reference_data", "repeat_expansions", "paraphase")
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: the reported SV holds no CNV scoring",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: synthetic genotypes",
}


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _swap_two_symbols(code: str) -> str:
    """The code with two neighbouring, different symbols swapped (not across a hyphen)."""
    for index in range(len(code) - 1):
        pair = code[index : index + 2]
        if "-" not in pair and pair[0] != pair[1]:
            return code[:index] + pair[::-1] + code[index + 2 :]
    raise AssertionError(f"no swappable pair in {code}")  # pragma: no cover


async def _viewer_client(transport, run: str):
    """A user who may open no project: the golden trio is outside their projects."""
    from httpx import AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import get_password_hash

    email = f"signed-record-viewer-{run}@example.org"
    # Made at run time: the repository holds no password-shaped literal.
    password = uuid4().hex
    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                """
                INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                VALUES (:username, :hashed_password, 'viewer', :email, '{}'::jsonb, now())
                """
            ),
            {"username": f"signed-record-viewer-{run}", "hashed_password": get_password_hash(password), "email": email},
        )
        await session.commit()
    client = AsyncClient(transport=transport, base_url="http://e2e")
    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return client


async def _collect(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services import report_signout_service as rss
    from backend.app.services.report_code import report_code
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    out: dict = {"facts": facts, "responses": {}}
    r = out["responses"]
    run = uuid4().hex[:10]
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{FAMILY}"

        # A panel applied, applied again, edited to version 2 and applied by another search.
        created = await ac.post("/api/panels/", json={"name": f"e2e-signed-record-{run}", "genes": ["GENE_PATH"]})
        r["panel_created"] = _cap(created)
        panel_id = created.json()["panel"]["_id"]
        out["panel_id"] = panel_id
        r["search_v1"] = _cap(await ac.get(f"{family}/small-variants", params={"panel_id": panel_id}))
        r["search_v1_again"] = _cap(
            await ac.get(f"{family}/small-variants", params={"panel_id": panel_id, "count_only": "true"})
        )
        r["panel_edited"] = _cap(await ac.put(f"/api/panels/{panel_id}", json={"genes": ["GENE_PATH", "BRCA2"]}))
        r["search_v2"] = _cap(await ac.get(f"{family}/structural-variants", params={"panel_id": panel_id}))
        r["audit"] = _cap(await ac.get(f"{family}/clinical-audit", params={"limit": 500}))

        # Two variants for the report: a small variant classified, an SV tagged.
        r["review_small"] = _cap(
            await ac.put(
                f"{family}/small-variants/{SMALL_VARIANT}/review",
                json={
                    "tags": ["report", "acmg_class_5"],
                    "acmg": {
                        "criteria": [
                            {"code": "PVS1", "strength": "very_strong", "accepted": True},
                            {"code": "PS2", "strength": "strong", "accepted": True},
                        ]
                    },
                },
            )
        )
        r["review_sv"] = _cap(
            await ac.put(f"{family}/structural-variants/{STRUCTURAL_VARIANT}/review", json={"tags": ["report"]})
        )

        r["signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        signed = r["signout"]["json"] or {}
        version = signed.get("version")
        code = signed.get("report_code")
        out["expected_code"] = report_code(FAMILY, version, signed.get("content_hash"))
        r["detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["list"] = _cap(await ac.get(f"{family}/report/sign-outs"))
        r["check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["chain"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": FAMILY})
        )

        # The code the EPD report quotes finds the record again.
        r["lookup"] = _cap(await ac.get(f"/api/report-codes/{code}"))
        r["lookup_typed"] = _cap(await ac.get(f"/api/report-codes/{code.lower().replace('-', ' ')}"))
        r["lookup_typo"] = _cap(await ac.get(f"/api/report-codes/{_swap_two_symbols(code)}"))
        unknown = report_code(FAMILY, 9999, "0" * 64)
        r["lookup_unknown"] = _cap(await ac.get(f"/api/report-codes/{unknown}"))
        viewer = await _viewer_client(transport, run)
        try:
            r["lookup_hidden"] = _cap(await viewer.get(f"/api/report-codes/{code}"))
            r["lookup_hidden_unknown"] = _cap(await viewer.get(f"/api/report-codes/{unknown}"))
        finally:
            await viewer.aclose()

        # A record signed by a build from before these sections: their keys left out.
        real_build = rss.build_report_snapshot

        async def _old_build(*args, **kwargs):
            body = await real_build(*args, **kwargs)
            for key in (*NEW_SECTIONS, "record_statement"):
                body.pop(key, None)
            return body

        mp = pytest.MonkeyPatch()
        mp.setattr(rss, "build_report_snapshot", _old_build)
        try:
            r["old_signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        finally:
            mp.undo()
        old = r["old_signout"]["json"] or {}
        r["old_detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{old.get('version')}"))
        r["old_check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["old_lookup"] = _cap(await ac.get(f"/api/report-codes/{old.get('report_code')}"))
        r["old_chain"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": FAMILY})
        )
        # The family ends on a record of today's build, for the modules that run after this one,
        # and with nothing tagged for the report: the golden trio's database is shared by the
        # modules of a run, and a module that lists the reported variants (as
        # test_e2e_report_lists_complete does) would otherwise find these two as well.
        r["final_signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        r["untag_small"] = _cap(
            await ac.put(f"{family}/small-variants/{SMALL_VARIANT}/review", json={"tags": [], "note": None})
        )
        r["untag_sv"] = _cap(
            await ac.put(f"{family}/structural-variants/{STRUCTURAL_VARIANT}/review", json={"tags": [], "note": None})
        )
        r["panel_removed"] = _cap(await ac.delete(f"/api/panels/{panel_id}"))
    return out


@pytest.fixture(scope="module")
def signed(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_signed_record") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def _ok(signed: dict, key: str) -> dict:
    resp = signed["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _snapshot(signed: dict) -> dict:
    return _ok(signed, "detail")["snapshot"]


# ------------------------------------------------------------------- the panel searches


def test_the_module_leaves_nothing_tagged_for_the_report(signed) -> None:
    # Run before another module of the same run, its reported variants would be that module's too.
    assert signed["responses"]["untag_small"]["status"] == 200, signed["responses"]["untag_small"]
    assert signed["responses"]["untag_sv"]["status"] == 200, signed["responses"]["untag_sv"]


def test_each_panel_version_a_search_applied_is_on_the_clinical_audit_trail_once(signed) -> None:
    for key in ("panel_created",):
        assert signed["responses"][key]["status"] == 201, signed["responses"][key]["text"]
    for key in ("search_v1", "search_v1_again", "panel_edited", "search_v2"):
        _ok(signed, key)
    panel_id = signed["panel_id"]
    events = [
        event
        for event in _ok(signed, "audit")["events"]
        if event["action"] == "panel_applied" and (event["after"] or {}).get("panel_id") == panel_id
    ]
    # Version 1 by the small-variant search (the second search recorded nothing), version 2
    # by the SV search; most recent first.
    assert [(event["after"]["version"], event["after"]["search"]) for event in events] == [
        (2, "structural_variants"),
        (1, "small_variants"),
    ]
    assert events[1]["summary"].startswith("Gene panel e2e-signed-record-")
    assert events[1]["after"]["gene_count"] == 1 and events[0]["after"]["gene_count"] == 2


# --------------------------------------------------------------------- the signed record


def test_the_sign_out_carries_a_report_code_made_from_the_record(signed) -> None:
    signout = _ok(signed, "signout")
    assert signout["report_code"] == signed["expected_code"]
    assert len(signout["report_code"]) <= 16
    detail = _ok(signed, "detail")
    assert detail["report_code"] == signout["report_code"]
    assert detail["verified"] is True
    listed = {entry["version"]: entry for entry in _ok(signed, "list")["signouts"]}
    assert listed[signout["version"]]["report_code"] == signout["report_code"]


def test_the_record_says_what_it_is(signed) -> None:
    assert _snapshot(signed)["record_statement"] == (
        "This record is the analytical sign-off in CoGA, which closes and freezes the analysis; "
        "the medical sign-off of the patient report happens in the EPD, which refers to this "
        "record by its report code."
    )


def test_each_reported_variant_is_described_as_reviewed(signed) -> None:
    descriptions = _snapshot(signed)["variant_descriptions"]
    [small] = [entry for entry in descriptions["small"] if entry["variant_id"] == SMALL_VARIANT]
    assert (small["gene"], small["transcript"], small["consequence"], small["impact"]) == (
        "GENE_PATH",
        "ENST_GENE_PATH",
        "stop_gained",
        "HIGH",
    )
    assert (small["chrom"], small["ref"], small["alt"]) == ("1", "C", "T")
    genotypes = {genotype["sample"]: genotype for genotype in small["genotypes"]}
    assert {sample: genotype["gt"] for sample, genotype in genotypes.items()} == {
        "FATHER": "0/0",
        "MOTHER": "0/0",
        "PROBAND": "0/1",
    }
    assert genotypes["PROBAND"]["dp"] == 30 and genotypes["PROBAND"]["ad"] == [15, 15]
    [sv] = [entry for entry in descriptions["structural"] if entry["variant_id"] == STRUCTURAL_VARIANT]
    assert (sv["sv_type"], sv["copy_change"], sv["genes"], sv["source"]) == ("DEL", "loss", ["BRCA2"], "needlr")
    assert {genotype["sample"]: genotype["gt"] for genotype in sv["genotypes"]}["PROBAND"] == "0/1"


def test_the_record_names_each_panel_version_the_analysis_applied(signed) -> None:
    applied = [
        panel for panel in _snapshot(signed)["gene_panels"]["applied"] if panel["panel_id"] == signed["panel_id"]
    ]
    assert [(panel["version"], panel["gene_count"]) for panel in applied] == [(1, 1), (2, 2)]
    assert all(panel["name"].startswith("e2e-signed-record-") for panel in applied)
    assert all(panel["first_applied_by"] for panel in applied)


def test_the_record_names_the_reference_data_releases(signed) -> None:
    reference = _snapshot(signed)["reference_data"]
    genes = reference["gene_reference"]
    assert {"GENE_PATH", "BRCA2"} <= set(genes["genes"])
    # The e2e stack loads no gene reference and no CNV knowledgebase: said as such.
    assert set(genes["not_in_reference"]) <= set(genes["genes"])
    assert "unavailable" not in genes
    assert reference["clinical_cnvs"] is None or reference["clinical_cnvs"]["loaded_at"]


def test_the_record_names_the_hpo_release_the_matching_ran_on(signed) -> None:
    # The stack's HPO terms carry the shipped release, as an import of the vendored ontology
    # writes it: a term without one made every sign-out record "release not recorded" (ENG-16).
    from backend.app.core.config import HPO_PINNED_RELEASE

    [hpo] = [module for module in _snapshot(signed)["modules"] if module["key"] == "hpo"]
    assert (hpo["version"], hpo["detail"]) == (HPO_PINNED_RELEASE, HPO_PINNED_RELEASE.rsplit("/", 1)[1])


def test_the_record_holds_each_repeat_call_with_its_status_and_thresholds(signed) -> None:
    repeats = _snapshot(signed)["repeat_expansions"]
    [htt] = [locus for locus in repeats["loci"] if locus["locus_id"] == "HD_HTT"]
    assert htt["thresholds"]["pathogenic_min"] == 36
    calls = {call["sample"]: call for call in htt["calls"]}
    assert calls["PROBAND"]["status"] == "pathogenic"
    assert 40 in [allele["repeat_count"] for allele in calls["PROBAND"]["alleles"]]
    assert calls["FATHER"]["status"] == "normal"
    assert repeats["catalogue"]["loci"] > 0 and len(repeats["catalogue"]["digest"]) == 64


def test_the_record_holds_the_paraphase_results(signed) -> None:
    paraphase = _snapshot(signed)["paraphase"]
    [gba] = [gene for gene in paraphase["genes"] if gene["gene"] == "GBA"]
    assert [result["sample"] for result in gba["samples"]] == ["PROBAND"]
    assert paraphase["catalogue"]["regions"] > 0


def test_the_record_verifies_and_still_matches_right_after_it_was_signed(signed) -> None:
    # Every new section reads back from JSONB as it was hashed.
    assert _ok(signed, "chain")["verified"] is True
    check = _ok(signed, "check")
    assert check["matches"] is True, check
    assert check["not_compared"] == []
    # Each section was read: none is named as not captured. (The e2e stack's HPO terms come
    # without an ontology release, which the modules name; that is not one of these.)
    assert [gap for gap in check["not_captured"] if gap["section"].split(".")[0] in NEW_SECTIONS] == []


# ----------------------------------------------------------------- the report-code lookup


def test_the_report_code_finds_the_record_as_typed_back(signed) -> None:
    signout = _ok(signed, "signout")
    for key in ("lookup", "lookup_typed"):
        found = _ok(signed, key)
        assert (found["family_id"], found["version"], found["report_code"]) == (
            FAMILY,
            signout["version"],
            signout["report_code"],
        )
        assert found["content_hash"] == signout["content_hash"]


def test_a_code_with_two_symbols_swapped_is_refused_as_a_typo(signed) -> None:
    resp = signed["responses"]["lookup_typo"]
    assert resp["status"] == 422, resp["text"]
    assert resp["json"]["detail"]["code"] == "report_code_invalid"


def test_an_unknown_code_and_one_outside_the_callers_projects_answer_alike(signed) -> None:
    unknown = signed["responses"]["lookup_unknown"]
    hidden = signed["responses"]["lookup_hidden"]
    hidden_unknown = signed["responses"]["lookup_hidden_unknown"]
    assert unknown["status"] == hidden["status"] == hidden_unknown["status"] == 404
    assert unknown["text"] == hidden["text"] == hidden_unknown["text"]


# ------------------------------------------------- a record from before these sections


def test_a_record_without_the_sections_still_verifies_reads_and_is_found(signed) -> None:
    old = _ok(signed, "old_signout")
    detail = _ok(signed, "old_detail")
    assert detail["verified"] is True
    assert not set(NEW_SECTIONS) & set(detail["snapshot"])
    assert "record_statement" not in detail["snapshot"]
    # Nothing it predates is called "not captured": the signed view says it is not held.
    assert [gap for gap in detail["not_captured"] if gap["section"].split(".")[0] in NEW_SECTIONS] == []
    assert detail["report_code"] == old["report_code"]
    assert _ok(signed, "old_lookup")["version"] == old["version"]
    assert _ok(signed, "old_chain")["verified"] is True
    # The check against it compares what it holds and names the sections it predates.
    check = _ok(signed, "old_check")
    assert check["version"] == old["version"]
    assert set(NEW_SECTIONS) <= set(check["not_compared"])
    assert check["matches"] is True, check
    final = _ok(signed, "final_signout")
    assert final["version"] == old["version"] + 1 and final["report_code"] != old["report_code"]
