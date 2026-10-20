"""A PGT family's signed record holds the embryo calls the family page shows (REG-2).

The owner decided on 2026-10-14 that the signed record freezes the PGT analysis with its
per-embryo results. Over a synthetic PGT family imported from a package (an affected father,
the mother, their affected child as the index and two embryos, a phased GLIMPSE2 family VCF on
chr1, the inheritance model AD and an ROI on chr1), through the API, this:

* reads the embryo calls the family page shows (``GET /families/{id}/pgt-analysis``): the
  disease haplotype the affected father and index share, the embryo that carries it at risk,
  the other unaffected, each with the homologs it carries at the ROI and the phasing QC the
  ROI marker review counts, and the haplotype file the calls were read from;
* reads each embryo's QC verdict against the thresholds (``pgt_embryo_qc``): six informative
  sites and three markers on the father's side, below the provisional thresholds, so a warning
  beside a call that stands; and the thresholds in force with the analysis's rules;
* signs the family out and reads the record back: it holds those calls as served, verifies,
  and still matches right after it was signed, with nothing named as not captured;
* signs out once more as a build from before the section (its key left out): that record
  verifies and reads, nothing it predates is "not captured", and the check names the
  section as not compared;
* signs out once more as a build from before the QC verdict (the section without it): that
  record verifies, reads and still matches, the verdict named as not compared;
* answers a user outside the family's projects with the 404 of an unknown family.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_FORMAT = "GT:DP:AD:AF:GP:PS"
# Between sites, so the ROI holds six of them whatever the bounds: 1,005,000 to 1,010,000.
ROI = "1:1004500-1010500"
SIGN_OUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: nothing reported",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: synthetic genotypes at twenty sites",
}


def _cell(gt: str) -> str:
    # A phased genotype with GLIMPSE2's posteriors and one phase set.
    return f"{gt}:30:15,15:0.5:0.9,0.05,0.05:1"


def _write_pgt_package(root: Path, family_id: str) -> dict[str, str]:
    """A PGT package: the father (affected), the mother, their affected child (the index)
    and two embryos. At even sites the father is heterozygous and the mother not, at odd
    sites the other way round, so each child's inherited homolog is read on both sides. The
    index and E_RISK inherit the father's homolog 1, E_CLEAR his homolog 0."""
    names = {role: f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "INDEX", "E_RISK", "E_CLEAR")}
    father, mother = names["FATHER"], names["MOTHER"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(
        "\n".join(
            [
                f"{family_id}\t{father}\t0\t0\t1\t2",
                f"{family_id}\t{mother}\t0\t0\t2\t1",
                f"{family_id}\t{names['INDEX']}\t{father}\t{mother}\t2\t2",
                f"{family_id}\t{names['E_RISK']}\t{father}\t{mother}\t2\t0",
                f"{family_id}\t{names['E_CLEAR']}\t{father}\t{mother}\t1\t0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        f"family_id: {family_id}\n"
        "ped: family.ped\n"
        f"roi: \"{ROI}\"\n"
        "metadata:\n"
        "  pgt:\n"
        "    inheritance_model: AD\n"
        "family:\n"
        "  members:\n"
        f"    {names['E_RISK']}: {{role: embryo}}\n"
        f"    {names['E_CLEAR']}: {{role: embryo}}\n"
        "datasets:\n"
        "  haplotypes:\n"
        "    family_vcf: haplotypes/family.glimpse2.vcf\n"
        "    source_format: glimpse2\n",
        encoding="utf-8",
    )
    columns = [names[role] for role in ("FATHER", "MOTHER", "INDEX", "E_RISK", "E_CLEAR")]
    header = [
        "##fileformat=VCFv4.2",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">',
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">',
        '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype posteriors">',
        '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">',
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", *columns]),
    ]
    rows = []
    for index in range(20):
        position = 1_000_000 + index * 1_000
        if index % 2 == 0:
            # The father's homolog 1 carries the ALT allele: the index and E_RISK have it.
            genotypes = ["0|1", "0|0", "1|0", "1|0", "0|0"]
        else:
            # The mother's homolog 1 carries it: E_RISK inherited it, the others her homolog 0.
            genotypes = ["0|0", "0|1", "0|0", "0|1", "0|0"]
        rows.append(
            "\t".join(["1", str(position), ".", "A", "G", "60", "PASS", ".", _FORMAT, *map(_cell, genotypes)])
        )
    vcf = root / "haplotypes" / "family.glimpse2.vcf"
    vcf.parent.mkdir(parents=True, exist_ok=True)
    vcf.write_text("\n".join(header + rows) + "\n", encoding="utf-8")
    return names


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


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _collect(root: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import report_signout_service as rss
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
        r["family"] = _cap(await ac.get(family))
        r["analysis"] = _cap(await ac.get(f"{family}/pgt-analysis"))
        r["signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        version = (r["signout"]["json"] or {}).get("version")
        r["detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["analysis_after"] = _cap(await ac.get(f"{family}/pgt-analysis"))
        r["chain"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
        )

        # A record signed by a build from before the section: its key left out.
        real_build = rss.build_report_snapshot

        async def _old_build(*args, **kwargs):
            body = await real_build(*args, **kwargs)
            body.pop("pgt_analysis", None)
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

        # A record signed by a build from before the embryos' QC verdict: the section without
        # each embryo's verdict and without the thresholds.
        async def _pre_verdict_build(*args, **kwargs):
            body = await real_build(*args, **kwargs)
            analysis = body.get("pgt_analysis")
            if isinstance(analysis, dict):
                for embryo in analysis.get("embryos") or []:
                    embryo.pop("qc_verdict", None)
                (analysis.get("rules") or {}).pop("qc_thresholds", None)
            return body

        mp = pytest.MonkeyPatch()
        mp.setattr(rss, "build_report_snapshot", _pre_verdict_build)
        try:
            r["pre_verdict_signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        finally:
            mp.undo()
        pre_verdict_version = (r["pre_verdict_signout"]["json"] or {}).get("version")
        r["pre_verdict_detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{pre_verdict_version}"))
        r["pre_verdict_check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
        r["pre_verdict_chain"] = _cap(
            await ac.get("/api/admin/integrity/verify", params={"table": "report_signouts", "family_id": family_id})
        )

        outsider = await _outsider_client(transport, uuid4().hex[:10])
        try:
            r["outsider"] = _cap(await outsider.get(f"{family}/pgt-analysis"))
            r["outsider_unknown"] = _cap(await outsider.get(f"/api/families/NO_SUCH_{family_id}/pgt-analysis"))
        finally:
            await outsider.aclose()
    return out


@pytest.fixture(scope="module")
def pgt(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_pgt_record")
    family_id = f"FAM_PGT_{uuid4().hex[:8]}"
    names = _write_pgt_package(base / family_id, family_id)
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
    # The phasing QC at the ROI: six sites (1,005,000 to 1,010,000), none a Mendelian error;
    # three of them read the homolog from the father, three from the mother.
    for embryo in (risk, clear):
        assert embryo["qc"]["informative_sites"] == 6 and embryo["qc"]["mendel_errors"] == 0, embryo
        assert (embryo["qc"]["paternal_markers"], embryo["qc"]["maternal_markers"]) == (3, 3), embryo
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
    assert check["matches"] is True, check
    assert check["not_compared"] == []


def test_a_pgt_record_signed_before_the_section_still_verifies_and_reads(pgt) -> None:
    old = _ok(pgt, "old_signout")
    detail = _ok(pgt, "old_detail")
    assert detail["verified"] is True and "pgt_analysis" not in detail["snapshot"]
    # Nothing it predates is "not captured": the signed view says the record does not hold it.
    assert [gap for gap in detail["not_captured"] if gap["section"] == "pgt_analysis"] == []
    assert detail["report_code"] == old["report_code"]
    assert _ok(pgt, "old_chain")["verified"] is True
    check = _ok(pgt, "old_check")
    assert check["version"] == old["version"]
    assert check["not_compared"] == ["pgt_analysis"]
    assert check["matches"] is True, check


def test_each_embryo_call_carries_its_qc_verdict_and_the_record_freezes_it(pgt) -> None:
    # The owner's decision of 2026-10-20: a call whose phasing QC at the ROI falls below a
    # threshold gets a warning beside it, and stands.
    analysis = _ok(pgt, "analysis")
    assert analysis["rules"]["qc_thresholds"] == {
        "min_informative_sites": 50,
        "max_mendel_rate": 0.02,
        "min_markers_per_side": 6,
    }
    embryos = _embryos(analysis)
    names = pgt["names"]
    assert (embryos[names["E_RISK"]]["call"], embryos[names["E_CLEAR"]]["call"]) == (
        "affected_or_at_risk",
        "unaffected_non_carrier",
    )
    for embryo in embryos.values():
        # Six sites at the ROI, none a Mendelian error, three read on the father's side: the
        # dominant haplotype is his, so his side alone is checked.
        assert embryo["qc_verdict"] == {
            "status": "warning",
            "sides": ["paternal"],
            "findings": [
                {"measure": "informative_sites", "value": 6, "threshold": 50},
                {"measure": "paternal_markers", "value": 3, "threshold": 6},
            ],
        }, embryo
    # The signed record freezes the verdicts and the thresholds with the embryos' results.
    held = _ok(pgt, "detail")["snapshot"]["pgt_analysis"]
    assert [embryo["qc_verdict"] for embryo in held["embryos"]] == [
        embryo["qc_verdict"] for embryo in analysis["embryos"]
    ]
    assert held["rules"]["qc_thresholds"] == analysis["rules"]["qc_thresholds"]


def test_a_pgt_record_signed_before_the_qc_verdict_still_verifies_reads_and_matches(pgt) -> None:
    signed = _ok(pgt, "pre_verdict_signout")
    detail = _ok(pgt, "pre_verdict_detail")
    assert detail["verified"] is True
    held = detail["snapshot"]["pgt_analysis"]
    assert held["embryos"] and all("qc_verdict" not in embryo for embryo in held["embryos"])
    assert "qc_thresholds" not in held["rules"]
    # Nothing it predates is "not captured".
    assert [gap for gap in detail["not_captured"] if gap["section"] == "pgt_analysis"] == []
    assert _ok(pgt, "pre_verdict_chain")["verified"] is True
    # Compared on what it holds: it matches, and the verdict is named as not compared.
    check = _ok(pgt, "pre_verdict_check")
    assert check["version"] == signed["version"]
    assert check["not_compared"] == ["pgt_analysis.qc_verdict"]
    assert check["matches"] is True and check["changed_sections"] == [], check


def test_a_user_outside_the_familys_projects_gets_the_404_of_an_unknown_family(pgt) -> None:
    hidden, unknown = pgt["responses"]["outsider"], pgt["responses"]["outsider_unknown"]
    assert hidden["status"] == unknown["status"] == 404, (hidden, unknown)
