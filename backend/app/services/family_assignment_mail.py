"""E-mail to the user a family is assigned to.

A save of a family's *Assigned to* that names someone the family did not already name there
tells that user by e-mail, through the mail server ``SMTP_HOST`` names (``outgoing_mail.py``),
after the save is committed. No mail goes out when the family keeps the user it held, when the
field is cleared, or when someone assigns the family to themselves; nor without ``SMTP_HOST``.

The mail names the family by its ID (the owner's choice: the lab's pseudonymous ID, no name or
other patient data), who assigned it and when, and links the family's page when CoGA knows its
own address. Its subject names no family.

A mail that cannot be sent leaves the save as it is. The log names neither the family, nor the
address, nor the mail server's error text, which can quote them: only the error's type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import quote

from .outgoing_mail import MailNotSent, app_url, mail_enabled, mail_field, new_message, plain_address, send_mail

logger = logging.getLogger(__name__)

SUBJECT = "CoGA: a family is assigned to you"


@dataclass(frozen=True)
class FamilyAssignment:
    """A family newly assigned to a user, and who assigned it."""

    family_id: str
    assignee_email: str
    assignee_name: str
    assigned_by: str
    assigned_at: datetime


def family_page_url(family_id: str) -> str | None:
    """The family's page (``/families/:familyId``, frontend/src/index.tsx), its ID encoded as
    ``routePath`` encodes it."""
    return app_url(f"/families/{quote(family_id, safe='')}")


def family_assignment_message(assignment: FamilyAssignment, recipient: str) -> EmailMessage:
    """The mail that tells a user of the family assigned to them."""
    assigned_at = assignment.assigned_at
    if assigned_at.tzinfo is None:
        assigned_at = assigned_at.replace(tzinfo=timezone.utc)
    family = mail_field(assignment.family_id)
    url = family_page_url(assignment.family_id)
    where = (
        ["Open the family:", url]
        if url
        else [f"Open it in CoGA: Families, then {family}."]
    )
    return new_message(
        SUBJECT,
        [recipient],
        [
            f"Dear {mail_field(assignment.assignee_name)},",
            "",
            "A family is assigned to you in CoGA.",
            "",
            f"Family: {family}",
            f"Assigned by: {mail_field(assignment.assigned_by)}",
            f"Assigned on: {assigned_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC",
            "",
            *where,
            "",
            "You receive this mail because the family names you under Assigned to.",
        ],
    )


def notify_family_assignment(assignment: FamilyAssignment) -> bool:
    """Tell a user of the family assigned to them. Best-effort: returns whether the mail went
    out, and never raises."""
    if not mail_enabled():
        logger.info("SMTP_HOST not set: no mail about the family assignment")
        return False
    recipient = plain_address(assignment.assignee_email)
    if recipient is None:
        logger.warning("no mail about the family assignment: the assignee has no plain e-mail address")
        return False
    try:
        send_mail(family_assignment_message(assignment, recipient))
    except MailNotSent as exc:
        logger.error("the mail about the family assignment was not sent: %s", exc)
        return False
    logger.info("the mail about the family assignment went to its assignee")
    return True
