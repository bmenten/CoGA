"""A ClinVar load on real Postgres and ClickHouse: released whole, or nothing of it kept.

The unit tests (``test_clinvar_load_jobs.py``) stand in for the job's rows, the release's
partition and its record. Against the real schemas, with NCBI served by a stand-in and a
synthetic ClinVar file (``fixtures/clinvar``), this checks that

* a load run to its end stores one row per allele in the release's partition, keyed by the
  allele in minimal form, records the release with its file and SHA-256 in
  ``reference_dataset_imports``, adds its event to the clinical audit chain
  ``system:clinvar-releases``, and ends ``completed`` with the records read, stored and
  skipped;
* a load of the release that is current already ends ``unchanged``;
* a newer release with more than 5 % fewer records is refused (``refused``): its partition is
  dropped and the current release stays;
* ClickHouse keeps the current release and the one before it;
* the daily check (``CLINVAR_WEEKLY_LOAD``) leaves an assembly without a release to a start,
  loads a newer release as ``clinvar-weekly``, loads again a release whose load could not be
  completed (a ``.md5`` NCBI has not published yet, a load a restart stopped), tells the
  administrators once of one its checks refused, and does not load that one again.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from backend.tests.integration._clinvar_source import (
    FOLDER,
    SOURCE,
    StandInNcbi,
    clinvar_file,
    load_clinvar_release,
    stand_in_ncbi,
)
from backend.tests.integration._datastores import fresh_assembly, fresh_species, run_closing_stores

pytestmark = pytest.mark.integration


@pytest.fixture()
def clinvar(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str, StandInNcbi]:
    return stand_in_ncbi(monkeypatch)


async def _with_assembly(name: str, test: Any) -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clinvar_release_store import clinvar_table

    sm = get_postgres_sessionmaker()
    async with sm() as s:
        species = await fresh_species(s, name)
        await fresh_assembly(s, species, name)
        # One active load at a time, across the shared integration database.
        await s.execute(text("DELETE FROM clinvar_load_jobs WHERE status IN ('queued', 'running')"))
        await s.commit()
    try:
        await test(sm)
    finally:
        await execute_clickhouse(f"DROP TABLE IF EXISTS {clinvar_table(name)}")
        async with sm() as s:
            await s.execute(text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": species})
            await s.commit()


def test_a_release_is_stored_recorded_and_audited_whole(clinvar: tuple[Any, str, StandInNcbi]) -> None:
    jobs, assembly, ncbi = clinvar
    release = date(2026, 11, 22)
    body = clinvar_file(assembly, release)
    ncbi.put(release, body)

    async def test(sm: Any) -> None:
        from backend.app.core.clickhouse import execute_clickhouse
        from backend.app.services.clickhouse_variant_ids import small_allele_key
        from backend.app.services.clinvar_release_store import clinvar_table

        job = await load_clinvar_release(jobs, assembly)
        assert job["status"] == "completed", job["error"]
        assert (job["release"], job["records_read"], job["records_stored"]) == (release, 10, 8)
        assert job["records_skipped"] == {"no ALT": 1, "not a sequence of bases": 1}
        assert job["source_sha256"] == hashlib.sha256(body).hexdigest()

        rows = await execute_clickhouse(
            f"SELECT release, variantId, classification, classification_terms, review_status, "
            f"conditions, genes, included_only FROM {clinvar_table(assembly)} "
            "WHERE allele_key = %(key)s",
            {"key": small_allele_key(assembly, "M-3243-A-G")},
        )
        assert rows == [
            (
                release,
                "M-3243-A-G",
                "Pathogenic",
                ["pathogenic"],
                "criteria provided, single submitter",
                ["Synthetic mitochondrial disorder"],
                ["MT-SYN1"],
                False,
            )
        ]
        async with sm() as s:
            recorded = (
                await s.execute(
                    text(
                        "SELECT r.inserted, r.source_version, r.source_release_date, r.source_url "
                        "FROM reference_dataset_imports r JOIN assemblies a ON a.id = r.assembly_id "
                        "WHERE a.assembly_name = :a AND r.dataset_type = 'clinvar'"
                    ),
                    {"a": assembly},
                )
            ).mappings().one()
            event = (
                await s.execute(
                    text(
                        "SELECT action, actor, after FROM clinical_audit_events "
                        "WHERE family_identifier = 'system:clinvar-releases' "
                        "AND metadata->>'assembly' = :a"
                    ),
                    {"a": assembly},
                )
            ).mappings().one()
            status = await jobs.get_clinvar_load_status(s)
        assert recorded["inserted"] == 8 and recorded["source_release_date"] == release
        assert recorded["source_version"] == (
            f"2026-11-22 (clinvar_20261122.vcf.gz, SHA-256 {hashlib.sha256(body).hexdigest()})"
        )
        assert recorded["source_url"] == f"{SOURCE}/{FOLDER}/clinvar_20261122.vcf.gz"
        assert (event["action"], event["actor"]) == ("clinvar_release_current", "startup-bootstrap")
        assert event["after"]["release"] == "2026-11-22" and event["after"]["records"] == 8
        [held] = [entry for entry in status.assemblies if entry.assembly_name == assembly]
        assert held.current is not None and held.current.release == release and held.previous is None

        # The release that is current already is not loaded again.
        again = await load_clinvar_release(jobs, assembly)
        assert again["status"] == "unchanged" and again["records_stored"] == 0

    run_closing_stores(lambda: _with_assembly(assembly, test), init_schema=True)


def test_a_refused_release_leaves_nothing_and_two_releases_are_kept(clinvar: tuple[Any, str, StandInNcbi]) -> None:
    jobs, assembly, ncbi = clinvar
    first, second, refused, third = date(2026, 11, 8), date(2026, 11, 15), date(2026, 11, 22), date(2026, 11, 29)

    async def test(sm: Any) -> None:
        from backend.app.services.clinvar_release_store import stored_clinvar_releases

        ncbi.put(first, clinvar_file(assembly, first))
        assert (await load_clinvar_release(jobs, assembly))["status"] == "completed"
        ncbi.put(second, clinvar_file(assembly, second))
        assert (await load_clinvar_release(jobs, assembly))["status"] == "completed"
        assert await stored_clinvar_releases(assembly) == {first, second}

        # 7 of 8 records is 12.5 % fewer: refused, its partition dropped, the current kept.
        ncbi.put(refused, clinvar_file(assembly, refused, records=8))
        job = await load_clinvar_release(jobs, assembly)
        assert job["status"] == "refused" and "more than 5% fewer" in job["error"]
        assert await stored_clinvar_releases(assembly) == {first, second}

        # A complete newer release becomes current; the one before the previous is dropped.
        ncbi.files.clear()
        ncbi.put(third, clinvar_file(assembly, third))
        assert (await load_clinvar_release(jobs, assembly))["status"] == "completed"
        assert await stored_clinvar_releases(assembly) == {second, third}

    run_closing_stores(lambda: _with_assembly(assembly, test), init_schema=True)


def test_the_daily_check_loads_a_newer_release_and_mails_a_refused_one_once(
    clinvar: tuple[Any, str, StandInNcbi], monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import clinvar_weekly_load as weekly

    jobs, assembly, ncbi = clinvar
    first, second, third, refused = date(2026, 11, 8), date(2026, 11, 15), date(2026, 11, 22), date(2026, 11, 29)
    mails: list[Any] = []

    async def administrators(_session: Any) -> list[str]:
        return ["admin@example.org"]

    monkeypatch.setattr(weekly, "mail_enabled", lambda: True)
    monkeypatch.setattr(weekly, "send_mail", mails.append)
    monkeypatch.setattr(weekly, "administrator_addresses", administrators)

    async def checked() -> list[Any]:
        # The test's own assembly: NCBI's folder of any other one is not stood in for.
        return [job for job in await weekly.check_for_new_clinvar_releases() if job.assembly_name == assembly]

    async def test(sm: Any) -> None:
        from backend.app.services.clinvar_releases import current_clinvar_release

        async def weekly_jobs() -> list[tuple[Any, str]]:
            async with sm() as s:
                rows = await s.execute(
                    text(
                        "SELECT release, status FROM clinvar_load_jobs "
                        "WHERE assembly_name = :a AND requested_by = 'clinvar-weekly' ORDER BY release, status"
                    ),
                    {"a": assembly},
                )
                return [tuple(row) for row in rows]

        # An assembly without a release: its first is a start's or an administrator's, not the
        # check's, which neither loads nor asks NCBI.
        ncbi.put(first, clinvar_file(assembly, first))
        assert await checked() == [] and await weekly_jobs() == []
        assert (await load_clinvar_release(jobs, assembly))["status"] == "completed"
        # Nothing newer: no load, no job.
        assert await checked() == [] and await weekly_jobs() == []

        # A newer release whose .md5 NCBI has not published yet: the load is not completed, no
        # mail goes out, and the next check loads the release again.
        ncbi.put(second, clinvar_file(assembly, second))
        ncbi.md5_withheld.add(second)
        [failed] = await checked()
        assert (failed.status, failed.requested_by, failed.release) == ("failed", "clinvar-weekly", second)
        assert (failed.error or "").startswith("The ClinVar file could not be downloaded (HTTPStatusError)")
        ncbi.md5_withheld.clear()
        [loaded] = await checked()
        assert (loaded.status, loaded.release) == ("completed", second)

        # A weekly load a restart stopped (its worker silent past the stale window) is closed as
        # failed, and the check loads its release again.
        ncbi.put(third, clinvar_file(assembly, third))
        async with sm() as s:
            await s.execute(
                text(
                    "INSERT INTO clinvar_load_jobs (assembly_id, assembly_name, release, status, requested_by, "
                    "worker_id, requested_at, started_at, heartbeat_at) "
                    "SELECT id, assembly_name, :release, 'running', 'clinvar-weekly', 'gone', "
                    "now() - interval '1 hour', now() - interval '1 hour', now() - interval '11 minutes' "
                    "FROM assemblies WHERE assembly_name = :a"
                ),
                {"a": assembly, "release": third},
            )
            await s.commit()
        [loaded] = await checked()
        assert (loaded.status, loaded.release) == ("completed", third)
        assert mails == []

        # A newer release with 7 of 8 records is refused: the current release stays, and every
        # active administrator is told once.
        ncbi.put(refused, clinvar_file(assembly, refused, records=7))
        [failed] = await checked()
        assert (failed.status, failed.release) == ("refused", refused) and "more than 5% fewer" in (failed.error or "")
        [mail] = mails
        body = mail.get_content()
        assert mail["To"] == "admin@example.org" and mail["Subject"] == weekly.SUBJECT
        assert f"did not release ClinVar {refused.isoformat()}" in body and assembly in body
        assert f"The current release stays ClinVar {third.isoformat()}." in body
        # The check does not load the refused release again, and tells no one twice.
        assert await checked() == [] and len(mails) == 1
        assert await weekly_jobs() == [
            (second, "completed"),
            (second, "failed"),
            (third, "completed"),
            (third, "failed"),
            (refused, "refused"),
        ]
        async with sm() as s:
            current = await current_clinvar_release(s, assembly)
            mailed = (
                await s.execute(
                    text("SELECT mailed_at IS NOT NULL FROM clinvar_load_jobs WHERE assembly_name = :a AND status = 'refused'"),
                    {"a": assembly},
                )
            ).scalar_one()
        assert current is not None and current.release == third and mailed

    run_closing_stores(lambda: _with_assembly(assembly, test), init_schema=True)
