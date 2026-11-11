"""Sign-in through the organisation's CAS server, such as login.ugent.be.

The browser goes to ``CAS_SERVER_URL/login?service=CAS_SERVICE_URL``; the CAS server signs the
user in (at Ghent University, with Microsoft Entra ID behind it) and sends the browser back to
the service, the app's /login page, with a one-time service ticket. The page hands the ticket
to the API, and this module checks it with the CAS server (``/serviceValidate``, server to
server over HTTPS) for that same service. The CAS server answers once per ticket, and only for
the service the ticket was issued to; its answer names the user and carries the attributes it
releases to CoGA.

CoGA accepts the sign-in only when the answer carries the user's object ID in the
organisation's Entra ID (``CAS_OBJECT_ID_ATTRIBUTE``, a UUID): the account is bound to it
(``users.azure_object_id``), never to an e-mail address, so a later Microsoft sign-in finds the
same account. The e-mail address and the name come from the other attributes. An answer
without one of them is logged with the names of the attributes it does carry, never their
values, so the administrator can ask the CAS server to release it or set ``CAS_*_ATTRIBUTE`` to
the name the server uses.

The answer is XML. It is read only when it is a CAS answer of reasonable size without a
document type declaration, so no entity in it is expanded. Anything else raises ``CasError``,
whose message says why and never quotes the ticket or a value from the answer.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from xml.etree import ElementTree

import httpx

from .azure import AzureIdentity
from .config import settings
from .sql import canonical_uuid

_CAS_NAMESPACE = "{http://www.yale.edu/tp/cas}"
_FETCH_TIMEOUT_SECONDS = 10.0
# A CAS answer names one user and a handful of attributes: a few kilobytes.
_MAX_ANSWER_BYTES = 64 * 1024
# A service ticket as CAS servers write it: "ST-" and URL-safe characters. Anything else is
# refused before the CAS server is asked.
_SERVICE_TICKET = re.compile(r"ST-[A-Za-z0-9._-]{1,250}")
# An attribute name a log line may quote. A name of any other form is counted, not quoted.
_ATTRIBUTE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9._-]{0,63}")
_MAX_QUOTED_ATTRIBUTES = 30

logger = logging.getLogger(__name__)


class CasError(Exception):
    """A ticket CoGA does not accept; the message says why and quotes neither the ticket nor
    a value from the CAS server's answer (at most the names of its attributes)."""


def login_url() -> str:
    """Where the browser signs in: the CAS server's login page, for CoGA's service."""
    return str(httpx.URL(f"{settings.cas_server_url}/login", params={"service": settings.cas_service_url}))


def logout_url() -> str:
    """The CAS server's sign-out page, which ends the browser's session there."""
    return f"{settings.cas_server_url}/logout"


def _first_value(attributes: dict[str, list[str]], name: str) -> str | None:
    for value in attributes.get(name, []):
        if value.strip():
            return value.strip()
    return None


def _carried(attributes: dict[str, list[str]]) -> str:
    """The names of the attributes a CAS answer carries, for a log line; never their values."""
    names = sorted(name for name in attributes if _ATTRIBUTE_NAME.fullmatch(name))
    quoted = names[:_MAX_QUOTED_ATTRIBUTES]
    others = len(attributes) - len(quoted)
    text = ", ".join(quoted) or "none"
    return f"{text} and {others} other(s)" if others else text


def _read_answer(body: bytes) -> tuple[str, dict[str, list[str]]]:
    """The user and the attributes of a successful CAS answer; CasError otherwise."""
    if len(body) > _MAX_ANSWER_BYTES:
        raise CasError("the CAS answer is too large")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise CasError("the CAS answer declares a document type")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise CasError("the CAS answer is not XML") from exc
    if root.tag != f"{_CAS_NAMESPACE}serviceResponse":
        raise CasError("the answer is not a CAS service response")
    failure = root.find(f"{_CAS_NAMESPACE}authenticationFailure")
    if failure is not None:
        code = (failure.get("code") or "unknown").strip()
        # The code CAS names (INVALID_TICKET, INVALID_SERVICE ...), never its message.
        raise CasError(f"the CAS server refused the ticket ({code[:40]})")
    success = root.find(f"{_CAS_NAMESPACE}authenticationSuccess")
    if success is None:
        raise CasError("the CAS answer reports no sign-in")
    user = (success.findtext(f"{_CAS_NAMESPACE}user") or "").strip()
    if not user:
        raise CasError("the CAS answer names no user")
    attributes: dict[str, list[str]] = {}
    holder = success.find(f"{_CAS_NAMESPACE}attributes")
    for element in list(holder) if holder is not None else []:
        if element.tag.startswith(_CAS_NAMESPACE):
            name = element.tag[len(_CAS_NAMESPACE) :]
            attributes.setdefault(name, []).append(element.text or "")
    return user, attributes


def identity_from_answer(body: bytes) -> AzureIdentity:
    """Who a successful CAS answer signs in, as Microsoft sign-in names a user."""
    user, attributes = _read_answer(body)
    object_id = canonical_uuid(_first_value(attributes, settings.cas_object_id_attribute))
    if object_id is None:
        raise CasError(
            f"the CAS answer carries no object ID ({settings.cas_object_id_attribute}); the CAS "
            f"server must release that attribute to CoGA (the answer carries: {_carried(attributes)})"
        )
    missing = [
        name
        for name in (
            settings.cas_email_attribute,
            settings.cas_given_name_attribute,
            settings.cas_family_name_attribute,
        )
        if _first_value(attributes, name) is None
    ]
    if missing:
        # Without them the account has no e-mail address (a first sign-in is refused) or shows
        # only part of the user's name.
        logger.warning(
            "cas sign-in: the CAS answer carries no %s (the answer carries: %s); the CAS server "
            "must release them to CoGA, or CAS_*_ATTRIBUTE must name the attribute it uses",
            ", ".join(missing),
            _carried(attributes),
        )
    given = _first_value(attributes, settings.cas_given_name_attribute)
    family = _first_value(attributes, settings.cas_family_name_attribute)
    return AzureIdentity(
        object_id=object_id,
        preferred_username=user,
        email=_first_value(attributes, settings.cas_email_attribute),
        given_name=given,
        family_name=family,
        name=" ".join(part for part in (given, family) if part) or None,
    )


async def _fetch_answer(ticket: str) -> bytes:
    params: dict[str, Any] = {"service": settings.cas_service_url, "ticket": ticket}
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.get(f"{settings.cas_server_url}/serviceValidate", params=params)
    except httpx.HTTPError as exc:
        raise CasError(f"the CAS server could not be reached ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise CasError(f"the CAS server answered HTTP {response.status_code}")
    return response.content


async def verify_service_ticket(ticket: str) -> AzureIdentity:
    """Who the service ticket signs in, as the CAS server confirms it for CoGA's service."""
    if not isinstance(ticket, str) or not _SERVICE_TICKET.fullmatch(ticket):
        raise CasError("the ticket is not a CAS service ticket")
    return identity_from_answer(await _fetch_answer(ticket))
