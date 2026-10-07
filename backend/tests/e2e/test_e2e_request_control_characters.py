"""A control character in a request's path or query string is refused at sign-in (E2E).

A ``%00`` in a URL arrives decoded (uvicorn and Starlette unquote the path, and the query
string is parsed the same way), so ``GET /api/families/FAM%00X`` bound a NUL into the family
lookup. Postgres refuses a NUL in a text parameter (SQLSTATE 22021), and the request failed
with a 500; so did a NUL in every other path or query value a route looked up, the admin
and reference routes included. No family or sample ID may hold a control character (C0 or
DEL, ``family_identifiers.py``), nor any other key a route looks up, so ``get_current_user``
refuses one in a path parameter, a query parameter's name or a ``family_id`` or
``sample_id`` query value, and a NUL in any query value, with a 400 once the caller is
signed in.

Over the real API (an in-process ``httpx.ASGITransport`` client, see test_e2e_api_contract.py)
and Postgres and ClickHouse, with a synthetic family made through the Family Builder, the
seeded admin and a viewer outside the family's project:

* a NUL in a family's path, in a sample's path on a write route and in a ``sample_id`` query
  value is answered 400, as are a tab, an escape and DEL in the family's path and a tab in
  the ``sample_id``; the member the write named still exists;
* the search form's gene and interval lists, typed one per line, are still searched; with a
  NUL in the gene list the search is refused;
* the answer is the same for the admin and for the viewer, and the same whether the ID
  before the NUL is a family's or no family's, so it tells nothing about which families
  exist or who may see them; without a token the answer is still the 401;
* each refusal is in ``audit_log_events`` under the caller's name, with status 400;
* every operation of the API, with a NUL in each of its path and query parameters in turn,
  answers 400; only the QC-report download, which checks its own signed link first,
  answers otherwise (403, or 422 without a link), and none answers 500.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_NUL = "%00"
# The QC-report download is opened by a browser navigation, without a bearer token: it
# checks the signed link in its query string before it looks anything up.
_SIGNS_IN_ITSELF = {("GET", "/api/families/{family_id}/qc-report/{sample_id}")}


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:
        out["json"] = None
    return out


async def _audit_rows(session: Any, *, method: str, path: str) -> list[dict[str, Any]]:
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            SELECT path, status_code, user_email, user_role
            FROM audit_log_events
            WHERE method = :method AND path = :path
            ORDER BY created_at
            """
        ),
        {"method": method, "path": path},
    )
    return [dict(row) for row in result.mappings().all()]


async def _sweep(ac: Any) -> list[dict[str, Any]]:
    """Every operation, a NUL in each of its path and query parameters in turn."""
    from backend.app.main import app

    probes: list[dict[str, Any]] = []
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            parameters = operation.get("parameters", [])
            path_names = [p["name"] for p in parameters if p["in"] == "path"]
            for where, name in [("path", n) for n in path_names] + [
                ("query", p["name"]) for p in parameters if p["in"] == "query"
            ]:
                url = path
                for path_name in path_names:
                    value = "E2E" + (_NUL + "X" if (where, path_name) == ("path", name) else "")
                    url = url.replace("{" + path_name + "}", value)
                if where == "query":
                    url = f"{url}?{quote(name)}=E2E{_NUL}X"
                resp = await ac.request(method.upper(), url)
                probes.append(
                    {"operation": (method.upper(), path), "parameter": f"{where}:{name}", "status": resp.status_code}
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

    family, sample, unknown = f"E2ECC{run}", f"E2ECC{run}M", f"E2ENONE{run}"
    viewer_email, viewer_password = f"e2e-viewer-{run}@example.com", f"viewer-password-{run}"

    await init_postgres_schema()
    # The family's variant search runs against the assembly's ClickHouse tables.
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        # A viewer in no project: the family below is not theirs to see.
        await session.execute(
            text(
                """
                INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                VALUES (:email, :hashed, 'viewer', :email, '{}'::jsonb, :created_at)
                """
            ),
            {
                "email": viewer_email,
                "hashed": get_password_hash(viewer_password),
                "created_at": datetime.now(timezone.utc),
            },
        )
        await session.commit()

    out: dict[str, Any] = {"family": family, "sample": sample, "unknown": unknown}
    r: dict[str, Any] = {}
    out["responses"] = r
    # A request the app fails on is answered 500, as a server answers it, so that every step
    # below is taken and asserted on its own.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac, AsyncClient(
            transport=transport, base_url="http://e2e"
        ) as viewer, AsyncClient(transport=transport, base_url="http://e2e") as anonymous:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
            login = await viewer.post("/api/auth/login", json={"email": viewer_email, "password": viewer_password})
            assert login.status_code == 200, login.text
            viewer.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

            r["create"] = _cap(
                await ac.post(
                    "/api/ped/manual",
                    json={
                        "family_id": family,
                        "project_id": project_id,
                        "members": [{"sample_id": sample, "sex": "female", "is_proband": True}],
                    },
                )
            )
            r["family"] = _cap(await ac.get(f"/api/families/{family}"))
            r["family_nul"] = _cap(await ac.get(f"/api/families/{family}{_NUL}X"))
            r["family_nul_viewer"] = _cap(await viewer.get(f"/api/families/{family}{_NUL}X"))
            r["unknown_nul"] = _cap(await ac.get(f"/api/families/{unknown}{_NUL}X"))
            r["unknown_nul_viewer"] = _cap(await viewer.get(f"/api/families/{unknown}{_NUL}X"))
            r["family_nul_anonymous"] = _cap(await anonymous.get(f"/api/families/{family}{_NUL}X"))
            r["family_tab"] = _cap(await ac.get(f"/api/families/{family}%09"))
            r["family_esc"] = _cap(await ac.get(f"/api/families/{family}%1B"))
            r["family_del"] = _cap(await ac.get(f"/api/families/{family}%7F"))
            r["query_nul"] = _cap(await ac.get(f"/api/families/{family}/hpo?sample_id={sample}{_NUL}"))
            r["query_tab"] = _cap(await ac.get(f"/api/families/{family}/hpo?sample_id={sample}%09"))
            # The search form's gene and interval lists, typed one per line.
            one_per_line = "gene=GENE1%0AGENE2&intervals=chr1:1-100%0Achr2:5-10&prioritize=true"
            r["gene_list"] = _cap(await ac.get(f"/api/families/{family}/small-variants?{one_per_line}"))
            r["gene_list_nul"] = _cap(await ac.get(f"/api/families/{family}/small-variants?gene=GENE1%0AGENE2{_NUL}"))
            r["delete_member_nul"] = _cap(
                await ac.delete(f"/api/families/{family}/members/{sample}{_NUL}?confirm=true")
            )
            r["members_after"] = _cap(await ac.get(f"/api/families/{family}"))
            out["sweep"] = await _sweep(ac)

        async with sessionmaker() as session:
            out["audit_family_nul"] = await _audit_rows(
                session, method="GET", path=f"/api/families/{family}\\x00X"
            )
            out["audit_delete_member_nul"] = await _audit_rows(
                session, method="DELETE", path=f"/api/families/{family}/members/{sample}\\x00"
            )
    finally:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
            out["cleanup"] = (await ac.delete(f"/api/admin/families/{family}?confirm=true")).status_code
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM users WHERE email = :email"), {"email": viewer_email})
            await session.commit()
    out["admin_email"] = settings.admin_email
    out["viewer_email"] = viewer_email
    return out


@pytest.fixture(scope="module")
def snap() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:10].upper()
    return _harness.run_async(lambda: _collect(run))


def test_the_family_is_there_to_be_looked_up(snap: dict[str, Any]) -> None:
    r = snap["responses"]
    assert r["create"]["status"] == 200, r["create"]
    assert r["family"]["status"] == 200, r["family"]
    assert [m["sample_id"] for m in r["family"]["json"]["members"]] == [snap["sample"]]


def test_a_nul_in_a_family_path_is_refused(snap: dict[str, Any]) -> None:
    # It used to reach the family lookup, and Postgres failed the request (500).
    r = snap["responses"]["family_nul"]
    assert r["status"] == 400, r
    assert r["json"]["detail"] == (
        f"family_id '{snap['family']}\\x00X' (in the path) contains a control character (\\x00). "
        "Family and sample IDs are printable text without spaces."
    )


def test_the_answer_is_the_same_whoever_asks_and_whatever_exists(snap: dict[str, Any]) -> None:
    r = snap["responses"]
    # The viewer may not see the family; the admin may. The answer is the same.
    assert r["family_nul_viewer"]["status"] == r["family_nul"]["status"] == 400
    assert r["family_nul_viewer"]["json"] == r["family_nul"]["json"]
    # A family that exists and one that does not: the same answer, but for the ID it echoes.
    assert r["unknown_nul"]["status"] == r["unknown_nul_viewer"]["status"] == 400
    assert r["unknown_nul_viewer"]["json"] == r["unknown_nul"]["json"]
    assert r["unknown_nul"]["json"]["detail"].replace(snap["unknown"], snap["family"]) == (
        r["family_nul"]["json"]["detail"]
    )


def test_without_a_token_the_answer_is_still_the_401(snap: dict[str, Any]) -> None:
    assert snap["responses"]["family_nul_anonymous"]["status"] == 401


@pytest.mark.parametrize(("key", "escape"), [("family_tab", "\\t"), ("family_esc", "\\x1b"), ("family_del", "\\x7f")])
def test_any_other_control_character_in_a_family_path_is_refused(snap: dict[str, Any], key: str, escape: str) -> None:
    # Postgres compares these, so the lookup used to answer 404: no family can hold one.
    r = snap["responses"][key]
    assert r["status"] == 400, r
    assert f"contains a control character ({escape})" in r["json"]["detail"]


def test_a_control_character_in_a_sample_id_query_value_is_refused(snap: dict[str, Any]) -> None:
    r = snap["responses"]
    assert r["query_nul"]["status"] == 400, r["query_nul"]
    assert r["query_nul"]["json"]["detail"].startswith(f"sample_id '{snap['sample']}\\x00' (in the query string)")
    assert r["query_tab"]["status"] == 400, r["query_tab"]
    assert r["query_tab"]["json"]["detail"].startswith(f"sample_id '{snap['sample']}\\t' (in the query string)")


def test_a_gene_list_typed_one_per_line_is_searched_but_not_with_a_nul(snap: dict[str, Any]) -> None:
    r = snap["responses"]
    # The family has no variants: the search runs and finds none.
    assert r["gene_list"]["status"] == 200, r["gene_list"]
    assert r["gene_list"]["json"]["total"] == 0
    assert r["gene_list_nul"]["status"] == 400, r["gene_list_nul"]
    assert r["gene_list_nul"]["json"]["detail"] == (
        "gene 'GENE1\\nGENE2\\x00' (in the query string) contains a control character (\\x00)."
    )


def test_a_write_naming_a_sample_with_a_nul_is_refused_and_writes_nothing(snap: dict[str, Any]) -> None:
    r = snap["responses"]
    assert r["delete_member_nul"]["status"] == 400, r["delete_member_nul"]
    assert [m["sample_id"] for m in r["members_after"]["json"]["members"]] == [snap["sample"]]


def test_each_refusal_is_audited_under_the_callers_name(snap: dict[str, Any]) -> None:
    rows = snap["audit_family_nul"]
    # The admin's request and the viewer's, each under its own name; the request without a
    # token was refused before anyone was signed in.
    assert sorted((row["user_email"] or "", row["status_code"]) for row in rows) == sorted(
        [(snap["admin_email"], 400), (snap["viewer_email"], 400), ("", 401)]
    ), rows
    assert [(row["user_email"], row["status_code"]) for row in snap["audit_delete_member_nul"]] == [
        (snap["admin_email"], 400)
    ]


def test_no_operation_answers_a_nul_with_a_500(snap: dict[str, Any]) -> None:
    probes = snap["sweep"]
    assert len({probe["operation"] for probe in probes}) > 100, "the sweep found too few operations"
    assert [probe for probe in probes if probe["status"] >= 500] == []
    others = {
        (probe["operation"], probe["parameter"], probe["status"])
        for probe in probes
        if probe["status"] != 400
    }
    assert {operation for operation, _parameter, _status in others} <= _SIGNS_IN_ITSELF, sorted(others)
    # Its link is invalid (403), or missing when a path parameter was probed (422).
    assert {status for _operation, _parameter, status in others} <= {403, 422}
