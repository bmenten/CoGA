"""A reader in a project reads its records and changes none; an analyst changes them (E2E, SEC-1).

Each project member is an analyst (read and write) or a reader (read only, a student say), per
project; an admin has every right in every project. Over the real API (an in-process
``httpx.ASGITransport`` client on one event loop, see test_e2e_api_contract.py) and Postgres
and ClickHouse:

* the admin makes two projects through the API: A, where R is an analyst, and B, where R and
  P are readers and Q an analyst; the project list, the user list and ``project_users`` hold
  the levels; an update that leaves the reader list out keeps every member's level, and a
  reader who is no member is refused;
* every route that changes something, walked from the app itself (FastAPI wraps an included
  router in an ``_IncludedRouter``; its ``effective_candidates`` are the routes), is asked by
  R for B's family, its sample and B itself, with a body the route accepts: each is refused
  with 403 and the reason (``Admin access required`` for an admin's route), except the routes
  that change only R's own data (filter presets, interface events); nothing of B's family
  changes, and each refusal is in the request audit under R's name;
* reading stays as it is: every route that reads a family or sample answers R as it answers
  Q, an analyst of B, except the clinical audit trail, which a reader may not read;
* R, an analyst in A, adds, changes and removes a phenotype of A's family; each change is one
  event on the family's clinical audit trail, with what changed, and the chain verifies;
* P, a reader only, is told which projects they read only (``/auth/me``, the project list
  and the family) and is refused a phenotype.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The values
are synthetic, and what the test creates is removed at the end (the append-only audit events
stay, as every e2e test's do).
"""

from __future__ import annotations

import re
from typing import Any, Iterator
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

READ_ONLY = (
    403,
    {
        "detail": "Read-only access: you are a reader in this project, so you can view and export "
        "its records but not change them."
    },
)
AUDIT_TRAIL_REFUSED = (
    403,
    {
        "detail": "Read-only access: the clinical audit trail is for analysts and administrators, "
        "and you are a reader in this family's project."
    },
)
ADMIN_ONLY = (403, {"detail": "Admin access required"})
SEIZURE = "HP:0001250"
# The routes whose change touches only the caller's own data: a reader may call them.
OWN_DATA = {
    ("DELETE", "/api/auth/small-variant-filter-presets/{preset_id}"),
    ("POST", "/api/families/{family_id}/small-variant-filter-presets"),
    ("DELETE", "/api/families/{family_id}/small-variant-filter-presets/{preset_id}"),
    ("POST", "/api/families/{family_id}/structural-variant-filter-presets"),
    ("DELETE", "/api/families/{family_id}/structural-variant-filter-presets/{preset_id}"),
    ("POST", "/api/ui-events"),
}
READS = ("GET", "HEAD", "OPTIONS")


def _answer(resp: Any) -> tuple[int, Any]:
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text[:200]


def _routes(routes: Any) -> Iterator[Any]:
    """Every route of the app, through the ``_IncludedRouter`` FastAPI wraps an included
    router in: its ``effective_candidates`` carry the full path and dependencies."""
    from fastapi.routing import APIRoute

    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from _routes(route.effective_candidates())
        elif isinstance(route, APIRoute) or getattr(route, "dependant", None) is not None:
            yield route


def _calls(dependant: Any) -> set[Any]:
    found: set[Any] = set()
    for dependency in dependant.dependencies:
        found.add(dependency.call)
        found |= _calls(dependency)
    return found


def _resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _example(schema: dict[str, Any], components: dict[str, Any], values: dict[str, Any], depth: int = 0) -> Any:
    """A value the schema accepts: its required fields filled by type, and every field that
    names a family, sample or project given the test's own."""
    schema = _resolve(schema, components)
    if depth > 8:
        return None
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [option for option in schema[key] if _resolve(option, components).get("type") != "null"]
            return _example(options[0] if options else {}, components, values, depth + 1)
    if "allOf" in schema:
        return _example(schema["allOf"][0], components, values, depth + 1)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        wanted = set(schema.get("required") or []) | (set(values) & set(properties))
        return {
            name: values[name] if name in values else _example(field, components, values, depth + 1)
            for name, field in properties.items()
            if name in wanted
        }
    if kind == "array":
        return [_example(schema.get("items", {}), components, values, depth + 1) for _ in range(schema.get("minItems", 0))]
    if kind == "integer":
        return max(int(schema.get("minimum", 1)), 1)
    if kind == "number":
        return 1
    if kind == "boolean":
        return False
    if kind == "string":
        if schema.get("format") == "date":
            return "2026-01-01"
        if schema.get("format") == "date-time":
            return "2026-01-01T00:00:00Z"
        return "x" * max(int(schema.get("minLength", 1)), 1)
    return None


def _request(spec: dict[str, Any], method: str, path: str, values: dict[str, Any]) -> dict[str, Any]:
    """The URL, query and body of a request the route accepts, naming the test's records."""
    operation = spec["paths"][path][method.lower()]
    components = spec.get("components", {}).get("schemas", {})
    url = re.sub(r"\{([^}]+)\}", lambda match: str(values.get(match.group(1), "x")), path)
    query = {
        parameter["name"]: values.get(parameter["name"], _example(parameter.get("schema", {}), components, {}))
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "query" and parameter.get("required")
    }
    request: dict[str, Any] = {"url": url, "params": query}
    content = (operation.get("requestBody") or {}).get("content") or {}
    if "application/json" in content:
        request["json"] = _example(content["application/json"].get("schema", {}), components, values)
    return request


async def _family_status(session: Any, family_id: str) -> dict[str, Any]:
    """The family's workflow fields: status, assignee and reviewer."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                """
                SELECT status_id::text AS status_id, assigned_to_id::text AS assigned_to_id,
                       reviewed_by_id::text AS reviewed_by_id
                FROM families WHERE family_id = :f
                """
            ),
            {"f": family_id},
        )
    ).mappings().one()
    return dict(row)


# The family's records a change by a reader would write to, counted.
_FAMILY_RECORDS = (
    "individual_hpo",
    "small_variant_reviews",
    "structural_variant_reviews",
    "report_signouts",
    "clinical_audit_events",
)


async def _family_records(session: Any, family_id: str) -> dict[str, int]:
    """How many rows the family has in each of ``_FAMILY_RECORDS``."""
    from sqlalchemy import text

    return {
        table: (
            await session.execute(
                text(f"SELECT count(*) FROM {table} t JOIN families f ON f.id = t.family_id WHERE f.family_id = :f"),
                {"f": family_id},
            )
        ).scalar_one()
        for table in _FAMILY_RECORDS
    }


async def _exercise(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_current_admin_user, get_current_user, get_password_hash
    from backend.app.main import app
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
    from backend.tests.e2e import _harness

    family_a, family_b = f"READER_A_{run}", f"READER_B_{run}"
    sample_a, sample_b = f"{family_a}_S1", f"{family_b}_S1"
    emails = {who: f"{who}-{run}@example.org" for who in ("r", "p", "q")}
    # Made at run time: the repository holds no password-shaped literal.
    password = uuid4().hex

    await init_postgres_schema()
    await init_clickhouse_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, shared_project_id, assembly_id = await _harness.ensure_e2e_project(session)
        species_id = (
            await session.execute(
                text("SELECT species_id::text FROM projects WHERE id = CAST(:id AS uuid)"), {"id": shared_project_id}
            )
        ).scalar_one()
        users = {}
        for who, email in emails.items():
            users[who] = (
                await session.execute(
                    text(
                        """
                        INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                        VALUES (:email, :hashed_password, 'viewer', :email, '{}'::jsonb, now())
                        RETURNING id::text
                        """
                    ),
                    {"email": email, "hashed_password": get_password_hash(password)},
                )
            ).scalar_one()
        await session.execute(
            text("INSERT INTO hpo_term (hpo_id, label) VALUES (:id, 'Seizure') ON CONFLICT (hpo_id) DO NOTHING"),
            {"id": SEIZURE},
        )
        await session.commit()

    out: dict[str, Any] = {"users": users, "emails": emails}
    projects: dict[str, str] = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"
            clients: dict[str, AsyncClient] = {}
            for who, email in emails.items():
                clients[who] = AsyncClient(transport=transport, base_url="http://e2e")
                login = await clients[who].post("/api/auth/login", json={"email": email, "password": password})
                assert login.status_code == 200, login.text
                clients[who].headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            reader, pure_reader, analyst = clients["r"], clients["p"], clients["q"]

            # --- the admin sets the levels -----------------------------------------------
            for name, members, readers in (
                ("A", [users["r"]], []),
                ("B", [users["r"], users["p"], users["q"]], [users["r"], users["p"]]),
            ):
                created = await admin.post(
                    "/api/projects/",
                    json={
                        "name": f"reader-role-{name}-{run}",
                        "species_id": species_id,
                        "assembly_id": assembly_id,
                        "user_ids": members,
                        "reader_user_ids": readers,
                    },
                )
                assert created.status_code == 201, created.text
                projects[name] = created.json()["_id"]
                out[f"created_{name}"] = created.json()
            for family_id, sample_id, project in ((family_a, sample_a, "A"), (family_b, sample_b, "B")):
                made = await admin.post(
                    "/api/ped/manual",
                    json={
                        "family_id": family_id,
                        "project_id": projects[project],
                        "members": [{"sample_id": sample_id, "sex": "female", "is_proband": True}],
                    },
                )
                assert made.status_code == 200, made.text

            listed = {project["_id"]: project for project in (await admin.get("/api/projects/")).json()}
            # Read with .get: a field the API lacks fails its own test below, not the whole run.
            out["admin_projects"] = {
                name: sorted(listed[project_id].get("reader_user_ids") or []) for name, project_id in projects.items()
            }
            accounts = {account["id"]: account for account in (await admin.get("/api/auth/users")).json()}
            out["accounts"] = {
                who: (sorted(accounts[user_id]["projects"]), accounts[user_id].get("reader_projects"))
                for who, user_id in users.items()
            }
            # An update without a reader list keeps each member's level.
            kept = await admin.put(f"/api/projects/{projects['B']}", json={"user_ids": [users["r"], users["p"], users["q"]]})
            out["kept"] = (kept.status_code, sorted(kept.json().get("reader_user_ids", [])))
            # A reader must be a member.
            out["outsider_reader"] = _answer(
                await admin.put(f"/api/projects/{projects['B']}", json={"reader_user_ids": [str(uuid4())]})
            )

            # --- every route that changes something, asked by R for B's records ----------
            async with sessionmaker() as session:
                out["family_b_status_before"] = await _family_status(session, family_b)
                out["family_b_records_before"] = await _family_records(session, family_b)
            spec = app.openapi()
            values = {
                "family_id": family_b,
                "sample_id": sample_b,
                "project_id": projects["B"],
                "hpo_id": SEIZURE,
                "annotation_id": str(uuid4()),
                "preset_id": str(uuid4()),
                "variant_id": "1-2000-C-T",
                "tag_key": "report",
                "version": "1",
                "user_id": users["r"],
            }
            swept: dict[tuple[str, str], dict[str, Any]] = {}
            for route in _routes(app.routes):
                calls = _calls(route.dependant)
                for method in sorted(route.methods or ()):
                    key = (method, route.path_format)
                    if method in READS or key[1] not in spec["paths"] or get_current_user not in calls:
                        continue
                    if get_current_admin_user in calls:
                        expected = "admin"
                    elif key in OWN_DATA:
                        expected = "own"
                    else:
                        expected = "read-only"
                    request = _request(spec, method, key[1], values)
                    url = request.pop("url")
                    swept[key] = {"expected": expected, "answer": _answer(await reader.request(method, url, **request))}
            out["swept"] = swept

            # --- reading stays as it is ----------------------------------------------------
            read_values = {"family_id": family_b, "sample_id": sample_b, "project_id": projects["B"], "version": "1"}
            reads: dict[str, tuple[int, int]] = {}
            for path, operations in sorted(spec["paths"].items()):
                if not {"family_id", "sample_id"} & set(re.findall(r"\{([^}]+)\}", path)) or "get" not in operations:
                    continue
                request = _request(spec, "GET", path, read_values)
                url = request.pop("url")
                reads[path] = (
                    (await reader.get(url, **request)).status_code,
                    (await analyst.get(url, **request)).status_code,
                )
            out["reads"] = reads
            out["family_read_only"] = {
                who: (await client.get(f"/api/families/{family_b}")).json().get("read_only")
                for who, client in (("r", reader), ("q", analyst), ("p", pure_reader))
            }

            # --- an analyst changes phenotypes, each change on the audit trail -------------
            added = await reader.post(
                f"/api/families/{family_a}/members/{sample_a}/hpo", json={"hpo_id": SEIZURE, "note": "since birth"}
            )
            out["added"] = _answer(added)
            annotation_id = added.json().get("id")
            out["changed"] = _answer(await reader.put(f"/api/families/{family_a}/hpo/{annotation_id}", json={"status": "absent"}))
            out["removed"] = (await reader.delete(f"/api/families/{family_a}/hpo/{annotation_id}")).status_code
            out["trail"] = _answer(await reader.get(f"/api/families/{family_a}/clinical-audit"))

            # --- a reader only ---------------------------------------------------------------
            out["me"] = (await pure_reader.get("/api/auth/me")).json().get("reader_projects")
            out["pure_reader_projects"] = {
                project["_id"]: project.get("read_only") for project in (await pure_reader.get("/api/projects/")).json()
            }
            out["pure_reader_hpo"] = _answer(
                await pure_reader.post(f"/api/families/{family_b}/members/{sample_b}/hpo", json={"hpo_id": SEIZURE})
            )
            out["pure_reader_events"] = (
                await pure_reader.post("/api/ui-events", json={"events": [{"event_type": "print"}]})
            ).status_code
            for client in clients.values():
                await client.aclose()

        async with sessionmaker() as session:
            try:
                out["levels"] = {
                    (row["project_id"], row["user_id"]): row["access_level"]
                    for row in (
                        await session.execute(
                            text(
                                """
                                SELECT project_id::text AS project_id, user_id::text AS user_id, access_level
                                FROM project_users
                                WHERE project_id IN (CAST(:a AS uuid), CAST(:b AS uuid))
                                """
                            ),
                            {"a": projects["A"], "b": projects["B"]},
                        )
                    ).mappings()
                }
            except DBAPIError as error:
                # A schema without the column fails its own test, not the whole run.
                await session.rollback()
                out["levels"] = type(error).__name__
        async with sessionmaker() as session:
            out["family_b_changes"] = await _family_records(session, family_b)
            out["family_b_status"] = await _family_status(session, family_b)
            out["families_in_b"] = (
                await session.execute(
                    text("SELECT count(*) FROM family_projects WHERE project_id = CAST(:b AS uuid)"), {"b": projects["B"]}
                )
            ).scalar_one()
            out["refusals_audited"] = (
                await session.execute(
                    text(
                        """
                        SELECT count(*) FROM audit_log_events
                        WHERE user_email = :email AND status_code = 403 AND method <> 'GET'
                        """
                    ),
                    {"email": emails["r"]},
                )
            ).scalar_one()
            chain = await verify_clinical_audit_chain(session, family_a)
            out["chain"] = (chain.verified, chain.rows_checked)
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM families WHERE family_id IN (:a, :b)"), {"a": family_a, "b": family_b}
            )
            for project_id in projects.values():
                await session.execute(text("DELETE FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id})
            for table in ("small_variant_filter_presets", "structural_variant_filter_presets"):
                await session.execute(text(f"DELETE FROM {table} WHERE owner = ANY(:owners)"), {"owners": list(emails.values())})
            for user_id in users.values():
                await session.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": user_id})
            await session.commit()
    out["projects"] = projects
    return out


@pytest.fixture(scope="module")
def ran() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run))


def test_the_admin_sets_each_members_level(ran: dict[str, Any]) -> None:
    users, projects = ran["users"], ran["projects"]
    assert sorted(ran["created_B"].get("reader_user_ids") or []) == sorted([users["r"], users["p"]])
    assert ran["admin_projects"] == {"A": [], "B": sorted([users["r"], users["p"]])}
    assert ran["accounts"] == {
        "r": (sorted([projects["A"], projects["B"]]), [projects["B"]]),
        "p": ([projects["B"]], [projects["B"]]),
        "q": ([projects["B"]], []),
    }
    assert ran["levels"] == {
        (projects["A"], users["r"]): "analyst",
        (projects["B"], users["r"]): "reader",
        (projects["B"], users["p"]): "reader",
        (projects["B"], users["q"]): "analyst",
    }


def test_an_update_without_readers_keeps_the_levels_and_a_reader_must_be_a_member(ran: dict[str, Any]) -> None:
    assert ran["kept"] == (200, sorted([ran["users"]["r"], ran["users"]["p"]]))
    assert ran["outsider_reader"][0] == 400


def test_every_route_that_changes_something_refuses_a_reader_with_the_reason(ran: dict[str, Any]) -> None:
    swept = ran["swept"]
    # A floor, so a walk that found no route cannot pass.
    assert len(swept) >= 70, sorted(swept)
    wrong = {
        key: probe
        for key, probe in swept.items()
        if (probe["expected"] == "admin" and probe["answer"] != ADMIN_ONLY)
        or (probe["expected"] == "read-only" and probe["answer"] != READ_ONLY)
        or (probe["expected"] == "own" and (probe["answer"][0] == 403 or not 200 <= probe["answer"][0] < 500))
    }
    assert wrong == {}
    # The reader's own data: their presets are saved; one that is not there is not found.
    own = {key: probe["answer"][0] for key, probe in swept.items() if probe["expected"] == "own"}
    assert own == {
        ("DELETE", "/api/auth/small-variant-filter-presets/{preset_id}"): 404,
        ("POST", "/api/families/{family_id}/small-variant-filter-presets"): 200,
        ("DELETE", "/api/families/{family_id}/small-variant-filter-presets/{preset_id}"): 404,
        ("POST", "/api/families/{family_id}/structural-variant-filter-presets"): 200,
        ("DELETE", "/api/families/{family_id}/structural-variant-filter-presets/{preset_id}"): 404,
        ("POST", "/api/ui-events"): 202,
    }


def test_nothing_of_the_readers_family_changed(ran: dict[str, Any]) -> None:
    # Before the reader's requests the family held only its creation: the pedigree it was made
    # with is the first event of its clinical audit trail (SEC-7).
    assert ran["family_b_records_before"] == {
        "individual_hpo": 0,
        "small_variant_reviews": 0,
        "structural_variant_reviews": 0,
        "report_signouts": 0,
        "clinical_audit_events": 1,
    }
    assert ran["family_b_changes"] == ran["family_b_records_before"]
    assert ran["family_b_status"] == ran["family_b_status_before"]
    assert ran["families_in_b"] == 1


def test_each_refusal_is_in_the_request_audit_under_the_readers_name(ran: dict[str, Any]) -> None:
    refused = [key for key, probe in ran["swept"].items() if probe["answer"][0] == 403]
    assert ran["refusals_audited"] >= len(refused)


def test_reading_stays_as_it_is_but_for_the_audit_trail(ran: dict[str, Any]) -> None:
    reads = ran["reads"]
    assert len(reads) >= 30, sorted(reads)
    differing = {path: answers for path, answers in reads.items() if answers[0] != answers[1]}
    assert differing == {"/api/families/{family_id}/clinical-audit": (403, 200)}
    for path in (
        "/api/families/{family_id}",
        "/api/families/{family_id}/hpo",
        "/api/families/{family_id}/members/{sample_id}",
        "/api/families/{family_id}/report/sign-outs",
        "/api/families/{family_id}/small-variant-filter-presets",
    ):
        assert reads[path] == (200, 200), path
    assert ran["family_read_only"] == {"r": True, "q": False, "p": True}


def test_an_analyst_changes_phenotypes_and_each_change_is_on_the_audit_trail(ran: dict[str, Any]) -> None:
    assert ran["added"][0] == 200, ran["added"]
    assert ran["changed"][0] == 200, ran["changed"]
    assert ran["removed"] == 204
    status, trail = ran["trail"]
    assert status == 200, trail
    phenotype_events = [event for event in trail["events"] if event["action"] == "phenotype"]
    sample = ran["added"][1]["sample_id"]
    assert [event["summary"] for event in phenotype_events] == [
        f"Phenotype removed for {sample}: {SEIZURE} Seizure (absent)",
        f"Phenotype changed for {sample}: {SEIZURE} Seizure: status present → absent",
        f"Phenotype added for {sample}: {SEIZURE} Seizure (present)",
    ]
    assert {event["actor"] for event in phenotype_events} == {ran["emails"]["r"]}
    removed, changed, added = phenotype_events
    assert added["before"] is None and added["after"]["note"] == "since birth"
    assert (changed["before"]["status"], changed["after"]["status"]) == ("present", "absent")
    assert removed["after"] is None
    # The chain holds the family's creation (its pedigree, SEC-7) and the three phenotype events.
    assert [event["action"] for event in trail["events"]] == ["phenotype", "phenotype", "phenotype", "pedigree"]
    assert ran["chain"] == (True, 4)


def test_a_reader_is_told_what_they_only_read_and_may_still_record_their_events(ran: dict[str, Any]) -> None:
    projects = ran["projects"]
    assert ran["me"] == [projects["B"]]
    assert ran["pure_reader_projects"] == {projects["B"]: True}
    assert ran["pure_reader_hpo"] == READ_ONLY
    assert ran["pure_reader_events"] == 202
