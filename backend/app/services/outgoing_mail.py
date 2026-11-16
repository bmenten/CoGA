"""The mail CoGA sends, through the mail server ``SMTP_HOST`` names.

Three mails go out: to every active administrator about an account that waits for approval
(``account_notifications.py``), to the user whose account an administrator activates
(``account_activation_mail.py``), and to the user a family is assigned to
(``family_assignment_mail.py``). Without ``SMTP_HOST`` no mail goes out. ``SMTP_FROM`` is the
sender, ``ADMIN_EMAIL`` when it is not set. With ``SMTP_STARTTLS`` the connection is encrypted
before anything is sent, and with ``SMTP_USERNAME`` CoGA signs in to the server, which needs
STARTTLS outside development (``core/config.py``). At Ghent University the server is the
central relay for UGent's servers, ``smtprelay.ugent.be`` (``docs/deployment-vm.md``).

A mail that cannot be sent is told as the error's type alone: the mail server's text can quote
the addresses.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

from ..core.coga_logging import describe_error
from ..core.config import settings

# A bounded wait: the CAS sign-in sends its mail before it answers.
SMTP_TIMEOUT_SECONDS = 10

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_MAX_FIELD = 200
# One plain address: no space, no comma, no angle bracket, one @ with text on both sides.
_PLAIN_ADDRESS = re.compile(r"[^\s@,<>\"]+@[^\s@,<>\"]+")


class MailNotSent(Exception):
    """Why a mail did not go out, in a sentence an administrator can read."""


def mail_enabled() -> bool:
    """Whether a mail server is set."""
    return bool(settings.smtp_host.strip())


def mail_field(value: str | None) -> str:
    """A value as one line of a mail: no control character, not too long."""
    cleaned = _CONTROL.sub(" ", value or "").strip()
    if len(cleaned) > _MAX_FIELD:
        cleaned = cleaned[: _MAX_FIELD - 1] + "…"
    return cleaned or "—"


def plain_address(value: str | None) -> str | None:
    """The address, when it is one plain e-mail address a ``To`` header can carry; else None."""
    address = (value or "").strip()
    return address if _PLAIN_ADDRESS.fullmatch(address) else None


def sender() -> str:
    return settings.smtp_from.strip() or settings.admin_email.strip()


def app_url(path: str) -> str | None:
    """The address of a CoGA page, when CoGA knows its own: the origin of the CAS service URL,
    which is CoGA's own sign-in page. ``path`` starts with ``/`` and is already encoded."""
    service = (settings.cas_service_url or "").strip()
    if not service:
        return None
    parts = urlsplit(service)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}{path}"


def new_message(subject: str, recipients: list[str], body: list[str]) -> EmailMessage:
    """A plain-text mail from CoGA's sender to ``recipients``."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender()
    message["To"] = ", ".join(recipients)
    message.set_content("\n".join(body))
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
