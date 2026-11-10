"""A PGT family's signed record holds the embryo calls the family page shows (REG-2).

The owner decided on 2026-10-14 that the signed record freezes the PGT analysis with its
per-embryo results. Over a synthetic PGT family imported from a package (an affected father,
the mother, their affected child as the index and two embryos, a phased GLIMPSE2 family VCF on
chr1, the inheritance model AD and an ROI on chr1), through the API, this:

* reads the embryo calls the family page shows (``GET /families/{id}/pgt-analysis``): the
  disease haplotype the affected father and index share, the embryo that carries it at risk,
  the other unaffected, each with the homologs it carries at the ROI and the phasing QC the
  ROI marker review counts over the window the call reads (the ROI and 1 Mb on each side), and
  the haplotype file the calls were read from;
* reads each embryo's QC verdict against the thresholds (``pgt_embryo_qc``): twenty informative
  sites, and three markers on the father's side left of the ROI and four right of it, below the
  provisional thresholds, so a warning beside a call that stands; and the thresholds in force
  with the analysis's rules;
* signs the family out and reads the record back: it holds those calls as served, verifies,
  and still matches right after it was signed, with nothing named as not captured;
* answers a user outside the family's projects with the 404 of an unknown family.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._pgt_package import SIGN_OUT_BODY, write_pgt_package

pytestmark = pytest.mark.integration


async def _outsider_client(transport, run: str):
    """A user who may open no project: the family is outside their projects."""
    from httpx import AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import get_password_hash

    email = f"pgt-record-viewer-{run}@example.org"
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
            {"username": f"pgt-record-viewer-{run}", "hashed_password": get_password_hash(password), "email": email},
        )
        await session.commit()
    client = AsyncClient(transport=transport, base_url="http://e2e")
    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return client


async def _collect(root: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session, folder_path=str(root), project_id=project_id, dry_run=False, user=admin, conflict_mode="cancel"
        )
    out: dict = {
        "completed": result.completed,
        "error": result.error,
        "statuses": {dataset.dataset_type: dataset.status for dataset in result.datasets},
        "responses": {},
    }
    r = out["responses"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"
        r["family"] = capture(await ac.get(family))
        r["analysis"] = capture(await ac.get(f"{family}/pgt-analysis"))
        r["signout"] = capture(await _harness.sign_out(ac, f"{family}/report/sign-out", SIGN_OUT_BODY))
        version = (r["signout"]["json"] or {}).get("version")
        r["detail"] = capture(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["check"] = capture(await ac.get(f"{family}/report/sign-out-check"))
        r["analysis_after"] = capture(await ac.get(f"{family}/pgt-analysis"))
        r["chain"] = capture(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
        )

        outsider = await _outsider_client(transport, uuid4().hex[:10])
        try:
            r["outsider"] = capture(await outsider.get(f"{family}/pgt-analysis"))
            r["outsider_unknown"] = capture(await outsider.get(f"/api/families/NO_SUCH_{family_id}/pgt-analysis"))
        finally:
            await outsider.aclose()
    return out


@pytest.fixture(scope="module")
def pgt(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_pgt_record")
    family_id = f"FAM_PGT_{uuid4().hex[:8]}"
    names = write_pgt_package(base / family_id, family_id)
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    out = _harness.run_async(lambda: _collect(base / family_id, family_id))
    out["family_id"] = family_id
    out["names"] = names
    assert out["completed"] is True, out
    assert out["statuses"].get("haplotypes") == "imported", out
    return out


def _ok(pgt: dict, key: str) -> dict:
    resp = pgt["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _embryos(analysis: dict) -> dict[str, dict]:
    return {embryo["sample"]: embryo for embryo in analysis["embryos"]}


def test_the_family_page_reads_each_embryo_call_from_the_stored_blocks(pgt) -> None:
    names = pgt["names"]
    family = _ok(pgt, "family")
    assert (family["roi"]["chr"], family["roi"]["start"], family["roi"]["end"]) == ("1", 1_004_500, 1_010_500)
    analysis = _ok(pgt, "analysis")

    assert analysis["region_of_interest"]["chrom"] == "1"
    assert analysis["inheritance_model"] == {"recorded": "AD", "applied": "AD", "mode": "dominant"}
    # The affected father and the affected index share the father's homolog: the disease haplotype.
    assert analysis["disease_haplotype_resolved"] is True
    [disease] = analysis["disease_haplotypes"]
    assert (disease["origin"], disease["kind"]) == ("paternal", "dominant")

    embryos = _embryos(analysis)
    assert set(embryos) == {names["E_RISK"], names["E_CLEAR"]}
    risk, clear = embryos[names["E_RISK"]], embryos[names["E_CLEAR"]]
    assert risk["call"] == "affected_or_at_risk" and clear["call"] == "unaffected_non_carrier"
    # What each call rests on: the disease homolog seen at the ROI in one, the other homolog
    # of the father across the ROI in the other.
    assert {(homolog["origin"], homolog["homolog"]) for homolog in risk["inherited"]} >= {("paternal", disease["homolog"])}
    clear_paternal = [homolog for homolog in clear["inherited"] if homolog["origin"] == "paternal"]
    assert clear_paternal and all(homolog["homolog"] != disease["homolog"] for homolog in clear_paternal)
    assert risk["roi_not_covered"] is False and clear["roi_not_covered"] is False
    # The phasing QC over the window the call reads, the ROI and 1 Mb on each side: all twenty
    # sites, none a Mendelian error; half read the homolog from the father, half from the mother.
    # Left of the ROI (1,000,000 to 1,004,000) three read the father's and two the mother's,
    # right of it (1,011,000 to 1,019,000) four the father's and five the mother's; the six in
    # the ROI are on neither flank.
    for embryo in (risk, clear):
        assert embryo["qc"]["informative_sites"] == 20 and embryo["qc"]["mendel_errors"] == 0, embryo
        assert (embryo["qc"]["paternal_markers"], embryo["qc"]["maternal_markers"]) == (10, 10), embryo
        assert (
            embryo["qc"]["paternal_markers_left"],
            embryo["qc"]["paternal_markers_right"],
            embryo["qc"]["maternal_markers_left"],
            embryo["qc"]["maternal_markers_right"],
        ) == (3, 4, 2, 5), embryo
    # The inputs: the haplotype file each member's blocks were built from, and a digest.
    sources = {source["sample"]: source for source in analysis["inputs"]["haplotype_sources"]}
    assert set(sources) == set(names.values())
    assert all(source["filename"] == "family.glimpse2.vcf" and source["source"] == "glimpse2" for source in sources.values())
    assert analysis["inputs"]["blocks_read"] > 0 and len(analysis["inputs"]["blocks_digest"]) == 64


def test_the_signed_record_holds_the_calls_the_family_page_shows(pgt) -> None:
    detail = _ok(pgt, "detail")
    assert detail["verified"] is True
    assert detail["snapshot"]["pgt_analysis"] == _ok(pgt, "analysis")
    # The record read back is the one the family page still shows, and the check agrees.
    assert _ok(pgt, "analysis_after") == detail["snapshot"]["pgt_analysis"]
    assert [gap for gap in detail["not_captured"] if gap["section"] == "pgt_analysis"] == []
    assert _ok(pgt, "chain")["verified"] is True
    check = _ok(pgt, "check")
    assert check["matches"] is True and check["changed_sections"] == [], check


def test_each_embryo_call_carries_its_qc_verdict_and_the_record_freezes_it(pgt) -> None:
    # The owner's decision of 2026-10-20: a call whose phasing QC at the ROI falls below a
    # threshold gets a warning beside it, and stands.
    analysis = _ok(pgt, "analysis")
    assert analysis["rules"]["qc_thresholds"] == {
        "min_informative_sites": 50,
        "max_mendel_rate": 0.02,
        "min_markers_per_flank": 6,
        "max_marker_discordance": 0.1,
        "min_discordance_markers": 20,
    }
    embryos = _embryos(analysis)
    names = pgt["names"]
    assert (embryos[names["E_RISK"]]["call"], embryos[names["E_CLEAR"]]["call"]) == (
        "affected_or_at_risk",
        "unaffected_non_carrier",
    )
    for embryo in embryos.values():
        # Twenty sites in the window, none a Mendelian error, three read on the father's side
        # left of the ROI and four right of it: the dominant haplotype is his, so his side
        # alone is checked, on each flank.
        assert embryo["qc_verdict"] == {
            "status": "warning",
            "sides": ["paternal"],
            "findings": [
                {"measure": "informative_sites", "value": 20, "threshold": 50, "flank": None},
                {"measure": "paternal_markers", "value": 3, "threshold": 6, "flank": "left"},
                {"measure": "paternal_markers", "value": 4, "threshold": 6, "flank": "right"},
            ],
        }, embryo
        # Seven markers on the father's side, under the 20 the mixed-homolog gate judges.
        assert (embryo["mixed_homologs"], embryo["qc"]["paternal_discordance"]) == ([], 0.0), embryo
    # The signed record freezes the verdicts and the thresholds with the embryos' results.
    held = _ok(pgt, "detail")["snapshot"]["pgt_analysis"]
    assert [embryo["qc_verdict"] for embryo in held["embryos"]] == [
        embryo["qc_verdict"] for embryo in analysis["embryos"]
    ]
    assert held["rules"]["qc_thresholds"] == analysis["rules"]["qc_thresholds"]
    assert [embryo["mixed_homologs"] for embryo in held["embryos"]] == [
        embryo["mixed_homologs"] for embryo in analysis["embryos"]
    ]


def test_a_user_outside_the_familys_projects_gets_the_404_of_an_unknown_family(pgt) -> None:
    hidden, unknown = pgt["responses"]["outsider"], pgt["responses"]["outsider_unknown"]
    assert hidden["status"] == unknown["status"] == 404, (hidden, unknown)
