"""What a coverage track's values are, as a coverage BED declares it (TRACE-9).

A coverage BED says what it holds when it is loaded: the BED upload's ``content`` and the
``content`` key of a package manifest's coverage entry, ``read_depth`` or ``log2_ratio``. Its
track's record keeps it (``content`` in ``sample_interval_track_sources.metadata``). A BED that
declares neither -- a manifest entry without the key, or a track stored before BEDs declared
it -- holds values of unknown kind (``unknown``): it is drawn without a depth or log2-ratio
axis and never read as depth. ``coverage_read_depth`` decides from it, and from the transforms
the other callers' records name, what each coverage track holds.

No imports, so that the loaders, the validation and the bookkeeping can all read it.
"""

from __future__ import annotations

from typing import Final, Literal

#: The key of a track's record (``sample_interval_track_sources.metadata``), of the upload's
#: query and of a manifest coverage entry that holds what a coverage BED declares.
COVERAGE_CONTENT_KEY: Final = "content"

READ_DEPTH: Final = "read_depth"
LOG2_RATIO: Final = "log2_ratio"
UNKNOWN_CONTENT: Final = "unknown"

CoverageContent = Literal["read_depth", "log2_ratio", "unknown"]
DeclaredCoverageContent = Literal["read_depth", "log2_ratio"]

#: What a coverage BED may declare it holds.
DECLARED_COVERAGE_CONTENTS: tuple[str, ...] = (READ_DEPTH, LOG2_RATIO)


def declared_coverage_content(value: object) -> CoverageContent:
    """What a coverage BED declares it holds, from the ``content`` of its upload or manifest
    entry: ``read_depth`` or ``log2_ratio``, else ``unknown`` (no declaration). A value that is
    neither is refused by the upload (422) and by the package validation before it gets here."""
    if value == READ_DEPTH:
        return READ_DEPTH
    if value == LOG2_RATIO:
        return LOG2_RATIO
    return UNKNOWN_CONTENT
