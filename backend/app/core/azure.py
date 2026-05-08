import json
from functools import lru_cache

import httpx
from jose import jwt


@lru_cache(maxsize=1)
def _get_openid_config(tenant_id: str) -> dict:
    url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    resp = httpx.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


@lru_cache(maxsize=1)
def _get_jwks(tenant_id: str) -> dict:
    config = _get_openid_config(tenant_id)
    jwks_uri = config["jwks_uri"]
    resp = httpx.get(jwks_uri, timeout=5)
    resp.raise_for_status()
    return resp.json()


def verify_azure_token(token: str, tenant_id: str, client_id: str) -> dict:
    """Validate an Azure AD JWT and return the decoded claims."""
    jwks = _get_jwks(tenant_id)
    header = jwt.get_unverified_header(token)
    for key in jwks["keys"]:
        if key["kid"] == header.get("kid"):
            rsa_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
            issuer = _get_openid_config(tenant_id)["issuer"]
            return jwt.decode(
                token,
                rsa_key,
                algorithms=["RS256"],
                audience=client_id,
                issuer=issuer,
            )
    raise ValueError("Unable to find appropriate key")
