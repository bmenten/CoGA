"""Sign-in through the organisation's OAuth 2.0 server, such as an OAuth application Ghent
University's ICT registered for CoGA (SEC-4).

The authorization code flow with PKCE (RFC 6749 §4.1, RFC 7636). The browser makes a one-time
``state`` and code verifier, keeps them in its tab, and goes to ``OAUTH_AUTHORIZE_URL`` for
CoGA's client with the verifier's S256 challenge. The server signs the user in (at Ghent
University, with Microsoft Entra ID behind it) and sends the browser back to
``OAUTH_REDIRECT_URL``, the app's /login page, with a one-time code and the same ``state``,
which the page checks before it hands the code and its verifier to the API. This module
redeems the code at ``OAUTH_TOKEN_URL`` (server to server over HTTPS, with the client ID and
secret, the redirect address and the verifier), and with the access token it gets, reads the
user from ``OAUTH_USERINFO_URL``. The server gives a code once, only to the client it was
issued to, and only with the verifier whose challenge it saw, so a code taken from the
address bar is worth nothing to anyone else.

CoGA accepts the sign-in only when the user info carries the user's object ID in the
organisation's Entra ID (``OAUTH_OBJECT_ID_CLAIM``, a UUID): the account is bound to it
(``users.azure_object_id``), never to an e-mail address, as with the CAS sign-in. The e-mail
address and the name come from the other fields. The user info is read as a JSON object of
reasonable size; a field is looked up in it, or in an ``attributes`` object inside it, and may
hold one value or a list of them.

Anything else raises ``OAuthError``, whose message says why and never quotes the code, the
verifier, the client secret, a token or the server's answer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, quote_plus

import httpx

from .azure import AzureIdentity
from .config import settings
from .sql import canonical_uuid

_FETCH_TIMEOUT_SECONDS = 10.0
# A token answer and the user info name one user and a handful of fields: a few kilobytes.
_MAX_ANSWER_BYTES = 64 * 1024
_MAX_ACCESS_TOKEN_LENGTH = 8192
# An authorization code as OAuth servers write it (RFC 6749 §A.11 allows any printable
# character; these are the URL-safe ones servers use). Anything else is refused before the
# server is asked.
_AUTHORIZATION_CODE = re.compile(r"[A-Za-z0-9._~+/=-]{1,2048}")
# A PKCE code verifier (RFC 7636 §4.1).
_CODE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
# The error code an OAuth server names (RFC 6749 §5.2), never its description.
_ERROR_CODE = re.compile(r"[a-z_]{1,40}")


class OAuthError(Exception):
    """A sign-in CoGA does not accept; the message says why and quotes neither the code, the
    verifier, a token, the client secret nor the server's answer."""


def authorize_url() -> str:
    """Where the browser signs in: the server's authorization page for CoGA's client. The
    browser adds its own ``state`` and PKCE challenge."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": str(settings.oauth_client_id),
        "redirect_uri": str(settings.oauth_redirect_url),
    }
    scope = " ".join(settings.oauth_scope.split())
    if scope:
        params["scope"] = scope
    return str(httpx.URL(str(settings.oauth_authorize_url), params=params))


def logout_url() -> str | None:
    """The server's sign-out page, when one is configured."""
    return settings.oauth_logout_url or None


def _error_code(body: bytes) -> str | None:
    """The error code an OAuth error answer names, when it names a well-formed one."""
    try:
        answer = json.loads(body)
    except ValueError:
        return None
    code = answer.get("error") if isinstance(answer, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else None


def _read_json_object(body: bytes, what: str) -> dict[str, Any]:
    if len(body) > _MAX_ANSWER_BYTES:
        raise OAuthError(f"the {what} is too large")
    try:
        answer = json.loads(body)
    except ValueError as exc:
        raise OAuthError(f"the {what} is not JSON") from exc
    if not isinstance(answer, dict):
        raise OAuthError(f"the {what} is not a JSON object")
    return answer


def _read_token_answer(response: httpx.Response) -> str:
    """The access token of a successful token answer: JSON (RFC 6749 §5.1), or the form an
    older server writes."""
    body = response.content
    if len(body) > _MAX_ANSWER_BYTES:
        raise OAuthError("the token answer is too large")
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in {"application/x-www-form-urlencoded", "text/plain"}:
        try:
            fields: Mapping[str, Any] = dict(parse_qsl(body.decode("ascii"), strict_parsing=True))
        except (UnicodeDecodeError, ValueError) as exc:
            raise OAuthError("the token answer is not readable") from exc
    else:
        fields = _read_json_object(body, "token answer")
    token = fields.get("access_token")
    if not isinstance(token, str) or not token.strip() or len(token) > _MAX_ACCESS_TOKEN_LENGTH:
        raise OAuthError("the token answer carries no access token")
    token_type = fields.get("token_type")
    if token_type is not None and (not isinstance(token_type, str) or token_type.lower() != "bearer"):
        raise OAuthError("the token answer carries no bearer token")
    return token.strip()


async def _redeem_code(client: httpx.AsyncClient, code: str, verifier: str) -> str:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(settings.oauth_redirect_url),
        "code_verifier": verifier,
    }
    auth: httpx.BasicAuth | None = None
    if settings.oauth_token_auth_method == "client_secret_post":
        form["client_id"] = str(settings.oauth_client_id)
        form["client_secret"] = str(settings.oauth_client_secret)
    else:
        # RFC 6749 §2.3.1: each part form-encoded before it is joined (a no-op for the
        # letters, digits and "-._~" servers write).
        auth = httpx.BasicAuth(
            quote_plus(str(settings.oauth_client_id)),
            quote_plus(str(settings.oauth_client_secret)),
        )
    try:
        response = await client.post(
            str(settings.oauth_token_url),
            data=form,
            auth=auth,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise OAuthError(f"the token endpoint could not be reached ({type(exc).__name__})") from exc
    if response.status_code != 200:
        code_name = _error_code(response.content[:_MAX_ANSWER_BYTES])
        detail = f", {code_name}" if code_name else ""
        raise OAuthError(f"the token endpoint refused the code (HTTP {response.status_code}{detail})")
    return _read_token_answer(response)


async def _user_info(client: httpx.AsyncClient, access_token: str) -> dict[str, Any]:
    try:
        response = await client.get(
            str(settings.oauth_userinfo_url),
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise OAuthError(f"the user-info endpoint could not be reached ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise OAuthError(f"the user-info endpoint answered HTTP {response.status_code}")
    return _read_json_object(response.content, "user info")


def _first_value(info: Mapping[str, Any], name: str) -> str | None:
    """A field of the user info, at its top level or in its ``attributes`` object: its value,
    or the first non-blank one of a list."""
    value = info.get(name)
    if value is None:
        attributes = info.get("attributes")
        if isinstance(attributes, Mapping):
            value = attributes.get(name)
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def identity_from_user_info(info: Mapping[str, Any]) -> AzureIdentity:
    """Who the user info signs in, as Microsoft sign-in names a user."""
    object_id = canonical_uuid(_first_value(info, settings.oauth_object_id_claim))
    if object_id is None:
        raise OAuthError(
            f"the user info carries no object ID ({settings.oauth_object_id_claim}); the OAuth "
            "server must release that field to CoGA"
        )
    given = _first_value(info, settings.oauth_given_name_claim)
    family = _first_value(info, settings.oauth_family_name_claim)
    return AzureIdentity(
        object_id=object_id,
        # The account's user name is its e-mail address.
        preferred_username=None,
        email=_first_value(info, settings.oauth_email_claim),
        given_name=given,
        family_name=family,
        name=" ".join(part for part in (given, family) if part) or None,
    )


async def verify_authorization_code(code: str, verifier: str) -> AzureIdentity:
    """Who the authorization code signs in, as the OAuth server confirms it for CoGA's
    client."""
    if not isinstance(code, str) or not _AUTHORIZATION_CODE.fullmatch(code):
        raise OAuthError("the code is not an authorization code")
    if not isinstance(verifier, str) or not _CODE_VERIFIER.fullmatch(verifier):
        raise OAuthError("the code verifier is not a PKCE code verifier")
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
        access_token = await _redeem_code(client, code, verifier)
        info = await _user_info(client, access_token)
    return identity_from_user_info(info)
