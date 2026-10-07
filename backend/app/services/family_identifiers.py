"""What a family or sample ID may hold, checked before CoGA stores one.

An ID names its family or sample in the pedigree, the clinical report, the audit trail, the
import job's record, the logs and the paths of the files kept for it. It is printable text
without spaces. The whitespace around an ID is stripped where it is read (a PED field, a
manifest, a request, a folder name); what remains may not hold

- a control character: C0 (``\\x00``-``\\x1f``: a line break, a tab, an escape) or DEL
  (``\\x7f``), the characters ``scrub_log`` blanks in a log line. In an ID such a character
  could start a new line in a log or a report, hide in what a screen shows, and a NUL cannot
  be stored at all;
- whitespace: a PED row is split on whitespace, and the stored pedigree and the rows
  ``family.add_members`` adds are PED rows, so an ID with a space could not be read back.

The package import reports an ID that breaks this rule as a validation error
(``family_id_invalid``, ``sample_id_invalid``); the Family Builder and the PED upload refuse
it with a 400. Either way nothing is written.
"""

from __future__ import annotations

import re

# C0 control characters and DEL, as in scrub_log.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

IDENTIFIER_RULE = "Family and sample IDs are printable text without spaces."


def visible(value: str) -> str:
    """``value`` as a message shows it: each character a reader cannot see (a control
    character, a non-breaking space) written as its escape, such as ``\\n`` or ``\\x1b``."""
    return "".join(
        character if character.isprintable() else character.encode("unicode_escape").decode("ascii")
        for character in value
    )


def identifier_problem(value: str) -> str | None:
    """What keeps ``value`` from being stored as a family or sample ID ("contains a control
    character (\\x1b)", "contains a space"), or None when nothing does. The value is taken
    as given: strip the whitespace around it first, where it is read."""
    if not value:
        return "is empty"
    control = CONTROL_CHARACTERS.search(value)
    if control is not None:
        return f"contains a control character ({visible(control.group())})"
    space = next((character for character in value if character.isspace()), None)
    if space == " ":
        return "contains a space"
    if space is not None:
        return f"contains whitespace ({visible(space)})"
    return None
