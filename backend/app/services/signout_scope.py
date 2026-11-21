"""What a sign-out of one of a family's analyses freezes and checks: its scope.

A family may hold several analyses, each imported from a package of its own: the monogenic and the
conventional NIPT of each of a woman's pregnancies, or short-read and then long-read WGS of the same
DNA. Each analysis (by its key: a NIPT's names its pregnancy, ``family_analysis_types``) is signed
out on its own, and its sign-out freezes and checks that analysis's data only
(the owner's decisions of 2026-11-19): its reported variants and their evidence drift, its repeat
and Paraphase findings with their reviews, its coverage tracks, its application's section and
Sample QC, its package's delivery and changes, and what its imports left unfinished or failed to
import. A family of one analysis, or of none, is signed out whole.

What is an analysis's data is read from the family's package records (``family_package_records``):
each lists the storage sources its import wrote, per kind of data (``SOURCES_KEY``), and its
datasets. An item belongs to the analysis when one of its sources is the analysis's own or is
claimed by no other analysis the family holds. So data an upload or an analyst added, or a package
that named no analysis, belongs to every analysis, and each sign-out freezes and checks it. An import
mark (``family_package_registration``) records the analysis of the import that left it, and
belongs to that analysis; one an upload or a deletion left records none, and belongs to every
analysis.

The small and structural variants a sign-out reports are those the analysis's own reviews tag for
the report, or the shared reviews made while the family held no analysis: a review is one
analysis's (``analysis_reviews``, the owner's decision of 2026-11-20).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


from .family_analysis_types import analysis_type_of, family_analyses
from .family_package_records import (
    SOURCE_KINDS,
    family_package_records,
    record_sources,
)

# The analyses whose sign-out freezes each application's section of the record: the PGT embryo
# calls (of a PGT family, and of a monogenic NIPT family's fetus), the NIPT results and the
# conventional NIPT results.
PGT_SECTION_ANALYSES = ("pgt", "monogenic_nipt")
NIPT_SECTION_ANALYSES = ("monogenic_nipt",)
CNIPT_SECTION_ANALYSES = ("conventional_nipt",)



@dataclass(frozen=True, slots=True)
class SignoutScope:
    """The data a sign-out of ``analysis`` freezes and checks. ``own`` is None for a sign-out of
    the whole family: every ``holds…`` answers True."""

    # The analysis signed out; None for a family that holds none.
    analysis: str | None = None
    # The sources the analysis's own package record lists, per kind; None: the whole family.
    own: Mapping[str, frozenset[str]] | None = None
    # The sources the records of the family's other analyses list, per kind.
    others: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # The family's analyses.
    held: frozenset[str] = frozenset()

    @property
    def whole(self) -> bool:
        """Whether the sign-out freezes and checks the whole family."""
        return self.own is None

    def holds(self, kind: str, sources: Iterable[object]) -> bool:
        """Whether an item of ``kind`` (``SOURCE_KINDS``) stored under ``sources`` is the
        analysis's: one of them is its own or no other analysis's, or none can be told."""
        if self.own is None:
            return True
        names = {str(source) for source in sources if source is not None and str(source)}
        if not names:
            return True
        own = self.own.get(kind, frozenset())
        others = self.others.get(kind, frozenset())
        return any(name in own or name not in others for name in names)


    def holds_record(self, key: object) -> bool:
        """Whether a package record (by its key in ``family_package_records``) is the analysis's:
        its own, or one of no analysis the family holds (a package that named none)."""
        if self.own is None:
            return True
        return str(key) == self.analysis or str(key) not in self.held

    def holds_mark(self, analysis_type: str | None) -> bool:
        """Whether an import mark (an ``import_unfinished`` entry, a failure of the
        ``import_incomplete`` flag) is the analysis's, by the analysis it records
        (``family_package_registration.MARK_ANALYSIS_KEY``): the package record of the import
        that left it -- the analysis's own, or one of no analysis the family holds
        (``holds_record``) -- or none, for an upload's or a deletion's. The dataset a mark names
        cannot say whose it is: analyses share dataset types (``snv``, ``qc``…)."""
        return analysis_type is None or self.holds_record(analysis_type)

    def holds_application(self, analyses: Collection[str]) -> bool:
        """Whether the record section of an application read for the analysis types ``analyses``
        (the NIPT results: monogenic NIPT) is the analysis's: it is of one of them, or the family
        holds an analysis of none of them. The section reads the analysis's own data: a NIPT's,
        its pregnancy's."""
        if self.own is None:
            return True
        return analysis_type_of(self.analysis) in analyses or not any(
            analysis_type_of(held) in analyses for held in self.held
        )


# The scope of a sign-out of the whole family.
WHOLE_FAMILY = SignoutScope()


def signout_scope(metadata: Mapping[str, Any] | None, analysis: str | None) -> SignoutScope:
    """The scope of a sign-out of ``analysis`` (``report_signout_service.resolve_signout_analysis``)
    of the family whose ``families.metadata`` is ``metadata``: the whole family when it holds one
    analysis or none, else the analysis's data by its package records."""
    held = family_analyses(metadata)
    if analysis is None or len(held) < 2:
        return SignoutScope(analysis=analysis)
    own: dict[str, frozenset[str]] = {kind: frozenset() for kind in SOURCE_KINDS}
    others: dict[str, set[str]] = {kind: set() for kind in SOURCE_KINDS}
    for key, record in family_package_records(metadata).items():
        if key == analysis:
            own = record_sources(record)
        elif key in held:
            for kind, names in record_sources(record).items():
                others[kind].update(names)
    return SignoutScope(
        analysis=analysis,
        own=own,
        others={kind: frozenset(names) for kind, names in others.items()},
        held=held,
    )
