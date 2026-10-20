"""The check of a Microsoft access token for CoGA's API (SEC-4, REQ-SEC-003).

``core/azure.py`` decides whether a bearer token is an access token Microsoft Entra ID (Azure
AD) issued for CoGA: signed (RS256) with one of the directory's published keys, a version 2.0
token of CoGA's own directory, for CoGA's API, asked for by CoGA itself, a user's token that
carries CoGA's scope, within its lifetime, and naming the user's object ID. Each test changes
one of these and expects the refusal; the directory is a stand-in made at run time
(``_microsoft_sign_in_fakes.FakeDirectory``), so the real check runs against keys no real
directory has.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from backend.app.core import azure
from backend.app.core.azure import AzureTokenError, verify_azure_token
from backend.tests._microsoft_sign_in_fakes import FakeDirectory, unsigned_token


@pytest.fixture()
def directory(monkeypatch: pytest.MonkeyPatch) -> FakeDirectory:
    return FakeDirectory().serve(monkeypatch)


def _verify(directory: FakeDirectory, token: str, *, scope_name: str = "access_as_user"):
    return asyncio.run(
        verify_azure_token(
            token, tenant_id=directory.tenant_id, client_id=directory.client_id, scope_name=scope_name
        )
    )


def _refused(directory: FakeDirectory, token: str, reason: str) -> None:
    with pytest.raises(AzureTokenError) as raised:
        _verify(directory, token)
    assert reason in str(raised.value)


def test_a_token_of_the_directory_for_coga_signs_in_the_object_id_it_names(directory: FakeDirectory) -> None:
    object_id = str(uuid4()).upper()
    identity = _verify(
        directory,
        directory.access_token(
            object_id,
            preferred_username="a.user@example.org",
            email="A.User@example.org",
            given_name="A",
            family_name="User",
            name="A User",
        ),
    )

    # The object ID in its canonical form: the account is found by it.
    assert identity.object_id == object_id.lower()
    assert identity.preferred_username == "a.user@example.org"
    assert identity.email == "A.User@example.org"
    assert (identity.given_name, identity.family_name, identity.name) == ("A", "User", "A User")
    # The key set came from the directory's own OpenID configuration.
    assert directory.fetched == [f"{directory.issuer}/.well-known/openid-configuration", directory.jwks_uri]


def test_the_scope_may_be_one_of_several_the_token_carries(directory: FakeDirectory) -> None:
    identity = _verify(directory, directory.access_token(scp="openid profile access_as_user"))
    assert identity.object_id


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        pytest.param({"aud": str(uuid4())}, "refused by the token check (InvalidAudienceError)", id="another-api"),
        pytest.param(
            {"iss": f"https://login.microsoftonline.com/{uuid4()}/v2.0"},
            "refused by the token check (InvalidIssuerError)",
            id="another-issuer",
        ),
        pytest.param({"tid": str(uuid4())}, "issued for another directory", id="another-directory"),
        pytest.param({"azp": str(uuid4())}, "asked for by another application", id="asked-for-by-another-app"),
        pytest.param({"ver": "1.0"}, "not a version 2.0 token", id="a-version-1-token"),
        pytest.param({"scp": "User.Read"}, "does not carry CoGA's API scope", id="another-scope"),
        # An application's own token (client credentials) has roles, no scope.
        pytest.param({"scp": None, "roles": ["Task.Read"]}, "does not carry CoGA's API scope", id="an-app-token"),
        pytest.param({"oid": None}, "refused by the token check (MissingRequiredClaimError)", id="no-object-id"),
        pytest.param({"oid": "not-an-object-id"}, "names no object ID", id="an-object-id-that-is-no-uuid"),
        pytest.param({"tid": None}, "refused by the token check (MissingRequiredClaimError)", id="no-directory"),
        pytest.param(
            {"exp": datetime.now(timezone.utc) - timedelta(minutes=2)}, "expired", id="expired-past-the-leeway"
        ),
        pytest.param(
            {"nbf": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "refused by the token check (ImmatureSignatureError)",
            id="not-valid-yet",
        ),
    ],
)
def test_a_token_that_is_not_for_coga_is_refused(directory: FakeDirectory, claims: dict, reason: str) -> None:
    _refused(directory, directory.access_token(**claims), reason)


def test_a_minute_of_clock_drift_is_allowed(directory: FakeDirectory) -> None:
    just_expired = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert _verify(directory, directory.access_token(exp=just_expired)).object_id


def test_a_token_signed_with_a_key_the_directory_does_not_publish_is_refused(directory: FakeDirectory) -> None:
    other = FakeDirectory()
    # Under the directory's own key ID, and under one it does not publish.
    _refused(
        directory,
        directory.access_token(signing=other.signing),
        "refused by the token check (InvalidSignatureError)",
    )
    _refused(
        directory,
        directory.access_token(signing=other.signing, kid=other.kid),
        "signed with a key the directory does not publish",
    )


def _hs256_with_public_key(directory: FakeDirectory) -> str:
    """HS256 signed with the directory's public key as the shared secret: the key confusion
    attack (PyJWT will not make such a token, so it is put together by hand)."""
    public_pem = directory.signing.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    def encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    claims = {key: value for key, value in directory.claims().items() if key not in {"iat", "nbf", "exp"}}
    claims["exp"] = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    signing_input = (
        f"{encode(json.dumps({'alg': 'HS256', 'typ': 'JWT', 'kid': directory.kid}).encode())}."
        f"{encode(json.dumps(claims).encode())}"
    )
    signature = hmac.new(public_pem, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{encode(signature)}"


def test_a_token_that_names_no_key_or_another_algorithm_is_refused(directory: FakeDirectory) -> None:
    _refused(directory, unsigned_token(directory.claims()), "not signed with RS256")
    _refused(directory, "not-a-token", "not a token")
    _refused(directory, _hs256_with_public_key(directory), "not signed with RS256")
    no_kid = jwt.encode(directory.claims(), directory.signing, algorithm="RS256")
    _refused(directory, no_kid, "names no signing key")


def test_a_rotated_key_is_fetched_once_and_a_made_up_one_is_not_fetched_again(
    directory: FakeDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _verify(directory, directory.access_token()).object_id
    fetches = len(directory.fetched)

    # Microsoft rotates its key: the first token signed with the new one fetches the set again.
    directory.rotate()
    assert _verify(directory, directory.access_token()).object_id
    assert len(directory.fetched) == fetches + 2

    # A key ID nobody publishes is not fetched for again within the interval.
    stranger = FakeDirectory()
    for _ in range(3):
        _refused(
            directory,
            directory.access_token(signing=stranger.signing, kid=stranger.kid),
            "signed with a key the directory does not publish",
        )
    assert len(directory.fetched) == fetches + 2

    # After the interval it is.
    keys = azure._signing_keys
    assert keys is not None
    monkeypatch.setattr(
        keys,
        "unknown_kid_fetched_at",
        keys.unknown_kid_fetched_at - azure._SIGNING_KEYS_REFETCH_INTERVAL_SECONDS - 1,
    )
    _refused(
        directory,
        directory.access_token(signing=stranger.signing, kid=stranger.kid),
        "signed with a key the directory does not publish",
    )
    assert len(directory.fetched) == fetches + 4


def test_the_keys_are_fetched_again_after_a_day(directory: FakeDirectory, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _verify(directory, directory.access_token()).object_id
    keys = azure._signing_keys
    assert keys is not None
    monkeypatch.setattr(keys, "fetched_at", keys.fetched_at - azure._SIGNING_KEYS_MAX_AGE_SECONDS - 1)
    fetches = len(directory.fetched)

    assert _verify(directory, directory.access_token()).object_id
    assert len(directory.fetched) == fetches + 2


def test_the_last_keys_serve_while_microsoft_is_unreachable(
    directory: FakeDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _verify(directory, directory.access_token()).object_id
    keys = azure._signing_keys
    assert keys is not None
    monkeypatch.setattr(keys, "fetched_at", keys.fetched_at - azure._SIGNING_KEYS_MAX_AGE_SECONDS - 1)
    directory.unreachable = True

    assert _verify(directory, directory.access_token()).object_id


def test_without_keys_an_unreachable_directory_refuses_the_token(directory: FakeDirectory) -> None:
    directory.unreachable = True
    _refused(directory, directory.access_token(), "the directory's signing keys could not be read (ConnectError)")


def test_a_configuration_that_names_another_issuer_or_key_set_is_not_used(
    directory: FakeDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        directory,
        "openid_configuration",
        lambda: {"issuer": "https://login.microsoftonline.com/common/v2.0", "jwks_uri": directory.jwks_uri},
    )
    _refused(directory, directory.access_token(), "names another issuer")

    azure.reset_signing_keys()
    monkeypatch.setattr(
        directory,
        "openid_configuration",
        lambda: {"issuer": directory.issuer, "jwks_uri": "https://keys.example.org/keys"},
    )
    _refused(directory, directory.access_token(), "names no Microsoft key set")
    assert "https://keys.example.org/keys" not in directory.fetched


def test_the_authority_and_issuer_are_the_directorys() -> None:
    tenant = str(uuid4())
    assert azure.authority(tenant) == f"https://login.microsoftonline.com/{tenant}"
    assert azure.expected_issuer(tenant) == f"https://login.microsoftonline.com/{tenant}/v2.0"
