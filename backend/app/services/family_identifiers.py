"""What a family or sample ID may hold, checked before CoGA stores one or looks one up.

An ID names its family or sample in the pedigree, the clinical report, the audit trail, the
import job's record, the logs, the paths of the files kept for it and the app's links. It is
printable text without spaces, and it is no path: one folder's name. The whitespace around an
ID is stripped where it is read (a PED field, a manifest, a request, a folder name); what
remains may not hold

- a control character: C0 (``\\x00``-``\\x1f``: a line break, a tab, an escape), DEL
  (``\\x7f``) or C1 (``\\x80``-``\\x9f``). In an ID such a character could start a new line in
  a log or a report, hide in what a screen shows, and a NUL cannot be stored at all;
- whitespace: a PED row is split on whitespace, and the stored pedigree and the rows
  ``family.add_members`` adds are PED rows, so an ID with a space could not be read back;
- any other character a reader cannot see (SEC-15): a text-direction character (Unicode's
  bidirectional formatting characters and the Arabic letter mark), with which
  ``FAM\\u202e100`` shows as ``FAM001``; a zero-width character (``\\u200b``-``\\u200d``,
  ``\\u2060``, and ``\\ufeff``, the byte-order mark); and any other character Python does not
  count as printable (a soft hyphen, a private-use or unassigned code point, or a lone
  surrogate, which is how Python reads a file name that is not UTF-8). Two IDs that look the
  same would name two families or samples;
- a path (SEC-15): a ``/`` or a ``\\`` anywhere, or an ID that is ``.`` or ``..``. A folder
  named after an ID (the files an import keeps, a family's data folders) and a URL's path
  segment read them as separators, the folder itself or its parent. ``..`` inside a longer
  ID (``F..1``) is part of one name and is kept.

The package import reports an ID that breaks this rule as a validation error
(``family_id_invalid``, ``sample_id_invalid``); the Family Builder, the PED upload and the
member and structure edits (a member's new sample ID, its father or mother, a member the
structure edit adds) refuse it with a 400. Either way nothing is written. The message shows
the ID with each character a reader cannot see written as its escape (``visible``), and
states the rule. IDs stored before a part of the rule came in are not changed: the data are
synthetic. The lookups, the folders kept for an ID (``core/family_folders.py``) and the URLs
built from one (``core/url_path.py``) still take an ID as data, whatever it holds.

On a case-insensitive file system (a macOS bind mount in development) two family IDs that
differ only in case share every folder named after an ID. The rule leaves case alone:
production storage is case-sensitive.

A request that looks an ID up is held to the first part of the rule: a path parameter, a
query parameter's name, or a ``family_id`` or ``sample_id`` query value that holds a C0
control character or DEL is refused with a 400, and so is a NUL in any query value
(``request_value_problem``, called by ``dependencies.get_current_user``). No family or sample
ID may hold one, nor any other key a route looks up, and Postgres cannot compare a NUL: a
``%00`` in the URL arrives decoded, and the lookup failed with a 500.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

# C0 control characters and DEL, as in scrub_log: what a request that looks an ID up may not
# hold.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
# What an ID may not hold: these and C1.
_IDENTIFIER_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# The characters that turn the text around them on screen: Unicode's bidirectional
# formatting characters (LRM, RLM, the embeddings, overrides and isolates) and the Arabic
# letter mark.
TEXT_DIRECTION_CHARACTERS = frozenset(
    "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u061c"
)
# The characters that take no width: the zero-width space, non-joiner and joiner, the word
# joiner and the zero-width no-break space (the byte-order mark).
ZERO_WIDTH_CHARACTERS = frozenset("\u200b\u200c\u200d\u2060\ufeff")
# A path's separators, here and on Windows, and a folder's names for itself and its parent.
_PATH_SEPARATORS = {"/": "a slash", "\\": "a backslash"}
_DOT_NAMES = frozenset({".", ".."})

IDENTIFIER_RULE = (
    "Family and sample IDs are printable text without spaces, '/' or '\\', and are not '.' or '..'."
)

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
    character (\\x1b)", "contains a space", "contains a zero-width character (\\u200b)",
    "contains a slash (/)", "is '..'"), or None when nothing does. The value is taken as
    given: strip the whitespace around it first, where it is read."""
    if not value:
        return "is empty"
    control = _IDENTIFIER_CONTROL_CHARACTERS.search(value)
    if control is not None:
        return f"contains a control character ({visible(control.group())})"
    space = next((character for character in value if character.isspace()), None)
    if space == " ":
        return "contains a space"
    if space is not None:
        return f"contains whitespace ({visible(space)})"
    unseen = next((character for character in value if not character.isprintable()), None)
    if unseen is not None:
        return f"contains {_unseen_kind(unseen)} ({visible(unseen)})"
    separator = next((character for character in value if character in _PATH_SEPARATORS), None)
    if separator is not None:
        return f"contains {_PATH_SEPARATORS[separator]} ({separator})"
    if value in _DOT_NAMES:
        return f"is '{value}'"
    return None


def _unseen_kind(character: str) -> str:
    """What a character a reader cannot see, other than a control character or whitespace,
    is called in a refusal."""
    if character in TEXT_DIRECTION_CHARACTERS:
        return "a text-direction character"
    if character in ZERO_WIDTH_CHARACTERS:
        return "a zero-width character"
    return "a character that is not printable"


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
