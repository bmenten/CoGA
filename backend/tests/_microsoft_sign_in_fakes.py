"""A stand-in Microsoft directory for the sign-in tests (SEC-4).

It makes an RSA key pair when it is created, publishes the public half as the directory's
signing key set, and signs access tokens with the private half, as Microsoft Entra ID does for
CoGA's API. ``serve`` puts it where ``core/azure.py`` reads the directory's OpenID
configuration and key set, so the real token check runs against it. Every key, ID and token is
made at run time: the repository holds none, and none belongs to a real directory.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.app.core import azure


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "kid": kid,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }


class FakeDirectory:
    """One directory (tenant) with CoGA's app registration in it."""

    def __init__(self, scope_name: str = "access_as_user") -> None:
        self.tenant_id = str(uuid4())
        self.client_id = str(uuid4())
        self.scope_name = scope_name
        self.signing = new_rsa_private_key()
        self.kid = f"kid-{uuid4().hex[:8]}"
        self.published: dict[str, rsa.RSAPrivateKey] = {self.kid: self.signing}
        # What the token check fetched, in order: the configuration and key-set URLs.
        self.fetched: list[str] = []
        self.unreachable = False

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_uri(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    def openid_configuration(self) -> dict[str, Any]:
        return {"issuer": self.issuer, "jwks_uri": self.jwks_uri}

    def key_set(self) -> dict[str, Any]:
        return {"keys": [public_jwk(private, kid) for kid, private in self.published.items()]}

    def rotate(self) -> None:
        """Publish a new signing key beside the old one and sign with it from now on."""
        self.signing = new_rsa_private_key()
        self.kid = f"kid-{uuid4().hex[:8]}"
        self.published[self.kid] = self.signing

    async def get_json(self, url: str) -> dict[str, Any]:
        self.fetched.append(url)
        if self.unreachable:
            raise azure.httpx.ConnectError("directory unreachable")
        if url == f"{self.issuer}/.well-known/openid-configuration":
            return self.openid_configuration()
        if url == self.jwks_uri:
            return self.key_set()
        raise azure.httpx.HTTPStatusError(
            "not found", request=azure.httpx.Request("GET", url), response=azure.httpx.Response(404)
        )

    def serve(self, monkeypatch) -> "FakeDirectory":
        """Make core/azure.py read this directory, with an empty key cache."""
        azure.reset_signing_keys()
        monkeypatch.setattr(azure, "_get_json", self.get_json)
        return self

    def configure(self, monkeypatch, settings) -> "FakeDirectory":
        """Point CoGA's settings at this directory."""
        monkeypatch.setattr(settings, "azure_tenant_id", self.tenant_id)
        monkeypatch.setattr(settings, "azure_client_id", self.client_id)
        monkeypatch.setattr(settings, "azure_api_scope", None)
        return self

    def claims(self, object_id: str | None = None, **overrides: Any) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "tid": self.tenant_id,
            "azp": self.client_id,
            "ver": "2.0",
            "scp": self.scope_name,
            "oid": object_id or str(uuid4()),
            "sub": uuid4().hex,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(hours=1),
            "preferred_username": "user@example.org",
            "email": "user@example.org",
            "given_name": "Test",
            "family_name": "User",
            "name": "Test User",
        }
        claims.update(overrides)
        return {name: value for name, value in claims.items() if value is not None}

    def access_token(
        self,
        object_id: str | None = None,
        *,
        signing: rsa.RSAPrivateKey | None = None,
        kid: str | None = None,
        headers: dict[str, Any] | None = None,
        **claims: Any,
    ) -> str:
        """An RS256 access token as the directory issues it for CoGA's API, with these claims
        changed (a claim given as None is left out)."""
        return jwt.encode(
            self.claims(object_id, **claims),
            signing or self.signing,
            algorithm="RS256",
            headers={"kid": kid or self.kid, **(headers or {})},
        )


def unsigned_token(claims: dict[str, Any]) -> str:
    """A token with ``alg: none`` and no signature."""
    def part(value: dict[str, Any]) -> str:
        raw = json.dumps(value, default=str).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."
