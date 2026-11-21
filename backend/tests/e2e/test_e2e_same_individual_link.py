"""Two samples of one person, linked as the same individual by a package, E2E.

The monogenic and the conventional NIPT of one pregnancy, whose plasmas carry two sample IDs
(the owner's decisions of 2026-11-19 and 2026-11-20). A synthetic family is imported into real
Postgres and ClickHouse, a new family each run:

1. a conventional NIPT package whose plasma (``PLASMA2``) names the monogenic NIPT's plasma
   (``PLASMA1``) as the same individual, imported first: refused, the family holding no such
   sample, and nothing is written;
2. the monogenic NIPT package (the father, ``PLASMA1`` and the fetus);
3. the conventional NIPT package again, with update: ``PLASMA2`` is added and linked to
   ``PLASMA1`` (``family.relationships.same_individual``).

Then:

* the family's relationships hold the link, and its pedigree change on the clinical audit trail
  names it;
* a structure edit that gives the two samples different sexes is refused (400), naming them;
* the cNIPT result is served for ``PLASMA2``;
* an overwrite of the conventional NIPT package that names the link again, while the family holds
  a member added by hand (in neither package): the package's link and the family's are one link,
  and the hand-added member is kept, as a family that holds another analysis keeps every member no
  package record lists;
* an overwrite of the conventional NIPT package that no longer names the link, again with a
  member added by hand: the link stays, since it joins that package's member to the monogenic
  NIPT's;
* the link taken out in the members editor: an update of the package that names it does not make
  it and warns of it (``package_same_individual_not_made``); an overwrite makes it.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from backend.tests._ccnv_output import write_ccnv_output
from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration


def _mnipt_package(root: Path, family_id: str, father: str, plasma: str, fetus: str) -> Path:
    root.mkdir(parents=True)
    (root / "nipt_combined.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr7>\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{plasma}\n"
        "chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=300\tGT:AD:DP\t0/1:500,500:1000\t0/1:900,100:1000\n",
        encoding="utf-8",
    )
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{plasma}\t0\t0\t2\t1\n"
        f"{family_id}\t{fetus}\t{father}\t{plasma}\t0\t2\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "analysis_type": "monogenic_nipt",
        "pregnancy_id": "ZW1",
        "ped": "family.ped",
        "samples": {plasma: {"assay": "nipt_cfdna"}},
        "datasets": {"snv": {"family_vcf": "nipt_combined.vcf", "source_format": "mutect2"}},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _cnipt_manifest(family_id: str, plasma: str, entry: dict[str, Any], *, same_as: str | None) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": "family.ped",
        "analysis_type": "conventional_nipt",
        "pregnancy_id": "ZW1",
        "samples": {plasma: {"assay": "cnipt_cfdna"}},
        "datasets": {"ccnv": {"per_sample": {plasma: entry}}},
    }
    if same_as is not None:
        manifest["family"] = {"relationships": {"same_individual": [{"samples": [plasma, same_as]}]}}
    return manifest


async def _active_members(sessionmaker: Any, family_id: str) -> list[str]:
    from sqlalchemy import text

    async with sessionmaker() as session:
        rows = await session.execute(
            text(
                """
                SELECT s.sample_id
                FROM family_members fm
                JOIN families f ON f.id = fm.family_id
                JOIN samples s ON s.id = fm.sample_id
                WHERE f.family_id = :f AND fm.active
                """
            ),
            {"f": family_id},
        )
        return sorted(str(row[0]) for row in rows)


def _without_same_individual_links(relationships: list[dict[str, Any]]) -> dict[str, Any]:
    """The family's relationships as a structure update names them, without its same-individual
    links."""
    return {
        "parent_child": [
            {
                "parent": link["sample_id_a"],
                "child": link["sample_id_b"],
                "parent_role": link["role_a"] if link["role_a"] in ("father", "mother") else "parent",
            }
            for link in relationships
            if link["relationship_type"] == "parent_child"
        ],
        "couples": [
            {
                "partners": [link["sample_id_a"], link["sample_id_b"]],
                "context": (link.get("metadata") or {}).get("context"),
            }
            for link in relationships
            if link["relationship_type"] == "couple"
        ],
        "same_individual": [],
    }


async def _import(sessionmaker: Any, admin: Any, project_id: str, root: Path, mode: str) -> dict[str, Any]:
    """The import's outcome; one refused before it writes anything raises ``ImportRefused``, which
    its job reports."""
    from backend.app.services import family_package_import as package_import
    from backend.app.services.family_package_common import ImportRefused

    async with sessionmaker() as session:
        try:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                manifest_text=kept_manifest(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=mode,
            )
        except ImportRefused as refused:
            return {"completed": False, "error": str(refused), "refused": True, "warnings": []}
    return {
        "completed": result.completed,
        "error": result.error,
        "refused": False,
        "warnings": [(warning.code, warning.message) for warning in result.validation.warnings],
    }


async def _links(sessionmaker: Any, family_id: str) -> list[tuple[str, str, str, str]]:
    from sqlalchemy import text

    async with sessionmaker() as session:
        rows = await session.execute(
            text(
                """
                SELECT fr.relationship_type, sa.sample_id AS a, sb.sample_id AS b, fr.source
                FROM family_relationships fr
                JOIN families f ON f.id = fr.family_id
                JOIN samples sa ON sa.id = fr.sample_id_a
                JOIN samples sb ON sb.id = fr.sample_id_b
                WHERE f.family_id = :f AND fr.active AND fr.relationship_type = 'same_individual'
                """
            ),
            {"f": family_id},
        )
        return sorted((str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows)


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Sample IDs are unique across families.
    father, plasma1, fetus, plasma2 = (f"{role}_{family_id}" for role in ("FATHER", "PLASMA1", "FETUS", "PLASMA2"))
    mnipt = _mnipt_package(base / family_id / "mnipt", family_id, father, plasma1, fetus)
    cnipt = base / family_id / "cnipt"
    cnipt.mkdir(parents=True)
    entry = write_ccnv_output(cnipt, plasma2)
    (cnipt / "family.ped").write_text(f"{family_id}\t{plasma2}\t0\t0\t2\t-9\trole=mother\n", encoding="utf-8")

    def write_cnipt(same_as: str | None) -> None:
        manifest = _cnipt_manifest(family_id, plasma2, entry, same_as=same_as)
        (cnipt / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    out: dict[str, Any] = {"members": (father, plasma1, fetus, plasma2)}
    write_cnipt(plasma1)
    out["first"] = await _import(sessionmaker, admin, project_id, cnipt, "cancel")
    async with sessionmaker() as session:
        out["family_after_first"] = (
            await session.execute(text("SELECT count(*) FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
    out["mnipt"] = await _import(sessionmaker, admin, project_id, mnipt, "cancel")
    out["cnipt"] = await _import(sessionmaker, admin, project_id, cnipt, "update")
    if not (out["mnipt"]["completed"] and out["cnipt"]["completed"]):
        return out
    out["links"] = await _links(sessionmaker, family_id)
    async with sessionmaker() as session:
        out["pedigree_events"] = [
            str(row[0])
            for row in await session.execute(
                text(
                    "SELECT summary FROM clinical_audit_events "
                    "WHERE family_identifier = :f AND action = 'pedigree' ORDER BY created_at"
                ),
                {"f": family_id},
            )
        ]

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        family = await ac.get(f"/api/families/{family_id}")
        out["family"] = {"status": family.status_code, "json": family.json()}
        refused = await ac.put(
            f"/api/families/{family_id}/structure",
            json={"members": [{"sample_id": plasma2, "sex": "male"}], "change_reason": "e2e: a wrong sex"},
        )
        out["sex_edit"] = {"status": refused.status_code, "json": refused.json()}
        cnipt_result = await ac.get(f"/api/families/{family_id}/cnipt")
        out["cnipt_result"] = {"status": cnipt_result.status_code, "json": cnipt_result.json()}

        async def add_by_hand(sample_id: str) -> int:
            added = await ac.put(
                f"/api/families/{family_id}/structure",
                json={"add_members": [{"sample_id": sample_id}], "change_reason": "e2e: a member added by hand"},
            )
            return added.status_code

        # A member added by hand is in neither package: an overwrite of the conventional NIPT's
        # package then differs from the family, takes the package's pedigree and keeps the member
        # (the family holds another analysis).
        hand1, hand2 = f"HAND1_{family_id}", f"HAND2_{family_id}"
        out["hand1_added"] = await add_by_hand(hand1)
        write_cnipt(plasma1)
        out["overwrite_with_link"] = await _import(sessionmaker, admin, project_id, cnipt, "overwrite")
        out["links_after_overwrite_with_link"] = await _links(sessionmaker, family_id)
        out["members_after_overwrite_with_link"] = await _active_members(sessionmaker, family_id)

        # The conventional NIPT's package again, without the link, as an overwrite.
        out["hand2_added"] = await add_by_hand(hand2)
        write_cnipt(None)
        out["overwrite"] = await _import(sessionmaker, admin, project_id, cnipt, "overwrite")
        out["links_after_overwrite"] = await _links(sessionmaker, family_id)
        out["members_after_overwrite"] = await _active_members(sessionmaker, family_id)
        out["hands"] = (hand1, hand2)

        # The link taken out by hand; the package that names it, as an update and as an overwrite.
        family = (await ac.get(f"/api/families/{family_id}")).json()
        unlinked = await ac.put(
            f"/api/families/{family_id}/structure",
            json={
                "relationships": _without_same_individual_links(family["relationships"]),
                "change_reason": "e2e: the link taken out",
            },
        )
        out["unlinked"] = {"status": unlinked.status_code, "links": await _links(sessionmaker, family_id)}
    write_cnipt(plasma1)
    out["update_naming_the_link"] = await _import(sessionmaker, admin, project_id, cnipt, "update")
    out["links_after_update_naming_the_link"] = await _links(sessionmaker, family_id)
    out["overwrite_naming_the_link"] = await _import(sessionmaker, admin, project_id, cnipt, "overwrite")
    out["links_after_overwrite_naming_the_link"] = await _links(sessionmaker, family_id)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("same_individual")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"SAME_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_a_link_to_a_sample_the_family_does_not_hold_refuses_the_import(run) -> None:
    _father, plasma1, _fetus, _plasma2 = run["members"]
    assert (run["first"]["completed"], run["first"]["refused"]) == (False, True), run["first"]
    assert f"links {plasma1} to one of its members as the same individual" in str(run["first"]["error"])
    assert "import the package that lists it first" in str(run["first"]["error"])
    assert run["family_after_first"] == 0


def test_the_package_links_the_two_plasmas_once_both_are_in_the_family(run) -> None:
    assert run["mnipt"]["completed"] is True, run["mnipt"]
    assert run["cnipt"]["completed"] is True, run["cnipt"]
    _father, plasma1, _fetus, plasma2 = run["members"]
    assert [(kind, {a, b}) for kind, a, b, _source in run["links"]] == [("same_individual", {plasma1, plasma2})]


def test_the_link_is_in_the_family_record_and_on_the_audit_trail(run) -> None:
    assert run["family"]["status"] == 200, run["family"]
    _father, plasma1, _fetus, plasma2 = run["members"]
    links = [
        {link["sample_id_a"], link["sample_id_b"]}
        for link in run["family"]["json"]["relationships"]
        if link["relationship_type"] == "same_individual"
    ]
    assert links == [{plasma1, plasma2}]
    named = " = ".join(sorted([plasma1, plasma2]))
    assert any(f"same individual {named} added" in summary for summary in run["pedigree_events"]), run[
        "pedigree_events"
    ]


def test_an_edit_that_gives_the_two_samples_different_sexes_is_refused(run) -> None:
    _father, plasma1, _fetus, plasma2 = run["members"]
    assert run["sex_edit"]["status"] == 400, run["sex_edit"]
    detail = str(run["sex_edit"]["json"]["detail"])
    assert "linked as the same individual but their sex differs (female, male)" in detail
    assert plasma1 in detail and plasma2 in detail


def test_the_cnipt_result_is_served_for_the_second_plasma(run) -> None:
    assert run["cnipt_result"]["status"] == 200, run["cnipt_result"]
    _father, _plasma1, _fetus, plasma2 = run["members"]
    assert [sample["sample_id"] for sample in run["cnipt_result"]["json"]["samples"]] == [plasma2]


def test_an_overwrite_naming_the_familys_link_again_takes_it_once(run) -> None:
    # The hand-added member is missing from the package; the family holds another analysis, so
    # the overwrite keeps it (a member no package record lists is kept) and takes the package's
    # pedigree beside it: the package's link and the family's are one link, not a link twice.
    assert run["hand1_added"] == 200
    assert run["overwrite_with_link"]["completed"] is True, run["overwrite_with_link"]
    _father, plasma1, _fetus, plasma2 = run["members"]
    assert [(kind, {a, b}) for kind, a, b, _source in run["links_after_overwrite_with_link"]] == [
        ("same_individual", {plasma1, plasma2})
    ]
    hand1, _hand2 = run["hands"]
    assert hand1 in run["members_after_overwrite_with_link"]
    assert set(run["members"]) <= set(run["members_after_overwrite_with_link"])


def test_an_overwrite_without_the_link_keeps_the_link_to_the_other_analysiss_member(run) -> None:
    assert run["hand2_added"] == 200
    assert run["overwrite"]["completed"] is True, run["overwrite"]
    _hand1, hand2 = run["hands"]
    # The overwrite kept the hand-added member (the family holds another analysis) and the link
    # that joins the package's member to the monogenic NIPT's.
    assert hand2 in run["members_after_overwrite"]
    assert [(kind, {a, b}) for kind, a, b, _source in run["links_after_overwrite"]] == [
        (kind, {a, b}) for kind, a, b, _source in run["links"]
    ]


def test_an_update_does_not_make_a_link_between_samples_the_family_holds_and_warns_and_an_overwrite_does(
    run,
) -> None:
    _father, plasma1, _fetus, plasma2 = run["members"]
    assert run["unlinked"] == {"status": 200, "links": []}
    update = run["update_naming_the_link"]
    assert update["completed"] is True, update
    assert run["links_after_update_naming_the_link"] == []
    [warning] = [message for code, message in update["warnings"] if code == "package_same_individual_not_made"]
    assert f"links {min(plasma1, plasma2)} and {max(plasma1, plasma2)} as the same individual" in warning
    assert "an update adds only the links of the members it adds" in warning
    overwrite = run["overwrite_naming_the_link"]
    assert overwrite["completed"] is True, overwrite
    assert [(kind, {a, b}) for kind, a, b, _source in run["links_after_overwrite_naming_the_link"]] == [
        ("same_individual", {plasma1, plasma2})
    ]
