"""The Microsoft sign-in settings and the refusal of a half configuration (SEC-4, REQ-SEC-006).

Microsoft sign-in needs the directory (tenant) ID and the application (client) ID of CoGA's
app registration, both GUIDs: one without the other, a tenant such as ``common`` or a domain
name, an API scope or the emergency override without Azure AD, and an API scope that is no
application ID URI followed by a scope name are refused when the settings load, in every
environment. An empty value, as Terraform passes an unset variable, is no value. The scope
defaults to ``api://<client ID>/access_as_user``.

Terraform passes the four settings to the backend, its variables default to the password
sign-in with the override off, and a plan refuses half a configuration.

The values are made here: random GUIDs, and secrets put together at run time.
"""

from __future__ import annotations

import base64
import re
import secrets
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


REPO = Path(__file__).resolve().parents[2]


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, APP_ENV="test", **values)


def _directory() -> dict[str, str]:
    return {"AZURE_TENANT_ID": str(uuid4()), "AZURE_CLIENT_ID": str(uuid4())}


def _production() -> dict[str, object]:
    """A deployment's settings, as Terraform gives them, with strong stand-ins for its secrets."""
    return {
        "APP_ENV": "production",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "CLICKHOUSE_PASSWORD": secrets.token_urlsafe(24),
        "INTEGRITY_ANCHOR_SIGNING_KEY": base64.b64encode(bytes(range(32))).decode(),
        "CORS_ORIGINS": ["https://coga.example.org"],
        "CORS_ORIGIN_REGEX": "",
        "GIT_SHA": "0123456789ab",
    }


def test_without_a_directory_users_sign_in_with_a_password() -> None:
    assert _settings().azure_ad_enabled is False
    # Terraform passes an unset variable as an empty value: that is no value.
    blank = _settings(AZURE_TENANT_ID="", AZURE_CLIENT_ID="  ", AZURE_API_SCOPE="")
    assert blank.azure_ad_enabled is False
    assert (blank.azure_tenant_id, blank.azure_client_id, blank.azure_api_scope) == (None, None, None)


def test_with_the_tenant_and_the_client_users_sign_in_with_microsoft() -> None:
    directory = _directory()
    settings = _settings(
        AZURE_TENANT_ID=f" {directory['AZURE_TENANT_ID'].upper()} ",
        AZURE_CLIENT_ID=directory["AZURE_CLIENT_ID"].upper(),
    )

    assert settings.azure_ad_enabled is True
    # Compared as Microsoft writes them in a token: in lower case.
    assert settings.azure_tenant_id == directory["AZURE_TENANT_ID"]
    assert settings.azure_client_id == directory["AZURE_CLIENT_ID"]
    assert settings.azure_api_scope_value == f"api://{directory['AZURE_CLIENT_ID']}/access_as_user"
    assert settings.azure_api_scope_name == "access_as_user"
    assert settings.azure_admin_override is False


def test_an_api_scope_of_its_own_is_used_by_its_name() -> None:
    settings = _settings(**_directory(), AZURE_API_SCOPE="api://coga.example.org/use_coga")
    assert settings.azure_api_scope_value == "api://coga.example.org/use_coga"
    assert settings.azure_api_scope_name == "use_coga"


@pytest.mark.parametrize(
    ("values", "named"),
    [
        pytest.param({"AZURE_TENANT_ID": str(uuid4())}, "AZURE_CLIENT_ID is not set", id="a-tenant-without-a-client"),
        pytest.param({"AZURE_CLIENT_ID": str(uuid4())}, "AZURE_TENANT_ID is not set", id="a-client-without-a-tenant"),
        pytest.param(
            {"AZURE_TENANT_ID": str(uuid4()), "AZURE_CLIENT_ID": ""},
            "AZURE_CLIENT_ID is not set",
            id="a-tenant-with-a-blank-client",
        ),
    ],
)
def test_a_half_configuration_is_refused(values: dict[str, str], named: str) -> None:
    with pytest.raises(ValidationError, match=named):
        _settings(**values)


@pytest.mark.parametrize("tenant", ["common", "organizations", "consumers", "example.org", "not-a-directory-id"])
def test_the_tenant_must_be_one_directorys_id(tenant: str) -> None:
    with pytest.raises(ValidationError, match="AZURE_TENANT_ID must be the directory"):
        _settings(AZURE_TENANT_ID=tenant, AZURE_CLIENT_ID=str(uuid4()))


def test_the_client_must_be_the_application_id() -> None:
    with pytest.raises(ValidationError, match="AZURE_CLIENT_ID must be the application"):
        _settings(AZURE_TENANT_ID=str(uuid4()), AZURE_CLIENT_ID="coga")


@pytest.mark.parametrize(
    "scope",
    ["access_as_user", "api://coga.example.org/.default", "https://coga.example.org/", "api://coga example/use"],
)
def test_an_api_scope_that_is_no_uri_and_name_is_refused(scope: str) -> None:
    with pytest.raises(ValidationError, match="AZURE_API_SCOPE must be"):
        _settings(**_directory(), AZURE_API_SCOPE=scope)


def test_a_scope_or_the_override_without_a_directory_is_refused() -> None:
    with pytest.raises(ValidationError, match="AZURE_API_SCOPE is set without"):
        _settings(AZURE_API_SCOPE="api://coga.example.org/use_coga")
    with pytest.raises(ValidationError, match="AZURE_ADMIN_OVERRIDE=true"):
        _settings(AZURE_ADMIN_OVERRIDE=True)
    assert _settings(**_directory(), AZURE_ADMIN_OVERRIDE=True).azure_admin_override is True


def test_a_deployment_starts_with_microsoft_sign_in_and_refuses_half_of_it() -> None:
    directory = _directory()
    deployed = Settings(_env_file=None, **_production(), **directory)
    assert deployed.azure_ad_enabled is True and deployed.is_development is False

    with pytest.raises(ValidationError, match="AZURE_CLIENT_ID is not set"):
        Settings(_env_file=None, **_production(), AZURE_TENANT_ID=directory["AZURE_TENANT_ID"])


def test_terraform_passes_every_microsoft_sign_in_setting_to_the_backend() -> None:
    cloudrun = (REPO / "terraform" / "cloudrun.tf").read_text()
    for name, value in (
        ("AZURE_TENANT_ID", "var.azure_ad_tenant_id"),
        ("AZURE_CLIENT_ID", "var.azure_ad_client_id"),
        ("AZURE_API_SCOPE", "var.azure_ad_api_scope"),
        ("AZURE_ADMIN_OVERRIDE", "tostring(var.azure_admin_override)"),
    ):
        assert re.search(rf'name\s*=\s*"{name}"\s*\n\s*value\s*=\s*{re.escape(value)}\n', cloudrun), name
    # A plan refuses half a configuration, as the backend does at startup.
    assert '(var.azure_ad_tenant_id == "") == (var.azure_ad_client_id == "")' in cloudrun
    variables = (REPO / "terraform" / "variables.tf").read_text()
    for name in ("azure_ad_tenant_id", "azure_ad_client_id", "azure_ad_api_scope"):
        assert re.search(rf'variable "{name}" \{{[^}}]*default\s*=\s*""', variables), name
    assert re.search(r'variable "azure_admin_override" \{[^}]*default\s*=\s*false', variables)
