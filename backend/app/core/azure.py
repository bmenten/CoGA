"""Microsoft sign-in: check an access token Microsoft Entra ID (Azure AD) issued for CoGA (SEC-4).

The browser signs a user in with Microsoft (MSAL, the authorization code flow with PKCE) and
asks for an access token for CoGA's API scope; it sends that token as the bearer of every API
request. This module decides whether such a token is one CoGA accepts:

- signed (RS256) with one of the directory's published signing keys, found by the token's
  ``kid``. The keys come from the directory's OpenID configuration on
  ``login.microsoftonline.com`` and are kept for a day; a token naming a key CoGA does not
  have makes it fetch them again, at most every five minutes, as Microsoft rotates its keys;
- a version 2.0 token (``ver``) of CoGA's own directory: issued by
  ``https://login.microsoftonline.com/<tenant>/v2.0`` (``iss``), for that tenant (``tid``);
- for CoGA's API (``aud``, the application ID) and asked for by CoGA itself (``azp``, the same
  application: the app registration serves both the browser and the API);
- a user's delegated token that carries CoGA's scope (``scp``), not an application's token;
- within its lifetime (``exp``, ``nbf``, with a minute's leeway for clock drift);
- naming the user's object ID (``oid``), by which CoGA finds the user's account.

Anything else raises ``AzureTokenError``. The token itself is never logged.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt

AZURE_LOGIN_HOST = "https://login.microsoftonline.com"
# Signing keys are refreshed after a day, and on a token naming an unknown key at most this
# often: a stream of tokens with made-up key IDs cannot turn every request into a fetch.
_SIGNING_KEYS_MAX_AGE_SECONDS = 24 * 60 * 60
_SIGNING_KEYS_REFETCH_INTERVAL_SECONDS = 5 * 60
_FETCH_TIMEOUT_SECONDS = 5.0
# Clock drift allowed between Microsoft and this server, on exp, nbf and iat.
_LEEWAY_SECONDS = 60


class AzureTokenError(Exception):
    """A bearer token CoGA does not accept as a Microsoft access token; the message says why,
    and never quotes the token."""


@dataclass(frozen=True)
class AzureIdentity:
    """Who a verified Microsoft access token signs in."""

    object_id: str  # oid, the user's object ID in the directory, as a canonical UUID
    preferred_username: str | None
    email: str | None
    given_name: str | None
    family_name: str | None
    name: str | None


def authority(tenant_id: str) -> str:
    """The sign-in authority of the directory, as the browser's MSAL uses it."""
    return f"{AZURE_LOGIN_HOST}/{tenant_id}"


def expected_issuer(tenant_id: str) -> str:
    """The issuer of every version 2.0 token of the directory."""
    return f"{AZURE_LOGIN_HOST}/{tenant_id}/v2.0"


async def _get_json(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.get(url)
    response.raise_for_status()
    document = response.json()
    if not isinstance(document, dict):
        raise ValueError("not a JSON object")
    return document


@dataclass
class _SigningKeys:
    tenant_id: str
    keys: dict[str, dict[str, Any]]
    fetched_at: float
    # When a token naming a key not in ``keys`` last made CoGA fetch the set.
    unknown_kid_fetched_at: float = float("-inf")


_signing_keys: _SigningKeys | None = None


def reset_signing_keys() -> None:
    """Forget the cached signing keys (tests; a changed tenant)."""
    global _signing_keys
    _signing_keys = None


async def _fetch_signing_keys(tenant_id: str) -> dict[str, dict[str, Any]]:
    """The directory's RSA signing keys by key ID, from its OpenID configuration."""
    configuration = await _get_json(f"{expected_issuer(tenant_id)}/.well-known/openid-configuration")
    if configuration.get("issuer") != expected_issuer(tenant_id):
        raise AzureTokenError("the directory's OpenID configuration names another issuer")
    jwks_uri = configuration.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri.startswith(f"{AZURE_LOGIN_HOST}/"):
        raise AzureTokenError("the directory's OpenID configuration names no Microsoft key set")
    key_set = await _get_json(jwks_uri)
    keys: dict[str, dict[str, Any]] = {}
    for key in key_set.get("keys") or []:
        if (
            isinstance(key, dict)
            and isinstance(key.get("kid"), str)
            and key.get("kty") == "RSA"
            and key.get("use", "sig") == "sig"
        ):
            keys[key["kid"]] = key
    if not keys:
        raise AzureTokenError("the directory publishes no RSA signing key")
    return keys


async def _signing_key(tenant_id: str, kid: str) -> dict[str, Any] | None:
    """The signing key with this ID: from the cache, else from a fresh fetch when one is due.

    The set is fetched again when it is a day old, and when a token names a key it does not
    hold, as after Microsoft rotates its keys; for an unknown key at most once per interval.
    """
    global _signing_keys
    now = time.monotonic()
    cached = _signing_keys if _signing_keys is not None and _signing_keys.tenant_id == tenant_id else None
    unknown_kid = False
    if cached is not None and now - cached.fetched_at < _SIGNING_KEYS_MAX_AGE_SECONDS:
        if kid in cached.keys:
            return cached.keys[kid]
        if now - cached.unknown_kid_fetched_at < _SIGNING_KEYS_REFETCH_INTERVAL_SECONDS:
            return None
        unknown_kid = True
    try:
        keys = await _fetch_signing_keys(tenant_id)
    except (httpx.HTTPError, ValueError, AzureTokenError) as exc:
        if cached is not None:
            # Microsoft unreachable: the keys of the last fetch still check the tokens they
            # signed, and the next fetch is tried after the interval.
            if unknown_kid:
                cached.unknown_kid_fetched_at = now
            else:
                cached.fetched_at = now - _SIGNING_KEYS_MAX_AGE_SECONDS + _SIGNING_KEYS_REFETCH_INTERVAL_SECONDS
            return cached.keys.get(kid)
        reason = str(exc) if isinstance(exc, AzureTokenError) else type(exc).__name__
        raise AzureTokenError(f"the directory's signing keys could not be read ({reason})") from exc
    _signing_keys = _SigningKeys(
        tenant_id=tenant_id,
        keys=keys,
        fetched_at=now,
        unknown_kid_fetched_at=now if unknown_kid else float("-inf"),
    )
    return keys.get(kid)


def _claim_text(claims: dict[str, Any], name: str) -> str | None:
    value = claims.get(name)
    if not isinstance(value, str):
        return None
    return value.strip() or None


async def verify_azure_token(
    token: str, *, tenant_id: str, client_id: str, scope_name: str
) -> AzureIdentity:
    """Check a bearer token as a Microsoft access token for CoGA's API (module docstring)."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AzureTokenError("not a token") from exc
    if header.get("alg") != "RS256":
        raise AzureTokenError("not signed with RS256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise AzureTokenError("names no signing key")
    jwk = await _signing_key(tenant_id, kid)
    if jwk is None:
        raise AzureTokenError("signed with a key the directory does not publish")
    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(
            token,
            public_key,  # type: ignore[arg-type]
            algorithms=["RS256"],
            audience=client_id,
            issuer=expected_issuer(tenant_id),
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "oid", "tid"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AzureTokenError("expired") from exc
    except jwt.PyJWTError as exc:
        raise AzureTokenError(f"refused by the token check ({type(exc).__name__})") from exc
    if claims.get("ver") != "2.0":
        raise AzureTokenError(
            "not a version 2.0 token: the app registration must ask for version 2 access tokens"
        )
    if str(claims.get("tid", "")).lower() != tenant_id:
        raise AzureTokenError("issued for another directory")
    if str(claims.get("azp", "")).lower() != client_id:
        raise AzureTokenError("asked for by another application")
    scopes = claims.get("scp")
    if not isinstance(scopes, str) or scope_name not in scopes.split():
        raise AzureTokenError("does not carry CoGA's API scope (an application's token, or another scope)")
    try:
        object_id = str(UUID(str(claims["oid"])))
    except ValueError as exc:
        raise AzureTokenError("names no object ID") from exc
    return AzureIdentity(
        object_id=object_id,
        preferred_username=_claim_text(claims, "preferred_username"),
        email=_claim_text(claims, "email"),
        given_name=_claim_text(claims, "given_name"),
        family_name=_claim_text(claims, "family_name"),
        name=_claim_text(claims, "name"),
    )
