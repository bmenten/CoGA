"""Blocks built for another pedigree call no embryo, from import to sign-out (E2E, CLIN-66).

The haplotype blocks are built when the phased genotypes are imported, with the couple, the
couple's children and the members' recorded sexes. Over the synthetic PGT family of
``test_e2e_signed_pgt_analysis.py`` (an affected father, the mother, their affected child as the
index and two embryos, a phased GLIMPSE2 family VCF on chr1, AD, an ROI on chr1), imported from a
package and read through the API, this:

* reads the embryo calls right after the import: the blocks were built for the pedigree as it is
  (``inputs.blocks_outdated`` false), and the embryos are called;
* changes an affected status: the blocks stay current, as the calls read the statuses when they
  are made;
* corrects an embryo's sex: the blocks are outdated now, every embryo reads uninformative, and no
  disease haplotype is resolved, though the blocks are still read and named;
* signs the family out and reads the record back: it holds the outdated flag and the uninformative
  calls, verifies and matches.

Before, the stored blocks were read as current whatever had changed. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from backend.tests.e2e.test_e2e_signed_pgt_analysis import SIGN_OUT_BODY, _write_pgt_package

pytestmark = pytest.mark.integration


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _collect(root: Path, family_id: str, names: dict[str, str]) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
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
            session, folder_path=str(root), project_id=project_id, dry_run=False, user=admin, conflict_mode="cancel"
        )
    out: dict = {
        "completed": result.completed,
        "statuses": {dataset.dataset_type: dataset.status for dataset in result.datasets},
        "responses": {},
    }
    r = out["responses"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        family = f"/api/families/{family_id}"
        r["imported"] = _cap(await ac.get(f"{family}/pgt-analysis"))
        # The mother becomes affected: a status the calls read when they are made.
        r["status_edit"] = _cap(
            await ac.put(f"{family}/members/{names['MOTHER']}", json={"clinical_status": "affected"})
        )
        r["after_status"] = _cap(await ac.get(f"{family}/pgt-analysis"))
        r["status_back"] = _cap(
            await ac.put(f"{family}/members/{names['MOTHER']}", json={"clinical_status": "unaffected"})
        )
        # E_CLEAR's sex corrected after the import: the blocks were built with the old one.
        r["sex_edit"] = _cap(await ac.put(f"{family}/members/{names['E_CLEAR']}", json={"sex": "female"}))
        r["after_sex"] = _cap(await ac.get(f"{family}/pgt-analysis"))
        r["signout"] = _cap(await ac.post(f"{family}/report/sign-out", json=SIGN_OUT_BODY))
        version = (r["signout"]["json"] or {}).get("version")
        r["detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
        r["check"] = _cap(await ac.get(f"{family}/report/sign-out-check"))
    return out


@pytest.fixture(scope="module")
def outdated(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_outdated_blocks")
    family_id = f"FAM_BLOCKS_{uuid4().hex[:8]}"
    names = _write_pgt_package(base / family_id, family_id)
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    out = _harness.run_async(lambda: _collect(base / family_id, family_id, names))
    out["names"] = names
    assert out["completed"] is True, out
    assert out["statuses"].get("haplotypes") == "imported", out
    return out


def _ok(out: dict, key: str) -> dict:
    resp = out["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _calls(analysis: dict) -> dict[str, str | None]:
    return {embryo["sample"]: embryo["call"] for embryo in analysis["embryos"]}


def test_blocks_imported_for_the_pedigree_as_it_is_are_current(outdated) -> None:
    names = outdated["names"]
    analysis = _ok(outdated, "imported")

    assert analysis["inputs"]["blocks_outdated"] is False
    assert _calls(analysis) == {names["E_RISK"]: "affected_or_at_risk", names["E_CLEAR"]: "unaffected_non_carrier"}


def test_a_changed_affected_status_leaves_them_current(outdated) -> None:
    _ok(outdated, "status_edit")
    analysis = _ok(outdated, "after_status")

    assert analysis["inputs"]["blocks_outdated"] is False
    assert all(call is not None for call in _calls(analysis).values())


def test_a_corrected_sex_makes_them_outdated_and_no_embryo_is_called(outdated) -> None:
    names = outdated["names"]
    _ok(outdated, "status_back")
    _ok(outdated, "sex_edit")
    analysis = _ok(outdated, "after_sex")

    # Before, the blocks built with the old sex were read as current.
    assert analysis["inputs"]["blocks_outdated"] is True
    assert _calls(analysis) == {names["E_RISK"]: "uninformative", names["E_CLEAR"]: "uninformative"}
    assert analysis["disease_haplotype_resolved"] is False and analysis["disease_haplotypes"] == []
    # The blocks are still read and named: what the record could not call from.
    assert analysis["inputs"]["blocks_read"] > 0 and len(analysis["inputs"]["blocks_digest"]) == 64


def test_the_signed_record_holds_the_outdated_flag_and_the_uninformative_calls(outdated) -> None:
    detail = _ok(outdated, "detail")

    assert detail["verified"] is True
    assert detail["snapshot"]["pgt_analysis"] == _ok(outdated, "after_sex")
    assert detail["snapshot"]["pgt_analysis"]["inputs"]["blocks_outdated"] is True
    check = _ok(outdated, "check")
    assert check["matches"] is True, check
    assert check["not_compared"] == []
