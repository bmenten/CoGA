"""E-mail to the user whose account an administrator activates.

A change on the *Users* page that makes an inactive account active (the approval of an account
that waited for it, or the activation again of one that was deactivated) tells the account's
user by e-mail, through the mail server ``SMTP_HOST`` names (``outgoing_mail.py``), after the
change is committed and answered. Without ``SMTP_HOST`` no mail goes out.

The mail says the account can sign in, who activated it, how it signs in, and links CoGA's
sign-in page when CoGA knows its own address; it holds no patient data. What the account sees
is left to its role and projects, which an administrator can change after the mail.

A mail that cannot be sent leaves the activation as it is. The log names neither the address
nor the mail server's error text, which can quote it: only the error's type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email.message import EmailMessage

from .outgoing_mail import MailNotSent, app_url, mail_enabled, mail_field, new_message, plain_address, send_mail

logger = logging.getLogger(__name__)

SUBJECT = "CoGA: your account is active"
# CoGA's sign-in page (frontend/src/index.tsx).
SIGN_IN_PAGE_PATH = "/login"
SIGN_IN_PHRASES = {
    "cas": "the organisation's sign-in (CAS)",
    "microsoft": "your Microsoft account",
    "password": "this e-mail address and the password you chose when you signed up",
}


@dataclass(frozen=True)
class AccountActivation:
    """An account an administrator made active, and who did it."""

    email: str
    name: str
    sign_in: str
    activated_by: str


def sign_in_page_url() -> str | None:
    """CoGA's sign-in page, when CoGA knows its own address (``outgoing_mail.app_url``)."""
    return app_url(SIGN_IN_PAGE_PATH)


def account_activation_message(activation: AccountActivation, recipient: str) -> EmailMessage:
    """The mail that tells a user their account is active."""
    url = sign_in_page_url()
    where = (
        ["Sign in at:", url]
        if url
        else ["Sign in to CoGA at the address where you asked for the account."]
    )
    return new_message(
        SUBJECT,
        [recipient],
        [
            f"Dear {mail_field(activation.name or activation.email)},",
            "",
            "An administrator activated your CoGA account: you can sign in now.",
            "",
            f"Account: {mail_field(activation.email)}",
            f"Activated by: {mail_field(activation.activated_by)}",
            f"Sign in with: {SIGN_IN_PHRASES.get(activation.sign_in, mail_field(activation.sign_in))}",
            "",
            *where,
            "",
            "What you see in CoGA follows your role and the projects an administrator gives you",
            "access to.",
            "",
            "You receive this mail because an administrator activated your CoGA account.",
        ],
    )


def notify_account_activation(activation: AccountActivation) -> bool:
    """Tell a user their account is active. Best-effort: returns whether the mail went out,
    and never raises."""
    if not mail_enabled():
        logger.info("SMTP_HOST not set: no mail about the account activation")
        return False
    recipient = plain_address(activation.email)
    if recipient is None:
        logger.warning("no mail about the account activation: the account has no plain e-mail address")
        return False
    try:
        send_mail(account_activation_message(activation, recipient))
    except MailNotSent as exc:
        logger.error("the mail about the account activation was not sent: %s", exc)
        return False
    logger.info("the mail about the account activation went to its user")
    return True
