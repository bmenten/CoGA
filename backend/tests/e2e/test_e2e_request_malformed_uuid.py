"""A value that is no UUID, or a UUID spelled otherwise, is answered and never fails (E2E).

asyncpg's uuid codec takes nothing but hex digits and hyphens. ``GET /api/admin/data/files/
<not a UUID>/download`` bound the value into ``CAST(:file_id AS uuid)`` and failed with a
500, and so did the import job, the HPO annotation and the NIPT artifact routes. The panel,
preset, user, project, species and clinical CNV routes, and the panel filter of the variant
pages, checked the value with ``uuid.UUID`` and then bound it as received, so a UUID in
braces or after ``urn:uuid:`` failed the same way. Each route now reads the value once
(``core/sql.py``: ``canonical_uuid``, ``require_uuid``).

Over the real API (an in-process ``httpx.ASGITransport`` client, see test_e2e_api_contract.py)
and Postgres and ClickHouse, with synthetic records made for the test (a family in each of two
projects, an HPO annotation, a gene panel, filter presets, a NIPT artifact, a raw import file,
an import job and a clinical CNV), the seeded admin and a viewer of the second project only:

* a value that is no UUID is answered as a UUID that names no record is: with the route's own
  404, with an empty list where the route lists by it, or with the 400 the route answers an
  invalid id with;
* a record's id in braces, after ``urn:uuid:``, in capitals or without hyphens names that
  record: it is read, changed and deleted as by its id, and a panel read so keeps its genes;
* for the viewer, the annotation of a family in the other project is answered as a value that
  is no UUID and an unknown UUID are, through the viewer's own family and through the other
  one, and stays as it was: the answer tells nothing about records the viewer may not see;
* a refused request is in ``audit_log_events`` under its caller's name, with its status;
* every operation of the API, with a value that is no UUID, a UUID in braces and a
  ``urn:uuid:`` UUID in each of its ``*_id`` path and query parameters in turn, answers as it
  answers a UUID that names no record, or with a 400, and none with a 500.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

HPO_TERM = "HP:0001250"


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code}
    try:
        out["json"] = resp.json()
    except ValueError:
        out["json"] = None
        out["text"] = resp.text[:200]
    return out


def _record(answer: dict[str, Any]) -> tuple[int, Any]:
    """The status, and the record the answer is about: its id, the ids of a list, the text
    of a file, or the refusal's detail."""
    body = answer["json"]
    if isinstance(body, dict):
        body = body.get("panel", body)
        for key in ("id", "_id", "panel_id", "file_id", "detail"):
            if key in body:
                return answer["status"], body[key]
        return answer["status"], None
    if isinstance(body, list):
        return answer["status"], sorted(str(row.get("id") or row.get("_id")) for row in body)
    return answer["status"], answer.get("text")


def _spellings(record_id: str) -> dict[str, str]:
    return {
        "braces": "{" + record_id + "}",
        "urn": "urn:uuid:" + record_id,
        "capitals": record_id.upper(),
        "no_hyphens": record_id.replace("-", ""),
    }


async def _ask(client: Any, method: str, template: str, value: str, body: Any = None) -> dict[str, Any]:
    url = template.replace("{v}", quote(value, safe=""))
    if body is None:
        return _cap(await client.request(method, url))
    return _cap(await client.request(method, url, json=body))


async def _answers(
    client: Any, method: str, template: str, not_a_uuid: str, record_id: str | None = None, body: Any = None
) -> dict[str, dict[str, Any]]:
    """The answers to a value that is no UUID, to a UUID that names no record (as it is, in
    braces and after ``urn:uuid:``), and, given a record, to each spelling of its id."""
    unknown = str(uuid4())
    values = {
        "not_a_uuid": not_a_uuid,
        "unknown": unknown,
        "unknown_braces": "{" + unknown + "}",
        "unknown_urn": "urn:uuid:" + unknown,
    }
    if record_id is not None:
        values["canonical"] = record_id
        values.update(_spellings(record_id))
    return {name: await _ask(client, method, template, value, body) for name, value in values.items()}


async def _audit_rows(session: Any, *, method: str, path: str) -> list[dict[str, Any]]:
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            SELECT path, status_code, user_email
            FROM audit_log_events
            WHERE method = :method AND path = :path
            ORDER BY created_at
            """
        ),
        {"method": method, "path": path},
    )
    return [dict(row) for row in result.mappings().all()]


def _example(schema: dict[str, Any], components: dict[str, Any], depth: int = 0) -> Any:
    """The least JSON value a schema accepts: its required fields only."""
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    if depth > 6:
        return None
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [option for option in schema[key] if option.get("type") != "null"]
            return _example(options[0], components, depth + 1) if options else None
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        return {
            name: _example(properties[name], components, depth + 1)
            for name in schema.get("required", [])
            if name in properties
        }
    if kind == "array":
        return [_example(schema.get("items", {}), components, depth + 1)] if schema.get("minItems") else []
    if kind == "string":
        return {"date-time": "2026-01-01T00:00:00Z", "date": "2026-01-01", "email": "e2e@example.com"}.get(
            schema.get("format", ""), "E2E"
        )
    if kind in ("integer", "number"):
        return schema.get("minimum", 1)
    if kind == "boolean":
        return False
    return None


async def _sweep(ac: Any, run: str) -> list[dict[str, Any]]:
    """Every operation; each ``*_id`` path and query parameter in turn holding a value that is
    no UUID, a UUID in braces and a ``urn:uuid:`` UUID, and, to compare, a UUID that names no
    record. The other path parameters name nothing, so no request finds a record to change:
    that is why an operation with a path parameter is sent the least JSON body its schema
    accepts (it would otherwise stop at the body), and one without is sent none."""
    from backend.app.main import app

    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    placeholder = f"E2ENONE{run}"
    probes: list[dict[str, Any]] = []
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            parameters = operation.get("parameters", [])
            path_names = [p["name"] for p in parameters if p["in"] == "path"]
            body = None
            json_body = (operation.get("requestBody") or {}).get("content", {}).get("application/json")
            if json_body is not None and path_names:
                body = _example(json_body.get("schema", {}), components)
            for parameter in parameters:
                name, where = parameter["name"], parameter["in"]
                if where not in ("path", "query") or not name.endswith("_id"):
                    continue
                forms = {
                    "unknown": str(uuid4()),
                    "not_a_uuid": f"NOT-A-UUID-SWEEP-{run}",
                    "braces": "{" + str(uuid4()) + "}",
                    "urn": "urn:uuid:" + str(uuid4()),
                }
                for form, value in forms.items():
                    url = path
                    for path_name in path_names:
                        fill = value if (where, path_name) == ("path", name) else placeholder
                        url = url.replace("{" + path_name + "}", quote(fill, safe=""))
                    # The parameter, and every other query parameter the operation requires.
                    query: list[tuple[str, str]] = []
                    for other in parameters:
                        if other["in"] != "query":
                            continue
                        if (where, other["name"]) == ("query", name):
                            query.append((name, value))
                        elif other.get("required"):
                            fill_value = _example(other.get("schema", {}), components)
                            if fill_value == "E2E":
                                fill_value = placeholder
                            elif isinstance(fill_value, bool):
                                fill_value = str(fill_value).lower()
                            query.append((other["name"], str(fill_value)))
                    if query:
                        url += "?" + "&".join(f"{quote(key)}={quote(item, safe='')}" for key, item in query)
                    if body is None:
                        resp = await ac.request(method.upper(), url)
                    else:
                        resp = await ac.request(method.upper(), url, json=body)
                    probes.append(
                        {
                            "operation": (method.upper(), path),
                            "parameter": f"{where}:{name}",
                            "form": form,
                            "status": resp.status_code,
                        }
                    )
    return probes


async def _collect(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    family_a, sample_a = f"E2EUUIDA{run}", f"E2EUUIDA{run}M"
    family_b, sample_b = f"E2EUUIDB{run}", f"E2EUUIDB{run}M"
    not_a_uuid = f"NOT-A-UUID-{run}"
    viewer_email, viewer_password = f"e2e-uuid-viewer-{run}@example.com", f"viewer-password-{run}"
    storage = Path(tempfile.mkdtemp(prefix="e2e-uuid-"))
    (storage / "raw.txt").write_text("synthetic raw import file\n")

    await init_postgres_schema()
    # The family's variant pages run against the assembly's ClickHouse tables.
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)
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
        await session.execute(
            text("INSERT INTO hpo_term (hpo_id, label) VALUES (:id, 'Seizure') ON CONFLICT (hpo_id) DO NOTHING"),
            {"id": HPO_TERM},
        )
        await session.commit()

    out: dict[str, Any] = {
        "not_a_uuid": not_a_uuid,
        "admin_email": settings.admin_email,
        "viewer_email": viewer_email,
    }
    r: dict[str, Any] = {}
    out["responses"] = r
    ids: dict[str, str] = {}
    out["ids"] = ids
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
                    "name": f"e2e-uuid-{run}",
                    "species_id": species_id,
                    "assembly_id": assembly_id,
                    "user_ids": [viewer_id],
                },
            )
            assert created.status_code == 201, created.text
            ids["project_b"] = created.json()["_id"]
            for family, sample, project in ((family_a, sample_a, project_id), (family_b, sample_b, ids["project_b"])):
                made = await ac.post(
                    "/api/ped/manual",
                    json={
                        "family_id": family,
                        "project_id": project,
                        "members": [{"sample_id": sample, "sex": "female", "is_proband": True}],
                    },
                )
                assert made.status_code == 200, made.text
            annotation = await ac.post(f"/api/families/{family_a}/members/{sample_a}/hpo", json={"hpo_id": HPO_TERM})
            assert annotation.status_code == 200, annotation.text
            ids["annotation"] = annotation.json()["id"]
            panel = await ac.post("/api/panels/", json={"name": f"E2E UUID panel {run}", "genes": ["E2EGENE1"]})
            assert panel.status_code == 201, panel.text
            ids["panel"] = panel.json()["panel"]["_id"]
            for key in ("preset_owner", "preset_family"):
                preset = await ac.post(
                    f"/api/families/{family_a}/small-variant-filter-presets", json={"name": f"e2e-uuid-{run}-{key}"}
                )
                assert preset.status_code == 200, preset.text
                ids[key] = preset.json()["_id"]
            artifact = await ac.post(
                "/api/admin/nipt/artifacts", json={"assembly_id": assembly_id, "variant_id": f"chr1-{run}-A-T"}
            )
            assert artifact.status_code == 200, artifact.text
            ids["artifact"] = artifact.json()["id"]
            async with sessionmaker() as session:
                family_a_uuid = (
                    await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_a})
                ).scalar_one()
                inserts = {
                    # Saving a structural-variant preset through the API fails on its own: it
                    # is made here.
                    "sv_preset": (
                        """
                        INSERT INTO structural_variant_filter_presets (family_id, scope, owner, name)
                        VALUES (CAST(:family AS uuid), 'family', :owner, :name)
                        RETURNING id::text
                        """,
                        {"family": family_a_uuid, "owner": settings.admin_username, "name": f"e2e-uuid-{run}"},
                    ),
                    "file": (
                        """
                        INSERT INTO raw_import_files (family_id, scope, file_name, storage_path)
                        VALUES (CAST(:family AS uuid), 'family', 'raw.txt', :path)
                        RETURNING id::text
                        """,
                        {"family": family_a_uuid, "path": str(storage / "raw.txt")},
                    ),
                    "job": (
                        """
                        INSERT INTO family_import_jobs (submitted_path, family_id, status, requested_by)
                        VALUES ('/e2e/none', :family, 'failed', :email)
                        RETURNING id::text
                        """,
                        {"family": family_a, "email": settings.admin_email},
                    ),
                    "cnv": (
                        """
                        INSERT INTO clinical_cnvs (assembly_id, chr, start, "end", label)
                        VALUES (CAST(:assembly AS uuid), 'chr1', 1000, 2000, :label)
                        RETURNING id::text
                        """,
                        {"assembly": assembly_id, "label": f"e2e-uuid-{run}"},
                    ),
                }
                for key, (sql, params) in inserts.items():
                    ids[key] = (await session.execute(text(sql), params)).scalar_one()
                await session.commit()
            ids.update({"project_a": project_id, "assembly": assembly_id, "species": species_id, "viewer": viewer_id})

            # --- the viewer, before anything is deleted ---------------------------------
            login = await viewer.post("/api/auth/login", json={"email": viewer_email, "password": viewer_password})
            assert login.status_code == 200, login.text
            viewer.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            for method in ("PUT", "DELETE"):
                body = {"note": "changed by the viewer"} if method == "PUT" else None
                for family in (family_b, family_a):
                    r[f"viewer_{method}_{'own' if family == family_b else 'other'}"] = await _answers(
                        viewer, method, f"/api/families/{family}/hpo/{{v}}", not_a_uuid, ids["annotation"], body
                    )
            r["viewer_file"] = await _answers(viewer, "GET", "/api/admin/data/files/{v}/download", not_a_uuid, ids["file"])
            r["annotation_after_viewer"] = _cap(await ac.get(f"/api/families/{family_a}/hpo"))

            # --- reads and changes: each spelling of the id -----------------------------
            reads = {
                "file_download": ("GET", "/api/admin/data/files/{v}/download", "file", None),
                "file_verify": ("POST", "/api/admin/data/files/{v}/verify", "file", None),
                "job": ("GET", "/api/family-imports/{v}", "job", None),
                "annotation_update": ("PUT", f"/api/families/{family_a}/hpo/{{v}}", "annotation", {"note": "e2e"}),
                "artifacts": ("GET", "/api/admin/nipt/artifacts?assembly_id={v}", "assembly", None),
                "assemblies": ("GET", "/api/assemblies/{v}", "species", None),
                "user": ("PATCH", "/api/auth/users/{v}", "viewer", {}),
                "cnv": ("GET", "/api/cnvs/entry/{v}", "cnv", None),
                "panel": ("GET", "/api/panels/{v}", "panel", None),
                "panel_versions": ("GET", "/api/panels/{v}/versions", "panel", None),
                "panel_version": ("GET", "/api/panels/{v}/versions/1", "panel", None),
                "panel_update": ("PUT", "/api/panels/{v}", "panel", {"genes": ["E2EGENE1"]}),
                "project_update": ("PUT", "/api/projects/{v}", "project_b", {}),
                "panel_filter": ("GET", f"/api/families/{family_a}/small-variants?panel_id={{v}}", "panel", None),
                "sv_panel_filter": ("GET", f"/api/families/{family_a}/structural-variants?panel_id={{v}}", "panel", None),
                "gene_profile": ("GET", "/api/genes/profile?symbol=E2EGENE1&project_id={v}", "project_a", None),
            }
            for key, (method, template, record, body) in reads.items():
                r[key] = await _answers(ac, method, template, not_a_uuid, ids[record], body)
            r["project_b_after"] = _cap(await ac.get("/api/projects/"))
            # Only to assemblies that do not exist: an upload to one replaces its reference data.
            upload = {"file": ("cytobands.txt", b"chr1\t0\t2300000\tp36.33\tgneg\n", "text/plain")}
            unknown_assembly = str(uuid4())
            r["reference_upload"] = {
                name: _cap(
                    await ac.post(f"/api/assemblies/{quote(value, safe='')}/reference-upload/cytobands", files=upload)
                )
                for name, value in {
                    "not_a_uuid": not_a_uuid,
                    "unknown": unknown_assembly,
                    "unknown_braces": "{" + unknown_assembly + "}",
                    "unknown_urn": "urn:uuid:" + unknown_assembly,
                }.items()
            }

            # --- deletes: refused as unknown, then done by a spelling other than the id --
            deletes = {
                "annotation_delete": ("DELETE", f"/api/families/{family_a}/hpo/{{v}}", "annotation", "braces"),
                "artifact_delete": ("DELETE", "/api/admin/nipt/artifacts/{v}", "artifact", "urn"),
                "preset_owner_delete": ("DELETE", "/api/auth/small-variant-filter-presets/{v}", "preset_owner", "capitals"),
                "preset_family_delete": (
                    "DELETE",
                    f"/api/families/{family_a}/small-variant-filter-presets/{{v}}",
                    "preset_family",
                    "no_hyphens",
                ),
                "sv_preset_delete": (
                    "DELETE",
                    f"/api/families/{family_a}/structural-variant-filter-presets/{{v}}",
                    "sv_preset",
                    "braces",
                ),
                "panel_delete": ("DELETE", "/api/panels/{v}", "panel", "urn"),
            }
            for key, (method, template, record, spelling) in deletes.items():
                r[key] = await _answers(ac, method, template, not_a_uuid)
                r[key]["deleted"] = await _ask(ac, method, template, _spellings(ids[record])[spelling])
                r[key]["again"] = await _ask(ac, method, template, ids[record])
            r["project_delete"] = await _answers(ac, "DELETE", "/api/projects/{v}", not_a_uuid)
            await ac.delete(f"/api/admin/families/{family_b}?confirm=true")
            braces = _spellings(ids["project_b"])["braces"]
            r["project_delete"]["deleted"] = await _ask(ac, "DELETE", "/api/projects/{v}", braces)
            r["project_delete"]["again"] = await _ask(ac, "DELETE", "/api/projects/{v}", ids["project_b"])

            out["sweep"] = await _sweep(ac, run)

        async with sessionmaker() as session:
            out["audit_not_a_uuid"] = await _audit_rows(
                session, method="GET", path=f"/api/admin/data/files/{not_a_uuid}/download"
            )
    finally:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
            # What the test did not delete, should it have stopped early.
            for family in (family_a, family_b):
                await ac.delete(f"/api/admin/families/{family}?confirm=true")
            for path, key in (
                ("projects", "project_b"),
                ("panels", "panel"),
                ("admin/nipt/artifacts", "artifact"),
                ("auth/small-variant-filter-presets", "preset_owner"),
                ("auth/small-variant-filter-presets", "preset_family"),
            ):
                if key in ids:
                    await ac.delete(f"/api/{path}/{ids[key]}")
        async with sessionmaker() as session:
            for table, key in (("family_import_jobs", "job"), ("clinical_cnvs", "cnv")):
                if key in ids:
                    await session.execute(text(f"DELETE FROM {table} WHERE id = CAST(:id AS uuid)"), {"id": ids[key]})
            await session.execute(text("DELETE FROM users WHERE email = :email"), {"email": viewer_email})
            await session.commit()
        shutil.rmtree(storage, ignore_errors=True)
    return out


@pytest.fixture(scope="module")
def snap() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:10].upper()
    return _harness.run_async(lambda: _collect(run))


def _status(answer: dict[str, Any]) -> tuple[int, Any]:
    return answer["status"], answer["json"]


# The answer to a UUID that names no record, and, where the route answers an invalid id with
# a 400 of its own, that 400 (None: the same answer as the unknown UUID).
NOT_FOUND = {
    "file_download": ((404, {"detail": "File not found"}), None),
    "file_verify": ((404, {"detail": "File not found"}), None),
    "job": ((404, {"detail": "Family import job not found"}), None),
    "annotation_update": ((404, {"detail": "HPO annotation was not found"}), None),
    "artifacts": ((200, []), None),
    "assemblies": ((200, []), (400, {"detail": "Invalid species id"})),
    "user": ((404, {"detail": "User not found"}), (400, {"detail": "Invalid user id"})),
    "cnv": ((404, {"detail": "Clinical CNV not found"}), (400, {"detail": "Invalid clinical CNV id"})),
    "panel": ((404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    "panel_versions": ((404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    "panel_version": ((404, {"detail": "Panel version not found"}), (400, {"detail": "Invalid panel id"})),
    "panel_update": ((404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    "project_update": ((404, {"detail": "Project not found"}), (400, {"detail": "Invalid project id"})),
    "panel_filter": ((404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    "sv_panel_filter": ((404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    "gene_profile": ((404, {"detail": "Project not found"}), (400, {"detail": "Project id is invalid"})),
    "reference_upload": ((404, {"detail": "Assembly not found"}), (400, {"detail": "Invalid assembly id"})),
    "annotation_delete": ((404, {"detail": "HPO annotation was not found"}), None),
    "artifact_delete": ((404, {"detail": "Artifact not found"}), None),
    "preset_owner_delete": ((404, {"detail": "Preset not found"}), None),
    "preset_family_delete": ((404, {"detail": "Preset not found"}), None),
    "sv_preset_delete": ((404, {"detail": "Preset not found"}), None),
    "panel_delete": ((404, {"detail": "Panel not found"}), (400, {"detail": "Invalid panel id"})),
    "project_delete": ((404, {"detail": "Project not found"}), (400, {"detail": "Invalid project id"})),
}


@pytest.mark.parametrize("key", sorted(NOT_FOUND))
def test_a_value_that_is_no_uuid_is_answered_as_an_unknown_record(snap: dict[str, Any], key: str) -> None:
    answers = snap["responses"][key]
    unknown, invalid = NOT_FOUND[key]
    # The unknown UUID, as it is, in braces and after urn:uuid:, names no record.
    for name in ("unknown", "unknown_braces", "unknown_urn"):
        assert _status(answers[name]) == unknown, (name, answers[name])
    # It used to fail asyncpg's uuid codec: a 500.
    assert _status(answers["not_a_uuid"]) == (invalid or unknown), answers["not_a_uuid"]


READS = [
    "file_download",
    "file_verify",
    "job",
    "annotation_update",
    "artifacts",
    "assemblies",
    "user",
    "cnv",
    "panel",
    "panel_versions",
    "panel_version",
    "panel_update",
    "project_update",
    "panel_filter",
    "sv_panel_filter",
    "gene_profile",
]


@pytest.mark.parametrize("key", READS)
@pytest.mark.parametrize("spelling", ["braces", "urn", "capitals", "no_hyphens"])
def test_each_spelling_of_a_records_id_names_the_record(snap: dict[str, Any], key: str, spelling: str) -> None:
    answers = snap["responses"][key]
    assert answers["canonical"]["status"] in (200, 404), answers["canonical"]
    # A UUID in braces or after urn:uuid: failed asyncpg's uuid codec here (a 500), but for
    # the panel, which came back without its genes.
    assert _record(answers[spelling]) == _record(answers["canonical"]), (spelling, answers[spelling])


def test_the_records_are_found_by_their_ids(snap: dict[str, Any]) -> None:
    r = snap["responses"]
    for key in READS:
        if key == "gene_profile":
            # The project is found and may be read; no gene is loaded, so none is found.
            assert _status(r[key]["canonical"]) == (404, {"detail": "Gene not found"})
        else:
            assert r[key]["canonical"]["status"] == 200, (key, r[key]["canonical"])
    assert snap["ids"]["artifact"] in [row["id"] for row in r["artifacts"]["canonical"]["json"]]
    assert snap["ids"]["assembly"] in [row["_id"] for row in r["assemblies"]["canonical"]["json"]]


@pytest.mark.parametrize("spelling", ["canonical", "braces", "urn", "capitals", "no_hyphens"])
def test_a_panel_named_by_any_spelling_keeps_its_genes(snap: dict[str, Any], spelling: str) -> None:
    # The genes were looked up under the id as written: the panel came back without any,
    # and an edit by its id in capitals saw none and made a new version.
    assert snap["responses"]["panel"][spelling]["json"]["genes"] == ["E2EGENE1"]
    assert snap["responses"]["panel_update"][spelling]["json"]["panel"]["version"] == 1


def test_a_project_updated_in_braces_keeps_its_members(snap: dict[str, Any]) -> None:
    projects = {row["_id"]: row for row in snap["responses"]["project_b_after"]["json"]}
    assert snap["ids"]["viewer"] in projects[snap["ids"]["project_b"]]["user_ids"]


@pytest.mark.parametrize(
    ("key", "deleted"),
    [
        ("annotation_delete", 204),
        ("artifact_delete", 200),
        ("preset_owner_delete", 204),
        ("preset_family_delete", 204),
        ("sv_preset_delete", 204),
        ("panel_delete", 204),
        ("project_delete", 204),
    ],
)
def test_a_record_is_deleted_by_another_spelling_of_its_id(snap: dict[str, Any], key: str, deleted: int) -> None:
    answers = snap["responses"][key]
    assert answers["deleted"]["status"] == deleted, answers["deleted"]
    # It is gone: its own id now names nothing.
    assert _status(answers["again"]) == NOT_FOUND[key][0], answers["again"]


def test_the_viewer_learns_nothing_of_an_annotation_in_another_project(snap: dict[str, Any]) -> None:
    r = snap["responses"]
    for method in ("PUT", "DELETE"):
        own = r[f"viewer_{method}_own"]
        # Through the viewer's own family: the other family's annotation is not found, as a
        # value that is no UUID and an unknown UUID are not.
        assert _status(own["canonical"]) == (404, {"detail": "HPO annotation was not found"}), own["canonical"]
        for name in ("not_a_uuid", "unknown", "braces", "urn"):
            assert _status(own[name]) == _status(own["canonical"]), (method, name, own[name])
        # Through the other family: the family's own refusal, whatever the annotation id.
        other = r[f"viewer_{method}_other"]
        assert other["canonical"]["status"] in (403, 404), other["canonical"]
        for name in ("not_a_uuid", "unknown", "braces", "urn"):
            assert _status(other[name]) == _status(other["canonical"]), (method, name, other[name])
    # Neither the update nor the delete reached it.
    annotations = r["annotation_after_viewer"]["json"]
    assert [(row["id"], row["note"]) for row in annotations] == [(snap["ids"]["annotation"], None)]
    # An admin route refuses the viewer before it reads the id.
    for name, answer in r["viewer_file"].items():
        assert answer["status"] == 403, (name, answer)


def test_a_refused_request_is_audited_under_its_callers_name(snap: dict[str, Any]) -> None:
    rows = snap["audit_not_a_uuid"]
    # The viewer's request and the admin's, each under its own name and with its own answer.
    assert sorted((row["user_email"], row["status_code"]) for row in rows) == sorted(
        [(snap["viewer_email"], 403), (snap["admin_email"], 404)]
    ), rows


def test_no_operation_answers_a_value_that_is_no_uuid_with_a_500(snap: dict[str, Any]) -> None:
    probes = snap["sweep"]
    assert len({probe["operation"] for probe in probes}) > 100, "the sweep found too few operations"
    assert [probe for probe in probes if probe["status"] >= 500] == []
    # Each answer is the unknown UUID's, or a 400 for a value the route reads as invalid.
    baseline = {
        (probe["operation"], probe["parameter"]): probe["status"] for probe in probes if probe["form"] == "unknown"
    }
    others = [
        probe
        for probe in probes
        if probe["form"] != "unknown"
        and probe["status"] not in (baseline[(probe["operation"], probe["parameter"])], 400)
    ]
    assert others == []
