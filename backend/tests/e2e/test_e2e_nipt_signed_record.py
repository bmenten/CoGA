"""A monogenic NIPT family's signed record, end to end (REG-2; owner decision of 2026-10-14).

The NIPT analysis is signed out through the family sign-out: one signed record per analysis,
which holds the NIPT results. Over the synthetic demo NIPT package (``demo/nipt_family``: a
joint VCF of a father and a maternal plasma at a fetal fraction of 12%, with a de novo
candidate and the eight categories), imported through the real package import, through the
API, this:

* reads the NIPT page's summary and variant list, which serve the verdicts and the
  classifications the record freezes;
* tags a de novo candidate and a paternal allele for the report and signs the family out, the
  drift and Sample QC gates acknowledged (the demo's paternity rests on too few sites, so the
  Sample QC gate holds; no NIPT quality check fails, and the 409 says so);
* reads the signed record back: the NIPT results as the page served them (the fetal fraction,
  the quality checks with their verdicts, each reported variant as the variant list
  classifies it, the de novo candidate's triage, the inputs), a verified content hash and
  chain, and a check that matches with nothing it does not compare;
* signs out once more as a build from before the NIPT section (its key left out) and shows
  that record still verifies and reads, and that the check names ``nipt`` as not compared.

The golden trio's records, a family that is no NIPT family, are checked by
``test_e2e_signed_analysis_record.py``: its check names nothing it does not compare.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_DEMO = Path(__file__).resolve().parents[3] / "demo" / "nipt_family"
# The committed package hardcodes these ids, and sample ids are unique across families: each
# run imports a copy with every id suffixed (as test_e2e_demo_smoke.py does).
_TOKENS = ("FAM_NIPT_DEMO", "FATHER_NIPT", "CFDNA_NIPT", "FETUS_NIPT")
DE_NOVO = "1-1001-A-G"  # GENE_DENOVO, category 1, in the fetal window
PATERNAL = "1-10000-A-G"  # a paternal allele the fetus inherited, category 7
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: the reported variants hold no ACMG classification",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the synthetic demo has too few paternal-informative sites",
}


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _import_demo(root: Path) -> dict:
    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="overwrite",
        )
    return {
        "completed": result.completed,
        "error": result.error,
        "dataset_status": {dataset.dataset_type: dataset.status for dataset in result.datasets},
    }


def _prepare_copy(parent: Path) -> dict[str, str]:
    """Copy the demo package under ``parent`` with every hardcoded id suffixed; the new ids."""
    suffix = uuid4().hex[:8]
    ids = {token: f"{token}_{suffix}" for token in _TOKENS}
    root = parent / ids["FAM_NIPT_DEMO"]
    shutil.copytree(_DEMO, root)
    for name in ("manifest.yaml", "nipt_trio.ped", "nipt_combined.vcf"):
        path = root / name
        content = path.read_text(encoding="utf-8")
        for old, new in ids.items():
            content = content.replace(old, new)
        path.write_text(content, encoding="utf-8")
    return {"root": str(root), **ids}


async def _collect(root: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services import report_signout_service as rss
    from backend.tests.e2e import _harness

    out: dict = {"import": await _import_demo(root), "family": family_id, "responses": {}}
    r = out["responses"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"

        # What the NIPT page shows.
        r["summary"] = _cap(await ac.get(f"{family}/nipt/summary"))
        r["variants"] = _cap(await ac.get(f"{family}/nipt/variants", params={"page_size": 500}))
        r["de_novo_view"] = _cap(await ac.get(f"{family}/nipt/variants", params={"inheritance": "de_novo"}))

        # Two variants for the report, from the NIPT page's list.
        for variant_id in (DE_NOVO, PATERNAL):
            r[f"review_{variant_id}"] = _cap(
                await ac.put(f"{family}/small-variants/{variant_id}/review", json={"tags": ["report"]})
            )

        # The gates, one by one: the drift of the unclassified reported variants, then the
        # Sample QC (the demo's paternity cannot be verified).
        r["refused_drift"] = _cap(await ac.post(f"{family}/report/sign-out", json={}))
        r["refused_qc"] = _cap(
            await ac.post(
                f"{family}/report/sign-out",
                json={key: SIGN_OUT_BODY[key] for key in ("acknowledge_drift", "drift_acknowledgement_reason")},
            )
        )
        r["signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        version = (r["signout"]["json"] or {}).get("version")
        r["detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["chain"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
        )
        r["audit"] = _cap(await ac.get(f"{family}/clinical-audit", params={"limit": 50}))

        # A record signed by a build from before the NIPT section: its key left out.
        real_build = rss.build_report_snapshot

        async def _old_build(*args, **kwargs):
            body = await real_build(*args, **kwargs)
            body.pop("nipt", None)
            return body

        mp = pytest.MonkeyPatch()
        mp.setattr(rss, "build_report_snapshot", _old_build)
        try:
            r["old_signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        finally:
            mp.undo()
        old_version = (r["old_signout"]["json"] or {}).get("version")
        r["old_detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{old_version}"))
        r["old_check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["old_chain"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
        )
        # The family ends on a record of today's build.
        r["final_signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
    return out


@pytest.fixture(scope="module")
def signed(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_DEMO / "manifest.yaml").exists():
        pytest.fail("the demo NIPT package is missing: it is committed (scripts/generate_nipt_demo.py rebuilds it)")
    copy = _prepare_copy(tmp_path_factory.mktemp("nipt_signed_record"))
    root = Path(copy["root"])
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root, copy["FAM_NIPT_DEMO"]))
    assert out["import"]["completed"] is True, out["import"]
    out["ids"] = copy
    return out


def _ok(signed: dict, key: str) -> dict:
    resp = signed["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _nipt(signed: dict) -> dict:
    return _ok(signed, "detail")["snapshot"]["nipt"]


# ------------------------------------------------------------------- the NIPT page


def test_the_nipt_page_serves_the_verdicts_the_record_freezes(signed) -> None:
    summary = _ok(signed, "summary")
    assert summary["fetal_fraction"]["ff_computed"] == pytest.approx(0.12, abs=0.01)
    qc = summary["qc"]
    assert qc["fetal_fraction_status"] == "pass"
    # No chrX site and no target table: neither signal tells the fetal sex, a warning.
    assert (qc["fetal_sex"]["call"], qc["fetal_sex"]["status"]) == ("indeterminate", "warn")
    assert qc["plasma_profile_status"] == "unknown"
    [candidate] = _ok(signed, "de_novo_view")["variants"]
    assert candidate["_id"] == DE_NOVO and candidate["nipt"]["de_novo"]["label"] == "high"


# ------------------------------------------------------------------------ the gates


def test_the_sample_qc_gate_holds_and_no_nipt_check_failed(signed) -> None:
    for variant_id in (DE_NOVO, PATERNAL):
        _ok(signed, f"review_{variant_id}")
    assert signed["responses"]["refused_drift"]["status"] == 409
    refused = signed["responses"]["refused_qc"]
    assert refused["status"] == 409, refused["text"]
    detail = refused["json"]["detail"]
    assert detail["gate"] == "sample_qc"
    assert any("paternity" in reason.lower() for reason in detail["unverifiable_checks"])
    # The NIPT page's checks passed, warned or could not run: none failed.
    assert detail["nipt_failed_checks"] == []


# ------------------------------------------------------------------ the signed record


def test_the_record_holds_the_nipt_results_the_page_showed(signed) -> None:
    nipt = _nipt(signed)
    summary = _ok(signed, "summary")
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
    # No per-target coverage table in the demo, no artifact listed.
    assert nipt["target_coverage"] is None and nipt["inputs"]["target_tables"] == []
    assert nipt["inputs"]["artifact_list"]["entries"] == 0
    assert nipt["model"]["reference"].startswith("R NIPT-M v0.5.1")


def test_each_reported_variant_is_frozen_as_the_nipt_variant_list_classifies_it(signed) -> None:
    frozen = {entry["variant_id"]: entry for entry in _nipt(signed)["variants"]}
    assert set(frozen) == {DE_NOVO, PATERNAL}
    listed = {variant["_id"]: variant["nipt"] for variant in _ok(signed, "variants")["variants"]}
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
    [candidate] = _ok(signed, "de_novo_view")["variants"]
    assert frozen[DE_NOVO]["de_novo"]["label"] == candidate["nipt"]["de_novo"]["label"] == "high"
    assert frozen[DE_NOVO]["de_novo"]["score"] == candidate["nipt"]["de_novo"]["score"]
    assert frozen[PATERNAL]["de_novo"] is None
    assert _nipt(signed)["not_classified"] == []


def test_the_record_verifies_and_still_matches_right_after_it_was_signed(signed) -> None:
    detail = _ok(signed, "detail")
    assert detail["verified"] is True
    assert detail["report_code"] == _ok(signed, "signout")["report_code"]
    assert _ok(signed, "chain")["verified"] is True
    check = _ok(signed, "check")
    assert check["matches"] is True, check
    assert check["not_compared"] == []
    assert [gap for gap in check["not_captured"] if gap["section"] == "nipt"] == []
    # The sign-out's audit event names the NIPT checks the acknowledgement covered: none.
    # Most recent first: the sign-out just made.
    event = next(event for event in _ok(signed, "audit")["events"] if event["action"] == "sign_out")
    assert event["after"]["version"] == detail["version"]
    assert event["after"]["nipt_failed_checks"] == []


# ------------------------------------------------ a record from before the NIPT section


def test_a_record_without_the_nipt_section_still_verifies_and_is_named_not_compared(signed) -> None:
    detail = _ok(signed, "old_detail")
    assert detail["verified"] is True
    assert "nipt" not in detail["snapshot"]
    assert [gap for gap in detail["not_captured"] if gap["section"] == "nipt"] == []
    assert _ok(signed, "old_chain")["verified"] is True
    check = _ok(signed, "old_check")
    assert check["version"] == _ok(signed, "old_signout")["version"]
    assert check["not_compared"] == ["nipt"]
    assert check["matches"] is True, check
    final = _ok(signed, "final_signout")
    assert final["version"] == check["version"] + 1
    assert "nipt" in final["snapshot"]
