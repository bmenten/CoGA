"""Which of a family's analyses a reading or a review of its variants is about: its scope.

A family may hold several analyses on the same DNA, each imported from a package of its own and
signed out on its own: short-read and then long-read WGS of the same samples, each with a
small-variant callset of its own. Each analysis is read on its own (the owner's decisions of
2026-11-20): the family's variant list, its filters, counts, ranking and export read the callsets
of one analysis, and a review (its tags, classification and note) is that analysis's. So a
variant both callsets hold has a row and a review in each analysis, never a row merged from
both, and an inheritance filter reads the members' calls of one callset.

An analysis's data are those its package record lists (``family_package_records``), and the data
no other analysis's record lists (an upload's, or a package's that named no analysis): a reading
of an analysis leaves out the sources the family's other analyses' records list. No two analyses
of a family share a source (``family_package_records.source_collisions``).

When the family holds several analyses whose records each list a small-variant callset (the
monogenic NIPT of each of a woman's pregnancies, say), a reading or a review of its small variants
names the analysis (``analysis``, its key: ``monogenic_nipt:ZW1``); without one it is refused
(``AnalysisNotNamed``), never read across them. A family with at most one such analysis is read
as that analysis (a monogenic and a conventional NIPT of one pregnancy: only the monogenic NIPT
holds small variants), or whole when it holds none. The structural-variant list and its reviews
are read the same way among the analyses whose records list a structural-variant caller (the
conventional NIPT of each pregnancy, with its ccnv calls).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from .family_analysis_types import family_analyses
from .family_package_records import (
    SOURCE_KINDS,
    analysis_label,
    family_package_records,
    record_members,
    record_sources,
)

# The analysis a review of a family that holds none is stored under: it belongs to each analysis
# the family later holds.
NO_ANALYSIS = ""


class AnalysisNotNamed(ValueError):
    """A reading or a review of the variants of a family that holds several analyses, each with
    variants of its own, that names none of them, or names one the family does not hold."""


@dataclass(frozen=True, slots=True)
class AnalysisScope:
    """The data of one of a family's analyses. ``analysis`` is None for the whole family (it
    holds no analysis); ``left_out`` holds, per kind (``SOURCE_KINDS``), the sources of the
    family's other analyses, which a reading of ``analysis`` leaves out."""

    analysis: str | None = None
    left_out: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # The members only the family's other analyses' packages list (a second sample of a person,
    # linked as the same individual, sequenced for another analysis): a reading of ``analysis``
    # neither requires nor reads their calls.
    left_out_members: frozenset[str] = frozenset()
    # The analyses among which a reading must name one: set only when none was named and the
    # family holds several analyses with variants of the kind read of their own.
    unnamed_among: tuple[str, ...] = ()
    # Whether the reading named ``analysis`` itself.
    asked: bool = False
    # The kind of variants (``SOURCE_KINDS``) by which ``analysis`` was chosen when the reading
    # named none: the family's one analysis with variants of that kind.
    kind: str = "small_variants"
    # The analyses the family holds: a review stored under none of them is each one's that has
    # no review of its own (analysis_reviews.py).
    held: frozenset[str] = frozenset()

    @property
    def review_analysis(self) -> str:
        """The analysis a review made in this scope is stored under (``NO_ANALYSIS`` for a family
        that holds none)."""
        return self.analysis or NO_ANALYSIS

    def left_out_sources(self, kind: str) -> frozenset[str]:
        """The sources of ``kind`` a reading of the analysis leaves out: the family's other
        analyses' sources, when the reading named the analysis or it was chosen by its variants
        of ``kind``. An analysis chosen by its small variants is not the scope of a reading of
        another kind: the genome view's track availability of a family with a monogenic and a
        conventional NIPT still finds the conventional NIPT's ccnv calls."""
        if not (self.asked or kind == self.kind):
            return frozenset()
        return self.left_out.get(kind, frozenset())

    def require_named(self) -> AnalysisScope:
        """This scope, or ``AnalysisNotNamed`` when the reading had to name an analysis."""
        if self.unnamed_among:
            names = ", ".join(analysis_label(key) or key for key in self.unnamed_among)
            raise AnalysisNotNamed(
                f"This family holds several analyses with variants of their own ({names}); name the "
                "analysis to read (analysis)."
            )
        return self


# The whole family: nothing left out.
WHOLE_FAMILY_SCOPE = AnalysisScope()


def _record_order(record: Mapping[str, Any]) -> str:
    registered = record.get("registered_at")
    return str(registered) if registered is not None else ""


def analyses_with(metadata: Mapping[str, Any] | None, kind: str) -> list[str]:
    """The family's analyses whose package record lists a source of ``kind`` (``SOURCE_KINDS``:
    a small-variant callset, a structural-variant caller), in the order they were imported (the
    earliest first)."""
    held = family_analyses(metadata)
    records = family_package_records(metadata)
    return [
        key
        for key, record in sorted(records.items(), key=lambda item: (_record_order(item[1]), item[0]))
        if key in held and record_sources(record)[kind]
    ]


def analysis_holding(metadata: Mapping[str, Any] | None, kind: str, source: str) -> str | None:
    """The one analysis of the family whose package record lists ``source`` among its sources
    of ``kind`` (the mtDNA calls, ``mito``), or None when no analysis it holds does. No two
    analyses of a family share a source (``family_package_records.source_collisions``)."""
    held = family_analyses(metadata)
    holders = sorted(
        key
        for key, record in family_package_records(metadata).items()
        if key in held and source in record_sources(record)[kind]
    )
    return holders[0] if len(holders) == 1 else None


def analysis_scope(
    metadata: Mapping[str, Any] | None,
    analysis: str | None = None,
    *,
    kind: str = "small_variants",
    holding: tuple[str, str] | None = None,
) -> AnalysisScope:
    """The scope of a reading of the family (whose ``families.metadata`` is ``metadata``) for
    ``analysis``, which must be one it holds. Without one: the family's one analysis with
    variants of ``kind`` (small variants, or for the structural-variant list and its reviews
    structural variants), else its one analysis, else the whole family; a family with several
    analyses with variants of that kind is read for the analysis that holds ``holding`` (a kind
    and a source: the mtDNA page reads the analysis with the mtDNA callset) when one does, and
    otherwise gives a scope that must still name one (``AnalysisScope.require_named``)."""
    held = family_analyses(metadata)
    asked = analysis is not None
    if analysis is not None:
        analysis = analysis.strip()
        if analysis not in held:
            raise AnalysisNotNamed("This family holds no such analysis.")
    else:
        with_kind = analyses_with(metadata, kind)
        holder = analysis_holding(metadata, *holding) if holding is not None and len(with_kind) > 1 else None
        if holder is not None:
            with_kind = [holder]
        if len(with_kind) > 1:
            return AnalysisScope(unnamed_among=tuple(with_kind), kind=kind, held=held)
        if with_kind:
            analysis = with_kind[0]
        elif len(held) == 1:
            (analysis,) = held
        else:
            return AnalysisScope(kind=kind, held=held)
    left_out: dict[str, set[str]] = {source_kind: set() for source_kind in SOURCE_KINDS}
    own_members: set[str] = set()
    other_members: set[str] = set()
    for key, record in family_package_records(metadata).items():
        if key == analysis:
            own_members.update(record_members(record))
        elif key in held:
            for source_kind, names in record_sources(record).items():
                left_out[source_kind].update(names)
            other_members.update(record_members(record))
    return AnalysisScope(
        analysis=analysis,
        left_out={source_kind: frozenset(names) for source_kind, names in left_out.items()},
        left_out_members=frozenset(other_members - own_members),
        asked=asked,
        kind=kind,
        held=held,
    )


def left_out_source_clauses(
    left_out: Collection[str], params: dict[str, Any], *, column: str = "e.source"
) -> list[str]:
    """The clause of a ClickHouse read that leaves out the sources ``left_out`` (a scope's
    ``left_out_sources``) on the rows' source ``column``, its value bound in ``params``; none
    when nothing is left out."""
    if not left_out:
        return []
    params["scope_left_out_sources"] = tuple(sorted(left_out))
    return [f"{column} NOT IN %(scope_left_out_sources)s"]


def review_belongs(scope_analysis: str | None, held: frozenset[str], review_analysis: object) -> bool:
    """Whether a review stored under ``review_analysis`` is the analysis ``scope_analysis``'s
    (None: the whole family, every review): its own, or one made under no analysis the family
    holds (before the family held one, or under a package that named none)."""
    if scope_analysis is None:
        return True
    return review_analysis == scope_analysis or review_analysis not in held
