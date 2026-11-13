"""Global Small Variant Explorer aggregation engine.

This service powers a *variant-centric* view of small variants (SNVs/indels)
aggregated across every project a user can access, independent of family
boundaries. Each result row is one unique variant (keyed by the ClickHouse
``UInt64`` variant key) summarising carrier counts across the whole accessible
cohort; the gene, transcript and cohort-frequency views (``variant_explorer_views``)
summarise the same rows by gene, by transcript and by frequency class.

Design notes
------------
* **Counts from the variant carrier summary.** The carriers of each variant are kept per
  family as its rows are written (``variant_carrier_summary``): a page groups one summary
  row per variant, project set and class of rows, not every family's ``entries`` row, so
  it reads the same amount of data whatever the number of families. The summary is
  grouped in its sort order (``xpos, key``), so a query holds one variant's sums at a time.
  A family whose variants are being written, or changed since the summary was last brought
  up to date, is named in ``pending_families``.
* **Permissions.** Every query is scoped to the project GUIDs the user can
  access (admins -> all projects, viewers -> ``metadata_project_ids``).
  ``project_guid`` in ClickHouse equals the Postgres project UUID string and
  ``family_guid`` equals the family UUID string. A summary row names the projects of its
  family, and a page keeps the rows of families linked to a project in scope.
* **Tags / classification** live in Postgres (``small_variant_reviews``,
  per-family). We bridge to ClickHouse on ``variant_id`` (TEXT, e.g.
  ``1-1000-A-T``) — the same bridge the family page uses — because
  ``variant_key`` is only stored when it fits a signed BIGINT.
* **Filters.** Each filter the explorer shares with the family search is the family
  search's own SQL (``clickhouse_variant_queries``), read on each summary row as its
  family page reads the rows of that class, so a row counts the carriers of the family
  pages that keep the variant (``_carriers_where``).
* **The samples their family shows.** A row holds a call of every sample of the VCF it was
  imported from, also of a member taken out of the family since. The summary counts, in
  each family's rows, the calls of the family's active members alone, as the family search
  does (``_visible_clickhouse_sample_ids``), and so do the carriers and the sample genotype
  filter (the owner's decision of 2026-11-06, ``_shown_members``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

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
    _xpos,
    _clickhouse_sample_ids,
    _small_annotation_filter_clauses,
    _small_annotation_key_membership_condition,
    _small_callset_clauses,
    _small_locus_clauses,
    _small_panel_filter_condition,
    _small_region_window_rows,
    _small_row_filter_clauses,
    _small_sample_call_condition,
    _small_variant_windows,
)
from .clickhouse_variant_records import PanelFilterConstraints, Region
from .family_variant_filters import (
    SmallVariantQueryFilters,
    SmallVariantSampleFilter,
    parse_genotype_filter,
)
from .genotypes import ALT_CLASSES, HET, HOM_ALT, classify_genotype, clickhouse_genotype_condition
from .access_control import CurrentUser, has_superuser_rights, user_metadata_project_ids
from .review_pg_utils import _as_utc, review_class_filter, review_class_label, review_class_label_sql
from .variant_carrier_summary import CARRIERS_SUFFIX, pending_variant_carrier_families
from .variant_ranking_cache import panel_members_digest


# Carrier / homozygous / heterozygous conditions on a genotype column, from the one
# genotype classification: a carrier has an ALT allele (a haploid "0" is not one),
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
# Cap the cohort-wide distinct-key count so the explorer never pays an unbounded
# uniqExact over millions of keys per page. Past the cap the total is reported as the cap
# with total_is_estimated=True (the UI renders "N+").
_EXPLORER_COUNT_CAP = 10_000


def _bounded_total(raw_count: int) -> tuple[int, bool]:
    """Map the (cap+1)-capped distinct-key count to (total, is_estimated): past the cap the
    true total is unknown, so report the cap with the estimate flag set (UI shows "N+")."""
    if raw_count > _EXPLORER_COUNT_CAP:
        return _EXPLORER_COUNT_CAP, True
    return raw_count, False


# Keyset (seek) pagination. The ORDER BY is `{sort_expr} {dir}, xpos ASC, key ASC`,
# so a page boundary is fully described by the last row's (sort_value, xpos, key) — all
# integers (carrier counts or xpos; key is the UInt64 variant key). The cursor encodes that
# triple plus the sort/order it was issued for, so a stale cursor (after the user changes
# sort) is detected and ignored rather than silently mis-paging.
_CURSOR_VERSION = "v1"
# Column index of each sort_expr in a row of `_ranked_variants` (see that query).
_SORT_RAW_COL = {
    "families": 2,
    "hom_samples": 3,
    "het_samples": 4,
    "total_samples": 5,
    "xpos": 1,
}
_RAW_XPOS_COL = 1
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
    except (ValueError, UnicodeDecodeError, binascii.Error):
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
# A review's classification is free text. The review dialog stores the class label
# ("Pathogenic - class 5", the same for ACMG and CNV classes); a review saved by another
# client may hold a class key ("acmg_class_4", "cnv_class_4") or the bare class word in any
# case or spelling ("Likely_Pathogenic", "VUS"). Each reads as the class it names:
# the class number when the text ends in one, else the class word. What is ranked is the class
# each review shows (review_class_label, as the family page reads it: a class word as its label,
# else the review's class tag, else the text).
_CLASS_NUMBER = re.compile(r"(?:^|_)class_([1-5])$")
_CLASSIFICATION_WORD_RANK = {
    "pathogenic": 5,
    "likely_pathogenic": 4,
    "uncertain_significance": 3,
    "variant_of_uncertain_significance": 3,
    "vus": 3,
    "likely_benign": 2,
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


def _classify_type(ref: str | None, alt: str | None) -> str:
    ref = ref or ""
    alt = alt or ""
    if len(ref) == 1 and len(alt) == 1:
        return "SNV"
    if len(ref) == len(alt) and len(ref) > 1:
        return "MNV"
    return "INDEL"


def _most_severe(
    values: Sequence[str], rank: Mapping[str, int] | Callable[[str], int]
) -> str | None:
    """The value of highest rank, as given; of two that rank alike, the first. ``rank`` is
    a table of lowercased values (an unlisted one ranks 0) or a function of the value."""
    index = _most_severe_index(values, rank)
    return None if index is None else str(values[index] or "").strip()


def _most_severe_index(
    values: Sequence[str], rank: Mapping[str, int] | Callable[[str], int]
) -> int | None:
    """The position in ``values`` of :func:`_most_severe`'s value; None when every value is
    empty."""
    best_index: int | None = None
    best_rank = -1
    for index, value in enumerate(values):
        text_value = str(value or "").strip()
        if not text_value:
            continue
        if callable(rank):
            value_rank = rank(text_value)
        else:
            value_rank = rank.get(text_value.lower(), 0)
        if value_rank > best_rank:
            best_rank = value_rank
            best_index = index
    return best_index


def _classification_rank(value: str) -> int:
    """The class a stored classification names, 5 (pathogenic) to 1 (benign); 0 for text
    that names no class, which any class outranks."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    match = _CLASS_NUMBER.search(slug)
    if match:
        return int(match.group(1))
    return _CLASSIFICATION_WORD_RANK.get(slug, 0)


def _first_non_empty(values: Sequence[str]) -> str | None:
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return None


@dataclass(slots=True)
class GlobalVariantFilters:
    """The explorer's filters: those it shares with the family search, read by the family
    search's builders (``_carriers_where``), the review filter and the per-sample genotype
    filter; no family, inheritance, callset or callers' FILTER filter."""

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
    # A cell of the cohort-frequency view (``variant_explorer_views``), read back: the
    # variant's carriers in scope between these bounds, and the gnomAD allele-frequency
    # class of the rows' own annotation (``GNOMAD_AF_CLASS_CONDITIONS``).
    min_total_samples: int | None = None
    max_total_samples: int | None = None
    gnomad_af_class: str | None = None

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
    if has_superuser_rights(user):
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
    """The IDs of the samples the user's families show (optionally of one assembly's
    projects): the samples the genotype filter can keep a variant for (``_shown_members``).

    Powers the per-sample genotype filter picker. A member taken out of its family is not
    listed: the filter keeps nothing for it.
    """

    rows = await _accessible_project_rows(session, user)
    if assembly_id:
        project_ids = sorted({row["project_id"] for row in rows if row["assembly_id"] == assembly_id})
    else:
        project_ids = sorted({row["project_id"] for row in rows})
    members = await _shown_members(session, project_ids)
    return sorted({member.sample_id for member in members}, key=lambda sample_id: (sample_id.lower(), sample_id))


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
# The samples their family shows                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _ShownMember:
    """A sample its family shows: an active member of a family of the scope's projects,
    with its role and phenotype in that family."""

    family_uuid: str
    sample_id: str
    sample_uuid: str
    role: str | None = None
    phenotype_summary: str | None = None


def _phenotype_summary(clinical_status: Any, affected: Any) -> str | None:
    """A member's phenotype as the carriers list shows it: its clinical status, else
    "affected" when it is marked so; None when neither says."""
    status = str(clinical_status or "").strip()
    if status and status != "unknown":
        return status
    return "affected" if affected else None


async def _shown_members(session: AsyncSession, project_ids: Sequence[str]) -> list[_ShownMember]:
    """The active members of every family of ``project_ids``: the samples the family page
    shows (``build_family_metadata_context`` reads a family's ``family_members`` that are
    active). A member taken out of the family (a structure edit's ``remove_members``) keeps
    its calls in the family's rows until its data is cleared; it is no longer one."""
    if not project_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT
                fm.family_id::text AS family_uuid,
                s.sample_id,
                s.id::text AS sample_uuid,
                fm.role,
                fm.affected,
                fm.clinical_status
            FROM family_members fm
            JOIN samples s ON s.id = fm.sample_id
            WHERE fm.active
              AND EXISTS (
                  SELECT 1 FROM family_projects fp
                  WHERE fp.family_id = fm.family_id AND fp.project_id IN :project_ids
              )
            ORDER BY fm.family_id, s.sample_id
            """
        ).bindparams(uuid_list_bindparam("project_ids")),
        {"project_ids": uuid_values(list(project_ids))},
    )
    return [
        _ShownMember(
            family_uuid=str(row["family_uuid"]),
            sample_id=str(row["sample_id"]),
            sample_uuid=str(row["sample_uuid"]),
            role=row.get("role"),
            phenotype_summary=_phenotype_summary(row.get("clinical_status"), row.get("affected")),
        )
        for row in result.mappings().all()
    ]


def _shown_calls(members: Sequence[_ShownMember]) -> tuple[tuple[str, str], ...]:
    """The (family UUID, sample ID) pair of every call the families show: each member's
    calls in its own family's rows, as the family search reads a family's members
    (``_clickhouse_sample_ids``)."""
    by_family: dict[str, list[str]] = {}
    for member in members:
        by_family.setdefault(member.family_uuid, []).append(member.sample_id)
    return tuple(
        (family_uuid, sample_id)
        for family_uuid, names in sorted(by_family.items())
        for sample_id in _clickhouse_sample_ids(names)
    )


def _shown_call(sample_column: str, *, family_column: str = "e.family_guid") -> str:
    """The call of ``sample_column`` in the row of ``family_column`` is one of a member its
    family shows (``%(shown_calls)s``, the pairs of :func:`_shown_calls`)."""
    return f"({family_column}, {sample_column}) IN %(shown_calls)s"


# --------------------------------------------------------------------------- #
# Postgres review (tag / classification) bridge                               #
# --------------------------------------------------------------------------- #

# The sort key of a review without a save time (none in Postgres: the column is NOT NULL).
_NO_TIME = datetime.min.replace(tzinfo=timezone.utc)


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

    A classification keeps a review, or its compound-het pair review, by the class it shows,
    as the family search's filter does (``list_matching_small_variant_review_ids``):
    the rule ``review_shows_class``, here in SQL (``review_class_label_sql``), so the Explorer
    keeps exactly the variants a family search keeps in some family. Whatever the case:
    Postgres's lower() and Python's agree on the class labels and words.
    """

    classifications = review_class_filter(classifications)
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
        shown, label_params = review_class_label_sql("r.classification", "r.tags")
        pair_shown, _same_params = review_class_label_sql(
            "r.compound_het_classification", "r.compound_het_tags"
        )
        clauses.append(
            f"(lower({shown}) IN :classifications OR lower({pair_shown}) IN :classifications)"
        )
        params.update(label_params)
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
    """Per variant_id: the union of its reviews' tags, the class each review shows with that
    review's classification text as stored (``classification_texts``, in the same order), and
    the latest review time, across the families of ``project_ids``.

    Each review shows its class as the family page reads it (``review_class_label``): a
    review classified by its class tag alone shows that class. Its own class comes
    before its compound-het pair's, and the reviews are taken saved last first, then by
    family id, whatever order the rows come in: of two reviews of the same class
    ``_most_severe`` keeps the first, so the class shown does not depend on row order.
    """

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
            r.family_id::text AS family_id,
            r.classification,
            r.tags,
            r.compound_het_classification,
            r.compound_het_tags,
            r.updated_at
        FROM small_variant_reviews r
        JOIN family_projects fp ON fp.family_id = r.family_id
        WHERE fp.project_id IN :project_ids
          AND r.variant_id = ANY(CAST(:variant_ids AS text[]))
        ORDER BY r.variant_id, r.updated_at DESC, r.family_id
        """
    ).bindparams(uuid_list_bindparam("project_ids"))
    result = await session.execute(
        query,
        {"project_ids": uuid_values(list(project_ids)), "variant_ids": variant_ids},
    )

    aggregated: dict[str, dict[str, Any]] = {}
    for row in _in_review_order(result.mappings().all()):
        variant_id = str(row["variant_id"] or "").strip()
        if not variant_id:
            continue
        entry = aggregated.setdefault(
            variant_id,
            {"tags": set(), "classifications": [], "classification_texts": [], "updated_at": None},
        )
        for tag in (row.get("tags") or []):
            text_tag = str(tag or "").strip()
            if text_tag:
                entry["tags"].add(text_tag)
        for tag in (row.get("compound_het_tags") or []):
            text_tag = str(tag or "").strip()
            if text_tag:
                entry["tags"].add(text_tag)
        for stored, tags in (
            (row.get("classification"), row.get("tags")),
            (row.get("compound_het_classification"), row.get("compound_het_tags")),
        ):
            shown = review_class_label(stored, tags)
            if shown:
                entry["classifications"].append(shown)
                entry["classification_texts"].append(stored)
        updated_at = row.get("updated_at")
        if updated_at is not None and (
            entry["updated_at"] is None or updated_at > entry["updated_at"]
        ):
            entry["updated_at"] = updated_at
    return aggregated


def _in_review_order(rows: Sequence[Any]) -> list[Any]:
    """``rows`` saved last first (a review without a time last), then by family id: the order
    the review lookup reads them in, applied here too so the result holds whatever order a
    driver hands them over in."""
    by_family = sorted(rows, key=lambda row: str(row.get("family_id") or ""))
    # A stable sort: reviews saved at the same time keep their family order.
    return sorted(
        by_family,
        key=lambda row: (row.get("updated_at") is not None, _as_utc(row.get("updated_at")) or _NO_TIME),
        reverse=True,
    )


# --------------------------------------------------------------------------- #
# The family search's filters                                                 #
# --------------------------------------------------------------------------- #


# The Explorer's genotype modes, as the family page's genotype filter names them.
_GENOTYPE_MODE_FILTERS = {"het": "het", "hom": "hom", "het_hom": "het|hom"}


def _family_search_filters(filters: GlobalVariantFilters) -> SmallVariantQueryFilters:
    """The explorer's filters as the family search's filter set, so both pages build each
    filter they share with one function (``clickhouse_variant_queries``): the location, the
    variant type, the rsID, the gene, the excluded ClinVar classes and the per-transcript
    annotation filters. The explorer offers no transcript, interval list, callset, excluded
    genes or intervals, and none of a family's inheritance, phase set or callers' FILTER."""
    return SmallVariantQueryFilters(
        page=1,
        page_size=1,
        chromosome=filters.chromosome,
        start=filters.start,
        end=filters.end,
        variant_type=filters.variant_type,
        gene=filters.gene,
        rsid=filters.rsid,
        exclude_clinvar=list(filters.exclude_clinvar),
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


def _sample_genotype_condition(
    scope: ExplorerScope,
    query_filters: SmallVariantQueryFilters,
    sample_id: str,
    mode: str,
    *,
    index: int,
    include_imputed: bool,
    shown_members: Sequence[_ShownMember],
    params: dict[str, Any],
) -> str:
    """The variant (alias ``e``) is one that ``sample_id`` carries with ``mode``'s genotype
    (het, hom, or either: ``het_hom``, also for an unknown mode) in one of its rows in the
    scope's projects, imputed callsets left out unless included.

    The sample's call is read by the family page's sample filter
    (``_small_sample_call_condition``): the same genotype classes, so a haploid ``1`` is
    homozygous and a ``1/2`` heterozygous. It is read by variant, not by row: the explorer
    keeps a variant the sample carries and counts every carrier of the cohort, where the
    family page keeps the rows of its family alone (the owner's decision of 2026-11-06). The
    locus narrows the scan of the sample's rows; a variant's position is the same in every
    row, so it changes no result.

    Only the sample's calls in the rows of a family that shows it are read
    (``shown_members``); the sample may be named by its ID or its UUID. A sample no family
    of the scope shows, such as a member taken out of its family, keeps no variant (the
    owner's decision of 2026-11-06)."""
    member_of = [member for member in shown_members if sample_id in (member.sample_id, member.sample_uuid)]
    if not member_of:
        return "0"
    params[f"explorer_sample_{index}_families"] = tuple(sorted({member.family_uuid for member in member_of}))
    token = _GENOTYPE_MODE_FILTERS.get(str(mode or "").strip().lower(), _GENOTYPE_MODE_FILTERS["het_hom"])
    genotype_classes, _include_absent = parse_genotype_filter(token)
    call_condition = _small_sample_call_condition(
        tuple(_clickhouse_sample_ids(member.sample_id for member in member_of)),
        SmallVariantSampleFilter(sample_name=sample_id, genotype_classes=genotype_classes),
        prefix=f"explorer_sample_{index}",
        params=params,
        alias="gs",
    )
    sample_rows = [
        "gs.sign = 1",
        "gs.project_guid IN %(project_guids)s",
        f"gs.family_guid IN %(explorer_sample_{index}_families)s",
        *_small_callset_clauses(
            query_filters, params=params, exclude_imputed=not include_imputed, alias="gs"
        ),
        *_small_locus_clauses(query_filters, params=params, alias="gs"),
        call_condition,
    ]
    entries_table = _small_table_name(scope.assembly_name, "entries")
    return f"e.key IN (SELECT gs.key FROM {entries_table} AS gs WHERE {' AND '.join(sample_rows)})"


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
    page. So a panel of regions only filters by its regions, and a variant in one of a
    panel's regions is kept.
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
    scope: ExplorerScope,
    query_filters: SmallVariantQueryFilters,
    panel_constraints: PanelFilterConstraints,
    params: dict[str, Any],
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
        # The locus narrows the scan of the gene index, as on the family page.
        query_filters,
        panel_constraints,
        params=params,
        region_condition=region_condition,
    )


def _carriers_where(
    scope: ExplorerScope,
    filters: GlobalVariantFilters,
    params: dict[str, Any],
    *,
    tag_variant_ids: Sequence[str] | None,
    shown_members: Sequence[_ShownMember],
    panel_constraints: PanelFilterConstraints | None = None,
) -> list[str]:
    """The WHERE clauses on the rows of the variant carrier summary (alias ``e``,
    ``variant_carrier_summary``) for these filters. ``panel_constraints`` is the panel as
    :func:`_panel_constraints` read it, and ``shown_members`` the samples the scope's families
    show (:func:`_shown_members`).

    A row of the summary holds the carriers of one family's rows of one class: their
    annotation set, rsID, gene symbols and whether their callset is imputed, the columns the
    filters read on a row. The carriers are the members its family shows (the owner's decision
    of 2026-11-06), so a sample its family does not show, such as a member taken out of it, is
    neither counted nor keeps a variant. A family is in scope when it is linked to one of the
    scope's projects (``hasAny(e.projects, …)``).

    Each filter the explorer shares with the family search is read by the family search's own
    builder (``clickhouse_variant_queries``), on each row as the family page reads it: the
    variant type, the location (the chromosome with or without ``chr``, chrM by any of its
    names), the rsID (``_small_row_filter_clauses``); the gene, the per-transcript annotation
    filters and the excluded ClinVar classes, each on the row's own annotation
    (``_small_annotation_filter_clauses``); and the gene panel (:func:`_panel_condition`). A
    variant's row is so kept exactly when its family's page keeps the rows of that class, and
    the explorer counts the carriers of the rows it keeps: a variant two families carry under
    two annotations (two VEP runs) is counted, under an annotation filter, in the family whose
    own annotation meets it (the owner's decision of 2026-11-06). The imputed callsets are left
    out unless included.

    The explorer's own filters are the review (tag and classification) filter, across the
    accessible families, and the sample genotype filter, read by variant
    (:func:`_sample_genotype_condition`)."""
    clauses = ["e.key != 0", "hasAny(e.projects, %(scope_projects)s)"]
    params["project_guids"] = tuple(scope.project_ids)
    params["scope_projects"] = list(scope.project_ids)
    if not filters.include_imputed:
        clauses.append("NOT e.imputed")

    if tag_variant_ids is not None:
        # An empty set means "review filter matched nothing".
        params["tag_variant_ids"] = tuple(tag_variant_ids) or ("",)
        clauses.append("e.variantId IN %(tag_variant_ids)s")

    query_filters = _family_search_filters(filters)
    # First: the annotation and panel subqueries repeat the locus these clauses bind. The
    # imputed callsets are the summary's own column, above.
    clauses.extend(_small_row_filter_clauses(scope, query_filters, params=params, exclude_imputed=False))

    # Each sample is its own membership subquery; AND-ing them means the variant must
    # satisfy every per-sample genotype constraint.
    for index, (sample_id, mode) in enumerate(filters.sample_genotype_filters):
        sample_id = str(sample_id).strip()
        if not sample_id:
            continue
        clauses.append(
            _sample_genotype_condition(
                scope,
                query_filters,
                sample_id,
                mode,
                index=index,
                include_imputed=filters.include_imputed,
                shown_members=shown_members,
                params=params,
            )
        )

    clauses.extend(_small_annotation_filter_clauses(scope, query_filters, params=params))

    if panel_constraints is not None:
        # A panel with neither genes nor regions keeps nothing ("0"); the search returns
        # before it asks ClickHouse.
        clauses.append(_panel_condition(scope, query_filters, panel_constraints, params) or "0")

    if filters.gnomad_af_class is not None:
        # An unknown class keeps nothing.
        condition = GNOMAD_AF_CLASS_CONDITIONS.get(filters.gnomad_af_class)
        clauses.append(
            _small_annotation_key_membership_condition(scope, query_filters, params=params, condition=condition)
            if condition
            else "0"
        )

    return clauses


# The gnomAD allele-frequency classes of the cohort-frequency view, on the annotation index
# (alias ``ai``): absent, below 0.1 %, 0.1–1 %, 1–5 %, 5 % and over.
GNOMAD_AF_CLASS_CONDITIONS: dict[str, str] = {
    "absent": "ai.max_gnomad_af IS NULL",
    "below_0.1": "ai.max_gnomad_af < 0.001",
    "0.1_1": "ai.max_gnomad_af >= 0.001 AND ai.max_gnomad_af < 0.01",
    "1_5": "ai.max_gnomad_af >= 0.01 AND ai.max_gnomad_af < 0.05",
    "above_5": "ai.max_gnomad_af >= 0.05",
}


def _total_samples_having(filters: GlobalVariantFilters, params: dict[str, Any]) -> str:
    """The carrier-count bounds of a cohort-frequency cell, after the variant's counts
    (``AND …`` to follow ``HAVING total_samples > 0``), or ''."""
    parts: list[str] = []
    if filters.min_total_samples is not None:
        params["min_total_samples"] = int(filters.min_total_samples)
        parts.append("total_samples >= %(min_total_samples)s")
    if filters.max_total_samples is not None:
        params["max_total_samples"] = int(filters.max_total_samples)
        parts.append("total_samples <= %(max_total_samples)s")
    return "".join(f" AND {part}" for part in parts)


# The counts of a variant over the summary rows a page keeps (``GROUP BY e.xpos, e.key``).
# An aggregate row (no family) adds its counts; a carrier row names one family and sample (a
# family whose rows of the variant fall in several classes, or a sample two families show),
# and a sample counts once over the classes and families kept, by its ID as the explorer
# counts samples. A contribution taken out is the same rows negated, so the sums net out
# whether or not the parts have merged: a (family, sample) counts when its rows sum above
# zero. The carrier rows are few, so they are gathered per variant and netted there.
_CARRIER_ROW = "e.family_guid != ''"


def _netted(field: int) -> str:
    """The carrier rows' tokens (family and sample) with the sum of their field ``field``
    (3 carriers, 4 heterozygous, 5 homozygous)."""
    return (
        "arrayReduce('sumMap', arrayMap(t -> [concat(t.1, '\\t', t.2)], carrier_list), "
        f"arrayMap(t -> [toInt64(t.{field})], carrier_list))"
    )


def _distinct_part(part: int, netted: str) -> str:
    """How many distinct families (``part`` 1) or samples (2) the tokens of ``netted`` that
    sum above zero name."""
    return (
        f"length(arrayDistinct(arrayMap(token -> splitByChar('\\t', token)[{part}], "
        f"arrayFilter((token, n) -> n > 0, ({netted}).1, ({netted}).2))))"
    )


_VARIANT_COUNTS = (
    "groupArrayIf((e.family_guid, e.sample_id, e.samples, e.het, e.hom), "
    f"{_CARRIER_ROW}) AS carrier_list, "
    f"{_netted(3)} AS carrier_rows, "
    f"sumIf(e.families, NOT {_CARRIER_ROW}) + {_distinct_part(1, 'carrier_rows')} AS families, "
    f"sumIf(e.hom, NOT {_CARRIER_ROW}) + {_distinct_part(2, _netted(5))} AS hom_samples, "
    f"sumIf(e.het, NOT {_CARRIER_ROW}) + {_distinct_part(2, _netted(4))} AS het_samples, "
    f"sumIf(e.samples, NOT {_CARRIER_ROW}) + {_distinct_part(2, 'carrier_rows')} AS total_samples"
)
# The summary is sorted by ``xpos, key``: grouped in that order, a query holds one variant's
# sums at a time, however many variants it reads.
_IN_ORDER = "SETTINGS optimize_aggregation_in_order = 1"


def _carriers_table(scope: ExplorerScope) -> str:
    return _small_table_name(scope.assembly_name, CARRIERS_SUFFIX)


async def _count_variants(
    scope: ExplorerScope, where_sql: str, params: dict[str, Any], having_sql: str = ""
) -> int:
    """The distinct variants with a carrier in the rows kept, counted up to the cap (one
    more distinguishes "exactly cap" from "more"): grouped in the summary's order, the count
    stops at the cap."""
    rows = await execute_clickhouse(
        f"""
        SELECT count()
        FROM (
            SELECT e.key AS key, {_VARIANT_COUNTS}
            FROM {_carriers_table(scope)} AS e
            WHERE {where_sql}
            GROUP BY e.xpos, e.key
            HAVING total_samples > 0 {having_sql}
            LIMIT {_EXPLORER_COUNT_CAP + 1}
        )
        {_IN_ORDER}
        """,
        params,
    )
    return int(rows[0][0]) if rows and rows[0] else 0


async def _pending_families(session: AsyncSession, scope: ExplorerScope) -> list[str]:
    """The families of the scope whose variants the summary does not hold as they are now:
    being written, or changed since the summary was last brought up to date."""
    return await pending_variant_carrier_families(
        session, scope.assembly_id, scope.assembly_name, scope.project_ids
    )


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

    pending = await _pending_families(session, scope)
    empty_page = GlobalVariantPageOut(
        total=0,
        page_size=page_size,
        assembly_id=scope.assembly_id,
        assembly_name=scope.assembly_name,
        pending_families=len(pending),
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
    # No family of the scope shows a sample: nothing to count.
    shown_members = await _shown_members(session, scope.project_ids)
    if not shown_members:
        return empty_page

    params: dict[str, Any] = {}
    where_clauses = _carriers_where(
        scope,
        filters,
        params,
        tag_variant_ids=tag_variant_ids,
        shown_members=shown_members,
        panel_constraints=panel_constraints,
    )
    where_sql = " AND ".join(where_clauses)

    having_sql = _total_samples_having(filters, params)
    total, total_is_estimated = _bounded_total(await _count_variants(scope, where_sql, params, having_sql))
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
        where_sql=where_sql,
        params=params,
        sort_expr=sort_expr,
        direction=direction,
        limit=page_size + 1,
        seek_having=seek_having,
        seek_params=seek_params,
        having_sql=having_sql,
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
        pending_families=len(pending),
        variants=variants,
    )


async def _ranked_variants(
    scope: ExplorerScope,
    *,
    where_sql: str,
    params: dict[str, Any],
    sort_expr: str,
    direction: str,
    limit: int,
    seek_having: str | None = None,
    seek_params: dict[str, Any] | None = None,
    having_sql: str = "",
) -> list[tuple[Any, ...]]:
    """The page's variants in their order, each as ``(key, xpos, families, hom_samples,
    het_samples, total_samples)``."""
    page_params = dict(params)
    page_params["limit"] = limit
    if seek_params:
        page_params.update(seek_params)
    seek_sql = f"{having_sql} AND {seek_having}" if seek_having else having_sql
    return list(
        await execute_clickhouse(
            f"""
            SELECT key, xpos, families, hom_samples, het_samples, total_samples
            FROM (
                SELECT e.key AS key, e.xpos AS xpos, {_VARIANT_COUNTS}
                FROM {_carriers_table(scope)} AS e
                WHERE {where_sql}
                GROUP BY e.xpos, e.key
                HAVING total_samples > 0 {seek_sql}
                ORDER BY {sort_expr} {direction}, xpos ASC, key ASC
                LIMIT %(limit)s
            )
            ORDER BY {sort_expr} {direction}, xpos ASC, key ASC
            {_IN_ORDER}
            """,
            page_params,
        )
        or []
    )


async def _variant_columns(
    scope: ExplorerScope, *, where_sql: str, params: dict[str, Any], rows: Sequence[tuple[Any, ...]]
) -> dict[int, tuple[Any, ...]]:
    """Per variant of ``rows``: its ID, chromosome, position, REF and ALT, an rsID of a row it
    is kept by, and the gene symbols of those rows; read from the summary rows the page keeps
    whose carriers have not been taken out."""
    if not rows:
        return {}
    column_params = dict(params)
    column_params["page_keys"] = tuple(int(row[0]) for row in rows)
    column_params["page_xpos"] = tuple(sorted({int(row[1]) for row in rows}))
    result = await execute_clickhouse(
        f"""
        SELECT
            key,
            min(variant_id),
            min(variant_chrom),
            min(variant_pos),
            min(variant_ref),
            min(variant_alt),
            minIf(row_rsid, row_rsid != ''),
            arraySort(arrayDistinct(arrayFlatten(groupArray(row_gene_symbols))))
        FROM (
            SELECT
                e.key AS key,
                min(e.variantId) AS variant_id,
                min(e.chrom) AS variant_chrom,
                min(e.pos) AS variant_pos,
                min(e.ref) AS variant_ref,
                min(e.alt) AS variant_alt,
                e.rsid AS row_rsid,
                e.gene_symbols AS row_gene_symbols,
                sum(e.samples) AS row_carriers
            FROM {_carriers_table(scope)} AS e
            WHERE {where_sql} AND e.xpos IN %(page_xpos)s AND e.key IN %(page_keys)s
            GROUP BY e.xpos, e.key, e.family_guid, e.sample_id, e.projects, e.annotationSetHash, e.rsid,
                     e.gene_symbols, e.imputed
            HAVING row_carriers > 0
        )
        GROUP BY key
        """,
        column_params,
    )
    return {int(row[0]): tuple(row) for row in result or []}


async def _fetch_variant_rows(
    session: AsyncSession,
    *,
    scope: ExplorerScope,
    where_sql: str,
    params: dict[str, Any],
    sort_expr: str,
    direction: str,
    limit: int,
    seek_having: str | None = None,
    seek_params: dict[str, Any] | None = None,
    having_sql: str = "",
) -> tuple[list[GlobalVariantRowOut], list[tuple[int, int, int]]]:
    """Run the aggregated variant query and hydrate it into row models.

    Shared by the paginated search and the CSV export so both return identical columns. The
    variants are ranked over the variant carrier summary (:func:`_ranked_variants`), then the
    page's variant columns (:func:`_variant_columns`), annotations and reviews are read for
    its rows alone. ``seek_having`` (with ``seek_params``) keyset-pages forward from a
    cursor; omit it for the first page / full export. Returns ``(rows, row_cursors)`` where
    ``row_cursors[i]`` is the ``(sort_value, xpos, key)`` of ``rows[i]`` for building the
    next cursor.
    """

    ranked = await _ranked_variants(
        scope,
        where_sql=where_sql,
        params=params,
        sort_expr=sort_expr,
        direction=direction,
        limit=limit,
        seek_having=seek_having,
        seek_params=seek_params,
        having_sql=having_sql,
    )

    # Per-row keyset cursor (sort_value, xpos, key), parallel to `variants`, so the caller
    # can take the boundary row's cursor after an N+1 probe trim.
    row_cursors = [
        (int(row[_SORT_RAW_COL[sort_expr]]), int(row[_RAW_XPOS_COL]), int(row[_RAW_KEY_COL]))
        for row in ranked
    ]

    columns = await _variant_columns(scope, where_sql=where_sql, params=params, rows=ranked)
    keys = [int(row[0]) for row in ranked]
    variant_ids = [str(columns.get(key, ("", ""))[1]) for key in keys]

    annotation_map = await _fetch_annotation_display(scope.assembly_name, keys)
    review_map = await _review_display_map(
        session, project_ids=scope.project_ids, variant_ids=variant_ids
    )

    variants: list[GlobalVariantRowOut] = []
    for row in ranked:
        key = int(row[0])
        _key, variant_id, chrom, pos, ref, alt, rsid, row_gene_symbols = columns.get(
            key, (key, "", "", 0, None, None, "", [])
        )
        variant_id = str(variant_id or "")
        annotation = annotation_map.get(key, {})
        review = review_map.get(variant_id, {})

        # Gene symbols come from the rows the page keeps (original case); the
        # annotation_index casefolds them. "-" is an intergenic placeholder we drop.
        gene_symbols = [value for value in (row_gene_symbols or []) if value and value != "-"]
        impacts = annotation.get("impacts", [])
        effects = annotation.get("effects", [])
        # The transcript the row's consequence and HGVS come from: [effect, HGVSc, HGVSp].
        shown = annotation.get("shown", [])
        clinvar_terms = annotation.get("clinvar_terms", [])
        # The class the reviews show, the most severe, and beside it the text of the review
        # that shows it, as stored.
        shown_classes: list[str] = review.get("classifications", [])
        shown_index = _most_severe_index(shown_classes, _classification_rank)
        classification = None if shown_index is None else shown_classes[shown_index].strip()
        classification_text = (
            None if shown_index is None else review["classification_texts"][shown_index]
        )
        impact = _most_severe(impacts, _IMPACT_RANK) or _first_non_empty(impacts)

        variants.append(
            GlobalVariantRowOut(
                key=str(key),
                variant_id=variant_id,
                chr=str(chrom or ""),
                pos=int(pos or 0),
                ref=ref,
                alt=alt,
                rsid=rsid or None,
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
                classification_text=classification_text,
                tags=sorted(review.get("tags", set())),
                total_samples=int(row[5] or 0),
                het_samples=int(row[4] or 0),
                hom_samples=int(row[3] or 0),
                total_families=int(row[2] or 0),
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

    An export is never silently partial, as for the family exports: the route
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
    # The families of the scope the carrier summary did not hold as they were: the file may
    # lack their variants or count them as before a change.
    pending_families: int = 0

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
    pending = len(await _pending_families(session, scope))

    # The panel first, as on the family page and in the search.
    panel_constraints = await _panel_constraints(session, scope, filters)
    if _panel_keeps_nothing(panel_constraints):
        return GlobalVariantExport(
            assembly_name=scope.assembly_name,
            rows=[],
            truncated_reason=None,
            limit=limit,
            pending_families=pending,
        )
    tag_variant_ids, review_filter_capped = await _review_filter_variant_ids(
        session, scope, filters
    )
    review_filter_cut = TRUNCATED_BY_REVIEW_FILTER_LIMIT if review_filter_capped else None
    # Nothing to export when the review filter matched nothing, or when no family of the
    # scope shows a sample.
    shown_members = [] if tag_variant_ids == [] else await _shown_members(session, scope.project_ids)
    if not shown_members:
        return GlobalVariantExport(
            assembly_name=scope.assembly_name,
            rows=[],
            truncated_reason=review_filter_cut,
            limit=limit,
            pending_families=pending,
        )

    params: dict[str, Any] = {}
    where_clauses = _carriers_where(
        scope,
        filters,
        params,
        tag_variant_ids=tag_variant_ids,
        shown_members=shown_members,
        panel_constraints=panel_constraints,
    )
    where_sql = " AND ".join(where_clauses)

    variants, _row_cursors = await _fetch_variant_rows(
        session,
        scope=scope,
        where_sql=where_sql,
        params=params,
        sort_expr=sort_expr,
        direction=direction,
        limit=limit + 1,
        having_sql=_total_samples_having(filters, params),
    )
    row_cut = TRUNCATED_BY_ROW_LIMIT if len(variants) > limit else None
    return GlobalVariantExport(
        assembly_name=scope.assembly_name,
        rows=variants[:limit],
        truncated_reason=review_filter_cut or row_cut,
        limit=limit,
        pending_families=pending,
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
    # the first distinct value of each, which would pair one transcript's consequence with
    # another's HGVS.
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
    """The carriers of the variant in the scope's families: the members each family shows,
    with their role and phenotype in that family (:func:`_shown_members`). A sample its
    family does not show, such as a member taken out of it, is not listed (the owner's
    decision of 2026-11-06)."""
    scope = await resolve_scope(session, user, assembly_id)
    if scope is None:
        return VariantCarriersOut(key=str(variant_key))
    shown_members = await _shown_members(session, scope.project_ids)
    if not shown_members:
        return VariantCarriersOut(key=str(variant_key))
    member_of_call = {(member.family_uuid, member.sample_id): member for member in shown_members}

    # The variant's position, from the annotation index (its key has a bloom filter there):
    # ``entries`` is sorted by project, family and position, so the carriers' rows are found
    # by position in each family's rows rather than read through.
    positions = await execute_clickhouse(
        f"""
        SELECT DISTINCT chrom, pos
        FROM {_small_table_name(scope.assembly_name, "variants/annotation_index")}
        WHERE key = %(key)s
        """,
        {"key": variant_key},
    )
    xpos_values = tuple(sorted({_xpos(str(chrom), int(pos)) for chrom, pos in positions or []}))
    if not xpos_values:
        return VariantCarriersOut(key=str(variant_key))
    entries_table = _small_table_name(scope.assembly_name, "entries")
    params: dict[str, Any] = {
        "project_guids": tuple(scope.project_ids),
        "key": variant_key,
        "xpos_values": xpos_values,
        "shown_calls": _shown_calls(shown_members),
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
          AND xpos IN %(xpos_values)s
          AND key = %(key)s
          AND {is_carrier}{genotype_clause}{source_clause}
          AND {_shown_call('sample_id', family_column='family_guid')}
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
    family_meta = await _fetch_family_meta(session, scope.project_ids, family_uuids)

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

        member = member_of_call.get((family_guid, record["sample_id"]))
        is_hom = record["is_hom"]
        if is_hom:
            hom_total += 1
        else:
            het_total += 1
        group.samples.append(
            VariantCarrierSampleOut(
                sample_id=record["sample_id"],
                individual_name=member.sample_id if member else None,
                role=member.role if member else None,
                genotype=record["genotype"],
                zygosity="homozygous" if is_hom else "heterozygous",
                family_id=meta["family_name"],
                family_uuid=family_guid,
                project_id=meta.get("project_id"),
                project_name=meta.get("project_name"),
                phenotype_summary=member.phenotype_summary if member else None,
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
