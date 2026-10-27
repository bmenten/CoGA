"""The phenotypes an import changes are on the family's clinical audit trail (E2E, SEC-1).

A phenotype added, changed or removed on screen is a ``phenotype`` event on the family's
hash-chained clinical audit trail (#887). So is every change an import makes to a family's
phenotypes (HPO terms). Through the real package import (``execute_family_package_import``,
the entry the import job runs), the real PED upload and Postgres:

* a package brings a member's phenotypes in: one event per member, as the user who ran the
  import, naming the package and its phenotype file, with what was added;
* the same package again changes nothing and writes nothing; one that changes a note and adds
  a term writes one event naming both;
* an overwrite that fails after its phenotypes were written is restored: the import's event
  and the restore's, which puts them back, and the family holds what it held before;
* a PED upload that replaces the family records the phenotypes it goes with as removed, before
  the family is deleted (the deletion unlinks the family's events, as it does every event of
  its trail; the chain, on the family's ID, keeps them);
* an import that fails on a family it created removes the family again, and records the
  phenotypes it had brought as removed;
* the family's trail, served through the API (``GET /api/families/{id}/clinical-audit``), holds
  the import's events as an edit's are served: before and after each an object (the member and
  the annotations it touched) or none;
* each family's chain verifies.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The values
are synthetic, and what the test creates is removed at the end (the append-only audit events
stay, as every e2e test's do).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

SEIZURE, DELAY, MICRO = "HP:0001250", "HP:0001263", "HP:0000252"
_TERMS = {SEIZURE: "Seizure", DELAY: "Global developmental delay", MICRO: "Microcephaly"}


def _write_pkg(root: Path, *, family_id: str, phenotypes: list[tuple[str, str, str]]) -> Path:
    """A one-member package whose phenotypes.tsv gives the member ``phenotypes``, each
    (term, status, note)."""
    root.mkdir(parents=True, exist_ok=True)
    sample = f"{family_id}_S1"
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t2\t2\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\n"
        "phenotypes:\n  file: phenotypes.tsv\n  format: hpo_tsv\n",
        encoding="utf-8",
    )
    lines = ["family_id\tindividual_id\thpo_id\tstatus\tnote"]
    lines += [f"{family_id}\t{sample}\t{term}\t{status}\t{note}" for term, status, note in phenotypes]
    (root / "phenotypes.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


async def _exercise(base: Path) -> dict[str, Any]:
    from fastapi import UploadFile
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.main import app

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
    from backend.app.services.ped_service import upload_ped_data
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        for term, label in _TERMS.items():
            await _harness.seed_hpo_term(session, term, label)
        await session.commit()
        labels = {
            row["hpo_id"]: row["label"]
            for row in (
                await session.execute(
                    text("SELECT hpo_id, label FROM hpo_term WHERE hpo_id IN (:a, :b, :c)"),
                    {"a": SEIZURE, "b": DELAY, "c": MICRO},
                )
            ).mappings()
        }

    run = uuid4().hex[:10]
    replaced_id, rolled_back_id = f"PHENO_IMPORT_{run}", f"PHENO_ROLLBACK_{run}"

    async def run_import(root: Path, conflict_mode: str, *, fail_after_phenotypes: bool = False) -> Any:
        real = package_import._import_dataset

        async def import_then_fail(*args: Any, **kwargs: Any) -> Any:
            summary = await real(*args, **kwargs)
            if kwargs["summary"].dataset_type == "phenotypes":
                raise RuntimeError("injected failure after the phenotypes were written")
            return summary

        if fail_after_phenotypes:
            package_import._import_dataset = import_then_fail
        try:
            async with sessionmaker() as session:
                return await package_import.execute_family_package_import(
                    session,
                    folder_path=str(root),
                    project_id=project_id,
                    dry_run=False,
                    user=admin,
                    conflict_mode=conflict_mode,
                )
        finally:
            package_import._import_dataset = real

    async def phenotype_events(family_id: str) -> list[dict[str, Any]]:
        async with sessionmaker() as session:
            return [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT actor, actor_id::text AS actor_id, summary, before, after, metadata,
                                   family_id IS NULL AS unlinked
                            FROM clinical_audit_events
                            WHERE family_identifier = :f AND action = 'phenotype'
                            ORDER BY created_at, id
                            """
                        ),
                        {"f": family_id},
                    )
                ).mappings()
            ]

    async def stored_phenotypes(family_id: str) -> list[tuple[str, str, str | None]]:
        async with sessionmaker() as session:
            return [
                tuple(row)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT ih.hpo_id, ih.status, ih.note
                            FROM individual_hpo ih JOIN families f ON f.id = ih.family_id
                            WHERE f.family_id = :f ORDER BY ih.hpo_id
                            """
                        ),
                        {"f": family_id},
                    )
                ).all()
            ]

    out: dict[str, Any] = {"admin": (admin.username, admin.id), "labels": labels, "ids": (replaced_id, rolled_back_id)}
    try:
        v1 = [(SEIZURE, "present", "since birth")]
        v2 = [(SEIZURE, "present", "since age 2"), (DELAY, "absent", "")]
        v3 = [*v2, (MICRO, "present", "")]
        out["v1"] = (await run_import(_write_pkg(base / "v1", family_id=replaced_id, phenotypes=v1), "cancel")).completed
        out["after_v1"] = await phenotype_events(replaced_id)
        out["v1_again"] = (await run_import(base / "v1", "overwrite")).completed
        out["after_v1_again"] = await phenotype_events(replaced_id)
        out["v2"] = (await run_import(_write_pkg(base / "v2", family_id=replaced_id, phenotypes=v2), "overwrite")).completed
        out["held_after_v2"] = await stored_phenotypes(replaced_id)
        failed = await run_import(
            _write_pkg(base / "v3", family_id=replaced_id, phenotypes=v3), "overwrite", fail_after_phenotypes=True
        )
        out["v3"] = (failed.completed, failed.error, any("restored" in line for line in failed.logs))
        out["held_after_v3"] = await stored_phenotypes(replaced_id)
        out["before_ped"] = await phenotype_events(replaced_id)
        # The trail as the family's report reads it (before: an import's events were lists,
        # which the served schema refused, and the trail answered 500).
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://e2e") as client:
            client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"
            served = await client.get(f"/api/families/{replaced_id}/clinical-audit")
        out["served"] = (served.status_code, served.json())

        async with sessionmaker() as session:
            ped = UploadFile(
                file=BytesIO(f"{replaced_id}\t{replaced_id}_S1\t0\t0\t2\t2\n".encode()), filename="replacement.ped"
            )
            await upload_ped_data(session, ped, True, admin, project_id)
        out["held_after_ped"] = await stored_phenotypes(replaced_id)
        out["after_ped"] = await phenotype_events(replaced_id)

        rolled_back = await run_import(
            _write_pkg(base / "new", family_id=rolled_back_id, phenotypes=v1), "cancel", fail_after_phenotypes=True
        )
        out["rolled_back"] = rolled_back.completed
        out["rolled_back_events"] = await phenotype_events(rolled_back_id)
        async with sessionmaker() as session:
            out["rolled_back_family"] = (
                await session.execute(text("SELECT count(*) FROM families WHERE family_id = :f"), {"f": rolled_back_id})
            ).scalar_one()
            out["chains"] = {}
            for family_id in (replaced_id, rolled_back_id):
                chain = await verify_clinical_audit_chain(session, family_id)
                out["chains"][family_id] = (chain.verified, chain.rows_checked)
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM families WHERE family_id IN (:a, :b)"), {"a": replaced_id, "b": rolled_back_id}
            )
            await session.commit()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_imported_phenotypes")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    return _harness.run_async(lambda: _exercise(base))


def _term(ran: dict[str, Any], hpo_id: str) -> str:
    return f"{hpo_id} {ran['labels'][hpo_id]}"


def test_a_package_brings_a_members_phenotypes_in_as_one_event(ran: dict[str, Any]) -> None:
    family_id = ran["ids"][0]
    assert ran["v1"] is True
    [event] = ran["after_v1"]
    assert event["summary"] == (
        f"Phenotypes imported for {family_id}_S1 by a package import of v1 (phenotypes.tsv): "
        f"added {_term(ran, SEIZURE)} (present)"
    )
    assert (event["actor"], event["actor_id"]) == ran["admin"]
    assert event["before"] is None
    assert event["after"]["sample_id"] == f"{family_id}_S1"
    assert [(state["hpo_id"], state["status"], state["note"]) for state in event["after"]["phenotypes"]] == [
        (SEIZURE, "present", "since birth")
    ]
    assert event["metadata"]["source"] == "package_import"
    assert event["metadata"]["change"] == "imported"
    assert event["metadata"]["package"].endswith("/v1")
    assert event["metadata"]["files"] == ["phenotypes.tsv"]


def test_the_same_package_again_writes_nothing(ran: dict[str, Any]) -> None:
    assert ran["v1_again"] is True
    assert ran["after_v1_again"] == ran["after_v1"]


def test_a_package_that_changes_a_note_and_adds_a_term_names_both(ran: dict[str, Any]) -> None:
    family_id = ran["ids"][0]
    assert ran["v2"] is True
    v2 = ran["before_ped"][1]
    assert v2["summary"] == (
        f"Phenotypes imported for {family_id}_S1 by a package import of v2 (phenotypes.tsv): "
        f"changed {_term(ran, SEIZURE)} (present): note edited; added {_term(ran, DELAY)} (absent)"
    )
    assert [state["note"] for state in v2["before"]["phenotypes"]] == ["since birth"]
    assert [(state["hpo_id"], state["note"]) for state in v2["after"]["phenotypes"]] == [
        (SEIZURE, "since age 2"),
        (DELAY, None),
    ]
    assert ran["held_after_v2"] == [(SEIZURE, "present", "since age 2"), (DELAY, "absent", None)]


def test_a_failed_overwrite_is_put_back_and_both_changes_are_on_the_trail(ran: dict[str, Any]) -> None:
    family_id = ran["ids"][0]
    completed, error, restored = ran["v3"]
    assert completed is False and "phenotypes" in (error or "") and restored is True
    assert ran["held_after_v3"] == ran["held_after_v2"]
    imported, put_back = ran["before_ped"][2:]
    assert imported["summary"] == (
        f"Phenotypes imported for {family_id}_S1 by a package import of v3 (phenotypes.tsv): "
        f"added {_term(ran, MICRO)} (present)"
    )
    assert put_back["summary"] == (
        f"Phenotypes put back for {family_id}_S1 after a failed package import of v3: "
        f"removed {_term(ran, MICRO)} (present)"
    )
    assert (put_back["before"], put_back["after"]) == (imported["after"], None)
    assert put_back["metadata"]["change"] == "put_back"
    assert len(ran["before_ped"]) == 4


def test_a_ped_upload_that_replaces_the_family_records_the_phenotypes_it_goes_with(ran: dict[str, Any]) -> None:
    family_id = ran["ids"][0]
    assert ran["held_after_ped"] == []
    removed = ran["after_ped"][-1]
    assert removed["summary"] == (
        f"Phenotypes removed for {family_id}_S1: the family was replaced by a PED upload (replacement.ped): "
        f"removed {_term(ran, SEIZURE)} (present); removed {_term(ran, DELAY)} (absent)"
    )
    assert [state["hpo_id"] for state in removed["before"]["phenotypes"]] == [SEIZURE, DELAY]
    assert removed["after"] is None
    assert (removed["metadata"]["source"], removed["metadata"]["change"], removed["metadata"]["file"]) == (
        "ped_upload",
        "replaced",
        "replacement.ped",
    )
    assert (removed["actor"], removed["actor_id"]) == ran["admin"]
    # The family the events were written for is gone: they are unlinked from it, every one.
    assert [event["unlinked"] for event in ran["after_ped"]] == [True] * 5


def test_a_family_a_failed_import_created_goes_with_its_phenotypes_recorded(ran: dict[str, Any]) -> None:
    family_id = ran["ids"][1]
    assert ran["rolled_back"] is False
    assert ran["rolled_back_family"] == 0
    imported, removed = ran["rolled_back_events"]
    assert imported["summary"].startswith(f"Phenotypes imported for {family_id}_S1 by a package import of new")
    assert removed["summary"] == (
        f"Phenotypes removed for {family_id}_S1 with the family a failed package import of new had created: "
        f"removed {_term(ran, SEIZURE)} (present)"
    )
    assert (removed["before"], removed["after"]) == (imported["after"], None)


def test_each_familys_chain_verifies(ran: dict[str, Any]) -> None:
    for family_id, (verified, rows_checked) in ran["chains"].items():
        assert verified is True, family_id
        assert rows_checked >= 2, family_id


def test_the_familys_trail_serves_the_imports_events(ran: dict[str, Any]) -> None:
    status, body = ran["served"]
    assert status == 200, body
    served = [event for event in body["events"] if event["action"] == "phenotype"]
    # Most recent first, as the trail lists them: the restore's, then the three imports'.
    assert [event["summary"] for event in served] == [event["summary"] for event in reversed(ran["before_ped"])]
    for event in served:
        assert event["variant_id"] is None
        for side in (event["before"], event["after"]):
            assert side is None or set(side) == {"sample_id", "phenotypes"}

