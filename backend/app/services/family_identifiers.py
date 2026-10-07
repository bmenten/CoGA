"""What a family or sample ID may hold, checked before CoGA stores one or looks one up.

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
(``family_id_invalid``, ``sample_id_invalid``); the Family Builder, the PED upload and the
member and structure edits (a member's new sample ID, its father or mother, a member the
structure edit adds) refuse it with a 400. Either way nothing is written.

A request that looks an ID up is held to the first half of the rule: a path parameter, a
query parameter's name, or a ``family_id`` or ``sample_id`` query value that holds a control
character is refused with a 400, and so is a NUL in any query value
(``request_value_problem``, called by ``dependencies.get_current_user``). No family or sample
ID may hold one, nor any other key a route looks up, and Postgres cannot compare a NUL: a
``%00`` in the URL arrives decoded, and the lookup failed with a 500.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

# C0 control characters and DEL, as in scrub_log.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

IDENTIFIER_RULE = "Family and sample IDs are printable text without spaces."

# The request values that carry a family or sample ID: their refusal states the rule.
_IDENTIFIER_PARAMETERS = frozenset({"family_id", "sample_id"})
_NUL = "\x00"


def visible(value: str) -> str:
    """``value`` as a message shows it: each character a reader cannot see (a control
    character, a non-breaking space) written as its escape, such as ``\\n`` or ``\\x1b``."""
    return "".join(
        character if character.isprintable() else character.encode("unicode_escape").decode("ascii")
        for character in value
    )


def control_character_problem(value: str) -> str | None:
    """What a control character in ``value`` makes it: "contains a control character
    (\\x00)", naming the first one; None when it holds none."""
    control = CONTROL_CHARACTERS.search(value)
    if control is None:
        return None
    return f"contains a control character ({visible(control.group())})"


def identifier_problem(value: str) -> str | None:
    """What keeps ``value`` from being stored as a family or sample ID ("contains a control
    character (\\x1b)", "contains a space"), or None when nothing does. The value is taken
    as given: strip the whitespace around it first, where it is read."""
    if not value:
        return "is empty"
    control = control_character_problem(value)
    if control is not None:
        return control
    space = next((character for character in value if character.isspace()), None)
    if space == " ":
        return "contains a space"
    if space is not None:
        return f"contains whitespace ({visible(space)})"
    return None


def request_value_problem(
    path_params: Mapping[str, Any], query_items: Iterable[tuple[str, str]]
) -> str | None:
    """Why a request is refused for a control character in its path or its query string, or
    None when nothing is.

    A path parameter names what the route looks up (a family or sample ID, an assembly, a
    tag), and so does a query parameter's name: neither may hold a control character. Neither
    may the value of a ``family_id`` or ``sample_id`` query parameter. Any other query value
    may hold a line break or a tab, as a gene list or an interval list typed one per line
    does, but not a NUL, which Postgres cannot compare. The message names the parameter and
    shows the value with its control characters escaped; a family or sample ID's message also
    states the rule."""
    for name, value in path_params.items():
        if isinstance(value, str):
            problem = control_character_problem(value)
            if problem is not None:
                return _request_value_message(name, value, "the path", problem)
    for name, value in query_items:
        problem = control_character_problem(name)
        if problem is not None:
            return f"The query string has a parameter name, '{visible(name)}', that {problem}."
        if name in _IDENTIFIER_PARAMETERS:
            problem = control_character_problem(value)
        else:
            problem = control_character_problem(_NUL) if _NUL in value else None
        if problem is not None:
            return _request_value_message(name, value, "the query string", problem)
    return None


def _request_value_message(name: str, value: str, where: str, problem: str) -> str:
    message = f"{name} '{visible(value)}' (in {where}) {problem}."
    if name in _IDENTIFIER_PARAMETERS:
        return f"{message} {IDENTIFIER_RULE}"
    return message
