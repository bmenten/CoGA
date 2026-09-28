"""CSV cell hardening shared by every CSV export path.

Spreadsheet applications (Excel, Google Sheets, LibreOffice) interpret a cell whose
text begins with ``=``, ``+``, ``-``, ``@`` or a leading tab / carriage-return / newline
as a *formula*. Because our exports carry imported annotations and user-controlled tags,
such a value can smuggle an executable formula into a downloaded CSV — the classic
"CSV / formula injection" sink (e.g. ``=HYPERLINK(...)`` or ``@SUM(...)`` exfiltrating
data on open).

``csv_safe_cell`` neutralises this by prefixing an at-risk value with a single quote,
which every mainstream spreadsheet treats as "keep the rest as literal text". It is the
one formatter that every export path must funnel its data cells through.
"""

from __future__ import annotations

# The leading characters a spreadsheet may treat as the start of a formula. Tab / CR / LF
# are included because a value can be pushed past a naive "first visible char" check.
_FORMULA_TRIGGERS = frozenset("=+-@\t\r\n")


def csv_safe_cell(value: str) -> str:
    """Return ``value`` neutralised against spreadsheet formula injection.

    A value whose first character is a formula trigger is prefixed with a single quote so
    the spreadsheet renders it as text; everything else is returned unchanged. Headers are
    developer-controlled and need not be routed through this — only data cells do.
    """

    if value and value[0] in _FORMULA_TRIGGERS:
        return "'" + value
    return value


# Response headers an export sets so the UI can say when a file was cut at the export cap
# (#512). Exposed through CORS in main.py for split-origin deployments.
EXPORT_ROWS_HEADER = "X-CoGA-Export-Rows"
EXPORT_TRUNCATED_HEADER = "X-CoGA-Export-Truncated"
EXPORT_LIMIT_HEADER = "X-CoGA-Export-Limit"
EXPORT_HEADERS = ("Content-Disposition", EXPORT_ROWS_HEADER, EXPORT_TRUNCATED_HEADER, EXPORT_LIMIT_HEADER)


def export_response_headers(filename_stem: str, *, rows: int, truncated: bool, limit: int) -> dict[str, str]:
    """Headers for a CSV export response.

    A truncated export says so in the file name as well as in the headers, so the file
    itself — once saved, forwarded or attached — cannot pass for the complete result.
    """

    filename = (
        f"{filename_stem}-TRUNCATED-first-{limit}.csv" if truncated else f"{filename_stem}.csv"
    )
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        EXPORT_ROWS_HEADER: str(rows),
        EXPORT_TRUNCATED_HEADER: "true" if truncated else "false",
        EXPORT_LIMIT_HEADER: str(limit),
    }
