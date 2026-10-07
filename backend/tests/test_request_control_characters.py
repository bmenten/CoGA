"""A request whose path or query string holds a control character is refused at sign-in.

A ``%00`` in a URL arrives decoded, so ``/api/families/FAM%00X`` carried a NUL into the family
lookup; Postgres cannot compare one, and the request failed with a 500, as did a NUL in any
other path or query value a route looked up. No family or sample ID may hold a control
character (C0 or DEL, ``family_identifiers.py``), nor any other key a route looks up, so
``get_current_user`` refuses one in a path parameter, a query parameter's name or a
``family_id`` or ``sample_id`` query value, and a NUL in any query value, with a 400 once the
caller is signed in: after the 401 for a missing token, so the refusal's audit row names the
caller, and before any route reads the value, with the same answer whoever asks. A gene or
interval list, typed one per line, keeps its line breaks. The requests here go through the
real ``get_current_user`` with a signed token; only the user lookup, the audit write and the
routes' services are replaced. All IDs are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.app import dependencies
from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import create_access_token
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import admin as admin_router
from backend.app.routers import families as families_router
from backend.app.routers import families_small_variants as small_variants_router
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_identifiers import (
    IDENTIFIER_RULE,
    control_character_problem,
    request_value_problem,
    visible,
)

NUL, TAB, LF, ESC, DEL = "\x00", "\t", "\n", "\x1b", "\x7f"
VIEWER, ADMIN = "viewer@example.com", "admin@example.com"
# The one route with a path or query value that does not sign its caller in: the QC-report
# download, opened by a browser navigation, checks its own signed link first (403), so the
# values reach no lookup unless the link was issued for that family and sample.
_SIGNS_IN_ITSELF = {("get", "/api/families/{family_id}/qc-report/{sample_id}")}


def _user(email: str, role: str) -> CurrentUser:
    return CurrentUser(
        id=f"user-{role}",
        username=email,
        email=email,
        role=role,
        created_at=datetime.now(timezone.utc),
    )


class _FakeSession:
    async def rollback(self) -> None:
        return None


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    """A client whose requests are signed in by the real ``get_current_user``. Records the
    family lookups the routes make and the audit rows the middleware writes."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    # Long enough for HS256 (the test default is shorter); the tokens are minted below.
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    users = {VIEWER: _user(VIEWER, "viewer"), ADMIN: _user(ADMIN, "admin")}

    async def fake_user_by_email(session: Any, email: str) -> CurrentUser | None:
        return users.get(email)

    lookups: list[str] = []

    async def fake_get_family_for_user(session: Any, family_id: str, user: CurrentUser) -> Any:
        lookups.append(family_id)
        raise HTTPException(status_code=404, detail="Family not found")

    async def fake_build_family_metadata_context(session: Any, *, family_identifier: str, **kwargs: Any) -> Any:
        lookups.append(family_identifier)
        raise HTTPException(status_code=404, detail="Family not found")

    async def fake_delete_family_with_data(session: Any, family_id: str, confirm: bool) -> Any:
        lookups.append(family_id)
        raise HTTPException(status_code=404, detail="Family not found")

    audit_rows: list[Any] = []

    async def record_audit_row(payload: Any) -> None:
        audit_rows.append(payload)

    async def override_get_postgres_session():
        yield _FakeSession()

    monkeypatch.setattr(dependencies, "get_current_user_by_email", fake_user_by_email)
    monkeypatch.setattr(families_router, "get_family_for_user", fake_get_family_for_user)
    monkeypatch.setattr(families_router, "build_family_metadata_context", fake_build_family_metadata_context)
    monkeypatch.setattr(small_variants_router, "build_family_metadata_context", fake_build_family_metadata_context)
    monkeypatch.setattr(admin_router, "delete_family_with_data", fake_delete_family_with_data)
    monkeypatch.setattr(request_logging, "write_audit_log_event", record_audit_row)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    with TestClient(app) as client:
        yield {"client": client, "lookups": lookups, "audit_rows": audit_rows}
    app.dependency_overrides = original_overrides


def _headers(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", [*range(0x20), 0x7F])
def test_every_c0_control_character_and_del_is_a_problem(code: int) -> None:
    character = chr(code)
    assert control_character_problem(f"FAM{character}001") == (
        f"contains a control character ({visible(character)})"
    )


@pytest.mark.parametrize("value", ["FAM001", "FAM 001", "FAM\xa0001", "FAM\x85001", "GRCh38", "BRCA1,TP53", ""])
def test_printable_text_and_whitespace_other_than_c0_are_no_problem(value: str) -> None:
    # A lookup is refused only a control character; a space, a no-break space or a C1
    # character finds nothing, as any unknown ID does.
    assert control_character_problem(value) is None


def test_a_family_or_sample_id_in_the_path_is_named_with_the_rule() -> None:
    assert request_value_problem({"family_id": f"FAM{NUL}X"}, []) == (
        f"family_id 'FAM\\x00X' (in the path) contains a control character (\\x00). {IDENTIFIER_RULE}"
    )
    assert request_value_problem({"family_id": "FAM1", "sample_id": f"S{ESC}1"}, []) == (
        f"sample_id 'S\\x1b1' (in the path) contains a control character (\\x1b). {IDENTIFIER_RULE}"
    )


def test_any_other_path_parameter_is_named_without_it() -> None:
    assert request_value_problem({"assembly": "GRCh38", "chrom": f"1{DEL}"}, []) == (
        "chrom '1\\x7f' (in the path) contains a control character (\\x7f)."
    )


@pytest.mark.parametrize("name", ["family_id", "sample_id"])
@pytest.mark.parametrize("character", [NUL, TAB, LF, ESC, DEL])
def test_a_family_or_sample_id_in_the_query_string_may_hold_no_control_character(name: str, character: str) -> None:
    assert request_value_problem({}, [("page", "1"), (name, f"S{character}1")]) == (
        f"{name} 'S{visible(character)}1' (in the query string) contains a control character "
        f"({visible(character)}). {IDENTIFIER_RULE}"
    )


def test_any_other_query_value_may_hold_a_line_break_or_a_tab_but_not_a_nul() -> None:
    # The gene and interval lists are typed one per line, and a pasted list keeps its tabs.
    one_per_line = [("gene", f"BRCA1{LF}TP53"), ("intervals", f"chr1:1-100{LF}chr2:5-10"), ("q", f"seiz{TAB}ure")]
    assert request_value_problem({}, one_per_line) is None
    # The NUL is named, though a line break comes first.
    assert request_value_problem({}, [("gene", f"BRCA1{LF}TP53{NUL}")]) == (
        "gene 'BRCA1\\nTP53\\x00' (in the query string) contains a control character (\\x00)."
    )


@pytest.mark.parametrize("character", [NUL, LF, DEL])
def test_a_query_parameter_name_may_hold_no_control_character(character: str) -> None:
    assert request_value_problem({}, [(f"k{character}ey", "1")]) == (
        f"The query string has a parameter name, 'k{visible(character)}ey', that contains a control "
        f"character ({visible(character)})."
    )


def test_a_value_a_route_converted_is_skipped_and_a_clean_request_has_no_problem() -> None:
    # A path parameter Starlette already converted (an int) holds no text.
    assert request_value_problem({"version": 3, "family_id": "FAM1"}, [("q", "a b"), ("gene", "TP53")]) is None


def test_the_path_is_checked_before_the_query_string() -> None:
    problem = request_value_problem({"family_id": f"F{NUL}"}, [("q", f"x{NUL}")])
    assert problem is not None and problem.startswith("family_id ")


# --------------------------------------------------------------------------- #
# At sign-in
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("character", [NUL, TAB, LF, ESC, DEL])
def test_a_control_character_in_a_family_path_is_refused_before_the_family_is_looked_up(
    api: dict[str, Any], character: str
) -> None:
    encoded = f"%{ord(character):02X}"
    response = api["client"].get(f"/api/families/FAM{encoded}X", headers=_headers(VIEWER))

    assert response.status_code == 400, response.text
    assert response.json() == {
        "detail": f"family_id 'FAM{visible(character)}X' (in the path) contains a control character "
        f"({visible(character)}). {IDENTIFIER_RULE}"
    }
    assert api["lookups"] == []


def test_the_refusal_is_the_same_whoever_asks(api: dict[str, Any]) -> None:
    # Decided from the request alone, before any lookup: whether such a family exists, or is
    # in the caller's projects, makes no difference to the answer.
    answers = [
        api["client"].get("/api/families/FAM%00X", headers=_headers(email)) for email in (VIEWER, ADMIN)
    ]
    assert [answer.status_code for answer in answers] == [400, 400]
    assert answers[0].json() == answers[1].json()
    assert api["lookups"] == []


def test_a_request_without_a_valid_token_gets_its_401_first(api: dict[str, Any]) -> None:
    unsigned = api["client"].get("/api/families/FAM%00X")
    forged = api["client"].get("/api/families/FAM%00X", headers={"Authorization": "Bearer not-a-token"})
    unknown = api["client"].get("/api/families/FAM%00X", headers=_headers("nobody@example.com"))

    assert [unsigned.status_code, forged.status_code, unknown.status_code] == [401, 401, 401]
    assert api["lookups"] == []


def test_a_control_character_in_a_query_value_or_name_is_refused(api: dict[str, Any]) -> None:
    client = api["client"]
    value = client.get("/api/families/FAM1/hpo?sample_id=S%09X", headers=_headers(VIEWER))
    name = client.get("/api/families/FAM1/hpo?sample%00_id=S1", headers=_headers(VIEWER))

    assert value.status_code == 400, value.text
    assert value.json()["detail"].startswith("sample_id 'S\\tX' (in the query string) contains")
    assert name.status_code == 400, name.text
    assert name.json()["detail"].startswith("The query string has a parameter name, 'sample\\x00_id'")
    assert api["lookups"] == []


def test_an_admin_route_refuses_it_at_sign_in_before_the_role_check(api: dict[str, Any]) -> None:
    client = api["client"]
    refused = client.delete("/api/admin/families/FAM%00X", headers=_headers(VIEWER))
    clean = client.delete("/api/admin/families/FAM1", headers=_headers(VIEWER))

    assert refused.status_code == 400, refused.text
    assert clean.status_code == 403, clean.text
    assert api["lookups"] == []


def test_the_refusal_is_audited_under_the_callers_name(api: dict[str, Any]) -> None:
    response = api["client"].get("/api/families/FAM%00X", headers=_headers(VIEWER))

    assert response.status_code == 400
    rows = [row for row in api["audit_rows"] if row.path == "/api/families/FAM\x00X"]
    assert len(rows) == 1, api["audit_rows"]
    assert rows[0].status_code == 400
    assert rows[0].user_email == VIEWER
    assert rows[0].user_role == "viewer"


def test_a_gene_and_an_interval_list_typed_one_per_line_reach_the_route(api: dict[str, Any]) -> None:
    client = api["client"]
    search = "gene=BRCA1%0ATP53&intervals=chr1:1-100%0Achr2:5-10&exclude_gene=TTN%09OBSCN"
    listed = client.get(f"/api/families/FAM1/small-variants?{search}", headers=_headers(VIEWER))
    with_nul = client.get("/api/families/FAM2/small-variants?gene=BRCA1%0ATP53%00", headers=_headers(VIEWER))

    assert listed.status_code == 404, listed.text
    assert with_nul.status_code == 400, with_nul.text
    assert with_nul.json()["detail"] == (
        "gene 'BRCA1\\nTP53\\x00' (in the query string) contains a control character (\\x00)."
    )
    assert api["lookups"] == ["FAM1"]


def test_a_request_without_a_control_character_reaches_the_route(api: dict[str, Any]) -> None:
    client = api["client"]
    family = client.get("/api/families/FAM%20001", headers=_headers(VIEWER))
    hpo = client.get("/api/families/FAM1/hpo?sample_id=S1", headers=_headers(ADMIN))

    # The route ran and looked the family up; a space is no control character.
    assert (family.status_code, hpo.status_code) == (404, 404)
    assert api["lookups"] == ["FAM 001", "FAM1"]


def test_every_route_that_takes_a_path_or_query_value_signs_its_caller_in() -> None:
    # The refusal lives in get_current_user, so it covers a route only when the route signs
    # its caller in: the OAuth2 scheme in an operation's security requirement says so (it is
    # used by get_current_user alone).
    assert dependencies.oauth2_scheme.scheme_name == "OAuth2PasswordBearer"
    not_signed_in = {
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
        if any(parameter["in"] in {"path", "query"} for parameter in operation.get("parameters", []))
        and {"OAuth2PasswordBearer": []} not in operation.get("security", [])
    }
    assert not_signed_in == _SIGNS_IN_ITSELF
