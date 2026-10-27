"""Content-Disposition values for a file named after values that are data (SEC-15).

A download is named after a family or sample ID: ``F1.ped``, ``family-F1-small-variants.csv``,
a signed URL's ``S1.cram``. A family or sample ID is printable text without spaces
(``services/family_identifiers.py``), so it may hold a ``"`` that ends the quoted name early, a
``\\`` that escapes within it, a ``%`` a client may decode, or a character outside ASCII. Put
into the header as it is, such a name was misread, or failed the response: Starlette encodes
a header as Latin-1, so an ID beyond it answered 500. ``content_disposition`` sends such a name
percent-encoded as UTF-8 in ``filename*`` (RFC 6266, RFC 8187), with an ASCII stand-in in
``filename`` for a client that reads only that one, as ``url_path`` encodes an ID in a path.
"""

from __future__ import annotations

import re
from urllib.parse import quote

_NOT_PLAIN = re.compile(r'[^\x20-\x7e]|["\\%]')
_NOT_STAND_IN = re.compile(r"[^A-Za-z0-9._-]")


def content_disposition(disposition: str, filename: str) -> str:
    """``<disposition>; filename="<filename>"``, ``disposition`` being ``attachment`` or
    ``inline``. A name outside printable ASCII, or holding ``"``, ``\\`` or ``%``, is sent in
    ``filename*``, percent-encoded as UTF-8, with ``_`` for each such character in
    ``filename``."""
    if not _NOT_PLAIN.search(filename):
        return f'{disposition}; filename="{filename}"'
    stand_in = _NOT_STAND_IN.sub("_", filename)
    return f"{disposition}; filename=\"{stand_in}\"; filename*=UTF-8''{quote(filename, safe='')}"
