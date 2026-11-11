"""A package folder that changes, or goes, after its import (E2E, real Postgres and ClickHouse).

The owner's decisions of 2026-11-11 (``package_changed_after_import.py``). Through the watcher's
rounds, the app over HTTP (an in-process ``httpx.ASGITransport`` client) as the e2e
administrator, and the import job as a worker runs it, for one family delivered into a watched
import folder and one folder added by hand:

- imported as a new family, the delivery keeps the listing its fingerprint was taken over and is
  the family's reference; a file added to the folder, and one touched, make the next round a new
  delivery of the folder and the reference ``changed_after_import``, naming both files; the
  family's record serves the change (the family page's notice), and the deliveries list shows it;
- a sign-out is refused (409, gate ``package_source_change``) until it is acknowledged with a
  reason; acknowledged, the signed record and its audit event keep the change and the reason;
- the folder's new delivery imported with update: it may be approved again, with overwrite only
  (an update is refused, 400; a dry run with overwrite is queued); the family's record names it, but its
  reference stays the first delivery, which stays changed, so the notice and the gate stay;
- changed again, its new delivery imported with overwrite: the reference is that delivery, the
  first one's change ends (``overwritten``), and the notice is gone;
- a file added and no round run: the sign-out's own check of the folder refuses it all the same,
  saying it found the change itself; the folder back as imported, the sign-out passes;
- changed again, the new delivery refused with a reason ends the change at once (by the refuser,
  with the reason); the next round raises nothing for that folder state;
- moved away, the folder is gone only after the rounds a share hiccup may take, saying so; a
  superuser confirms it as archived with a reason (409 for a delivery that is not gone): the
  family page says so, a sign-out passes without an acknowledgement and its record freezes it;
  put back unchanged, the archived change ends by itself; changed once more, to a state neither
  imported nor refused, it is raised again;
- a folder added by hand is followed after its import too.

Each transition is on the ``system:import-inbox`` chain, which still verifies. The values are
synthetic: the families are new and uniquely named. Skipped unless ``RUN_INTEGRATION=1`` (see
conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, job_outcome, one_sample_package
from backend.tests.e2e.test_e2e_import_watcher import write_package

pytestmark = pytest.mark.integration

# Every other acknowledgement a sign-out can give, so only the package folder's gate is seen.
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e validation",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e validation",
    "acknowledge_unreviewed_findings": True,
    "unreviewed_findings_acknowledgement_reason": "e2e validation",
    "acknowledge_import_incomplete": True,
    "import_incomplete_acknowledgement_reason": "e2e validation",
}
_REASON = "e2e: the added note and the touched PED change nothing the report rests on."
_ARCHIVED_REASON = "e2e: the run folder was archived after the report, on purpose."


async def _exercise(base: Path, away: Path, run: str) -> dict[str, Any]:
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
    family = f"E2E_CHANGED_{run}"
    sample = f"{family}_S1"
    folder = one_sample_package(base / project / family, family, variants=2, datasets=("snv",))
    (folder / "manifest.yaml").unlink()
    write_package(
        folder,
        project=project,
        family_id=family,
        full=False,
        members=[{"sample_id": sample, "sex": "male", "role": "proband", "clinical_status": "affected"}],
        datasets={"snv": {"family_vcf": "snv/family.vcf"}},
    )
    by_hand = f"E2E_CHANGED_HAND_{run}"
    hand_folder = one_sample_package(base / "by-hand" / by_hand, by_hand, variants=2, datasets=("snv",))
    (hand_folder / "manifest.yaml").unlink()
    roots = [base.resolve()]

    async def round_() -> Any:
        return await import_watcher.run_watch_round(sessionmaker, roots)

    async def deliveries(name: str) -> list[dict[str, Any]]:
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, state, package_fingerprint, source_change, file_listing, origin "
                        "FROM import_deliveries WHERE folder_name = :n ORDER BY first_seen_at, id"
                    ),
                    {"n": name},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def events(delivery_id: str) -> list[dict[str, Any]]:
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT action, actor, after FROM clinical_audit_events "
                        "WHERE family_identifier = :chain AND metadata ->> 'delivery_id' = :d ORDER BY created_at, id"
                    ),
                    {"chain": import_watcher.IMPORT_INBOX_CHAIN, "d": delivery_id},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def record() -> dict[str, Any]:
        async with sessionmaker() as session:
            package = (
                await session.execute(
                    text("SELECT metadata -> 'package_import' FROM families WHERE family_id = :f"), {"f": family}
                )
            ).scalar_one()
        return {
            "import_mode": package.get("import_mode"),
            "delivery": package["delivery"]["id"],
            "reference": package["reference_delivery"]["id"],
        }

    out: dict[str, Any] = {"family": family, "by_hand": by_hand}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"

        async def import_delivery(delivery_id: str, **body: Any) -> dict[str, Any]:
            approved = capture(
                await client.post(
                    f"/api/family-imports/deliveries/{delivery_id}/approve",
                    json={"revision": 0, "acknowledge_warnings": True, **body},
                )
            )
            assert approved["status"] == 200, approved
            job_id = approved["json"]["job"]["_id"]
            worker = f"e2e-changed-{uuid4().hex[:8]}"
            await claim_import_job(job_id, worker)
            await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
            await round_()
            return await job_outcome(job_id)

        async def family_change() -> Any:
            response = capture(await client.get(f"/api/families/{family}"))
            assert response["status"] == 200, response
            return response["json"]["package_source_change"]

        url = f"/api/families/{family}/report/sign-out"

        async def sign_out(**acknowledged: Any) -> dict[str, Any]:
            return capture(await _harness.sign_out(client, url, {**_OTHER_GATES, **acknowledged}))

        def archive(delivery_id: str, reason: str = _ARCHIVED_REASON) -> Any:
            return client.post(f"/api/family-imports/deliveries/{delivery_id}/archive", json={"reason": reason})

        # 1. Delivered and imported, as a new family: its delivery is the family's reference.
        await round_()
        (first,) = await deliveries(family)
        out["first_job"] = await import_delivery(first["id"])
        (out["imported"],) = await deliveries(family)
        out["change_when_imported"] = await family_change()
        out["record_first"] = await record()

        # 2. A file added and one touched after the import.
        (folder / "notes").mkdir()
        (folder / "notes" / "added.txt").write_text("a note\n", encoding="utf-8")
        ped = folder / "family.ped"
        os.utime(ped, ns=(ped.stat().st_atime_ns, ped.stat().st_mtime_ns + 5_000_000_000))
        out["changed_round"] = await round_()
        out["after_change"] = await deliveries(family)
        out["family_change"] = await family_change()
        out["listed"] = capture(await client.get("/api/family-imports/deliveries", params={"limit": 200}))

        # 3. Sign-out: refused until acknowledged; acknowledged, the record keeps the change.
        out["signout_refused"] = capture(await _harness.sign_out(client, url, _OTHER_GATES))
        out["signout"] = await sign_out(
            acknowledge_package_source_change=True, package_source_change_acknowledgement_reason=_REASON
        )
        async with sessionmaker() as session:
            out["signout_event"] = (
                await session.execute(
                    text(
                        "SELECT after FROM clinical_audit_events e JOIN families f ON f.id = e.family_id "
                        "WHERE f.family_id = :f AND e.action = 'sign_out' ORDER BY e.created_at DESC LIMIT 1"
                    ),
                    {"f": family},
                )
            ).scalar_one()

        # 4. The folder's new delivery imported with update: the change stays raised.
        second = out["after_change"][1]
        out["update_job"] = await import_delivery(second["id"], conflict_mode="update")
        out["after_update"] = await deliveries(family)
        out["change_after_update"] = await family_change()
        out["record_after_update"] = await record()
        out["signout_after_update"] = capture(await _harness.sign_out(client, url, _OTHER_GATES))
        # The update-imported delivery may be approved again, to overwrite only (a dry run here:
        # step 5 imports with overwrite).
        listed = capture(await client.get("/api/family-imports/deliveries", params={"limit": 200}))
        out["approvable_again"] = {row["_id"]: row["overwrite_approvable"] for row in listed["json"]}
        again_url = f"/api/family-imports/deliveries/{second['id']}/approve"
        out["again_with_update"] = capture(
            await client.post(again_url, json={"revision": 0, "acknowledge_warnings": True, "conflict_mode": "update"})
        )
        out["again_dry_run"] = capture(
            await client.post(
                again_url,
                json={"revision": 0, "acknowledge_warnings": True, "conflict_mode": "overwrite", "dry_run": True},
            )
        )

        # 5. Changed again; its new delivery imported with overwrite ends the change.
        (folder / "notes" / "second.txt").write_text("another note\n", encoding="utf-8")
        await round_()
        out["changed_again"] = await deliveries(family)
        third = out["changed_again"][2]
        out["overwrite_job"] = await import_delivery(third["id"], conflict_mode="overwrite")
        out["after_overwrite"] = await deliveries(family)
        out["change_after_overwrite"] = await family_change()
        out["record_after_overwrite"] = await record()
        out["first_events"] = await events(first["id"])

        # 6. A file added, and no round run: the sign-out's own check finds it.
        unseen = folder / "notes" / "unseen.txt"
        unseen.write_text("not seen by the watcher\n", encoding="utf-8")
        out["signout_unseen"] = capture(await _harness.sign_out(client, url, _OTHER_GATES))
        out["unseen_states"] = await deliveries(family)
        unseen.unlink()
        out["signout_as_imported"] = await sign_out()

        # 7. Changed again; its new delivery refused with a reason ends the change at once.
        (folder / "notes" / "third.txt").write_text("a third note\n", encoding="utf-8")
        await round_()
        out["changed_for_refusal"] = await deliveries(family)
        out["family_change_for_refusal"] = await family_change()
        refused_delivery = out["changed_for_refusal"][3]
        out["refused"] = capture(
            await client.post(
                f"/api/family-imports/deliveries/{refused_delivery['id']}/refuse",
                json={"reason": "e2e: a note only, nothing to import."},
            )
        )
        out["after_refusal"] = await deliveries(family)
        out["change_after_refusal"] = await family_change()
        out["refusal_round"] = await round_()
        out["after_refusal_round"] = await deliveries(family)

        # 8. Back as the overwrite read it, then moved away: gone after the rounds a share hiccup
        # may take; confirmed as archived, then put back unchanged, the change ends by itself.
        (folder / "notes" / "third.txt").unlink()
        await round_()
        out["as_imported_again"] = await deliveries(family)
        out["archive_not_gone"] = capture(await archive(third["id"]))
        moved = away / family
        shutil.move(str(folder), str(moved))
        out["gone_rounds"] = []
        for _ in range(import_watcher.GONE_AFTER_ROUNDS):
            await round_()
            out["gone_rounds"].append(await deliveries(family))
        out["family_change_gone"] = await family_change()
        out["archive_refused_one"] = capture(await archive(refused_delivery["id"]))
        out["archived"] = capture(await archive(third["id"]))
        out["archive_again"] = capture(await archive(third["id"]))
        out["family_change_archived"] = await family_change()
        out["signout_archived"] = await sign_out()
        out["archived_round"] = await round_()
        out["still_archived"] = await deliveries(family)
        shutil.move(str(moved), str(folder))
        out["back_round"] = await round_()
        out["back"] = await deliveries(family)
        out["change_back"] = await family_change()

        # 9. A state neither imported nor refused is raised again.
        (folder / "notes" / "fourth.txt").write_text("a fourth note\n", encoding="utf-8")
        await round_()
        out["raised_again"] = await deliveries(family)
        out["third_events"] = await events(third["id"])

        # 10. A folder added by hand is followed after its import too.
        added = capture(
            await client.post(
                "/api/family-imports/deliveries",
                json={"project_id": project_id, "folder_path": str(hand_folder), "analysis_type": "long_read_wgs"},
            )
        )
        assert added["status"] == 200, added
        out["hand_job"] = await import_delivery(added["json"]["_id"])
        (hand_folder / "snv" / "family.vcf").write_text(
            (hand_folder / "snv" / "family.vcf").read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        await round_()
        out["hand"] = await deliveries(by_hand)

        out["verified"] = capture(
            await client.get(
                "/api/admin/integrity/verify",
                params={"table": "clinical_audit_events", "family_id": import_watcher.IMPORT_INBOX_CHAIN},
            )
        )
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("changed_after_import")
    away = tmp_path_factory.mktemp("changed_after_import_away")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    return _harness.run_async(lambda: _exercise(base, away, uuid4().hex[:10].upper()))


def _actions(events: list[dict[str, Any]]) -> list[str]:
    return [event["action"] for event in events]


def test_an_imported_delivery_keeps_the_listing_of_its_folder_and_is_the_reference(ran) -> None:
    assert ran["first_job"]["status"] == "completed", ran["first_job"]
    imported = ran["imported"]
    assert imported["state"] == "imported" and imported["source_change"] is None
    assert {"coga-package.json", "family.ped", "snv/family.vcf"} <= set(imported["file_listing"])
    assert ran["change_when_imported"] is None
    assert ran["record_first"] == {"import_mode": "new_family", "delivery": imported["id"], "reference": imported["id"]}


def test_a_folder_changed_after_its_import_is_raised_with_the_files_that_changed(ran) -> None:
    first, new = ran["after_change"]
    assert ran["changed_round"].changed_after_import == 1
    assert new["state"] == "needs_attention"
    assert first["state"] == "changed_after_import"
    change = first["source_change"]
    assert (change["kind"], change["fingerprint"], change["listed"]) == ("changed", new["package_fingerprint"], True)
    assert (change["added"], change["removed"], change["changed"]) == (["notes/added.txt"], [], ["family.ped"])
    # The family's record serves it, for the family page's notice.
    served = ran["family_change"]
    assert served["delivery_id"] == first["id"] and served["kind"] == "changed"
    assert served["imported_fingerprint"] == first["package_fingerprint"]
    assert served["added"] == ["notes/added.txt"] and served["changed"] == ["family.ped"]
    assert (served["found_at_sign_out"], served["archived"]) == (False, None)
    listed = {item["_id"]: item for item in ran["listed"]["json"]}
    assert listed[first["id"]]["state"] == "changed_after_import"
    assert listed[first["id"]]["source_change"]["added_count"] == 1


def test_a_sign_out_warns_of_the_change_and_keeps_its_acknowledgement(ran) -> None:
    refused = ran["signout_refused"]
    assert refused["status"] == 409, refused
    detail = refused["json"]["detail"]
    assert detail["gate"] == "package_source_change"
    assert "has changed since its import: 1 file(s) added, 1 changed in size or time" in detail["message"]
    assert "Import the folder's new delivery with overwrite" in detail["message"]
    signed = ran["signout"]
    assert signed["status"] == 200, signed
    snapshot = signed["json"]["snapshot"]
    assert snapshot["package_source_change"]["added"] == ["notes/added.txt"]
    assert snapshot["acknowledged_package_source_change"] is True
    assert snapshot["package_source_change_acknowledgement_reason"] == _REASON
    event = ran["signout_event"]
    assert event["package_source_change"]["changed"] == ["family.ped"]
    assert event["package_source_change_concerns_fingerprint"] == detail["concerns_fingerprint"]


def test_importing_the_folders_new_delivery_with_update_keeps_the_change(ran) -> None:
    assert ran["update_job"]["status"] == "completed", ran["update_job"]
    first, second = ran["after_update"]
    # The record names the update's delivery; its reference stays the first, which stays changed.
    assert ran["record_after_update"] == {"import_mode": "update", "delivery": second["id"], "reference": first["id"]}
    assert second["state"] == "imported"
    assert first["state"] == "changed_after_import"
    assert first["source_change"]["fingerprint"] == second["package_fingerprint"]
    assert ran["change_after_update"]["delivery_id"] == first["id"]
    gated = ran["signout_after_update"]
    assert gated["status"] == 409 and gated["json"]["detail"]["gate"] == "package_source_change", gated


def test_an_update_imported_delivery_is_approved_again_only_with_overwrite(ran) -> None:
    first, second = ran["after_update"]
    assert ran["approvable_again"][second["id"]] is True
    assert ran["approvable_again"][first["id"]] is False
    refused = ran["again_with_update"]
    assert refused["status"] == 400, refused
    assert "approved again only to overwrite" in refused["json"]["detail"]
    dry_run = ran["again_dry_run"]
    assert dry_run["status"] == 200, dry_run
    assert dry_run["json"]["delivery"]["state"] == "imported"


def test_importing_a_new_delivery_with_overwrite_ends_the_change(ran) -> None:
    first, second, third = ran["changed_again"]
    # Changed again: the reference's change follows the folder.
    assert ran["overwrite_job"]["status"] == "completed", ran["overwrite_job"]
    assert ran["record_after_overwrite"] == {"import_mode": "overwrite", "delivery": third["id"], "reference": third["id"]}
    states = {row["id"]: row["state"] for row in ran["after_overwrite"]}
    assert (states[first["id"]], states[second["id"]], states[third["id"]]) == ("imported", "imported", "imported")
    assert ran["change_after_overwrite"] is None
    assert _actions(ran["first_events"])[-1] == "import_delivery_change_resolved"
    ended = ran["first_events"][-1]["after"]
    assert (ended["resolution"], ended["imported_delivery_id"]) == ("overwritten", third["id"])


def test_a_sign_out_checks_the_folder_itself_before_the_watcher_has(ran) -> None:
    refused = ran["signout_unseen"]
    assert refused["status"] == 409, refused
    detail = refused["json"]["detail"]
    assert detail["gate"] == "package_source_change"
    assert "This sign-out's own check of the folder found it" in detail["message"]
    change = detail["package_source_change"]
    assert (change["found_at_sign_out"], change["detected_at"], change["added"]) == (True, None, ["notes/unseen.txt"])
    # The watcher has not raised it: the reference is imported still.
    assert {row["state"] for row in ran["unseen_states"]} == {"imported"}
    # The folder back as imported, the sign-out passes the gate without an acknowledgement.
    passed = ran["signout_as_imported"]
    assert passed["status"] == 200, passed
    assert passed["json"]["snapshot"]["package_source_change"] is None


def test_refusing_the_new_delivery_of_a_changed_folder_ends_the_change_for_good(ran) -> None:
    third = ran["changed_for_refusal"][2]
    assert third["state"] == "changed_after_import"
    assert ran["family_change_for_refusal"]["added"] == ["notes/third.txt"]
    assert ran["refused"]["status"] == 200, ran["refused"]
    # At once, by the refusal.
    assert ran["after_refusal"][2]["state"] == "imported"
    assert ran["change_after_refusal"] is None
    # The next round raises nothing for the refused folder state.
    assert ran["refusal_round"].changed_after_import == 0
    assert [row["state"] for row in ran["after_refusal_round"]] == ["imported", "imported", "imported", "refused"]
    resolved = [event for event in ran["third_events"] if event["action"] == "import_delivery_change_resolved"]
    assert resolved[0]["after"]["resolution"] == "refused"
    assert resolved[0]["after"]["reason"] == "e2e: a note only, nothing to import."
    assert resolved[0]["actor"] != "system:import-watcher"


def test_a_gone_folder_confirmed_as_archived_no_longer_gates_and_is_frozen(ran) -> None:
    assert ran["as_imported_again"][2]["state"] == "imported"
    # Only a delivery changed after import with its folder gone can be archived.
    assert ran["archive_not_gone"]["status"] == 409, ran["archive_not_gone"]
    first_round, second_round = ran["gone_rounds"]
    assert first_round[2]["state"] == "imported"
    assert second_round[2]["state"] == "changed_after_import"
    assert second_round[2]["source_change"]["kind"] == "gone"
    assert ran["family_change_gone"]["kind"] == "gone" and ran["family_change_gone"]["archived"] is None
    assert ran["archive_refused_one"]["status"] == 409
    archived = ran["archived"]
    assert archived["status"] == 200, archived
    assert archived["json"]["state"] == "archived"
    assert archived["json"]["source_change"]["archived"]["reason"] == _ARCHIVED_REASON
    assert ran["archive_again"]["status"] == 409
    page = ran["family_change_archived"]
    assert page["kind"] == "gone" and page["archived"]["reason"] == _ARCHIVED_REASON
    assert page["archived"]["archived_by"] and page["archived"]["archived_at"]
    # A sign-out passes without an acknowledgement, and its record freezes the archiving.
    signed = ran["signout_archived"]
    assert signed["status"] == 200, signed
    snapshot = signed["json"]["snapshot"]
    assert snapshot["package_source_change"]["archived"]["reason"] == _ARCHIVED_REASON
    assert snapshot["acknowledged_package_source_change"] is False
    # The next round keeps it archived while the folder is gone.
    assert ran["still_archived"][2]["state"] == "archived"
    # Back unchanged, the change ends by itself.
    assert ran["back"][2]["state"] == "imported" and ran["change_back"] is None
    events = ran["third_events"]
    archiving = next(event for event in events if event["action"] == "import_delivery_archived")
    assert archiving["after"]["reason"] == _ARCHIVED_REASON and archiving["actor"] != "system:import-watcher"
    resolved = [event["after"] for event in events if event["action"] == "import_delivery_change_resolved"]
    assert [(entry["resolution"], entry["previous_state"]) for entry in resolved[1:]] == [("as_imported", "archived")]


def test_a_folder_state_neither_imported_nor_refused_is_raised_again(ran) -> None:
    raised = ran["raised_again"][2]
    assert raised["state"] == "changed_after_import"
    assert raised["source_change"]["added"] == ["notes/fourth.txt"]


def test_a_folder_added_by_hand_is_followed_after_its_import(ran) -> None:
    assert ran["hand_job"]["status"] == "completed", ran["hand_job"]
    (hand,) = ran["hand"]
    assert hand["origin"] == "manual"
    assert hand["state"] == "changed_after_import"
    assert hand["source_change"]["changed"] == ["snv/family.vcf"]


def test_the_import_inbox_chain_still_verifies(ran) -> None:
    verified = ran["verified"]
    assert verified["status"] == 200 and verified["json"]["verified"] is True, verified
