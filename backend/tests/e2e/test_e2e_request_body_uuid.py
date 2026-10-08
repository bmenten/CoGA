"""A UUID a request body names a record by is read as one in a path is (E2E).

The three writes to the NIPT artifact list (``POST /api/admin/nipt/artifacts``, its
``auto-seed`` and its table ``import``, a form) and the import request (``POST
/api/family-imports``) bound the ``assembly_id`` or ``project_id`` of their body as they
received it. A value that is no UUID failed asyncpg's uuid codec or the insert's uuid cast,
and a UUID of no assembly or project failed the insert's foreign key: each a 500. Each now
reads the value once (``core/sql.py``: ``require_uuid``) and looks the record up before it
writes anything.

Over the real API (an in-process ``httpx.ASGITransport`` client, see test_e2e_api_contract.py)
and Postgres and ClickHouse, with synthetic records made for the test (a project, a family in
it, a variant tag), the seeded admin and a viewer who is a member of the project:

* a value that is no UUID, and a UUID of no record as it is, in braces and after
  ``urn:uuid:``, are answered as an unknown assembly or project is, with a 404, and so is a
  blank project; nothing is written for them: no artifact, no event in the artifact list's
  audit chain, no import job;
* the assembly's and the project's id in braces, after ``urn:uuid:``, in capitals or without
  hyphens names that record: the artifact, its audit event and the import job hold the
  canonical id; an import request that names no project (``null`` or ``""``) is queued
  without one, as before;
* the artifact list's audit chain verifies afterwards;
* the viewer is refused (403) by each of these routes, whatever the id, and nothing is
  written;
* each answer is in ``audit_log_events`` under its caller's name;
* every operation whose JSON or form body names a project, an assembly, a species or a user
  by its UUID, with a value that is no UUID, a UUID in braces and a ``urn:uuid:`` UUID in
  each such field in turn, answers as it answers a UUID that names no record, or with a 400
  or 422, and none with a 500; every other ``*_id`` field a body carries is one the test
  knows to hold text.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

# The *_id fields of a request body that name a record by its UUID, and those that hold an
# identifier of text: a family, a sample, a variant, an HPO term, a PanelApp panel, a taxon,
# a UI session or element, an OAuth client. A body field the API gains fails
# test_every_id_a_body_carries_is_known until it is listed in one of the two.
UUID_BODY_FIELDS = {"assembly_id", "project_id", "project_ids", "shared_project_ids", "species_id", "user_ids"}
TEXT_BODY_FIELDS = {
    "client_id",
    "family_id",
    "father_id",
    "hpo_id",
    "mother_id",
    "new_sample_id",
    "panelapp_id",
    "partner_variant_id",
    "sample_id",
    "session_id",
    "target_id",
    "tax_id",
    "variant_id",
}

ADMIN_ONLY = (403, {"detail": "Admin access required"})
ASSEMBLY_NOT_FOUND = (404, {"detail": "Assembly not found"})
PROJECT_NOT_FOUND = (404, {"detail": "Project not found"})
# A position on chr1 no golden-trio or demo variant is near: the table import finds no
# annotation that would keep an allele off the list.
TABLE_POSITION = 248_950_000


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code}
    try:
        out["json"] = resp.json()
    except ValueError:
        out["json"] = None
        out["text"] = resp.text[:200]
    return out


def _status(answer: dict[str, Any]) -> tuple[int, Any]:
    return answer["status"], answer["json"]


def _spellings(record_id: str) -> dict[str, str]:
    return {
        "canonical": record_id,
        "braces": "{" + record_id + "}",
        "urn": "urn:uuid:" + record_id,
        "capitals": record_id.upper(),
        "no_hyphens": record_id.replace("-", ""),
    }


def _refused(not_a_uuid: str) -> dict[str, str]:
    unknown = str(uuid4())
    return {
        "not_a_uuid": not_a_uuid,
        "unknown": unknown,
        "unknown_braces": "{" + unknown + "}",
        "unknown_urn": "urn:uuid:" + unknown,
    }


def _resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _id_fields(schema: dict[str, Any], components: dict[str, Any], prefix: str = "", depth: int = 0) -> list[str]:
    """The *_id and *_ids fields of a request body, as dotted paths (``members[].sample_id``)."""
    schema = _resolve(schema, components)
    if depth > 6:
        return []
    found: list[str] = []
    for key in ("anyOf", "oneOf", "allOf"):
        for option in schema.get(key, []):
            found += _id_fields(option, components, prefix, depth + 1)
    for name, field in (schema.get("properties") or {}).items():
        if name.endswith(("_id", "_ids")):
            found.append(prefix + name)
        field = _resolve(field, components)
        if field.get("type") == "array":
            found += _id_fields(field.get("items", {}), components, f"{prefix}{name}[].", depth + 1)
        else:
            found += _id_fields(field, components, f"{prefix}{name}.", depth + 1)
    return found


def _body_operations() -> list[tuple[str, str, str, list[str]]]:
    """(method, path, media type, the *_id fields of the body) of each operation whose
    body carries one."""
    from backend.app.main import app

    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    operations = []
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            content = (operation.get("requestBody") or {}).get("content") or {}
            for media, body in content.items():
                fields = _id_fields(body.get("schema", {}), components)
                if fields:
                    operations.append((method.upper(), path, media, fields))
    return operations


def _sweep_bodies(run: str, records: dict[str, str]) -> dict[tuple[str, str], dict[str, Any]]:
    """For each operation whose body names a record by its UUID, a body it accepts apart
    from that field, so that the request reaches the record's lookup."""
    tag = {"label": f"e2e-body-{run}", "scope": "project", "project_id": records["project"]}
    return {
        ("POST", "/api/ped/manual"): {
            "family_id": f"E2EBODYP{run}",
            "members": [{"sample_id": f"E2EBODYP{run}M", "sex": "female"}],
        },
        ("PUT", "/api/families/{family_id}/roi"): {},
        ("POST", "/api/families/{family_id}/small-variant-tags"): tag,
        ("PUT", "/api/families/{family_id}/small-variant-tags/{tag_key}"): tag,
        ("POST", "/api/projects/"): {
            "name": f"e2e-body-uuid-{run}-probe",
            "species_id": records["species"],
            "assembly_id": records["assembly"],
        },
        ("PUT", "/api/projects/{project_id}"): {},
        ("POST", "/api/assemblies/"): {
            "species_id": records["species"],
            "assembly_name": f"E2EBODY{run}",
            "version": "probe",
            "release_date": "2026-01-01",
        },
        ("POST", "/api/admin/nipt/artifacts"): {"variant_id": f"1-1-A-G-{run}", "assay_key": f"E2E_BODY_{run}"},
        ("POST", "/api/admin/nipt/artifacts/auto-seed"): {"assay_key": f"E2E_BODY_{run}"},
        ("POST", "/api/admin/nipt/artifacts/import"): {"assay_key": f"E2E_BODY_{run}"},
        ("PUT", "/api/admin/families/{family_id}/projects"): {},
        ("POST", "/api/admin/variant-tags"): tag,
        ("PUT", "/api/admin/variant-tags/{tag_key}"): tag,
        ("POST", "/api/family-imports"): {"folder_path": f"/nonexistent/e2e-body-uuid-{run}/sweep", "dry_run": True},
        ("POST", "/api/family-imports/validate"): {"folder_path": f"/nonexistent/e2e-body-uuid-{run}/sweep"},
    }


def _table(position: int) -> tuple[str, bytes, str]:
    return ("recurrent.tsv", f"CHROM\tPOS\tREF\tALT\nchr1\t{position}\tA\tG\n".encode(), "text/tab-separated-values")


async def _sweep(ac: Any, run: str, records: dict[str, str]) -> list[dict[str, Any]]:
    """Each operation whose body names a record by its UUID; each such field in turn
    holding a value that is no UUID, a UUID in braces and a ``urn:uuid:`` UUID, and, to
    compare, a UUID that names no record. The path names the test's own family, project and
    tag; no value names a record, so nothing is found and nothing changes."""
    bodies = _sweep_bodies(run, records)
    path_values = {
        "family_id": records["family"],
        "sample_id": records["sample"],
        "project_id": records["project"],
        "tag_key": records["tag_key"],
    }
    probes: list[dict[str, Any]] = []
    for method, path, media, fields in _body_operations():
        for field in fields:
            if field not in UUID_BODY_FIELDS:
                continue
            url = path
            for name, value in path_values.items():
                url = url.replace("{" + name + "}", quote(value, safe=""))
            unknown = str(uuid4())
            forms = {
                "unknown": unknown,
                "not_a_uuid": f"NOT-A-UUID-BODY-{run}",
                "braces": "{" + str(uuid4()) + "}",
                "urn": "urn:uuid:" + str(uuid4()),
            }
            for form, value in forms.items():
                body = dict(bodies.get((method, path), {}))
                body[field] = [value] if field.endswith("_ids") else value
                if media == "multipart/form-data":
                    resp = await ac.request(method, url, data=body, files={"file": _table(TABLE_POSITION)})
                else:
                    resp = await ac.request(method, url, json=body)
                probes.append(
                    {
                        "operation": (method, path),
                        "field": field,
                        "form": form,
                        **_cap(resp),
                    }
                )
    return probes


async def _counts(session: Any, sql: str, params: dict[str, Any]) -> dict[str, int]:
    from sqlalchemy import text

    rows = (await session.execute(text(sql), params)).all()
    return {str(key): int(count) for key, count in rows}


async def _request_audit(session: Any, paths: list[str], user_emails: list[str]) -> dict[str, int]:
    """How many POSTs of each path ``audit_log_events`` holds, by caller and answer."""
    return await _counts(
        session,
        """
        SELECT path || ' ' || user_email || ' ' || status_code::text, count(*)
        FROM audit_log_events
        WHERE method = 'POST' AND path = ANY(:paths) AND user_email = ANY(:emails)
        GROUP BY 1
        """,
        {"paths": paths, "emails": user_emails},
    )


async def _collect(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.nipt_artifact_pg import NIPT_ARTIFACT_AUDIT_CHAIN
    from backend.tests.e2e import _harness

    family, sample = f"E2EBODY{run}", f"E2EBODY{run}M"
    assay = f"E2E_BODY_{run}"
    folder = f"/nonexistent/e2e-body-uuid-{run}"
    viewer_email, viewer_password = f"e2e-body-viewer-{run}@example.com", f"viewer-password-{run}"
    audited_paths = ["/api/admin/nipt/artifacts", "/api/family-imports"]

    await init_postgres_schema()
    # The table import reads the assembly's annotation in ClickHouse.
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, _project, assembly_id = await _harness.ensure_e2e_project(session)
        species_id = (
            await session.execute(
                text("SELECT species_id::text FROM assemblies WHERE id = CAST(:id AS uuid)"), {"id": assembly_id}
            )
        ).scalar_one()
        viewer_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                    VALUES (:email, :hashed, 'viewer', :email, '{}'::jsonb, :created_at)
                    RETURNING id::text
                    """
                ),
                {
                    "email": viewer_email,
                    "hashed": get_password_hash(viewer_password),
                    "created_at": datetime.now(timezone.utc),
                },
            )
        ).scalar_one()
        await session.commit()

    not_a_uuid = f"NOT-A-UUID-{run}"
    out: dict[str, Any] = {
        "run": run,
        "assembly": assembly_id,
        "admin_email": settings.admin_email,
        "viewer_email": viewer_email,
    }
    r: dict[str, Any] = {"add": {}, "seed": {}, "import": {}, "job": {}, "viewer": {}}
    out["responses"] = r
    records: dict[str, str] = {"family": family, "sample": sample, "assembly": assembly_id, "species": species_id}
    out["records"] = records
    # A request the app fails on is answered 500, as a server answers it, so that every step
    # below is taken and asserted on its own.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac, AsyncClient(
            transport=transport, base_url="http://e2e"
        ) as viewer:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            # --- the records -----------------------------------------------------------
            created = await ac.post(
                "/api/projects/",
                json={
                    "name": f"e2e-body-uuid-{run}",
                    "species_id": species_id,
                    "assembly_id": assembly_id,
                    "user_ids": [viewer_id],
                },
            )
            assert created.status_code == 201, created.text
            records["project"] = created.json()["_id"]
            made = await ac.post(
                "/api/ped/manual",
                json={
                    "family_id": family,
                    "project_id": records["project"],
                    "members": [{"sample_id": sample, "sex": "female", "is_proband": True}],
                },
            )
            assert made.status_code == 200, made.text
            tag = await ac.post(
                "/api/admin/variant-tags",
                json={"label": f"e2e-body-{run}", "scope": "project", "project_id": records["project"]},
            )
            assert tag.status_code == 200, tag.text
            records["tag_key"] = tag.json()["key"]
            login = await viewer.post("/api/auth/login", json={"email": viewer_email, "password": viewer_password})
            assert login.status_code == 200, login.text
            viewer.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

            async with sessionmaker() as session:
                chain_events = "SELECT count(*) FROM clinical_audit_events WHERE family_identifier = :chain"
                out["chain_before"] = (
                    await session.execute(text(chain_events), {"chain": NIPT_ARTIFACT_AUDIT_CHAIN})
                ).scalar_one()
                out["audit_before"] = await _request_audit(
                    session, audited_paths, [settings.admin_email, viewer_email]
                )

            # --- the NIPT artifact list: each value, as the admin -----------------------
            assemblies = {**_refused(not_a_uuid), **_spellings(assembly_id)}
            for index, (name, value) in enumerate(assemblies.items()):
                r["add"][name] = _cap(
                    await ac.post(
                        "/api/admin/nipt/artifacts",
                        json={"assembly_id": value, "assay_key": assay, "variant_id": f"1-100-A-G-{run}-{name}"},
                    )
                )
                r["seed"][name] = _cap(
                    await ac.post("/api/admin/nipt/artifacts/auto-seed", json={"assembly_id": value, "assay_key": assay})
                )
                r["import"][name] = _cap(
                    await ac.post(
                        "/api/admin/nipt/artifacts/import",
                        data={"assembly_id": value, "assay_key": assay},
                        files={"file": _table(TABLE_POSITION + index)},
                    )
                )

            # --- the import request: each project, as the admin -------------------------
            projects = {
                "none_null": None,
                "none_empty": "",
                "blank": "  ",
                **_refused(not_a_uuid),
                **_spellings(records["project"]),
            }
            for name, value in projects.items():
                r["job"][name] = _cap(
                    await ac.post(
                        "/api/family-imports",
                        json={"folder_path": f"{folder}/{name}", "project_id": value, "dry_run": True},
                    )
                )

            # --- the viewer: refused before the id is read ------------------------------
            for name, value in {"not_a_uuid": not_a_uuid, "unknown": str(uuid4()), "canonical": assembly_id}.items():
                r["viewer"][f"add_{name}"] = _cap(
                    await viewer.post(
                        "/api/admin/nipt/artifacts",
                        json={"assembly_id": value, "assay_key": assay, "variant_id": f"1-200-A-G-{run}-{name}"},
                    )
                )
                r["viewer"][f"seed_{name}"] = _cap(
                    await viewer.post(
                        "/api/admin/nipt/artifacts/auto-seed", json={"assembly_id": value, "assay_key": assay}
                    )
                )
                r["viewer"][f"import_{name}"] = _cap(
                    await viewer.post(
                        "/api/admin/nipt/artifacts/import",
                        data={"assembly_id": value, "assay_key": assay},
                        files={"file": _table(TABLE_POSITION + 99)},
                    )
                )
            for name, value in {"not_a_uuid": not_a_uuid, "unknown": str(uuid4()), "canonical": records["project"]}.items():
                r["viewer"][f"job_{name}"] = _cap(
                    await viewer.post(
                        "/api/family-imports",
                        json={"folder_path": f"{folder}/viewer-{name}", "project_id": value, "dry_run": True},
                    )
                )

            # --- what was written ---------------------------------------------------------
            r["chain"] = _cap(
                await ac.get(
                    "/api/admin/integrity/verify",
                    params={"table": "clinical_audit_events", "family_id": NIPT_ARTIFACT_AUDIT_CHAIN},
                )
            )
            async with sessionmaker() as session:
                out["chain_after"] = (
                    await session.execute(text(chain_events), {"chain": NIPT_ARTIFACT_AUDIT_CHAIN})
                ).scalar_one()
                events = await session.execute(
                    text(
                        """
                        SELECT action, variant_id, metadata
                        FROM clinical_audit_events
                        WHERE family_identifier = :chain AND metadata ->> 'assay_key' = :assay
                        ORDER BY created_at
                        """
                    ),
                    {"chain": NIPT_ARTIFACT_AUDIT_CHAIN, "assay": assay},
                )
                out["events"] = [
                    {
                        "action": row["action"],
                        "variant_id": row["variant_id"],
                        "assembly_id": (row["metadata"] or {}).get("assembly_id"),
                    }
                    for row in events.mappings().all()
                ]
                artifacts = await session.execute(
                    text(
                        """
                        SELECT variant_id, assembly_id::text AS assembly_id
                        FROM nipt_artifact_variants
                        WHERE assay_key = :assay OR variant_id LIKE :run
                        ORDER BY variant_id
                        """
                    ),
                    {"assay": assay, "run": f"%-{run}-%"},
                )
                out["artifacts"] = [dict(row) for row in artifacts.mappings().all()]
                jobs = await session.execute(
                    text(
                        """
                        SELECT submitted_path, project_id::text AS project_id
                        FROM family_import_jobs
                        WHERE submitted_path LIKE :folder
                        """
                    ),
                    {"folder": f"{folder}/%"},
                )
                out["jobs"] = {row["submitted_path"].rsplit("/", 1)[-1]: row["project_id"] for row in jobs.mappings()}
                out["audit_after"] = await _request_audit(
                    session, audited_paths, [settings.admin_email, viewer_email]
                )

            out["sweep"] = await _sweep(ac, run, records)
            async with sessionmaker() as session:
                leftovers = await session.execute(
                    text(
                        """
                        SELECT 'family ' || family_id FROM families WHERE family_id LIKE :family
                        UNION ALL
                        SELECT 'project ' || name FROM projects WHERE name LIKE :project
                        UNION ALL
                        SELECT 'assembly ' || assembly_name FROM assemblies WHERE assembly_name LIKE :family
                        """
                    ),
                    {"family": f"E2EBODY%{run}%", "project": f"e2e-body-uuid-{run}-%"},
                )
                # What the sweep made: nothing, but for the family the test made itself.
                out["sweep_made"] = sorted(str(row[0]) for row in leftovers.all())
    finally:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
            listed = await ac.get("/api/admin/nipt/artifacts", params={"assembly_id": assembly_id, "assay_key": assay})
            for row in listed.json() if listed.status_code == 200 else []:
                await ac.delete(f"/api/admin/nipt/artifacts/{row['id']}")
            if "tag_key" in records:
                await ac.delete(f"/api/admin/variant-tags/{records['tag_key']}")
            for made_family in (family, f"E2EBODYP{run}"):
                await ac.delete(f"/api/admin/families/{made_family}?confirm=true")
            if "project" in records:
                await ac.delete(f"/api/projects/{records['project']}")
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM family_import_jobs WHERE submitted_path LIKE :folder"), {"folder": f"{folder}/%"}
            )
            await session.execute(
                text("DELETE FROM projects WHERE name LIKE :name"), {"name": f"e2e-body-uuid-{run}-%"}
            )
            await session.execute(
                text("DELETE FROM assemblies WHERE assembly_name = :name"), {"name": f"E2EBODY{run}"}
            )
            await session.execute(text("DELETE FROM users WHERE email = :email"), {"email": viewer_email})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def snap() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:10].upper()
    return _harness.run_async(lambda: _collect(run))


REFUSED = ["not_a_uuid", "unknown", "unknown_braces", "unknown_urn"]
SPELLINGS = ["canonical", "braces", "urn", "capitals", "no_hyphens"]


@pytest.mark.parametrize("route", ["add", "seed", "import"])
@pytest.mark.parametrize("name", REFUSED)
def test_an_artifact_for_an_assembly_that_is_not_one_is_refused_as_unknown(
    snap: dict[str, Any], route: str, name: str
) -> None:
    # A value that is no UUID failed the uuid codec, an unknown UUID the add's foreign key,
    # and the braces and the prefix the codec again: each a 500.
    assert _status(snap["responses"][route][name]) == ASSEMBLY_NOT_FOUND, snap["responses"][route][name]


def test_nothing_is_written_for_an_assembly_that_is_not_one(snap: dict[str, Any]) -> None:
    written = {row["variant_id"] for row in snap["artifacts"]}
    # The add of each refused value, and the allele of its table import (the refused values
    # came first: the positions from TABLE_POSITION on), are not on the list.
    assert written.isdisjoint(f"1-100-A-G-{snap['run']}-{name}" for name in REFUSED)
    assert written.isdisjoint(f"1-{TABLE_POSITION + index}-A-G" for index in range(len(REFUSED)))
    # The entries the list holds are the five accepted adds and imports, on the real assembly.
    assert len(written) == 2 * len(SPELLINGS), written
    assert {row["assembly_id"] for row in snap["artifacts"]} == {snap["assembly"]}


@pytest.mark.parametrize("name", SPELLINGS)
def test_each_spelling_of_the_assembly_id_names_the_assembly(snap: dict[str, Any], name: str) -> None:
    r = snap["responses"]
    added = r["add"][name]
    assert added["status"] == 200, added
    # The entry is stored, and answered, under the canonical id.
    assert added["json"]["assembly_id"] == snap["assembly"]
    assert added["json"]["variant_id"] == f"1-100-A-G-{snap['run']}-{name}"
    assert _status(r["seed"][name]) == (200, {"seeded": 0, "min_carrier_samples": 5})
    assert r["import"][name]["status"] == 200, r["import"][name]
    assert r["import"][name]["json"]["imported"] == 1


def test_the_audit_trail_holds_one_event_per_change_under_the_canonical_id(snap: dict[str, Any]) -> None:
    run = snap["run"]
    events = snap["events"]
    added = [event for event in events if event["action"] == "nipt_artifact_added"]
    imported = [event for event in events if event["action"] == "nipt_artifacts_imported"]
    # One event per accepted add and import, none for a refused one, nothing else.
    assert sorted(event["variant_id"] for event in added) == sorted(f"1-100-A-G-{run}-{name}" for name in SPELLINGS)
    assert len(imported) == len(SPELLINGS)
    assert len(events) == 2 * len(SPELLINGS), events
    # Each records the assembly by its canonical id: a spelling as received (capitals) was
    # recorded as it came, and an audit search by the assembly's id missed it.
    assert {event["assembly_id"] for event in events} == {snap["assembly"]}
    # The viewer's attempts and the cleanup are not among them; the chain grew by exactly these.
    assert snap["chain_after"] - snap["chain_before"] == len(events)


def test_the_artifact_lists_audit_chain_verifies(snap: dict[str, Any]) -> None:
    chain = snap["responses"]["chain"]
    assert chain["status"] == 200, chain
    assert chain["json"]["verified"] is True, chain["json"]
    assert chain["json"]["rows_checked"] == snap["chain_after"]


@pytest.mark.parametrize("name", ["blank", *REFUSED])
def test_an_import_naming_a_project_that_is_not_one_is_refused_as_unknown(snap: dict[str, Any], name: str) -> None:
    # A value that is no UUID failed the insert's uuid cast, an unknown UUID its foreign key
    # (in braces too): each a 500.
    assert _status(snap["responses"]["job"][name]) == PROJECT_NOT_FOUND, snap["responses"]["job"][name]
    # No job was written for it.
    assert name not in snap["jobs"]


@pytest.mark.parametrize("name", SPELLINGS)
def test_each_spelling_of_the_project_id_names_the_project(snap: dict[str, Any], name: str) -> None:
    job = snap["responses"]["job"][name]
    assert job["status"] == 200, job
    assert job["json"]["project_id"] == snap["records"]["project"]
    assert snap["jobs"][name] == snap["records"]["project"]


@pytest.mark.parametrize("name", ["none_null", "none_empty"])
def test_an_import_that_names_no_project_is_queued_without_one(snap: dict[str, Any], name: str) -> None:
    job = snap["responses"]["job"][name]
    assert job["status"] == 200, job
    assert job["json"]["project_id"] is None
    assert snap["jobs"][name] is None


def test_the_viewer_is_refused_whatever_the_id(snap: dict[str, Any]) -> None:
    answers = snap["responses"]["viewer"]
    assert len(answers) == 12
    for name, answer in answers.items():
        assert _status(answer) == ADMIN_ONLY, (name, answer)
    # Nothing was written for them.
    written = {row["variant_id"] for row in snap["artifacts"]}
    assert not [variant for variant in written if variant.startswith("1-200-A-G-")]
    assert f"1-{TABLE_POSITION + 99}-A-G" not in written
    assert not [path for path in snap["jobs"] if path.startswith("viewer-")]


def test_each_answer_is_audited_under_its_callers_name(snap: dict[str, Any]) -> None:
    before, after = snap["audit_before"], snap["audit_after"]
    added = {key: after.get(key, 0) - before.get(key, 0) for key in after}
    admin, viewer = snap["admin_email"], snap["viewer_email"]
    artifacts, jobs = "/api/admin/nipt/artifacts", "/api/family-imports"
    assert {key: count for key, count in added.items() if count} == {
        f"{artifacts} {admin} 404": len(REFUSED),
        f"{artifacts} {admin} 200": len(SPELLINGS),
        f"{artifacts} {viewer} 403": 3,
        f"{jobs} {admin} 404": len(REFUSED) + 1,
        f"{jobs} {admin} 200": len(SPELLINGS) + 2,
        f"{jobs} {viewer} 403": 3,
    }


def test_every_id_a_body_carries_is_known() -> None:
    unknown: list[tuple[str, str, str]] = []
    nested: list[tuple[str, str, str]] = []
    for method, path, _media, fields in _body_operations():
        for field in fields:
            name = field.rsplit(".", 1)[-1]
            if name not in UUID_BODY_FIELDS | TEXT_BODY_FIELDS:
                unknown.append((method, path, field))
            elif name in UUID_BODY_FIELDS and field != name:
                nested.append((method, path, field))
    # A new body id: list it as a UUID (the sweep below then sends it every form) or as text.
    assert unknown == []
    # The sweep sets a body's top-level fields only.
    assert nested == []


def test_no_operation_answers_a_body_id_that_is_no_uuid_with_a_500(snap: dict[str, Any]) -> None:
    probes = snap["sweep"]
    operations = {probe["operation"] for probe in probes}
    assert len(operations) >= 15, operations
    assert [probe for probe in probes if probe["status"] >= 500] == []
    baseline = {(probe["operation"], probe["field"]): probe for probe in probes if probe["form"] == "unknown"}
    # Each body reached the record's lookup: none was refused as malformed (422) apart from
    # the field (give a new operation its body in _sweep_bodies).
    assert [probe for probe in baseline.values() if probe["status"] == 422] == []
    # Each answer is the unknown UUID's, or a 400 or 422 for a value the route reads as invalid.
    others = [
        probe
        for probe in probes
        if probe["form"] != "unknown"
        and probe["status"] not in (baseline[(probe["operation"], probe["field"])]["status"], 400, 422)
    ]
    assert others == []
    # Nothing was found, so nothing was made: only the test's own family is there.
    assert snap["sweep_made"] == [f"family {snap['records']['family']}"]


@pytest.mark.parametrize(
    ("operation", "field", "refusal"),
    [
        (("POST", "/api/admin/nipt/artifacts"), "assembly_id", ASSEMBLY_NOT_FOUND),
        (("POST", "/api/admin/nipt/artifacts/auto-seed"), "assembly_id", ASSEMBLY_NOT_FOUND),
        (("POST", "/api/admin/nipt/artifacts/import"), "assembly_id", ASSEMBLY_NOT_FOUND),
        (("POST", "/api/family-imports"), "project_id", PROJECT_NOT_FOUND),
    ],
)
def test_the_sweep_finds_each_field_this_reads_refused_as_unknown(
    snap: dict[str, Any], operation: tuple[str, str], field: str, refusal: tuple[int, Any]
) -> None:
    probes = [probe for probe in snap["sweep"] if (probe["operation"], probe["field"]) == (operation, field)]
    assert {probe["form"]: _status(probe) for probe in probes} == {
        form: refusal for form in ("unknown", "not_a_uuid", "braces", "urn")
    }
