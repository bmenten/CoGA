"""A member is added to a delivery's package in CoGA before it is approved (E2E, real Postgres and ClickHouse).

The owner's decision of 2026-11-11 (``import_delivery_revisions.py``): a sample the package's files
name and no member is, a column of the phased family VCF, is added as a member, with a reason;
the package of a folder added by hand, which CoGA made, takes any member and is edited as JSON.
Through the app over HTTP, as the e2e administrator:

- a watched PGT delivery whose package file lists the couple and two embryos, while its phased
  family VCF also holds the index: it needs attention, one issue naming the index's column, and
  its package offers the index as a member to add;
- a sample the files do not name is not added, and a member the package delivered is not
  removed (400);
- the index added as the couple's child changes the pedigree the pipeline phased with: the
  delivery needs attention (``package_pedigree_changed``); removed again, it needs its member;
  added as a relative of the affected father, the PGT index, it is ready;
- approved, the import runs: the family holds the index as an affected relative of the father,
  and the family's record names the member added among the corrections;
- a folder added by hand whose PED lacks the index its phased family VCF holds: CoGA still
  proposes the PED's members and the datasets, the delivery offers the index; corrected as
  JSON, the index and a member in no file are added, the revision lists each whole, and the
  index's column is no issue any more.

The values are synthetic: the families are new and uniquely named. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, job_outcome
from backend.tests.e2e._pgt_package import ROI, write_pgt_package
from backend.tests.e2e.test_e2e_import_watcher import write_package

pytestmark = pytest.mark.integration


def _member(sample_id: str, sex: str, **fields: Any) -> dict[str, Any]:
    return {"sample_id": sample_id, "sex": sex, **fields}


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import import_watcher
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project = (
            await session.execute(text("SELECT name FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_id})
        ).scalar_one()

    # A watched PGT delivery whose package file lacks the index its phased family VCF holds.
    family = f"E2E_ADDED_{run}"
    folder = base / project / family
    names = write_pgt_package(folder, family)
    (folder / "family.ped").unlink()
    (folder / "manifest.yaml").unlink()
    father, mother, index = names["FATHER"], names["MOTHER"], names["INDEX"]
    write_package(
        folder,
        project=project,
        family_id=family,
        full=False,
        analysis_type="pgt_m",
        family={"family_id": family, "roi": ROI, "pgt": {"inheritance_model": "AD", "affected_parents": [father]}},
        members=[
            _member(father, "male", role="father", clinical_status="affected"),
            _member(mother, "female", role="mother", clinical_status="unaffected"),
            _member(names["E_RISK"], "female", father=father, mother=mother, role="embryo"),
            _member(names["E_CLEAR"], "male", father=father, mother=mother, role="embryo"),
        ],
        datasets={"haplotypes": {"family_vcf": "haplotypes/family.glimpse2.vcf", "source_format": "glimpse2"}},
        pipeline_used_pedigree=["haplotypes"],
    )
    await import_watcher.run_watch_round(sessionmaker, [base.resolve()])
    async with sessionmaker() as session:
        delivery_id = (
            await session.execute(text("SELECT id::text FROM import_deliveries WHERE folder_name = :n"), {"n": family})
        ).scalar_one()

    out: dict[str, Any] = {"family": family, "names": names}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"
        url = f"/api/family-imports/deliveries/{delivery_id}"

        async def revise(revision: int, reason: str, *changes: tuple[list[str], Any]) -> dict[str, Any]:
            body = {"revision": revision, "reason": reason, "changes": [{"path": path, "value": value} for path, value in changes]}
            return capture(await client.post(f"{url}/revisions", json=body))

        out["delivery0"] = capture(await client.get("/api/family-imports/deliveries", params={"limit": 200}))
        out["package0"] = capture(await client.get(f"{url}/package"))
        out["not_named"] = await revise(0, "A sample no file names.", (["members", "NOBODY"], {"sex": "male"}))
        out["delivered_removed"] = await revise(0, "An embryo the pipeline listed.", (["members", names["E_CLEAR"]], None))
        out["as_child"] = await revise(
            0,
            "The index is the couple's child.",
            (["members", index], {"sex": "female", "father": father, "mother": mother, "role": "proband"}),
        )
        out["removed"] = await revise(1, "Not the couple's child after all.", (["members", index], None))
        out["as_relative"] = await revise(
            2,
            "The affected relative of the father, read for segregation.",
            (["members", index], {"sex": "female", "role": "relative", "clinical_status": "affected", "related_to": [father]}),
            (["family", "pgt", "indexes"], [index]),
        )
        out["approved"] = capture(await client.post(f"{url}/approve", json={"revision": 3, "acknowledge_warnings": True}))
        job_id = out["approved"]["json"]["job"]["_id"]
        worker = f"e2e-added-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        out["job"] = await job_outcome(job_id)

        # A folder added by hand whose PED lacks the index, corrected as JSON.
        by_hand = f"E2E_ADDED_HAND_{run}"
        hand_folder = base / project / by_hand
        hand_names = write_pgt_package(hand_folder, by_hand)
        (hand_folder / "manifest.yaml").unlink()
        ped = hand_folder / "family.ped"
        ped.write_text("".join(f"{line}\n" for line in ped.read_text().splitlines() if hand_names["INDEX"] not in line))
        out["hand_names"], out["hand_family"] = hand_names, by_hand
        added = capture(
            await client.post(
                "/api/family-imports/deliveries",
                json={"folder_path": str(hand_folder), "project_id": project_id, "analysis_type": "pgt_m"},
            )
        )
        out["hand_added"] = added
        hand_url = f"/api/family-imports/deliveries/{added['json']['_id']}"
        package = capture(await client.get(f"{hand_url}/package"))
        out["hand_package"] = package
        edited = json.loads(json.dumps(package["json"]["package"]))
        hand_index = _member(
            hand_names["INDEX"], "female", role="relative", clinical_status="affected", related_to=[hand_names["FATHER"]]
        )
        uncle = _member(
            f"{by_hand}_UNCLE", "male", role="relative", clinical_status="affected", related_to=[hand_names["FATHER"]]
        )
        edited["members"].extend([hand_index, uncle])
        out["hand_json"] = capture(
            await client.post(
                f"{hand_url}/revisions",
                json={"revision": 0, "reason": "The index of the phased VCF, and the father's affected brother.", "package": edited},
            )
        )
        out["hand_both"] = capture(
            await client.post(
                f"{hand_url}/revisions",
                json={
                    "revision": 1,
                    "reason": "Both at once.",
                    "package": edited,
                    "changes": [{"path": ["project"], "value": project}],
                },
            )
        )

    async with sessionmaker() as session:
        out["members"] = {
            row["sample_id"]: dict(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT s.sample_id, fm.clinical_status, fm.role, s.sex FROM family_members fm "
                        "JOIN samples s ON s.id = fm.sample_id JOIN families f ON f.id = fm.family_id "
                        "WHERE f.family_id = :f"
                    ),
                    {"f": family},
                )
            ).mappings()
        }
        out["record"] = (
            await session.execute(
                text("SELECT metadata -> 'package_import' -> 'delivery' FROM families WHERE family_id = :f"), {"f": family}
            )
        ).scalar_one()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("delivery_members_added")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def _issues(response: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    issues: dict[str, list[dict[str, Any]]] = {}
    for issue in response["json"]["delivery"]["issues"]:
        issues.setdefault(issue["code"], []).append(issue)
    return issues


def test_a_phased_vcf_column_no_member_is_needs_attention_and_is_offered_as_a_member(ran) -> None:
    index = ran["names"]["INDEX"]
    [delivery] = [row for row in ran["delivery0"]["json"] if row["folder_name"] == ran["family"]]
    assert delivery["state"] == "needs_attention"
    [issue] = [issue for issue in delivery["issues"] if issue["code"] == "dataset_vcf_sample_unknown"]
    assert issue["message"].startswith(f"The phased family VCF has a sample column {index} that is no member")
    package = ran["package0"]
    assert package["status"] == 200, package
    assert package["json"]["origin"] == "watched"
    assert package["json"]["addable_members"] == [index]
    assert sorted(package["json"]["members_as_checked"]) == sorted(
        ran["names"][role] for role in ("FATHER", "MOTHER", "E_RISK", "E_CLEAR")
    )


def test_no_other_sample_is_added_and_no_delivered_member_removed(ran) -> None:
    assert ran["not_named"]["status"] == 400, ran["not_named"]
    assert ran["not_named"]["json"]["detail"].startswith("members.NOBODY is no sample the package's files name")
    assert ran["delivered_removed"]["status"] == 400, ran["delivered_removed"]
    assert "is a member of the pipeline's package and is not removed" in ran["delivered_removed"]["json"]["detail"]


def test_a_member_added_with_parents_changes_the_pedigree_the_pipeline_phased_with(ran) -> None:
    index = ran["names"]["INDEX"]
    as_child = ran["as_child"]
    assert as_child["status"] == 200, as_child
    issues = _issues(as_child)
    assert "dataset_vcf_sample_unknown" not in issues
    assert f"({index})" in issues["package_pedigree_changed"][0]["message"]
    [change] = as_child["json"]["package"]["revisions"][-1]["changes"]
    assert (change["path"], change["kind"], change["before"]) == (["members", index], "laboratory", None)

    removed = ran["removed"]
    assert removed["status"] == 200, removed
    assert set(_issues(removed)) >= {"dataset_vcf_sample_unknown"}
    assert "package_pedigree_changed" not in _issues(removed)
    assert removed["json"]["package"]["addable_members"] == [index]


def test_the_index_added_as_a_relative_is_ready_and_imported_with_its_link(ran) -> None:
    names = ran["names"]
    index, father = names["INDEX"], names["FATHER"]
    as_relative = ran["as_relative"]
    assert as_relative["status"] == 200, as_relative
    assert as_relative["json"]["delivery"]["state"] == "ready", as_relative["json"]["delivery"]["issues"]
    assert ran["approved"]["status"] == 200, ran["approved"]
    assert ran["job"]["status"] == "completed", ran["job"]
    assert ran["members"][index]["clinical_status"] == "affected"
    assert ran["members"][index]["role"] == "relative"
    assert ran["members"][index]["sex"] == "female"
    record = ran["record"]
    assert record["revision"] == 3
    added = [
        change
        for correction in record["corrections"]
        for change in correction["changes"]
        if change["path"] == ["members", index] and change["after"]
    ]
    assert added[-1]["after"] == {
        "sample_id": index,
        "sex": "female",
        "role": "relative",
        "clinical_status": "affected",
        "related_to": [father],
    }


def test_a_folder_added_by_hand_whose_ped_lacks_the_index_is_corrected_as_json(ran) -> None:
    names = ran["hand_names"]
    added = ran["hand_added"]
    assert added["status"] == 200, added
    # The PED's members and the datasets are proposed although the phased VCF names the index.
    assert "discover_failed" not in {issue["code"] for issue in added["json"]["issues"]}
    package = ran["hand_package"]["json"]
    assert package["origin"] == "manual"
    assert sorted(member["sample_id"] for member in package["package"]["members"]) == sorted(
        names[role] for role in ("FATHER", "MOTHER", "E_RISK", "E_CLEAR")
    )
    assert "haplotypes" in package["package"]["datasets"]
    assert package["addable_members"] == [names["INDEX"]]

    corrected = ran["hand_json"]
    assert corrected["status"] == 200, corrected
    changes = corrected["json"]["package"]["revisions"][-1]["changes"]
    assert [change["path"][1] for change in changes] == [names["INDEX"], f"{ran['hand_family']}_UNCLE"]
    assert all((change["kind"], change["before"]) == ("laboratory", None) for change in changes)
    assert changes[0]["after"]["related_to"] == [names["FATHER"]]
    assert "dataset_vcf_sample_unknown" not in {issue["code"] for issue in corrected["json"]["delivery"]["issues"]}
    assert ran["hand_both"]["status"] == 422, ran["hand_both"]
