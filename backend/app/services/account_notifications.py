"""E-mail to the administrators about an account that waits for approval.

A sign-up (with a password, where the organisation's sign-in is not configured) and a first
sign-in through the organisation's CAS server each make an inactive account, which an
administrator activates, archives or deletes on the *Users* page. Every active administrator
is told by e-mail, through the mail server ``SMTP_HOST`` names (``outgoing_mail.py``); without
it no mail goes out, and the log says so. The mail names the account (its name, e-mail address, affiliation, how it
signs in and when it was made) and the *Users* page; it holds no patient data.

A mail that cannot be sent leaves the sign-up or the sign-in as it is: the account waits on the
*Users* page all the same. The log names neither the addresses nor the mail server's error
text, which can quote them: only the error's type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from .access_control import ADMIN_ROLES
from .outgoing_mail import MailNotSent, app_url, mail_enabled, mail_field, new_message, send_mail

logger = logging.getLogger(__name__)

# Where an administrator approves accounts (frontend/src/index.tsx).
USERS_PAGE_PATH = "/admin/access/users"
SUBJECT = "CoGA: a new account waits for approval"
TEST_SUBJECT = "CoGA: test of the account-request mail"
SIGN_IN_LABELS = {
    "cas": "the organisation's sign-in (CAS)",
    "password": "a sign-up with a password",
}

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


async def administrator_addresses(session: AsyncSession) -> list[str]:
    """The e-mail address of each active administrator: who approves accounts. An archived
    account is inactive, and is not among them."""
    result = await session.execute(_ADMIN_ADDRESSES, {"admin_roles": sorted(ADMIN_ROLES)})
    return [str(row[0]) for row in result.all()]


def users_page_url() -> str | None:
    """The *Users* page's address, when CoGA knows its own (``outgoing_mail.app_url``)."""
    return app_url(USERS_PAGE_PATH)


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
    return new_message(
        SUBJECT,
        recipients,
        [
            "A new CoGA account waits for an administrator's approval.",
            "",
            f"Name: {mail_field(name)}",
            f"E-mail address: {mail_field(account.email)}",
            f"Affiliation: {mail_field(account.affiliation)}",
            f"Made by: {SIGN_IN_LABELS.get(account.sign_in, mail_field(account.sign_in))}",
            f"Made on: {created.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC",
            "",
            "It signs in to nothing until it is activated, and is a viewer's with no project.",
            _where_to_approve(),
            "",
            "You receive this mail as an active CoGA administrator.",
        ],
    )


def mail_check_message(recipients: list[str]) -> EmailMessage:
    """A mail that shows the account-request mail reaches the administrators."""
    return new_message(
        TEST_SUBJECT,
        recipients,
        [
            "This is a test, sent from CoGA's Users page.",
            "",
            "When an account waits for approval (a sign-up, or a first sign-in through the",
            "organisation), each active CoGA administrator receives a mail like it at this",
            "address.",
            "",
            "You receive this mail as an active CoGA administrator.",
        ],
    )


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
