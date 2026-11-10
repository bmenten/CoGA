"""E-mail to the administrators about an account that waits for approval.

A sign-up (with a password, where the organisation's sign-in is not configured) and a first
sign-in through the organisation's CAS server each make an inactive account, which an
administrator activates, archives or deletes on the *Users* page. Every active administrator
is told by e-mail, through the mail server ``SMTP_HOST`` names; without it no mail goes out,
and the log says so. The mail names the account (its name, e-mail address, affiliation, how it
signs in and when it was made) and the *Users* page; it holds no patient data.

A mail that cannot be sent leaves the sign-up or the sign-in as it is: the account waits on the
*Users* page all the same. The log names neither the addresses nor the mail server's error
text, which can quote them: only the error's type.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error
from ..core.config import settings
from .access_control import ADMIN_ROLES

logger = logging.getLogger(__name__)

# Where an administrator approves accounts (frontend/src/index.tsx).
USERS_PAGE_PATH = "/admin/access/users"
# A bounded wait: the CAS sign-in sends its mail before it answers.
SMTP_TIMEOUT_SECONDS = 10
SUBJECT = "CoGA: a new account waits for approval"
TEST_SUBJECT = "CoGA: test of the account-request mail"
SIGN_IN_LABELS = {
    "cas": "the organisation's sign-in (CAS)",
    "password": "a sign-up with a password",
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_MAX_FIELD = 200

_ADMIN_ADDRESSES = text(
    """
    SELECT DISTINCT email
    FROM users
    WHERE is_active AND role IN :admin_roles
    ORDER BY email
    """
).bindparams(bindparam("admin_roles", expanding=True, type_=String()))


@dataclass(frozen=True)
class AccountRequest:
    """The account a mail is about."""

    email: str
    first_name: str
    last_name: str
    affiliation: str
    sign_in: str
    created_at: datetime


class MailNotSent(Exception):
    """Why a mail did not go out, in a sentence an administrator can read."""


def mail_enabled() -> bool:
    """Whether a mail server is set."""
    return bool(settings.smtp_host.strip())


async def administrator_addresses(session: AsyncSession) -> list[str]:
    """The e-mail address of each active administrator: who approves accounts. An archived
    account is inactive, and is not among them."""
    result = await session.execute(_ADMIN_ADDRESSES, {"admin_roles": sorted(ADMIN_ROLES)})
    return [str(row[0]) for row in result.all()]


def users_page_url() -> str | None:
    """The *Users* page's address, when CoGA knows its own: the origin of the CAS service URL,
    which is CoGA's own sign-in page."""
    service = (settings.cas_service_url or "").strip()
    if not service:
        return None
    parts = urlsplit(service)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}{USERS_PAGE_PATH}"


def _field(value: str | None) -> str:
    """A value of the account as one line of the mail: no control character, not too long."""
    cleaned = _CONTROL.sub(" ", value or "").strip()
    if len(cleaned) > _MAX_FIELD:
        cleaned = cleaned[: _MAX_FIELD - 1] + "…"
    return cleaned or "—"


def _sender() -> str:
    return settings.smtp_from.strip() or settings.admin_email.strip()


def _where_to_approve() -> str:
    url = users_page_url()
    if url:
        return f"Activate, archive or delete it on the Users page:\n{url}"
    return "Activate, archive or delete it in CoGA, under Administration > Users."


def account_request_message(account: AccountRequest, recipients: list[str]) -> EmailMessage:
    """The mail that tells the administrators of an account waiting for approval."""
    name = f"{account.first_name or ''} {account.last_name or ''}"
    created = account.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = _sender()
    message["To"] = ", ".join(recipients)
    message.set_content(
        "\n".join(
            [
                "A new CoGA account waits for an administrator's approval.",
                "",
                f"Name: {_field(name)}",
                f"E-mail address: {_field(account.email)}",
                f"Affiliation: {_field(account.affiliation)}",
                f"Made by: {SIGN_IN_LABELS.get(account.sign_in, _field(account.sign_in))}",
                f"Made on: {created.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC",
                "",
                "It signs in to nothing until it is activated, and is a viewer's with no project.",
                _where_to_approve(),
                "",
                "You receive this mail as an active CoGA administrator.",
            ]
        )
    )
    return message


def mail_check_message(recipients: list[str]) -> EmailMessage:
    """A mail that shows the account-request mail reaches the administrators."""
    message = EmailMessage()
    message["Subject"] = TEST_SUBJECT
    message["From"] = _sender()
    message["To"] = ", ".join(recipients)
    message.set_content(
        "\n".join(
            [
                "This is a test, sent from CoGA's Users page.",
                "",
                "When an account waits for approval (a sign-up, or a first sign-in through the",
                "organisation), each active CoGA administrator receives a mail like it at this",
                "address.",
                "",
                "You receive this mail as an active CoGA administrator.",
            ]
        )
    )
    return message


def send_mail(message: EmailMessage) -> None:
    """Hand a mail to the mail server. Blocks for up to the timeout: run it in a thread.

    Raises ``MailNotSent`` with a sentence that names the error's type only.
    """
    if not mail_enabled():
        raise MailNotSent("No mail server is set: set SMTP_HOST.")
    try:
        with smtplib.SMTP(
            settings.smtp_host.strip(), settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
        ) as server:
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_username.strip():
                server.login(settings.smtp_username.strip(), settings.smtp_password)
            server.send_message(message)
    except Exception as exc:  # every failure is told as its type alone
        raise MailNotSent(
            f"The mail server could not take the mail ({describe_error(exc)})."
        ) from exc


def notify_account_request(account: AccountRequest, recipients: list[str]) -> bool:
    """Tell the administrators of an account waiting for approval. Best-effort: returns
    whether the mail went out, and never raises."""
    if not mail_enabled():
        logger.info("SMTP_HOST not set: no mail about the account waiting for approval")
        return False
    if not recipients:
        logger.warning("no active administrator to tell about the account waiting for approval")
        return False
    try:
        send_mail(account_request_message(account, recipients))
    except MailNotSent as exc:
        logger.error("the mail about the account waiting for approval was not sent: %s", exc)
        return False
    logger.info(
        "the mail about the account waiting for approval went to %d administrator(s)",
        len(recipients),
    )
    return True
