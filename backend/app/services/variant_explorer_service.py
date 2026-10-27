"""Global Small Variant Explorer aggregation engine.

This service powers a *variant-centric* view of small variants (SNVs/indels)
aggregated across every project a user can access, independent of family
boundaries. Each result row is one unique variant (keyed by the ClickHouse
``UInt64`` variant key) summarising carrier counts across the whole accessible
cohort.

Design notes
------------
* **Source of truth = ``entries``.** Carrier counts are aggregated directly from
  ``entries`` with ``sign = 1``: no pre-aggregated table can drift from it when a
  re-import or delete collapses rows. Counting distinct ``sampleId`` also dedupes
  any sample that appears under multiple accessible projects, and lets us
  return the distinct family count in the same query.
* **Permissions.** Every query is scoped to the project GUIDs the user can
  access (admins -> all projects, viewers -> ``metadata_project_ids``).
  ``project_guid`` in ClickHouse equals the Postgres project UUID string and
  ``family_guid`` equals the family UUID string.
* **Tags / classification** live in Postgres (``small_variant_reviews``,
  per-family). We bridge to ClickHouse on ``variant_id`` (TEXT, e.g.
  ``1-1000-A-T``) — the same bridge the family page uses — because
  ``variant_key`` is only stored when it fits a signed BIGINT.
* This module is intentionally generic ("aggregate variant keys, scoped to
  projects") so future gene-/transcript-/ClinVar-centric views can be added as
  sibling functions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from ..core.csv_export import TRUNCATED_BY_REVIEW_FILTER_LIMIT, TRUNCATED_BY_ROW_LIMIT
from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    GlobalVariantPageOut,
    GlobalVariantRowOut,
    VariantCarrierFamilyGroupOut,
    VariantCarrierSampleOut,
    VariantCarriersOut,
    VariantExplorerAssemblyOut,
)
from .clickhouse_family_variants import _fetch_panel_constraints
from .clickhouse_variant_ids import _small_table_name
from .clickhouse_variant_queries import (
    _SMALL_REGION_WINDOW_MATCH,
    IMPUTED_SMALL_VARIANT_SOURCES,
    _small_annotation_filter_condition,
    _small_panel_filter_condition,
    _small_region_window_rows,
    _small_variant_windows,
)
from .clickhouse_variant_records import PanelFilterConstraints, Region, _status_filter_terms
from .family_variant_filters import SmallVariantQueryFilters
from .genotypes import ALT_CLASSES, HET, HOM_ALT, classify_genotype, clickhouse_genotype_condition
from .access_control import CurrentUser, is_admin_user, user_metadata_project_ids
from .variant_ranking_cache import panel_members_digest


# Carrier / homozygous / heterozygous conditions on a genotype column, from the one
# genotype classification (#511): a carrier has an ALT allele (a haploid "0" is not one),
# and a haploid "1" or a "2/2" counts as homozygous.
def _gt_is_carrier(column: str, params: dict[str, Any]) -> str:
    return clickhouse_genotype_condition(column, ALT_CLASSES, param="gt_alt", params=params)


def _gt_is_hom(column: str, params: dict[str, Any]) -> str:
    return clickhouse_genotype_condition(column, {HOM_ALT}, param="gt_hom", params=params)


def _gt_is_het(column: str, params: dict[str, Any]) -> str:
    return clickhouse_genotype_condition(column, {HET}, param="gt_het", params=params)

_SORT_EXPR = {
    "total_samples": "total_samples",
    "het_samples": "het_samples",
    "hom_samples": "hom_samples",
    "total_families": "families",
    "position": "xpos",
}
_DEFAULT_SORT = "total_samples"
# Cap the cohort-wide distinct-key count (#270) so the explorer never pays an unbounded
# uniqExact over millions of keys per page. Past the cap the total is reported as the cap
# with total_is_estimated=True (the UI renders "N+").
_EXPLORER_COUNT_CAP = 10_000


def _bounded_total(raw_count: int) -> tuple[int, bool]:
    """Map the (cap+1)-capped distinct-key count to (total, is_estimated): past the cap the
    true total is unknown, so report the cap with the estimate flag set (UI shows "N+")."""
    if raw_count > _EXPLORER_COUNT_CAP:
        return _EXPLORER_COUNT_CAP, True
    return raw_count, False


# Keyset (seek) pagination (#274). The ORDER BY is `{sort_expr} {dir}, xpos ASC, key ASC`,
# so a page boundary is fully described by the last row's (sort_value, xpos, key) — all
# integers (carrier counts or xpos; key is the UInt64 variant key). The cursor encodes that
# triple plus the sort/order it was issued for, so a stale cursor (after the user changes
# sort) is detected and ignored rather than silently mis-paging.
_CURSOR_VERSION = "v1"
# Column index of each sort_expr in the raw `_fetch_variant_rows` SELECT (see that query).
_SORT_RAW_COL = {
    "families": 8,
    "hom_samples": 9,
    "het_samples": 10,
    "total_samples": 11,
    "xpos": 7,
}
_RAW_XPOS_COL = 7
_RAW_KEY_COL = 0


def _filters_fingerprint(
    filters: GlobalVariantFilters,
    assembly_id: str | None,
    panel_constraints: PanelFilterConstraints | None = None,
) -> str:
    """A short stable hash of everything that changes the result set (filter surface,
    assembly, and the genes and regions the panel filter applied). Bound into the cursor so
    a cursor leaked across a filter change (e.g. clicked during an in-flight refetch), or
    across an edit of the panel, is rejected rather than silently mis-paging."""
    payload = json.dumps(
        {"f": asdict(filters), "a": assembly_id, "p": panel_members_digest(panel_constraints)},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _encode_cursor(
    sort: str, order: str, fingerprint: str, sort_value: int, xpos: int, key: int
) -> str:
    raw = f"{_CURSOR_VERSION}:{sort}:{order}:{fingerprint}:{sort_value}:{xpos}:{key}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(
    cursor: str, sort: str, order: str, fingerprint: str
) -> tuple[int, int, int] | None:
    """Decode a cursor to (sort_value, xpos, key), or None if it is malformed or was issued
    for a different sort/order/filter set (treated as "start from the first page")."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        parts = raw.split(":")
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return None
    if len(parts) != 7:
        return None
    version, c_sort, c_order, c_fingerprint, sort_value, xpos, key = parts
    if (version, c_sort, c_order, c_fingerprint) != (_CURSOR_VERSION, sort, order, fingerprint):
        return None
    try:
        return int(sort_value), int(xpos), int(key)
    except ValueError:
        return None


def _seek_having(sort_expr: str, direction: str) -> str:
    """The HAVING predicate selecting rows strictly after the cursor under the ORDER BY
    `{sort_expr} {direction}, xpos ASC, key ASC`. Bound params: cur_sort/cur_xpos/cur_key.
    Correct even when sort_expr is `xpos` (the redundant middle term collapses cleanly)."""
    cmp = "<" if direction == "DESC" else ">"
    return (
        f"( {sort_expr} {cmp} %(cur_sort)s "
        f"OR ({sort_expr} = %(cur_sort)s AND xpos > %(cur_xpos)s) "
        f"OR ({sort_expr} = %(cur_sort)s AND xpos = %(cur_xpos)s AND key > %(cur_key)s) )"
    )

# Keys are lowercased to match _most_severe's case-insensitive lookup (and the
# casefolded impacts stored in annotation_index).
_IMPACT_RANK = {"high": 4, "moderate": 3, "low": 2, "modifier": 1}
_CLASSIFICATION_RANK = {
    "acmg_class_5": 5,
    "pathogenic": 5,
    "acmg_class_4": 4,
    "likely_pathogenic": 4,
    "acmg_class_3": 3,
    "uncertain_significance": 3,
    "vus": 3,
    "acmg_class_2": 2,
    "likely_benign": 2,
    "acmg_class_1": 1,
    "benign": 1,
}
_CLINVAR_RANK = {
    "pathogenic": 7,
    "pathogenic/likely_pathogenic": 6,
    "likely_pathogenic": 5,
    "drug_response": 4,
    "conflicting_interpretations_of_pathogenicity": 3,
    "uncertain_significance": 2,
    "likely_benign": 1,
    "benign": 0,
}

# The tag / classification filter reaches ClickHouse as a variant_id list inlined into the
# query, so it holds at most this many ids. Past it a search can miss matches: the export
# then reports itself truncated.
_MAX_TAG_FILTER_VARIANT_IDS = 200_000


def _split_terms(value: str | None) -> list[str]:
    if not value:
        return []
    return [token for token in re.split(r"[\s,;]+", value.strip()) if token]


def _classify_type(ref: str | None, alt: str | None) -> str:
    ref = ref or ""
    alt = alt or ""
    if len(ref) == 1 and len(alt) == 1:
        return "SNV"
    if len(ref) == len(alt) and len(ref) > 1:
        return "MNV"
    return "INDEL"


def _most_severe(values: Sequence[str], rank: dict[str, int]) -> str | None:
    best_value: str | None = None
    best_rank = -1
    for value in values:
        text_value = str(value or "").strip()
        if not text_value:
            continue
        value_rank = rank.get(text_value.lower(), 0)
        if value_rank > best_rank:
            best_rank = value_rank
            best_value = text_value
    return best_value


def _first_non_empty(values: Sequence[str]) -> str | None:
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return None


@dataclass(slots=True)
class GlobalVariantFilters:
    """Annotation-only filter surface (no family/sample/inheritance filters)."""

    chromosome: str | None = None
    start: int | None = None
    end: int | None = None
    variant_type: str | None = None
    gene: str | None = None
    # The gene panel's id, as the request names it. The search reads the panel's genes and
    # regions for its assembly as the family page does (_panel_constraints).
    panel_id: str | None = None
    impacts: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    clinvar: list[str] = field(default_factory=list)
    exclude_clinvar: list[str] = field(default_factory=list)
    rsid: str | None = None
    hgvsc: str | None = None
    hgvsp: str | None = None
    canonical_only: bool = False
    mane_only: bool = False
    lof_only: bool = False
    max_gnomad_af: float | None = None
    max_gnomad_exomes_af: float | None = None
    max_gnomad_genomes_af: float | None = None
    max_gnomad_popmax_af: float | None = None
    max_topmed_af: float | None = None
    max_gnomad_ac: int | None = None
    max_gnomad_hom_count: int | None = None
    max_gnomad_hemi_count: int | None = None
    # ClinVar P/LP keeps a variant past the frequency ceilings, as in the family search.
    clinvar_overrides_frequency: bool = False
    min_cadd: float | None = None
    min_revel: float | None = None
    min_spliceai: float | None = None
    sift: str | None = None
    polyphen: str | None = None
    classifications: list[str] = field(default_factory=list)
    review_tags: list[str] = field(default_factory=list)
    # When False (default), imputed entries (GLIMPSE2/SHAPEIT) are excluded.
    include_imputed: bool = False
    # Per-sample genotype constraints, AND-ed together: the variant must satisfy
    # every constraint. Each is (sample_id, mode) with mode in
    # {"het", "hom", "het_hom"}.
    sample_genotype_filters: list[tuple[str, str]] = field(default_factory=list)

    def has_review_filter(self) -> bool:
        return bool(self.classifications) or bool(self.review_tags)


@dataclass(slots=True)
class ExplorerScope:
    assembly_id: str
    assembly_name: str
    project_ids: list[str]


# --------------------------------------------------------------------------- #
# Scope resolution                                                            #
# --------------------------------------------------------------------------- #


async def _accessible_project_rows(session: AsyncSession, user: CurrentUser) -> list[dict[str, Any]]:
    """Return one row per project the user can access, with assembly metadata."""

    base_query = """
        SELECT
            p.id::text AS project_id,
            a.id::text AS assembly_id,
            a.assembly_name,
            a.version,
            sp.name AS species_name
        FROM projects p
        JOIN assemblies a ON a.id = p.assembly_id
        LEFT JOIN species sp ON sp.id = p.species_id
    """
    if is_admin_user(user):
        result = await session.execute(text(base_query))
    else:
        project_ids = user_metadata_project_ids(user)
        if not project_ids:
            return []
        result = await session.execute(
            text(base_query + " WHERE p.id IN :project_ids").bindparams(
                uuid_list_bindparam("project_ids")
            ),
            {"project_ids": uuid_values(project_ids)},
        )
    return [dict(row) for row in result.mappings().all()]


async def list_explorer_assemblies(
    session: AsyncSession, user: CurrentUser
) -> list[VariantExplorerAssemblyOut]:
    rows = await _accessible_project_rows(session, user)
    by_assembly: dict[str, VariantExplorerAssemblyOut] = {}
    for row in rows:
        assembly_id = row["assembly_id"]
        entry = by_assembly.get(assembly_id)
        if entry is None:
            by_assembly[assembly_id] = VariantExplorerAssemblyOut(
                assembly_id=assembly_id,
                assembly_name=row["assembly_name"],
                version=row.get("version"),
                species_name=row.get("species_name"),
                project_count=1,
            )
        else:
            entry.project_count += 1
    # Most projects first, then name.
    return sorted(
        by_assembly.values(),
        key=lambda item: (-item.project_count, item.assembly_name),
    )


async def list_explorer_samples(
    session: AsyncSession, user: CurrentUser, assembly_id: str | None = None
) -> list[str]:
    """Sample IDs the user can access (optionally restricted to an assembly).

    Powers the per-sample genotype filter picker.
    """

    rows = await _accessible_project_rows(session, user)
    if assembly_id:
        project_ids = sorted({row["project_id"] for row in rows if row["assembly_id"] == assembly_id})
    else:
        project_ids = sorted({row["project_id"] for row in rows})
    if not project_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT DISTINCT s.sample_id
            FROM samples s
            JOIN sample_projects sp ON sp.sample_id = s.id
            WHERE sp.project_id IN :project_ids
            ORDER BY s.sample_id
            """
        ).bindparams(uuid_list_bindparam("project_ids")),
        {"project_ids": uuid_values(project_ids)},
    )
    return [str(row[0]) for row in result.all()]


async def resolve_scope(
    session: AsyncSession, user: CurrentUser, assembly_id: str | None
) -> ExplorerScope | None:
    """Resolve the assembly + accessible project GUIDs for a request.

    Returns ``None`` when the user has no accessible projects (or none for the
    requested assembly), which the caller renders as an empty result set.
    """

    rows = await _accessible_project_rows(session, user)
    if not rows:
        return None

    if assembly_id is None:
        # Default to the assembly carrying the most accessible projects.
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["assembly_id"]] = counts.get(row["assembly_id"], 0) + 1
        assembly_id = max(counts, key=lambda key: (counts[key],))

    scoped = [row for row in rows if row["assembly_id"] == assembly_id]
    if not scoped:
        return None
    return ExplorerScope(
        assembly_id=assembly_id,
        assembly_name=scoped[0]["assembly_name"],
        project_ids=sorted({row["project_id"] for row in scoped}),
    )


# --------------------------------------------------------------------------- #
# Postgres review (tag / classification) bridge                               #
# --------------------------------------------------------------------------- #


async def _variant_ids_matching_reviews(
    session: AsyncSession,
    *,
    project_ids: Sequence[str],
    classifications: Sequence[str],
    tags: Sequence[str],
    limit: int,
) -> tuple[list[str], bool]:
    """variant_id strings tagged/classified in any accessible family, and whether more
    matched than ``limit``.

    At most ``limit`` ids, the first in variant_id order, so a capped list is the same
    list on every request; the query reads one row more to know whether more matched.
    """

    classifications = [value.strip() for value in classifications if str(value).strip()]
    tags = [value.strip() for value in tags if str(value).strip()]
    if not classifications and not tags:
        return [], False

    clauses = [
        "r.variant_id IS NOT NULL",
        "r.variant_id <> ''",
        "fp.project_id IN :project_ids",
    ]
    params: dict[str, Any] = {
        "project_ids": uuid_values(list(project_ids)),
        "limit": int(limit) + 1,
    }
    bind_params = [uuid_list_bindparam("project_ids")]

    if classifications:
        clauses.append(
            "(r.classification IN :classifications"
            " OR r.compound_het_classification IN :classifications)"
        )
        params["classifications"] = classifications
        bind_params.append(bindparam("classifications", expanding=True))
    if tags:
        clauses.append(
            "(EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.tags) AS t WHERE t IN :tags)"
            " OR EXISTS ("
            "SELECT 1 FROM jsonb_array_elements_text(r.compound_het_tags) AS t WHERE t IN :tags))"
        )
        params["tags"] = tags
        bind_params.append(bindparam("tags", expanding=True))

    query = text(
        f"""
        SELECT DISTINCT r.variant_id
        FROM small_variant_reviews r
        JOIN family_projects fp ON fp.family_id = r.family_id
        WHERE {' AND '.join(clauses)}
        ORDER BY r.variant_id
        LIMIT :limit
        """
    ).bindparams(*bind_params)
    result = await session.execute(query, params)
    rows = result.all()
    variant_ids = dict.fromkeys(
        str(row[0]).strip() for row in rows[:limit] if str(row[0] or "").strip()
    )
    return list(variant_ids), len(rows) > limit


async def _review_filter_variant_ids(
    session: AsyncSession, scope: ExplorerScope, filters: GlobalVariantFilters
) -> tuple[list[str] | None, bool]:
    """The review (tag/classification) filter as a variant_id allow-list, and whether more
    variants matched it than the list holds.

    The list is None without a review filter and [] when the filter matches nothing. It
    holds at most ``_MAX_TAG_FILTER_VARIANT_IDS`` ids (an extreme tag set would blow up
    the IN clause); past that a search through it can miss matches, which the export
    reports.
    """
    if not filters.has_review_filter():
        return None, False
    return await _variant_ids_matching_reviews(
        session,
        project_ids=scope.project_ids,
        classifications=filters.classifications,
        tags=filters.review_tags,
        limit=_MAX_TAG_FILTER_VARIANT_IDS,
    )


async def _review_display_map(
    session: AsyncSession,
    *,
    project_ids: Sequence[str],
    variant_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Per variant_id: union of tags + most-severe classification across families."""

    variant_ids = [str(value).strip() for value in variant_ids if str(value).strip()]
    if not variant_ids or not project_ids:
        return {}

    # The ids go as one array parameter, not an expanding IN (one parameter per id): an
    # export hydrates up to 50,001 rows, and asyncpg refuses a statement with more than
    # 32,767 parameters, which failed every export of a larger result.
    query = text(
        """
        SELECT
            r.variant_id,
            r.classification,
            r.tags,
            r.compound_het_classification,
            r.compound_het_tags,
            r.updated_at
        FROM small_variant_reviews r
        JOIN family_projects fp ON fp.family_id = r.family_id
        WHERE fp.project_id IN :project_ids
          AND r.variant_id = ANY(CAST(:variant_ids AS text[]))
        """
    ).bindparams(uuid_list_bindparam("project_ids"))
    result = await session.execute(
        query,
        {"project_ids": uuid_values(list(project_ids)), "variant_ids": variant_ids},
    )

    aggregated: dict[str, dict[str, Any]] = {}
    for row in result.mappings().all():
        variant_id = str(row["variant_id"] or "").strip()
        if not variant_id:
            continue
        entry = aggregated.setdefault(
            variant_id, {"tags": set(), "classifications": [], "updated_at": None}
        )
        for tag in (row.get("tags") or []):
            text_tag = str(tag or "").strip()
            if text_tag:
                entry["tags"].add(text_tag)
        for tag in (row.get("compound_het_tags") or []):
            text_tag = str(tag or "").strip()
            if text_tag:
                entry["tags"].add(text_tag)
        for value in (row.get("classification"), row.get("compound_het_classification")):
            text_value = str(value or "").strip()
            if text_value:
                entry["classifications"].append(text_value)
        updated_at = row.get("updated_at")
        if updated_at is not None and (
            entry["updated_at"] is None or updated_at > entry["updated_at"]
        ):
            entry["updated_at"] = updated_at
    return aggregated


# --------------------------------------------------------------------------- #
# ClickHouse annotation filtering                                             #
# --------------------------------------------------------------------------- #


def _annotation_index_clauses(
    filters: GlobalVariantFilters, params: dict[str, Any]
) -> list[str]:
    """The filters that hold for a variant as a whole, as WHERE conditions on the
    ``annotation_index`` (alias ``ai``), which pools a variant's transcripts: the gene, the
    rsID and the excluded ClinVar significances, as on the family page.

    The annotation filters that the family page applies per transcript are
    :func:`_transcript_annotation_condition`'s, and the panel is :func:`_panel_condition`.
    """

    clauses: list[str] = []

    gene_terms = [term.lower() for term in _split_terms(filters.gene)]
    if gene_terms:
        params["ann_genes"] = gene_terms
        clauses.append(
            "(arrayExists(g -> lowerUTF8(g) IN %(ann_genes)s, ai.gene_symbols)"
            " OR arrayExists(g -> lowerUTF8(g) IN %(ann_genes)s, ai.gene_ids))"
        )

    # Normalised as the stored terms are and as the family search reads them
    # (``Likely_benign`` is ``likely benign``): lowercased alone, an API value with an
    # underscore excluded nothing (CLIN-50).
    exclude_clinvar = list(_status_filter_terms(filters.exclude_clinvar))
    if exclude_clinvar:
        params["ann_exclude_clinvar"] = exclude_clinvar
        clauses.append("NOT hasAny(ai.clinvar_terms, %(ann_exclude_clinvar)s)")

    rsid = (filters.rsid or "").strip()
    if rsid:
        params["ann_rsid"] = rsid
        clauses.append("positionCaseInsensitive(ifNull(ai.rsid, ''), %(ann_rsid)s) > 0")

    return clauses


def _family_search_annotation_filters(filters: GlobalVariantFilters) -> SmallVariantQueryFilters:
    """The explorer's per-transcript annotation filters as the family search's filter set,
    so both pages build their condition with one function. The explorer offers no
    transcript filter."""
    return SmallVariantQueryFilters(
        page=1,
        page_size=1,
        impact=list(filters.impacts),
        effect=list(filters.effects),
        clinvar=list(filters.clinvar),
        clinvar_overrides_frequency=filters.clinvar_overrides_frequency,
        hgvsc=filters.hgvsc,
        hgvsp=filters.hgvsp,
        canonical_only=filters.canonical_only,
        mane_only=filters.mane_only,
        lof_only=filters.lof_only,
        max_gnomad_af=filters.max_gnomad_af,
        max_gnomad_exomes_af=filters.max_gnomad_exomes_af,
        max_gnomad_genomes_af=filters.max_gnomad_genomes_af,
        max_gnomad_popmax_af=filters.max_gnomad_popmax_af,
        max_topmed_af=filters.max_topmed_af,
        max_gnomad_ac=filters.max_gnomad_ac,
        max_gnomad_hom_count=filters.max_gnomad_hom_count,
        max_gnomad_hemi_count=filters.max_gnomad_hemi_count,
        min_cadd=filters.min_cadd,
        min_revel=filters.min_revel,
        min_spliceai=filters.min_spliceai,
        sift=filters.sift,
        polyphen=filters.polyphen,
    )


def _transcript_annotation_condition(
    filters: GlobalVariantFilters, params: dict[str, Any]
) -> str | None:
    """The condition one transcript of a variant must meet, on a row of the
    ``annotations`` table (alias ``a``), or None without such a filter.

    It is the family search's own condition (``_small_annotation_filter_condition``), so
    both pages keep and drop the same variants: the impact, consequence, ClinVar, HGVS,
    frequency ceilings (with the ClinVar P/LP rescue), in-silico minimums and Canonical,
    MANE and LoF only must hold on one transcript together. The explorer tested each
    against the annotation index, which pools a variant's transcripts, so with *MANE only*
    and impact HIGH it kept a variant whose HIGH transcript is not its MANE transcript,
    which the family page drops (CLIN-29).
    """
    return _small_annotation_filter_condition(
        _family_search_annotation_filters(filters), params=params
    )


def _locus_clauses(
    filters: GlobalVariantFilters, params: dict[str, Any], *, alias: str = ""
) -> list[str]:
    """The chromosome, start and end filters on the ``chrom`` and ``pos`` columns of
    ``alias`` (none: the ``entries`` table)."""
    prefix = f"{alias}." if alias else ""
    clauses: list[str] = []
    if filters.chromosome:
        params["chromosome"] = filters.chromosome
        clauses.append(f"{prefix}chrom = %(chromosome)s")
    if filters.start is not None:
        params["start"] = filters.start
        clauses.append(f"{prefix}pos >= %(start)s")
    if filters.end is not None:
        params["end"] = filters.end
        clauses.append(f"{prefix}pos <= %(end)s")
    return clauses


# --------------------------------------------------------------------------- #
# The gene panel, as the family page applies it                               #
# --------------------------------------------------------------------------- #


async def _panel_constraints(
    session: AsyncSession, scope: ExplorerScope, filters: GlobalVariantFilters
) -> PanelFilterConstraints | None:
    """The panel's genes and regions as the family page reads them, for the explorer's
    assembly; None without a panel filter.

    It is the family page's own reading (``_fetch_panel_constraints``): the panel's genes
    and the names of its stored regions, the regions stored for this assembly (PanelApp's
    coordinates), and each gene's loci in this assembly's gene reference. An id that is no
    UUID is refused (400), and a panel that does not exist answers 404, as on the family
    page. The explorer used to read the panel's gene symbols alone: a panel of regions only
    filtered nothing, and a variant in one of a panel's regions was left out (CLIN-48).
    """
    if not filters.panel_id:
        return None
    return await _fetch_panel_constraints(session, filters.panel_id, assembly_id=scope.assembly_id)


def _panel_keeps_nothing(panel_constraints: PanelFilterConstraints | None) -> bool:
    """A panel with neither genes nor regions for the assembly keeps no variant, as on the
    family page."""
    return panel_constraints is not None and not (
        panel_constraints.genes or panel_constraints.regions
    )


def _region_key_condition(
    assembly_name: str, regions: Sequence[Region], *, prefix: str, params: dict[str, Any]
) -> str | None:
    """``key IN`` the variants of the assembly that overlap one of ``regions`` by the family
    page's rule (``_small_region_filter_condition``), each variant at its position in the
    annotation index; None without a region.

    Testing each row against every region took minutes across every family of the cohort
    for a panel of a thousand genes. Here the annotation index is joined to the regions on
    64-kb windows (``_small_region_window_rows``), the join the family page makes over a
    family's own rows (``_small_family_region_condition``): each region is listed under
    every window it touches and each variant under every window its REF touches, and a pair
    that shares a window is kept when the two overlap by that rule. The regions are read as
    the family page reads them (``_small_region_bounds``).
    """
    region_rows = _small_region_window_rows(regions, prefix=prefix, params=params)
    if region_rows is None:
        return None
    columns, windows = _small_variant_windows("ai")
    annotation_index = _small_table_name(assembly_name, "variants/annotation_index")
    return (
        f"key IN (SELECT v.key FROM (SELECT ai.key AS key, {columns}"
        f" FROM {annotation_index} AS ai {windows})"
        f" AS v INNER JOIN ({region_rows}) AS r {_SMALL_REGION_WINDOW_MATCH})"
    )


def _panel_condition(
    scope: ExplorerScope, panel_constraints: PanelFilterConstraints, params: dict[str, Any]
) -> str | None:
    """The family page's panel condition (``_small_panel_filter_condition``) on the
    explorer's entry rows (alias ``e``): a variant in one of the panel's regions, or
    annotated with one of its genes in the row's own gene symbols or in the gene index.
    Every panel is matched both ways, whatever its size, as on the family page: a panel of
    more than 1,000 regions, such as the Mendeliome, was matched on gene symbols alone
    (owner decision 2026-10-14). Only the regions are tested another way, with the same
    answer (:func:`_region_key_condition`)."""

    def region_condition(
        regions: Sequence[Region], *, prefix: str, params: dict[str, Any]
    ) -> str | None:
        return _region_key_condition(scope.assembly_name, regions, prefix=prefix, params=params)

    return _small_panel_filter_condition(
        scope,
        # No locus: the gene index is read for the whole assembly; the entry rows carry
        # the locus filter.
        SmallVariantQueryFilters(page=1, page_size=1),
        panel_constraints,
        params=params,
        region_condition=region_condition,
    )


def _entries_where(
    scope: ExplorerScope,
    filters: GlobalVariantFilters,
    params: dict[str, Any],
    *,
    tag_variant_ids: Sequence[str] | None,
    panel_constraints: PanelFilterConstraints | None = None,
) -> list[str]:
    """The WHERE clauses on the ``entries`` rows (alias ``e``) for these filters.
    ``panel_constraints`` is the panel as :func:`_panel_constraints` read it."""
    clauses = ["sign = 1", "project_guid IN %(project_guids)s"]
    params["project_guids"] = tuple(scope.project_ids)

    if tag_variant_ids is not None:
        # An empty set means "review filter matched nothing".
        params["tag_variant_ids"] = tuple(tag_variant_ids) or ("",)
        clauses.append("variantId IN %(tag_variant_ids)s")

    if not filters.include_imputed:
        params["imputed_sources"] = IMPUTED_SMALL_VARIANT_SOURCES
        clauses.append("lowerUTF8(source) NOT IN %(imputed_sources)s")

    sample_source = (
        "" if filters.include_imputed else " AND lowerUTF8(source) NOT IN %(imputed_sources)s"
    )
    entries_table = _small_table_name(scope.assembly_name, "entries")
    for index, (sample_id, mode) in enumerate(filters.sample_genotype_filters):
        sample_id = str(sample_id).strip()
        if not sample_id:
            continue
        sample_param = f"sample_gt_{index}"
        params[sample_param] = sample_id
        normalized_mode = str(mode or "het_hom").strip().lower()
        if normalized_mode == "hom":
            genotype_condition = _gt_is_hom("s_gt", params)
        elif normalized_mode == "het":
            genotype_condition = _gt_is_het("s_gt", params)
        else:  # het_hom: any carrier
            genotype_condition = _gt_is_carrier("s_gt", params)
        # Each sample is its own membership subquery; AND-ing them means the
        # variant must satisfy every per-sample genotype constraint.
        clauses.append(
            "key IN (SELECT key FROM "
            f"{entries_table}"
            " ARRAY JOIN `calls.sampleId` AS s_id, `calls.gt` AS s_gt"
            " WHERE sign = 1 AND project_guid IN %(project_guids)s"
            f" AND s_id = %({sample_param})s"
            f" AND ({genotype_condition}){sample_source})"
        )

    annotation_clauses = _annotation_index_clauses(filters, params)
    if annotation_clauses:
        annotation_index = _small_table_name(scope.assembly_name, "variants/annotation_index")
        clauses.append(
            "key IN (SELECT DISTINCT ai.key FROM "
            f"{annotation_index} AS ai WHERE {' AND '.join(annotation_clauses)})"
        )

    if panel_constraints is not None:
        # A panel with neither genes nor regions keeps nothing ("0"); the search returns
        # before it asks ClickHouse.
        clauses.append(_panel_condition(scope, panel_constraints, params) or "0")

    transcript_condition = _transcript_annotation_condition(filters, params)
    if transcript_condition:
        # One transcript row must meet every per-transcript filter. The locus narrows the
        # scan of the per-transcript table, as in the family search; a variant's position
        # is the same in every table, so it changes no result.
        annotations = _small_table_name(scope.assembly_name, "variants/annotations")
        transcript_where = [*_locus_clauses(filters, params, alias="a"), transcript_condition]
        clauses.append(
            f"key IN (SELECT a.key FROM {annotations} AS a WHERE {' AND '.join(transcript_where)})"
        )

    variant_type = (filters.variant_type or "").strip().upper()
    if variant_type == "SNV":
        clauses.append("length(ref) = 1 AND length(alt) = 1")
    elif variant_type == "MNV":
        clauses.append("length(ref) = length(alt) AND length(ref) > 1")
    elif variant_type == "INDEL":
        clauses.append("NOT (length(ref) = 1 AND length(alt) = 1)")

    clauses.extend(_locus_clauses(filters, params))

    return clauses


# --------------------------------------------------------------------------- #
# Main aggregated search                                                      #
# --------------------------------------------------------------------------- #


async def search_global_small_variants(
    session: AsyncSession,
    *,
    user: CurrentUser,
    filters: GlobalVariantFilters,
    assembly_id: str | None = None,
    cursor: str | None = None,
    page_size: int = 50,
    sort: str = _DEFAULT_SORT,
    order: str = "desc",
) -> GlobalVariantPageOut:
    page_size = max(1, min(page_size, 200))
    # Normalize to the canonical sort/order actually used so the cursor records exactly that
    # (an unknown sort would otherwise be queried as the default but stored verbatim, making
    # every subsequent cursor self-reject).
    sort = sort if sort in _SORT_EXPR else _DEFAULT_SORT
    order = "asc" if str(order).lower() == "asc" else "desc"
    sort_expr = _SORT_EXPR[sort]
    direction = "ASC" if order == "asc" else "DESC"

    scope = await resolve_scope(session, user, assembly_id)
    if scope is None:
        return GlobalVariantPageOut(total=0, page_size=page_size, assembly_id=assembly_id)

    empty_page = GlobalVariantPageOut(
        total=0,
        page_size=page_size,
        assembly_id=scope.assembly_id,
        assembly_name=scope.assembly_name,
    )
    # The panel first, as on the family page: a panel that does not exist answers 404.
    panel_constraints = await _panel_constraints(session, scope, filters)
    if _panel_keeps_nothing(panel_constraints):
        return empty_page
    # A capped tag set is reported by the export only: the page has no field for it.
    tag_variant_ids, _review_filter_capped = await _review_filter_variant_ids(
        session, scope, filters
    )
    if tag_variant_ids == []:
        return empty_page

    entries_table = _small_table_name(scope.assembly_name, "entries")

    params: dict[str, Any] = {}
    where_clauses = _entries_where(
        scope,
        filters,
        params,
        tag_variant_ids=tag_variant_ids,
        panel_constraints=panel_constraints,
    )
    where_sql = " AND ".join(where_clauses)
    has_carrier = f"arrayExists(g -> {_gt_is_carrier('g', params)}, `calls.gt`)"

    # Distinct variant keys with at least one carrier, counted only up to the cap (one
    # extra row distinguishes "exactly cap" from "more than cap"). The inner GROUP BY key
    # + LIMIT bounds the work to O(cap) instead of an unbounded uniqExact over the cohort.
    count_rows = await execute_clickhouse(
        f"""
        SELECT count()
        FROM (
            SELECT key
            FROM {entries_table} AS e
            WHERE {where_sql}
              AND {has_carrier}
            GROUP BY key
            LIMIT {_EXPLORER_COUNT_CAP + 1}
        )
        """,
        params,
    )
    raw_count = int(count_rows[0][0]) if count_rows and count_rows[0] else 0
    total, total_is_estimated = _bounded_total(raw_count)
    if total == 0:
        return empty_page

    # Keyset (seek) page: the cursor carries the previous page's last (sort_value, xpos,
    # key); a stale cursor (sort/order/filters, or the panel's genes and regions, changed)
    # decodes to None and we start at top.
    fingerprint = _filters_fingerprint(filters, assembly_id, panel_constraints)
    seek_having: str | None = None
    seek_params: dict[str, Any] | None = None
    decoded = _decode_cursor(cursor, sort, order, fingerprint) if cursor else None
    if decoded is not None:
        cur_sort, cur_xpos, cur_key = decoded
        seek_having = _seek_having(sort_expr, direction)
        seek_params = {"cur_sort": cur_sort, "cur_xpos": cur_xpos, "cur_key": cur_key}

    # Fetch one extra row as a "has next page" probe so the last page never advertises a
    # cursor that would land on an empty page (which a bare len==page_size check would).
    variants, row_cursors = await _fetch_variant_rows(
        session,
        scope=scope,
        entries_table=entries_table,
        where_sql=where_sql,
        params=params,
        sort_expr=sort_expr,
        direction=direction,
        limit=page_size + 1,
        seek_having=seek_having,
        seek_params=seek_params,
    )

    next_cursor: str | None = None
    if len(variants) > page_size:
        next_cursor = _encode_cursor(sort, order, fingerprint, *row_cursors[page_size - 1])
        variants = variants[:page_size]

    return GlobalVariantPageOut(
        total=total,
        total_is_estimated=total_is_estimated,
        page_size=page_size,
        next_cursor=next_cursor,
        assembly_id=scope.assembly_id,
        assembly_name=scope.assembly_name,
        variants=variants,
    )


async def _fetch_variant_rows(
    session: AsyncSession,
    *,
    scope: Any,
    entries_table: str,
    where_sql: str,
    params: dict[str, Any],
    sort_expr: str,
    direction: str,
    limit: int,
    seek_having: str | None = None,
    seek_params: dict[str, Any] | None = None,
) -> tuple[list[GlobalVariantRowOut], list[tuple[int, int, int]]]:
    """Run the aggregated variant query and hydrate it into row models.

    Shared by the paginated search and the CSV export so both return identical
    columns. ``seek_having`` (with ``seek_params``) keyset-pages forward from a cursor;
    omit it for the first page / full export. Returns ``(rows, row_cursors)`` where
    ``row_cursors[i]`` is the ``(sort_value, xpos, key)`` of ``rows[i]`` for building the
    next cursor.
    """

    page_params = dict(params)
    page_params["limit"] = limit
    is_hom = _gt_is_hom("gt", page_params)
    is_carrier = _gt_is_carrier("gt", page_params)
    if seek_params:
        page_params.update(seek_params)
    having_sql = f"HAVING {seek_having}" if seek_having else ""

    rows = await execute_clickhouse(
        f"""
        SELECT
            key,
            any(variant_id) AS variant_id,
            any(chrom) AS chrom,
            min(pos) AS pos,
            any(ref) AS ref,
            any(alt) AS alt,
            any(rsid) AS rsid,
            min(xpos) AS xpos,
            uniqExact(family_guid) AS families,
            uniqExactIf(sample_id, is_hom) AS hom_samples,
            uniqExactIf(sample_id, NOT is_hom) AS het_samples,
            uniqExact(sample_id) AS total_samples,
            arrayDistinct(arrayFlatten(groupArray(gene_symbols))) AS gene_symbols
        FROM (
            SELECT
                key,
                variantId AS variant_id,
                chrom,
                pos,
                ref,
                alt,
                rsid,
                xpos,
                family_guid,
                gene_symbols,
                sample_id,
                ({is_hom}) AS is_hom
            FROM {entries_table} AS e
            ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
            WHERE {where_sql}
              AND {is_carrier}
        )
        GROUP BY key
        {having_sql}
        ORDER BY {sort_expr} {direction}, xpos ASC, key ASC
        LIMIT %(limit)s
        """,
        page_params,
    )

    # Per-row keyset cursor (sort_value, xpos, key), parallel to `variants`, so the caller
    # can take the boundary row's cursor after an N+1 probe trim.
    row_cursors = [
        (int(row[_SORT_RAW_COL[sort_expr]]), int(row[_RAW_XPOS_COL]), int(row[_RAW_KEY_COL]))
        for row in rows
    ]

    keys = [int(row[0]) for row in rows]
    variant_ids = [str(row[1]) for row in rows]

    annotation_map = await _fetch_annotation_display(scope.assembly_name, keys)
    review_map = await _review_display_map(
        session, project_ids=scope.project_ids, variant_ids=variant_ids
    )

    variants: list[GlobalVariantRowOut] = []
    for row in rows:
        key = int(row[0])
        variant_id = str(row[1])
        chrom = str(row[2] or "")
        pos = int(row[3] or 0)
        ref = row[4]
        alt = row[5]
        rsid = row[6] or None
        annotation = annotation_map.get(key, {})
        review = review_map.get(variant_id, {})

        # Gene symbols come from `entries` (original case); the annotation_index
        # casefolds them. "-" is an intergenic placeholder we drop.
        gene_symbols = [
            value for value in (row[12] or []) if value and value != "-"
        ]
        impacts = annotation.get("impacts", [])
        effects = annotation.get("effects", [])
        # The transcript the row's consequence and HGVS come from: [effect, HGVSc, HGVSp].
        shown = annotation.get("shown", [])
        clinvar_terms = annotation.get("clinvar_terms", [])
        classification = _most_severe(review.get("classifications", []), _CLASSIFICATION_RANK)
        impact = _most_severe(impacts, _IMPACT_RANK) or _first_non_empty(impacts)

        variants.append(
            GlobalVariantRowOut(
                key=str(key),
                variant_id=variant_id,
                chr=chrom,
                pos=pos,
                ref=ref,
                alt=alt,
                rsid=rsid,
                type=_classify_type(ref, alt),
                gene=_first_non_empty(gene_symbols)
                or _first_non_empty(annotation.get("gene_ids", [])),
                gene_symbols=gene_symbols,
                impact=impact.upper() if impact else None,
                consequence=_first_non_empty(shown[:1]) or _first_non_empty(effects),
                effects=effects,
                hgvsc=_first_non_empty(shown[1:2]) if shown else _first_non_empty(annotation.get("hgvsc_values", [])),
                hgvsp=_first_non_empty(shown[2:3]) if shown else _first_non_empty(annotation.get("hgvsp_values", [])),
                clinvar=_most_severe(clinvar_terms, _CLINVAR_RANK) or _first_non_empty(clinvar_terms),
                classification=classification,
                tags=sorted(review.get("tags", set())),
                total_samples=int(row[11] or 0),
                het_samples=int(row[10] or 0),
                hom_samples=int(row[9] or 0),
                total_families=int(row[8] or 0),
                last_seen=review.get("updated_at"),
            )
        )

    return variants, row_cursors


# Hard cap on rows pulled into a single CSV export, as for the family exports. Generous
# enough for any realistic filtered result set, but bounds memory/response size for an
# unfiltered "download everything" request. A larger result is reported, not cut silently.
_MAX_EXPORT_ROWS = 50_000


@dataclass(slots=True)
class GlobalVariantExport:
    """The rows of a Variant Explorer CSV export, and why, if at all, they are not every
    filtered variant.

    An export is never silently partial, as for the family exports (#512, #725): the route
    names the file and sets the X-CoGA-Export-* headers from this.
    ``truncated_reason`` is ``TRUNCATED_BY_ROW_LIMIT`` when more variants matched than
    ``limit`` (the file holds the first ``limit``), and
    ``TRUNCATED_BY_REVIEW_FILTER_LIMIT`` when the tag / classification filter matched more
    variants than the search takes (a match can be missing however few rows the file has).
    """

    assembly_name: str | None
    rows: list[GlobalVariantRowOut]
    truncated_reason: str | None
    limit: int

    @property
    def truncated(self) -> bool:
        return self.truncated_reason is not None


async def export_global_small_variants(
    session: AsyncSession,
    *,
    user: CurrentUser,
    filters: GlobalVariantFilters,
    assembly_id: str | None = None,
    sort: str = _DEFAULT_SORT,
    order: str = "desc",
    limit: int = _MAX_EXPORT_ROWS,
) -> GlobalVariantExport:
    """Fetch up to ``limit`` filtered variants for CSV export, and say whether that is all.

    Applies the same filtering and sorting as the paginated search, without pagination.
    It asks for one row more than ``limit``: when that row comes back, more variants
    matched than the file holds. A capped tag / classification filter makes the export
    truncated too, and that reason wins: it says the file may lack matches anywhere, not
    only past row ``limit``.
    """

    limit = max(1, min(limit, _MAX_EXPORT_ROWS))
    sort_expr = _SORT_EXPR.get(sort, _SORT_EXPR[_DEFAULT_SORT])
    direction = "ASC" if str(order).lower() == "asc" else "DESC"

    scope = await resolve_scope(session, user, assembly_id)
    if scope is None:
        return GlobalVariantExport(assembly_name=None, rows=[], truncated_reason=None, limit=limit)

    # The panel first, as on the family page and in the search.
    panel_constraints = await _panel_constraints(session, scope, filters)
    if _panel_keeps_nothing(panel_constraints):
        return GlobalVariantExport(
            assembly_name=scope.assembly_name, rows=[], truncated_reason=None, limit=limit
        )
    tag_variant_ids, review_filter_capped = await _review_filter_variant_ids(
        session, scope, filters
    )
    review_filter_cut = TRUNCATED_BY_REVIEW_FILTER_LIMIT if review_filter_capped else None
    if tag_variant_ids == []:
        return GlobalVariantExport(
            assembly_name=scope.assembly_name,
            rows=[],
            truncated_reason=review_filter_cut,
            limit=limit,
        )

    entries_table = _small_table_name(scope.assembly_name, "entries")
    params: dict[str, Any] = {}
    where_clauses = _entries_where(
        scope,
        filters,
        params,
        tag_variant_ids=tag_variant_ids,
        panel_constraints=panel_constraints,
    )
    where_sql = " AND ".join(where_clauses)

    variants, _row_cursors = await _fetch_variant_rows(
        session,
        scope=scope,
        entries_table=entries_table,
        where_sql=where_sql,
        params=params,
        sort_expr=sort_expr,
        direction=direction,
        limit=limit + 1,
    )
    row_cut = TRUNCATED_BY_ROW_LIMIT if len(variants) > limit else None
    return GlobalVariantExport(
        assembly_name=scope.assembly_name,
        rows=variants[:limit],
        truncated_reason=review_filter_cut or row_cut,
        limit=limit,
    )


async def _fetch_annotation_display(
    assembly_name: str, keys: Sequence[int]
) -> dict[int, dict[str, list[str]]]:
    if not keys:
        return {}
    annotation_index = _small_table_name(assembly_name, "variants/annotation_index")
    rows = await execute_clickhouse(
        f"""
        SELECT
            key,
            arrayDistinct(arrayFlatten(groupArray(gene_ids))) AS gene_ids,
            arrayDistinct(arrayFlatten(groupArray(impacts))) AS impacts,
            arrayDistinct(arrayFlatten(groupArray(effects))) AS effects,
            arrayDistinct(arrayFlatten(groupArray(clinvar_terms))) AS clinvar_terms,
            arrayDistinct(arrayFlatten(groupArray(hgvsc_values))) AS hgvsc_values,
            arrayDistinct(arrayFlatten(groupArray(hgvsp_values))) AS hgvsp_values
        FROM {annotation_index}
        WHERE key IN %(keys)s
        GROUP BY key
        """,
        {"keys": tuple(keys)},
    )
    # gene_symbols are taken from the entries side (original case); the annotation
    # index casefolds them and the row loop never reads them here, so they are not
    # aggregated.
    result: dict[int, dict[str, list[str]]] = {}
    for row in rows:
        result[int(row[0])] = {
            "gene_ids": [str(value) for value in (row[1] or [])],
            "impacts": [str(value) for value in (row[2] or [])],
            "effects": [str(value) for value in (row[3] or [])],
            "clinvar_terms": [str(value) for value in (row[4] or [])],
            "hgvsc_values": [str(value) for value in (row[5] or [])],
            "hgvsp_values": [str(value) for value in (row[6] or [])],
        }
    # The row shows the most severe impact over the transcripts; its consequence and HGVS
    # come from a transcript of that impact (MANE Select, then canonical, first), never
    # the first distinct value of each, which paired one transcript's consequence with
    # another's HGVS (CLIN-50).
    annotations = _small_table_name(assembly_name, "variants/annotations")
    transcripts = await execute_clickhouse(
        f"""
        SELECT key, impact, effects, hgvsc, hgvsp, mane_select, canonical
        FROM {annotations}
        WHERE key IN %(keys)s
        """,
        {"keys": tuple(keys)},
    )
    shown: dict[int, tuple[tuple[int, bool, bool], list[str]]] = {}
    for key, impact, effects, hgvsc, hgvsp, mane_select, canonical in transcripts:
        rank = (_IMPACT_RANK.get(str(impact or "").lower(), 0), bool(mane_select), bool(canonical))
        if int(key) not in shown or rank > shown[int(key)][0]:
            first_effect = next((str(value) for value in (effects or []) if value), "")
            shown[int(key)] = (rank, [first_effect, str(hgvsc or ""), str(hgvsp or "")])
    for key, (_rank, (effect, hgvsc, hgvsp)) in shown.items():
        if key in result:
            result[key]["shown"] = [effect, hgvsc, hgvsp]
    return result


# --------------------------------------------------------------------------- #
# Carrier drill-down                                                          #
# --------------------------------------------------------------------------- #


# Cap on raw carrier rows pulled for the carrier drill-down. Far above any
# realistic carrier count for one variant; above it the response is flagged
# truncated so the UI can say "showing the first N carriers".
_VARIANT_CARRIER_ROW_LIMIT = 2000


async def get_variant_carriers(
    session: AsyncSession,
    *,
    user: CurrentUser,
    variant_key: int,
    assembly_id: str | None = None,
    genotype: str | None = None,
    include_imputed: bool = False,
) -> VariantCarriersOut:
    scope = await resolve_scope(session, user, assembly_id)
    if scope is None:
        return VariantCarriersOut(key=str(variant_key))

    entries_table = _small_table_name(scope.assembly_name, "entries")
    params: dict[str, Any] = {
        "project_guids": tuple(scope.project_ids),
        "key": variant_key,
    }
    is_carrier = _gt_is_carrier("gt", params)
    genotype_clause = ""
    normalized_genotype = (genotype or "").strip().lower()
    if normalized_genotype in {"hom", "homozygous"}:
        genotype_clause = f" AND {_gt_is_hom('gt', params)}"
    elif normalized_genotype in {"het", "heterozygous"}:
        genotype_clause = f" AND {_gt_is_het('gt', params)}"
    source_clause = ""
    if not include_imputed:
        params["imputed_sources"] = IMPUTED_SMALL_VARIANT_SOURCES
        source_clause = " AND lowerUTF8(source) NOT IN %(imputed_sources)s"

    params["carrier_limit"] = _VARIANT_CARRIER_ROW_LIMIT + 1
    rows = await execute_clickhouse(
        f"""
        SELECT family_guid, sample_id, gt, variantId
        FROM {entries_table}
        ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
        WHERE sign = 1
          AND project_guid IN %(project_guids)s
          AND key = %(key)s
          AND {is_carrier}{genotype_clause}{source_clause}
        ORDER BY family_guid, sample_id
        LIMIT %(carrier_limit)s
        """,
        params,
    )
    truncated = len(rows) > _VARIANT_CARRIER_ROW_LIMIT
    if truncated:
        rows = rows[:_VARIANT_CARRIER_ROW_LIMIT]

    # Dedupe to one carrier record per (family, sample); prefer hom over het.
    carriers: dict[tuple[str, str], dict[str, Any]] = {}
    variant_id: str | None = None
    for family_guid, sample_id, gt, row_variant_id in rows:
        family_guid = str(family_guid)
        sample_id = str(sample_id)
        gt = str(gt)
        variant_id = variant_id or (str(row_variant_id) if row_variant_id else None)
        is_hom = classify_genotype(gt) == HOM_ALT
        existing = carriers.get((family_guid, sample_id))
        if existing is None or (is_hom and not existing["is_hom"]):
            carriers[(family_guid, sample_id)] = {
                "family_guid": family_guid,
                "sample_id": sample_id,
                "genotype": gt,
                "is_hom": is_hom,
            }

    if not carriers:
        return VariantCarriersOut(key=str(variant_key), variant_id=variant_id)

    family_uuids = sorted({record["family_guid"] for record in carriers.values()})
    sample_keys = sorted({record["sample_id"] for record in carriers.values()})

    family_meta = await _fetch_family_meta(session, scope.project_ids, family_uuids)
    sample_meta = await _fetch_sample_meta(session, sample_keys)

    groups: dict[str, VariantCarrierFamilyGroupOut] = {}
    het_total = 0
    hom_total = 0
    for record in carriers.values():
        family_guid = record["family_guid"]
        meta = family_meta.get(family_guid)
        if meta is None:
            # Family not visible under the user's accessible projects: skip.
            continue
        group = groups.get(family_guid)
        if group is None:
            group = VariantCarrierFamilyGroupOut(
                family_id=meta["family_name"],
                family_uuid=family_guid,
                project_id=meta.get("project_id"),
                project_name=meta.get("project_name"),
            )
            groups[family_guid] = group

        sample_info = sample_meta.get(record["sample_id"]) or {}
        is_hom = record["is_hom"]
        if is_hom:
            hom_total += 1
        else:
            het_total += 1
        group.samples.append(
            VariantCarrierSampleOut(
                sample_id=record["sample_id"],
                individual_name=sample_info.get("individual_name"),
                role=sample_info.get("role"),
                genotype=record["genotype"],
                zygosity="homozygous" if is_hom else "heterozygous",
                family_id=meta["family_name"],
                family_uuid=family_guid,
                project_id=meta.get("project_id"),
                project_name=meta.get("project_name"),
                phenotype_summary=sample_info.get("phenotype_summary"),
            )
        )

    ordered_groups: list[VariantCarrierFamilyGroupOut] = []
    for group in sorted(groups.values(), key=lambda item: item.family_id.lower()):
        group.samples.sort(key=lambda sample: sample.sample_id.lower())
        group.carrier_count = len(group.samples)
        ordered_groups.append(group)

    return VariantCarriersOut(
        key=str(variant_key),
        variant_id=variant_id,
        total_families=len(ordered_groups),
        total_samples=het_total + hom_total,
        het_samples=het_total,
        hom_samples=hom_total,
        truncated=truncated,
        families=ordered_groups,
    )


async def _fetch_family_meta(
    session: AsyncSession, project_ids: Sequence[str], family_uuids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    if not family_uuids or not project_ids:
        return {}
    query = text(
        """
        SELECT
            f.id::text AS family_uuid,
            f.family_id AS family_name,
            p.id::text AS project_id,
            p.name AS project_name
        FROM families f
        JOIN family_projects fp ON fp.family_id = f.id
        JOIN projects p ON p.id = fp.project_id
        WHERE f.id IN :family_uuids
          AND fp.project_id IN :project_ids
        ORDER BY f.family_id, p.name
        """
    ).bindparams(
        uuid_list_bindparam("family_uuids"),
        uuid_list_bindparam("project_ids"),
    )
    result = await session.execute(
        query,
        {
            "family_uuids": uuid_values(list(family_uuids)),
            "project_ids": uuid_values(list(project_ids)),
        },
    )
    meta: dict[str, dict[str, Any]] = {}
    for row in result.mappings().all():
        family_uuid = str(row["family_uuid"])
        # First accessible project wins (rows are ordered deterministically).
        meta.setdefault(
            family_uuid,
            {
                "family_name": row["family_name"],
                "project_id": row["project_id"],
                "project_name": row["project_name"],
            },
        )
    return meta


async def _fetch_sample_meta(
    session: AsyncSession, sample_keys: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Resolve ClickHouse ``sampleId`` values (sample name OR UUID) to metadata."""

    if not sample_keys:
        return {}
    query = text(
        """
        SELECT
            s.sample_id AS sample_name,
            s.id::text AS sample_uuid,
            fm.role,
            fm.affected,
            fm.clinical_status
        FROM samples s
        LEFT JOIN family_members fm
            ON fm.sample_id = s.id AND fm.family_id = s.family_id AND fm.active
        WHERE s.sample_id IN :sample_keys
           OR s.id::text IN :sample_keys
        """
    ).bindparams(bindparam("sample_keys", expanding=True))
    result = await session.execute(query, {"sample_keys": list(sample_keys)})
    meta: dict[str, dict[str, Any]] = {}
    for row in result.mappings().all():
        clinical_status = str(row.get("clinical_status") or "").strip()
        phenotype = None
        if clinical_status and clinical_status != "unknown":
            phenotype = clinical_status
        elif row.get("affected"):
            phenotype = "affected"
        info = {"individual_name": str(row["sample_name"]), "role": row.get("role"), "phenotype_summary": phenotype}
        # Key by both the human name and the UUID so either stored form resolves.
        meta[str(row["sample_name"])] = info
        meta[str(row["sample_uuid"])] = info
    return meta
