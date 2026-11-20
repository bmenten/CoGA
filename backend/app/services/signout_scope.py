"""What a sign-out of one of a family's analyses freezes and checks: its scope.

A family may hold several analyses, each imported from a package of its own: the monogenic and the
conventional NIPT of one pregnancy, or short-read and then long-read WGS of the same DNA. Each
analysis is signed out on its own, and its sign-out freezes and checks that analysis's data only
(the owner's decisions of 2026-11-19): its reported variants and their evidence drift, its repeat
and Paraphase findings with their reviews, its coverage tracks, its application's section and
Sample QC, its package's delivery and changes, and what its imports left unfinished. A family of one
analysis, or of none, is signed out whole.

What is an analysis's data is read from the family's package records (``family_package_records``):
each lists the storage sources its import wrote, per kind of data (``SOURCES_KEY``), and its
datasets. An item belongs to the analysis when one of its sources is the analysis's own or is
claimed by no other analysis the family holds. So data an upload or an analyst added, or a package
that named no analysis, belongs to every analysis, and each sign-out freezes and checks it; and so
does a reported variant whose sources cannot be told (its stored rows are gone).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from .clickhouse_variant_ids import _small_table_name, _structural_table_name
from .family_analysis_types import family_analysis_types
from .family_metadata_context import FamilyMetadataContext
from .family_package_records import SOURCE_KINDS, SOURCES_KEY, family_package_records

# The analyses whose sign-out freezes each application's section of the record: the PGT embryo
# calls (of a PGT family, and of a monogenic NIPT family's fetus), the NIPT results and the
# conventional NIPT results.
PGT_SECTION_ANALYSES = ("pgt", "monogenic_nipt")
NIPT_SECTION_ANALYSES = ("monogenic_nipt",)
CNIPT_SECTION_ANALYSES = ("conventional_nipt",)

# The variants whose stored sources one ClickHouse read looks up.
_CHUNK = 500


class PackageRecordShapeError(ValueError):
    """A package record whose sources or datasets are not in the shape the import writes them:
    the sign-out stops rather than guess which data are the analysis's."""


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
    # The datasets the analysis's own record lists, and those the other analyses' records list.
    own_datasets: frozenset[str] = frozenset()
    other_datasets: frozenset[str] = frozenset()
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

    def holds_dataset(self, name: object) -> bool:
        """Whether a package dataset (by its type: ``snv``, ``ccnv``…) is the analysis's: its own
        record lists it, or no other analysis's record does."""
        if self.own is None:
            return True
        return str(name) in self.own_datasets or str(name) not in self.other_datasets

    def holds_record(self, key: object) -> bool:
        """Whether a package record (by its key in ``family_package_records``) is the analysis's:
        its own, or one of no analysis the family holds (a package that named none)."""
        if self.own is None:
            return True
        return str(key) == self.analysis or str(key) not in self.held

    def holds_application(self, analyses: Collection[str]) -> bool:
        """Whether the record section of an application read for ``analyses`` (the NIPT results:
        monogenic NIPT) is the analysis's: it is one of them, or the family holds none of them."""
        if self.own is None:
            return True
        return self.analysis in analyses or not self.held.intersection(analyses)


# The scope of a sign-out of the whole family.
WHOLE_FAMILY = SignoutScope()


def _record_sources(record: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    sources = record.get(SOURCES_KEY)
    if (
        not isinstance(sources, Mapping)
        or set(sources) != set(SOURCE_KINDS)
        or not all(
            isinstance(names, list) and all(isinstance(name, str) for name in names) for names in sources.values()
        )
    ):
        raise PackageRecordShapeError("A package record's sources are not in the shape the import writes.")
    return {kind: frozenset(names) for kind, names in sources.items()}


def _record_datasets(record: Mapping[str, Any]) -> frozenset[str]:
    datasets = record.get("datasets")
    if not isinstance(datasets, Mapping):
        raise PackageRecordShapeError("A package record's datasets are not in the shape the import writes.")
    return frozenset(str(name) for name in datasets)


def signout_scope(metadata: Mapping[str, Any] | None, analysis: str | None) -> SignoutScope:
    """The scope of a sign-out of ``analysis`` (``report_signout_service.resolve_signout_analysis``)
    of the family whose ``families.metadata`` is ``metadata``: the whole family when it holds one
    analysis or none, else the analysis's data by its package records."""
    held = frozenset(family_analysis_types(metadata))
    if analysis is None or len(held) < 2:
        return SignoutScope(analysis=analysis)
    own: dict[str, frozenset[str]] = {kind: frozenset() for kind in SOURCE_KINDS}
    others: dict[str, set[str]] = {kind: set() for kind in SOURCE_KINDS}
    own_datasets: frozenset[str] = frozenset()
    other_datasets: set[str] = set()
    for key, record in family_package_records(metadata).items():
        if key == analysis:
            own = _record_sources(record)
            own_datasets = _record_datasets(record)
        elif key in held:
            for kind, names in _record_sources(record).items():
                others[kind].update(names)
            other_datasets.update(_record_datasets(record))
    return SignoutScope(
        analysis=analysis,
        own=own,
        others={kind: frozenset(names) for kind, names in others.items()},
        own_datasets=own_datasets,
        other_datasets=frozenset(other_datasets),
        held=held,
    )


def analysis_listing_source(metadata: Mapping[str, Any] | None, kind: str, source: object) -> str | None:
    """The analysis whose data an item of ``kind`` (``SOURCE_KINDS``) stored under ``source`` is:
    the one analysis the family holds whose package record lists the source among its sources
    of that kind; None when no such record lists it (data an upload added, or a package that named
    no analysis: every analysis's) or several do (the analyses cannot be told apart)."""
    name = str(source) if source is not None else ""
    if not name:
        return None
    held = family_analysis_types(metadata)
    listing = [
        key
        for key, record in family_package_records(metadata).items()
        if key in held and name in _record_sources(record).get(kind, frozenset())
    ]
    return listing[0] if len(listing) == 1 else None


async def stored_variant_sources(
    context: FamilyMetadataContext, *, small_ids: Collection[str], structural_ids: Collection[str]
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """The sources the family's stored rows of each small variant (``small_ids``) and each
    structural variant (``structural_ids``) are stored under, by variant ID; a variant with no
    stored row is left out. Read from the ``entries`` tables, current rows only (``sign = 1``)."""
    if not context.assembly_name:
        return {}, {}
    small: dict[str, set[str]] = {}
    unique_small = sorted({str(value) for value in small_ids if value})
    entries = _small_table_name(context.assembly_name, "entries")
    for offset in range(0, len(unique_small), _CHUNK):
        chunk = unique_small[offset : offset + _CHUNK]
        rows = await execute_clickhouse(
            f"""
            SELECT variantId, groupUniqArray(source)
            FROM {entries}
            WHERE family_guid = %(family_guid)s AND sign = 1 AND variantId IN %(variant_ids)s
            GROUP BY variantId
            """,
            {"family_guid": context.family_uuid, "variant_ids": tuple(chunk)},
        )
        for variant_id, sources in rows:
            small.setdefault(str(variant_id), set()).update(str(source) for source in sources or [])
    structural: dict[str, set[str]] = {}
    unique_structural = sorted({str(value) for value in structural_ids if value})
    sv_entries = _structural_table_name(context.assembly_name, "entries")
    for offset in range(0, len(unique_structural), _CHUNK):
        chunk = unique_structural[offset : offset + _CHUNK]
        rows = await execute_clickhouse(
            f"""
            SELECT variantId, groupUniqArray(source)
            FROM {sv_entries}
            WHERE family_guid = %(family_guid)s AND sign = 1 AND variantId IN %(variant_ids)s
            GROUP BY variantId
            """,
            {"family_guid": context.family_uuid, "variant_ids": tuple(chunk)},
        )
        for variant_id, sources in rows:
            structural.setdefault(str(variant_id), set()).update(str(source) for source in sources or [])
    return (
        {key: frozenset(names) for key, names in small.items()},
        {key: frozenset(names) for key, names in structural.items()},
    )


_REVIEWED_SMALL_VARIANTS = text(
    "SELECT DISTINCT variant_id FROM small_variant_reviews WHERE family_id = CAST(:family_uuid AS uuid)"
)
_REVIEWED_STRUCTURAL_VARIANTS = text(
    "SELECT DISTINCT variant_id FROM structural_variant_reviews "
    "WHERE family_id = CAST(:family_uuid AS uuid) AND variant_id IS NOT NULL"
)


@dataclass(frozen=True, slots=True)
class ReviewsLeftOut:
    """The variant IDs of the family's reviews a scoped sign-out leaves out: the small and the
    structural variants stored only under other analyses' sources. Empty for the whole family."""

    small: frozenset[str] = frozenset()
    structural: frozenset[str] = frozenset()


async def reviews_left_out(
    session: AsyncSession, context: FamilyMetadataContext, scope: SignoutScope
) -> ReviewsLeftOut:
    """The reviews of the family whose variants are not the analysis's (``SignoutScope.holds``),
    by the sources their stored rows are stored under; none for a sign-out of the whole family."""
    if scope.whole:
        return ReviewsLeftOut()
    params = {"family_uuid": context.family_uuid}
    small_ids = [str(value) for value in (await session.execute(_REVIEWED_SMALL_VARIANTS, params)).scalars()]
    structural_ids = [
        str(value) for value in (await session.execute(_REVIEWED_STRUCTURAL_VARIANTS, params)).scalars()
    ]
    small_sources, structural_sources = await stored_variant_sources(
        context, small_ids=small_ids, structural_ids=structural_ids
    )
    return ReviewsLeftOut(
        small=frozenset(
            variant_id
            for variant_id in small_ids
            if not scope.holds("small_variants", small_sources.get(variant_id, ()))
        ),
        structural=frozenset(
            variant_id
            for variant_id in structural_ids
            if not scope.holds("structural_variants", structural_sources.get(variant_id, ()))
        ),
    )
