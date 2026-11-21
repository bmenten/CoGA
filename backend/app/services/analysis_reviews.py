"""A family's reviews per analysis: which review of a variant a reading for one of its analyses
reads, and which review a save in it writes.

A review (a classification, tags, a note, a compound-het pair, an ACMG record or a CNV scoring)
is one analysis's (the owner's decision of 2026-11-20): a variant reviewed in the short-read
analysis is not in the long-read analysis's record unless it is reviewed there too, and the
report in the EPD refers 1:1 to one analysis's signed record. So a review is stored under the
analysis it was made in (``small_variant_reviews.analysis``, ``structural_variant_reviews.analysis``:
``AnalysisScope.review_analysis``), and a variant has a review in each analysis that reviewed it.

A review stored under no analysis the family holds (made while the family held none:
``NO_ANALYSIS``) is shared: each analysis that has no review of its own of the variant reads it.
So a reading for analysis A reads, per variant, its effective review: A's own when A has one,
else the shared one. A save in A always writes A's own review, created from the review A read
(the shared one) and the values the save sends when A has none; the shared review stays as it
is. A save that clears A's review of a variant with a shared review keeps A's review, empty: it
hides the shared one, so A reads the variant as not reviewed. An empty review is no review to
every reader.

A reading of the whole family (a family that holds no analysis) reads its reviews, each stored
under none.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .analysis_scope import NO_ANALYSIS, AnalysisScope, review_belongs

# The order a review saved at no known time takes among a variant's reviews: the first saved.
_NO_TIME = datetime.min.replace(tzinfo=timezone.utc)


def scope_review_clause(scope: AnalysisScope, params: dict[str, Any], *, column: str = "analysis") -> str:
    """The SQL condition that a review row, whose analysis is ``column``, may be the scope's
    effective review of its variant (``review_belongs``): its own, or one stored under no
    analysis the family holds; any review for the whole family. Binds its values into
    ``params``. ``column`` is the caller's own identifier, never a request's value."""
    if scope.analysis is None:
        return "TRUE"
    params["review_scope_analysis"] = scope.analysis
    params["review_scope_held"] = sorted(scope.held)
    return (
        f"({column} = CAST(:review_scope_analysis AS text) "
        f"OR NOT ({column} = ANY(CAST(:review_scope_held AS text[]))))"
    )


def _stamp(review: Mapping[str, Any]) -> datetime:
    value = review.get("updated_at")
    if not isinstance(value, datetime):
        return _NO_TIME
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _preference(review: Mapping[str, Any], scope: AnalysisScope) -> tuple[bool, bool, datetime, str]:
    """How a variant's review ranks among those the scope may read: its own first, then the one
    stored under no analysis, then the one saved last (then by analysis, so the pick is the same
    whatever order the rows come in)."""
    analysis = str(review.get("analysis") or "")
    return (analysis == scope.review_analysis, analysis == NO_ANALYSIS, _stamp(review), analysis)


@dataclass(frozen=True, slots=True)
class ScopedReview:
    """A variant's reviews as a save in a scope reads them: the scope's own review (the row the
    save writes; None when the scope has none) and the best of the variant's other reviews the
    scope may read (the shared one its own replaces)."""

    own: dict[str, Any] | None = None
    shared: dict[str, Any] | None = None

    @property
    def effective(self) -> dict[str, Any] | None:
        """The review the scope reads: its own, else the shared one."""
        return self.own if self.own is not None else self.shared


def scoped_review(rows: Iterable[Mapping[str, Any]], scope: AnalysisScope) -> ScopedReview:
    """The scope's own review of a variant and the shared one, among ``rows`` (the variant's
    reviews in the family, each with its ``analysis``)."""
    own: dict[str, Any] | None = None
    shared: dict[str, Any] | None = None
    for row in rows:
        if not review_belongs(scope.analysis, scope.held, row.get("analysis")):
            continue
        if str(row.get("analysis") or "") == scope.review_analysis:
            own = dict(row)
        elif shared is None or _preference(row, scope) > _preference(shared, scope):
            shared = dict(row)
    return ScopedReview(own=own, shared=shared)


def effective_reviews(rows: Iterable[Mapping[str, Any]], scope: AnalysisScope) -> dict[str, dict[str, Any]]:
    """By variant ID, the review the scope reads of each variant among ``rows`` (reviews of one
    family, each with its ``variant_id`` and ``analysis``): the scope's own, else the shared one
    (``scoped_review``). A review without a variant ID names no variant, and is left out."""
    chosen: dict[str, dict[str, Any]] = {}
    for row in rows:
        variant_id = row.get("variant_id")
        if not variant_id or not review_belongs(scope.analysis, scope.held, row.get("analysis")):
            continue
        key = str(variant_id)
        current = chosen.get(key)
        if current is None or _preference(row, scope) > _preference(current, scope):
            chosen[key] = dict(row)
    return chosen


def effective_reviews_with_content(
    rows: Iterable[Mapping[str, Any]],
    scope: AnalysisScope,
    has_content: Callable[[dict[str, Any]], bool],
) -> dict[str, dict[str, Any]]:
    """``effective_reviews`` without the empty ones: an analysis's empty review hides the shared
    one, and reads as no review."""
    return {
        variant_id: review
        for variant_id, review in effective_reviews(rows, scope).items()
        if has_content(review)
    }


def keeps_own_review(
    reviews: ScopedReview, has_content: Callable[[dict[str, Any]], bool], fields: dict[str, Any]
) -> bool:
    """Whether a save that writes ``fields`` as the scope's own review keeps that review: it
    holds something, or it hides a shared review that does (it is then kept empty)."""
    return has_content(fields) or (reviews.shared is not None and has_content(reviews.shared))


# The SQL condition that the review row ``r`` is in effect in some analysis of its family: it is
# an analysis's own, or a shared review (stored under no analysis the family holds) that some
# analysis the family holds, or a family that holds none, reads (no review of its own of the
# variant replaces it). For the readers of every family's reviews, the Variant Explorer's. The
# family's analyses are ``families.metadata`` ``analyses`` (family_analysis_types.py), its text
# values that are not empty.
_HELD_ANALYSES = (
    "SELECT held.name FROM families f, "
    "jsonb_array_elements_text(CASE WHEN jsonb_typeof(f.metadata -> 'analyses') = 'array' "
    "THEN f.metadata -> 'analyses' ELSE '[]'::jsonb END) AS held(name) "
    "WHERE f.id = {alias}.family_id AND held.name <> ''"
)


def review_in_effect_sql(alias: str, table: str) -> str:
    """The SQL condition that the review row ``alias`` of ``table`` (``small_variant_reviews``
    or ``structural_variant_reviews``) is in effect in some analysis of its family: an
    analysis's own review, or a shared review some analysis of the family reads, or any review of
    a family that holds no analysis. The alias and table are the caller's own identifiers."""
    if table not in {"small_variant_reviews", "structural_variant_reviews"} or not alias.isidentifier():
        raise ValueError("review_in_effect_sql takes a review table and an alias")
    held = _HELD_ANALYSES.format(alias=alias)
    return (
        f"({alias}.analysis IN ({held}) "
        f"OR NOT EXISTS ({held}) "
        f"OR EXISTS (SELECT 1 FROM ({held}) AS reads(name) WHERE NOT EXISTS ("
        f"SELECT 1 FROM {table} own WHERE own.family_id = {alias}.family_id "
        f"AND own.variant_id = {alias}.variant_id AND own.analysis = reads.name)))"
    )
