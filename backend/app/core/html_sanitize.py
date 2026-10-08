"""Strict-allowlist HTML sanitiser for imported reference-metadata snippets.

The clinical-CNV knowledgebase import stores a small ``details_html`` fragment that the
UI renders via ``dangerouslySetInnerHTML``. That HTML originates from imported reference
files (not curated in-app), so it is treated as untrusted input and is a stored-XSS sink.

``sanitize_reference_html`` reduces the fragment to a conservative allowlist of formatting
tags and attributes, dropping every element (``script``, ``img``, ``iframe`` …), attribute
(``onerror``, ``style`` …) and URL scheme (``javascript:``, ``data:`` …) that is not
explicitly permitted. It is applied at ingest (so stored data is clean at rest) and again
at read time, as defence in depth (a row that reached the table any other way is served
safely too) — the API therefore never returns unsanitised ``details_html``.

Implemented on the stdlib :class:`html.parser.HTMLParser` (default-deny, re-serialise with
proper escaping) so the sanitiser is self-contained and has no third-party dependency: a
security control should not hinge on an optional package being installed. The parser runs
with ``convert_charrefs=True``, so character references reach ``handle_data`` decoded and
are escaped again there (``handle_charref``/``handle_entityref`` are never called). Because
attribute values arrive already entity-decoded from the tokeniser, obfuscated schemes such
as ``javascript&#58;`` are resolved before the scheme check, then re-escaped on output.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

# Inline/text formatting + simple structure only. Deliberately excludes anything that can
# load or execute: script, img, iframe, svg, object, embed, style, form, input, ...
_ALLOWED_TAGS = frozenset(
    {
        "a", "abbr", "b", "br", "code", "div", "em", "i", "li", "ol",
        "p", "span", "strong", "sub", "sup", "u", "ul",
    }
)

# Per-tag attribute allowlist. Only anchors keep attributes; every other tag is emitted
# bare (dropping class/style/id and every ``on*`` event handler).
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
}

_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})

# Void elements: emitted self-closing, never given an end tag.
_VOID_TAGS = frozenset({"br"})


def _href_is_safe(value: str) -> bool:
    """True if ``value`` carries no scheme, or a scheme on the allowlist.

    Rejects ``javascript:``/``data:``/``vbscript:`` etc. (default-deny: anything that is not
    http/https/mailto). A ``:`` that appears only after a ``/``, ``?`` or ``#`` is part of a
    path/query/fragment, not a scheme, so those relative references stay allowed.
    """

    stripped = value.strip()
    if not stripped:
        return True
    scheme, sep, _rest = stripped.partition(":")
    if not sep:
        return True  # no ':' at all → relative reference
    if any(c in scheme for c in "/?#"):
        return True  # the ':' is inside a path/query/fragment, not a scheme
    return scheme.strip().lower() in _ALLOWED_URL_SCHEMES


class _AllowlistSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def _emit_start(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        if tag not in _ALLOWED_TAGS:
            return  # drop the tag; its text/children are still emitted by later callbacks
        allowed = _ALLOWED_ATTRS.get(tag, frozenset())
        rendered: list[str] = []
        for name, value in attrs:
            lname = name.lower()
            if lname not in allowed or value is None:
                continue
            if lname == "href" and not _href_is_safe(value):
                continue
            rendered.append(f' {lname}="{escape(value, quote=True)}"')
        attr_str = "".join(rendered)
        if tag in _VOID_TAGS:
            self._parts.append(f"<{tag}{attr_str}/>")
        elif self_closing:
            self._parts.append(f"<{tag}{attr_str}></{tag}>")
        else:
            self._parts.append(f"<{tag}{attr_str}>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit_start(tag.lower(), attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit_start(tag.lower(), attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _ALLOWED_TAGS and lowered not in _VOID_TAGS:
            self._parts.append(f"</{lowered}>")

    def handle_data(self, data: str) -> None:
        # Re-escape text (incl. inert content of dropped script/style elements) so it can
        # only ever render as text, never as markup.
        self._parts.append(escape(data, quote=False))

    def result(self) -> str:
        return "".join(self._parts)


def sanitize_reference_html(raw: str | None) -> str | None:
    """Return ``raw`` reduced to the safe allowlist, or ``raw`` unchanged if empty/None."""

    if raw is None or not raw.strip():
        return raw
    parser = _AllowlistSanitizer()
    parser.feed(raw)
    parser.close()
    return parser.result()
