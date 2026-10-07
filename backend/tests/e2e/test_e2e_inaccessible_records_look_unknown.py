"""A record outside the caller's projects answers like an unknown one (E2E, real Postgres).

A viewer used to get 403 "Not authorized" for a family or sample of another project, and 404
"Family not found" or "Sample not found" for an ID nobody uses, so they could find out which
family and sample IDs exist in other projects. Both now answer 404 with the same body, at the
checkpoint every family and sample endpoint goes through; a project ID given to the Family
Builder or the gene profile is treated the same way (REQ-SEC-001). Only the request's audit row
tells the two apart: it names the hidden record's kind in ``request_meta.record_hidden``.

The test makes two projects of its own: the family's, where the admin creates the family through
the Family Builder, and the viewer's. The viewer asks for the family, its member and its sample,
and for IDs nobody uses, over HTTP through an in-process ``httpx.ASGITransport`` client on one
event loop (see test_e2e_api_contract.py for why), and gets the same status and body for each
pair. Every route with a family or sample ID in its path is asked the same way, headers
included, so a route added later with its own refusal fails here too. The audit rows are read
back from Postgres. The values are synthetic, and what the test creates is dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_NOT_FOUND = {
    "family": (404, '{"detail":"Family not found"}'),
    "sample": (404, '{"detail":"Sample not found"}'),
    "project": (404, '{"detail":"Project not found"}'),
}
# The other path parameters of the swept routes, each given a value of its kind.
_PATH_VALUES = {
    "annotation_id": str(uuid4()),
    "bed_type": "coverage",
    "data_type": "small_variants",
    "kind": "depth_bigwig",
    "preset_id": str(uuid4()),
    "source": "hificnv",
    "tag_key": "report",
    "variant_id": "1-2000-C-T",
    "version": "1",
}


def _blank(text: str, ids: tuple[str, ...]) -> str:
    """The text with the IDs asked for blanked, so two answers can be compared."""
    for value in sorted(ids, key=len, reverse=True):
        text = text.replace(value, "<id>")
    return text


def _answer(resp, *ids: str) -> tuple[int, str]:
    return resp.status_code, _blank(resp.text, ids)


async def _each(ids: dict[str, str], ask) -> dict[str, tuple[int, str]]:
    """The answer to ``ask(value)`` for each ID, with that ID blanked."""
    return {case: _answer(await ask(value), value) for case, value in ids.items()}


def _url(path: str, values: dict[str, str]) -> str:
    """The path with each parameter's value ("x" for one this test has no value for)."""
    return re.sub(r"\{([^}]+)\}", lambda match: values.get(match.group(1), "x"), path)


def _query_value(schema: dict[str, Any]) -> Any:
    """A value of a required query parameter's type."""
    schema = next((s for s in schema.get("anyOf", []) if s.get("type") != "null"), schema)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "array":
        return [_query_value(schema.get("items", {}))]
    return {"integer": 1, "number": 1, "boolean": "false"}.get(kind, "1")


async def _sweep(client, spec: dict, *, families: dict[str, str], samples: dict[str, str]) -> dict:
    """Every route with a family or sample ID in its path, asked with each ID pair."""
    answers: dict[str, dict[str, tuple]] = {}
    for path, operations in sorted(spec["paths"].items()):
        names = re.findall(r"\{([^}]+)\}", path)
        if not {"family_id", "sample_id"} & set(names):
            continue
        for method, operation in operations.items():
            query = {
                parameter["name"]: _query_value(parameter.get("schema", {}))
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "query" and parameter.get("required")
            }
            body = {"json": {}} if "requestBody" in operation else {}
            route = f"{method.upper()} {path}"
            answers[route] = {}
            for family_case, family_id in families.items() if "family_id" in names else [("-", "")]:
                for sample_case, sample_id in samples.items() if "sample_id" in names else [("-", "")]:
                    url = _url(path, {**_PATH_VALUES, "family_id": family_id, "sample_id": sample_id})
                    resp = await client.request(method.upper(), url, params=query, **body)
                    ids = (*families.values(), *samples.values())
                    headers = tuple(sorted((key, _blank(value, ids)) for key, value in resp.headers.items()))
                    answers[route][f"{family_case}/{sample_case}"] = (*_answer(resp, *ids), headers)
    return answers


async def _exercise(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.tests.e2e import _harness

    family_id, sample_id = f"LOOK_UNKNOWN_{run}", f"LOOK_UNKNOWN_{run}_S1"
    unknown_family, unknown_sample = f"NO_FAMILY_{run}", f"NO_SAMPLE_{run}"
    builder_family = f"LOOK_UNKNOWN_{run}_BUILT"
    email = f"viewer-{run}@example.org"
    # Made at run time: the repository holds no password-shaped literal.
    password = uuid4().hex

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, shared_project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        projects = {}
        for owner in ("theirs", "mine"):
            projects[owner] = (
                await session.execute(
                    text(
                        """
                        INSERT INTO projects (name, description, species_id, assembly_id, metadata)
                        SELECT :name, 'look-unknown', species_id, assembly_id, '{}'::jsonb
                        FROM projects WHERE id = CAST(:shared AS uuid)
                        RETURNING id::text
                        """
                    ),
                    {"name": f"look-unknown-{owner}-{run}", "shared": shared_project_id},
                )
            ).scalar_one()
        viewer_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                    VALUES (:username, :hashed_password, 'viewer', :email, '{}'::jsonb, now())
                    RETURNING id::text
                    """
                ),
                {"username": f"viewer-{run}", "hashed_password": get_password_hash(password), "email": email},
            )
        ).scalar_one()
        await session.execute(
            text("INSERT INTO project_users (project_id, user_id) VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"),
            {"p": projects["mine"], "u": viewer_id},
        )
        await session.commit()

    out: dict = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin, AsyncClient(
            transport=transport, base_url="http://e2e"
        ) as viewer:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"
            created = await admin.post(
                "/api/ped/manual",
                json={
                    "family_id": family_id,
                    "project_id": projects["theirs"],
                    "members": [{"sample_id": sample_id, "sex": "female", "is_proband": True}],
                },
            )
            out["created"] = (created.status_code, created.text)
            out["admin"] = {
                "family": (await admin.get(f"/api/families/{family_id}")).status_code,
                "member": (await admin.get(f"/api/families/{family_id}/members/{sample_id}")).status_code,
            }

            login = await viewer.post("/api/auth/login", json={"email": email, "password": password})
            assert login.status_code == 200, login.text
            viewer.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

            families = {"theirs": family_id, "unknown": unknown_family}
            samples = {"theirs": sample_id, "unknown": unknown_sample}
            out["family"] = await _each(families, lambda f: viewer.get(f"/api/families/{f}"))
            out["member"] = await _each(families, lambda f: viewer.get(f"/api/families/{f}/members/{sample_id}"))
            out["sample"] = await _each(samples, lambda s: viewer.get(f"/api/bed/{s}/coverage", params={"chrom": "1"}))
            # The one route that takes a family ID in its query string.
            out["gene_profile_family"] = await _each(
                families, lambda f: viewer.get("/api/genes/profile", params={"symbol": "BRCA1", "family_id": f})
            )
            out["hpo_write"] = await _each(
                families,
                lambda f: viewer.post(f"/api/families/{f}/members/{sample_id}/hpo", json={"hpo_id": "HP:0001250"}),
            )
            listed = await viewer.get("/api/families/")
            out["listed"] = (listed.status_code, [family["family_id"] for family in listed.json()])

            project_ids = {"theirs": projects["theirs"], "unknown": str(uuid4())}
            builder_members = [{"sample_id": f"{builder_family}_S1"}]
            out["builder"] = await _each(
                project_ids,
                lambda p: viewer.post(
                    "/api/ped/manual", json={"family_id": builder_family, "project_id": p, "members": builder_members}
                ),
            )
            out["gene_profile"] = await _each(
                project_ids, lambda p: viewer.get("/api/genes/profile", params={"symbol": "BRCA1", "project_id": p})
            )

            out["sweep"] = await _sweep(viewer, app.openapi(), families=families, samples=samples)

        async with sessionmaker() as session:
            out["hpo_rows"] = (
                await session.execute(
                    text(
                        """
                        SELECT count(*) FROM individual_hpo ih
                        JOIN families f ON f.id = ih.family_id
                        WHERE f.family_id = :family_id
                        """
                    ),
                    {"family_id": family_id},
                )
            ).scalar_one()
            out["builder_family_rows"] = (
                await session.execute(
                    text("SELECT count(*) FROM families WHERE family_id = :family_id"), {"family_id": builder_family}
                )
            ).scalar_one()
            audited = (
                await session.execute(
                    text(
                        """
                        SELECT path, status_code, request_body->>'project_id' AS project_id,
                               request_meta->>'record_hidden' AS record_hidden
                        FROM audit_log_events
                        WHERE user_email = :email
                        """
                    ),
                    {"email": email},
                )
            ).mappings().all()
            asked = {
                "family": (f"/api/families/{family_id}", None),
                "unknown family": (f"/api/families/{unknown_family}", None),
                "sample": (f"/api/bed/{sample_id}/coverage", None),
                "unknown sample": (f"/api/bed/{unknown_sample}/coverage", None),
                "project": ("/api/ped/manual", project_ids["theirs"]),
                "unknown project": ("/api/ped/manual", project_ids["unknown"]),
            }
            # Each request's audit row, as (status, record_hidden), per case asked.
            out["audit"] = {
                case: {
                    (row["status_code"], row["record_hidden"])
                    for row in audited
                    if row["path"] == path and (project_id is None or row["project_id"] == project_id)
                }
                for case, (path, project_id) in asked.items()
            }
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM families WHERE family_id IN (:family_id, :builder_family)"),
                {"family_id": family_id, "builder_family": builder_family},
            )
            for project_id in projects.values():
                await session.execute(text("DELETE FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id})
            await session.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": viewer_id})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def looked() -> dict:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run))


def test_the_family_exists_for_its_own_project(looked) -> None:
    # Without this the comparisons below could pass on two unknown families.
    assert looked["created"][0] == 200, looked["created"]
    assert looked["admin"] == {"family": 200, "member": 200}


def test_a_family_in_another_project_answers_like_an_unknown_one(looked) -> None:
    assert looked["family"] == {"theirs": _NOT_FOUND["family"], "unknown": _NOT_FOUND["family"]}
    assert looked["member"] == {"theirs": _NOT_FOUND["family"], "unknown": _NOT_FOUND["family"]}
    assert looked["gene_profile_family"] == {"theirs": _NOT_FOUND["family"], "unknown": _NOT_FOUND["family"]}


def test_a_sample_in_another_project_answers_like_an_unknown_one(looked) -> None:
    assert looked["sample"] == {"theirs": _NOT_FOUND["sample"], "unknown": _NOT_FOUND["sample"]}


def test_a_write_to_a_family_in_another_project_answers_like_an_unknown_one(looked) -> None:
    assert looked["hpo_write"] == {"theirs": _NOT_FOUND["family"], "unknown": _NOT_FOUND["family"]}
    assert looked["hpo_rows"] == 0


def test_the_family_list_leaves_out_a_family_in_another_project(looked) -> None:
    status, family_ids = looked["listed"]
    assert status == 200
    assert not [family_id for family_id in family_ids if family_id.startswith("LOOK_UNKNOWN_")]


def test_a_project_of_another_team_answers_like_an_unknown_one(looked) -> None:
    assert looked["builder"] == {"theirs": _NOT_FOUND["project"], "unknown": _NOT_FOUND["project"]}
    assert looked["builder_family_rows"] == 0
    assert looked["gene_profile"] == {"theirs": _NOT_FOUND["project"], "unknown": _NOT_FOUND["project"]}


def test_only_the_audit_row_says_that_the_record_exists(looked) -> None:
    assert looked["audit"] == {
        "family": {(404, "family")},
        "unknown family": {(404, None)},
        "sample": {(404, "sample")},
        "unknown sample": {(404, None)},
        "project": {(404, "project")},
        "unknown project": {(404, None)},
    }


def test_every_route_with_a_family_or_sample_id_answers_alike(looked) -> None:
    sweep = looked["sweep"]
    # A floor, so a sweep that matched no route cannot pass.
    assert len(sweep) >= 60, sorted(sweep)
    differing = {route: answers for route, answers in sweep.items() if len(set(answers.values())) > 1}
    assert differing == {}
