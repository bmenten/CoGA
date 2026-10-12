"""URL paths built from values that are data, such as family and sample IDs.

A family or sample ID is printable text without spaces (``services/family_identifiers.py``),
so it may hold ``/``, ``?``, ``#``, ``%`` or ``..``. Put into a URL as it is, such an ID
changes what the URL names: ``?`` and ``#`` end the path, ``%`` starts an escape the server
decodes, and ``/`` with ``..`` lets the client resolve the request to another path (the
class of #521). ``url_path`` percent-encodes each value as exactly one path segment, as the
frontend's ``apiPath`` (``lib/apiPath.ts``) does.

Two kinds of value still name no resource: one that is exactly ``.`` or ``..``, which a
client resolves whatever the encoding (a browser reads ``%2E%2E`` as ``..``), and one holding
``/``, which reaches the server as ``%2F`` and is decoded before the routes are matched, so a
route that takes it as one parameter does not match (404).
"""

from __future__ import annotations

from urllib.parse import quote


def url_path(*segments: object) -> str:
    """``/a/b/c`` from ``segments``, each percent-encoded as one path segment: nothing in a
    value is left as a separator (``/``), a query or fragment start (``?``, ``#``) or an
    escape (``%``)."""
    return "".join(f"/{quote(str(segment), safe='')}" for segment in segments)
