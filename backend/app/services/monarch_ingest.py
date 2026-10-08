"""Ingest Monarch Initiative gene -> disease associations.

Downloads the Monarch Knowledge Graph denormalized association TSVs (human causal
and the cross-species noncausal file), filters to human gene -> disease edges,
aggregates them into one record per (gene, disease), and replaces the contents of
the `monarch_gene_disease` table.

Reference data only; keyed by HGNC and joined onto the gene profile by symbol at
read time. See docs/monarch-integration.md.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .bounded_download import download_bounded_bytes, gunzip_bounded
from .monarch_phenotype_score import reset_information_content_cache

logger = logging.getLogger(__name__)

# Monthly releases; "latest" always redirects to the newest dated release.
MONARCH_KG_BASE_URL = "https://data.monarchinitiative.org/monarch-kg/latest"
MONARCH_METADATA_URL = f"{MONARCH_KG_BASE_URL}/metadata.yaml"
_GENE_TSV_BASE = f"{MONARCH_KG_BASE_URL}/tsv/gene_associations"
_DISEASE_TSV_BASE = f"{MONARCH_KG_BASE_URL}/tsv/disease_associations"

# Pre-split denormalized association files. The causal file is taxon-filtered to
# human (9606); the noncausal file is cross-species but gene -> disease rows are
# HGNC-subject and we filter to those.
MONARCH_GENE_DISEASE_FILES = (
    f"{_GENE_TSV_BASE}/gene_disease.9606.tsv.gz",
    f"{_GENE_TSV_BASE}/gene_disease.noncausal.tsv.gz",
)

# Disease -> phenotype (HPO). Cross-species file; filtered to MONDO -> HP rows.
MONARCH_DISEASE_PHENOTYPE_FILE = f"{_DISEASE_TSV_BASE}/disease_phenotype.all.tsv.gz"

_HGNC_PREFIX = "HGNC:"
_MONDO_PREFIX = "MONDO:"
_HPO_PREFIX = "HP:"

# Predicate priority, strongest relationship first. Used to pick the representative
# predicate when a (gene, disease) pair is asserted by several edges.
_PREDICATE_PRIORITY = (
    "causes",
    "gene_associated_with_condition",
    "contributes_to",
    "associated_with_increased_likelihood_of",
)
# `causal` mirrors Monarch's own causal/noncausal split: only `biolink:causes`
# (the human causal file) is strong direct causation. `gene_associated_with_condition`
# and `contributes_to` come from the noncausal file and are broader associations.
_CAUSAL_PREDICATES = frozenset({"causes"})


def _strip_prefix(value: str, sep: str = ":") -> str:
    """Drop a CURIE-style namespace prefix, e.g. ``biolink:causes`` -> ``causes``."""
    text_value = (value or "").strip()
    _, _, tail = text_value.rpartition(sep)
    return tail or text_value


def _predicate_rank(predicate: str) -> int:
    try:
        return _PREDICATE_PRIORITY.index(predicate)
    except ValueError:
        return len(_PREDICATE_PRIORITY)


@dataclass(slots=True)
class _Association:
    hgnc_id: str
    gene_symbol: str
    mondo_id: str
    disease_label: str
    predicates: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)

    @property
    def predicate(self) -> str:
        return min(self.predicates, key=_predicate_rank)

    @property
    def causal(self) -> bool:
        return bool(self.predicates & _CAUSAL_PREDICATES)


async def _download_gzip_tsv(url: str) -> str:
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        raw = await download_bounded_bytes(client, url, source=url)
    if url.endswith(".gz"):
        raw = gunzip_bounded(raw, source=url)
    return raw.decode("utf-8")


async def _fetch_release_version() -> str | None:
    """Read the release id (e.g. ``2026-06-08``) from the KG metadata file."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(MONARCH_METADATA_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:  # pragma: no cover - network failure path
        logger.warning("Could not fetch Monarch release metadata: %s", exc)
        return None
    for line in response.text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            return stripped.split(":", 1)[1].strip().strip("'\"") or None
    return None


async def _resolve_release_version(release_version: str | None) -> str:
    """Resolve the Monarch KG release id, raising if it cannot be determined.

    A signed clinical report's gene-disease / phenotype provenance must record which
    Monarch release produced it, so we refuse to load data with an unknown version.
    Previously a failed metadata fetch only logged a warning and the refresh committed
    every row with ``release_version = NULL`` and reported success — silently losing the
    provenance of data that feeds clinical reports. Pass an explicit ``release_version``
    to bypass the network lookup.
    """
    if release_version is None:
        release_version = await _fetch_release_version()
    if not release_version:
        raise RuntimeError(
            "Refusing to load Monarch data with an unknown release version: the KG "
            "metadata (version:) could not be read. Retry when it is reachable, or pass "
            "an explicit release_version."
        )
    return release_version


def parse_gene_disease_tsv(
    text_value: str,
    *,
    associations: dict[tuple[str, str], _Association],
) -> None:
    """Fold the rows of one denormalized gene_disease TSV into ``associations``.

    Keyed by ``(hgnc_id, mondo_id)``; predicates and sources accumulate so a pair
    asserted by several sources/predicates collapses to a single record.
    """
    reader = csv.DictReader(io.StringIO(text_value), delimiter="\t")
    for row in reader:
        subject = (row.get("subject") or "").strip()
        obj = (row.get("object") or "").strip()
        if not subject.startswith(_HGNC_PREFIX) or not obj.startswith(_MONDO_PREFIX):
            continue
        if (row.get("negated") or "").strip().lower() == "true":
            continue
        predicate = _strip_prefix(row.get("predicate") or "")
        if not predicate:
            continue
        key = (subject, obj)
        record = associations.get(key)
        if record is None:
            record = _Association(
                hgnc_id=subject,
                gene_symbol=(row.get("subject_label") or "").strip(),
                mondo_id=obj,
                disease_label=(row.get("object_label") or "").strip(),
            )
            associations[key] = record
        record.predicates.add(predicate)
        source = _strip_prefix(row.get("primary_knowledge_source") or "")
        if source:
            record.sources.add(source)
        if not record.gene_symbol:
            record.gene_symbol = (row.get("subject_label") or "").strip()
        if not record.disease_label:
            record.disease_label = (row.get("object_label") or "").strip()


async def _replace_gene_disease_rows(
    session: AsyncSession,
    *,
    associations: Iterable[_Association],
    release_version: str | None,
    now: datetime,
) -> int:
    rows = [
        {
            "hgnc_id": assoc.hgnc_id,
            "gene_symbol": assoc.gene_symbol or assoc.hgnc_id,
            "mondo_id": assoc.mondo_id,
            "disease_label": assoc.disease_label or None,
            "predicate": assoc.predicate,
            "predicates": json.dumps(sorted(assoc.predicates)),
            "sources": json.dumps(sorted(assoc.sources)),
            "causal": assoc.causal,
            "release_version": release_version,
            "updated_at": now,
        }
        for assoc in associations
        if assoc.gene_symbol or assoc.hgnc_id
    ]

    await session.execute(text("DELETE FROM monarch_gene_disease"))
    if rows:
        await session.execute(
            text(
                """
                INSERT INTO monarch_gene_disease (
                    hgnc_id,
                    gene_symbol,
                    mondo_id,
                    disease_label,
                    predicate,
                    predicates,
                    sources,
                    causal,
                    release_version,
                    updated_at
                )
                VALUES (
                    :hgnc_id,
                    :gene_symbol,
                    :mondo_id,
                    :disease_label,
                    :predicate,
                    CAST(:predicates AS jsonb),
                    CAST(:sources AS jsonb),
                    :causal,
                    :release_version,
                    :updated_at
                )
                """
            ),
            rows,
        )
    return len(rows)


async def refresh_monarch_gene_disease(
    session: AsyncSession,
    *,
    release_version: str,
) -> dict[str, Any]:
    """Download, parse, and replace the Monarch gene -> disease table, in the caller's
    transaction (``refresh_monarch`` resolves the release and commits).

    Returns a summary dict suitable for an admin response.
    """
    started_at = datetime.now(timezone.utc)

    associations: dict[tuple[str, str], _Association] = {}
    files_loaded = 0
    for url in MONARCH_GENE_DISEASE_FILES:
        text_value = await _download_gzip_tsv(url)
        parse_gene_disease_tsv(text_value, associations=associations)
        files_loaded += 1

    records = list(associations.values())
    written = await _replace_gene_disease_rows(
        session,
        associations=records,
        release_version=release_version,
        now=started_at,
    )

    completed_at = datetime.now(timezone.utc)
    summary = {
        "release_version": release_version,
        "files_loaded": files_loaded,
        "gene_disease_pairs": written,
        "genes": len({assoc.hgnc_id for assoc in records}),
        "diseases": len({assoc.mondo_id for assoc in records}),
        "causal_pairs": sum(1 for assoc in records if assoc.causal),
        "completed_at": completed_at,
        "duration_seconds": (completed_at - started_at).total_seconds(),
    }
    logger.info(
        "Monarch gene-disease refresh complete: %s pairs across %s genes (release %s)",
        summary["gene_disease_pairs"],
        summary["genes"],
        release_version,
    )
    return summary


@dataclass(slots=True)
class _DiseasePhenotype:
    mondo_id: str
    disease_label: str
    hpo_id: str
    phenotype_label: str
    sources: set[str] = field(default_factory=set)
    # True only while *every* assertion of the pair is negated; a single present
    # assertion flips it to False (present wins over excluded).
    negated: bool = True


def parse_disease_phenotype_tsv(
    text_value: str,
    *,
    phenotypes: dict[tuple[str, str], _DiseasePhenotype],
) -> None:
    """Fold the rows of the denormalized disease_phenotype TSV into ``phenotypes``.

    Keyed by ``(mondo_id, hpo_id)``; sources accumulate and a pair is only kept as
    ``negated`` when no source asserts it as present.
    """
    reader = csv.DictReader(io.StringIO(text_value), delimiter="\t")
    for row in reader:
        subject = (row.get("subject") or "").strip()
        obj = (row.get("object") or "").strip()
        if not subject.startswith(_MONDO_PREFIX) or not obj.startswith(_HPO_PREFIX):
            continue
        negated = (row.get("negated") or "").strip().lower() == "true"
        key = (subject, obj)
        record = phenotypes.get(key)
        if record is None:
            record = _DiseasePhenotype(
                mondo_id=subject,
                disease_label=(row.get("subject_label") or "").strip(),
                hpo_id=obj,
                phenotype_label=(row.get("object_label") or "").strip(),
            )
            phenotypes[key] = record
        record.negated = record.negated and negated
        source = _strip_prefix(row.get("primary_knowledge_source") or "")
        if source:
            record.sources.add(source)
        if not record.disease_label:
            record.disease_label = (row.get("subject_label") or "").strip()
        if not record.phenotype_label:
            record.phenotype_label = (row.get("object_label") or "").strip()


async def _replace_disease_phenotype_rows(
    session: AsyncSession,
    *,
    phenotypes: Iterable[_DiseasePhenotype],
    release_version: str | None,
    now: datetime,
) -> int:
    rows = [
        {
            "mondo_id": record.mondo_id,
            "disease_label": record.disease_label or None,
            "hpo_id": record.hpo_id,
            "phenotype_label": record.phenotype_label or None,
            "negated": record.negated,
            "sources": json.dumps(sorted(record.sources)),
            "release_version": release_version,
            "updated_at": now,
        }
        for record in phenotypes
    ]

    await session.execute(text("DELETE FROM monarch_disease_phenotype"))
    if rows:
        # Chunked to keep the executemany parameter set bounded (~265k rows).
        chunk = 5000
        insert = text(
            """
            INSERT INTO monarch_disease_phenotype (
                mondo_id,
                disease_label,
                hpo_id,
                phenotype_label,
                negated,
                sources,
                release_version,
                updated_at
            )
            VALUES (
                :mondo_id,
                :disease_label,
                :hpo_id,
                :phenotype_label,
                :negated,
                CAST(:sources AS jsonb),
                :release_version,
                :updated_at
            )
            """
        )
        for start in range(0, len(rows), chunk):
            await session.execute(insert, rows[start : start + chunk])
    return len(rows)


async def refresh_monarch_disease_phenotype(
    session: AsyncSession,
    *,
    release_version: str,
) -> dict[str, Any]:
    """Download, parse, and replace the Monarch disease -> phenotype table, in the caller's
    transaction (``refresh_monarch`` resolves the release and commits)."""
    started_at = datetime.now(timezone.utc)

    phenotypes: dict[tuple[str, str], _DiseasePhenotype] = {}
    text_value = await _download_gzip_tsv(MONARCH_DISEASE_PHENOTYPE_FILE)
    parse_disease_phenotype_tsv(text_value, phenotypes=phenotypes)

    records = list(phenotypes.values())
    written = await _replace_disease_phenotype_rows(
        session,
        phenotypes=records,
        release_version=release_version,
        now=started_at,
    )

    completed_at = datetime.now(timezone.utc)
    summary = {
        "release_version": release_version,
        "disease_phenotype_pairs": written,
        "phenotype_diseases": len({record.mondo_id for record in records}),
        "phenotypes": len({record.hpo_id for record in records}),
        "excluded_phenotype_pairs": sum(1 for record in records if record.negated),
        "completed_at": completed_at,
        "duration_seconds": (completed_at - started_at).total_seconds(),
    }
    logger.info(
        "Monarch disease-phenotype refresh complete: %s pairs across %s diseases (release %s)",
        summary["disease_phenotype_pairs"],
        summary["phenotype_diseases"],
        release_version,
    )
    return summary


async def refresh_monarch(session: AsyncSession) -> dict[str, Any]:
    """Refresh all Monarch tables (gene -> disease and disease -> phenotype).

    Fetches the release version once and shares it across both refreshes so the
    tables stay consistent. Returns a merged summary.
    """
    started_at = datetime.now(timezone.utc)
    # Resolve once (raising if the release is unknown) and share it across both refreshes
    # so the tables stay on a single, recorded release.
    release_version = await _resolve_release_version(None)

    # Stage both table swaps in one transaction so a failure can't leave the tables on
    # mismatched releases (gene_disease new, disease_phenotype old/empty).
    gene_disease = await refresh_monarch_gene_disease(session, release_version=release_version)
    disease_phenotype = await refresh_monarch_disease_phenotype(session, release_version=release_version)
    await session.commit()
    # The information-content map is derived from disease_phenotype; drop the cache so
    # the next scoring request recomputes it against the new release.
    reset_information_content_cache()

    completed_at = datetime.now(timezone.utc)
    summary = {
        **gene_disease,
        **disease_phenotype,
        "files_loaded": gene_disease.get("files_loaded", 0) + 1,
        "completed_at": completed_at,
        "duration_seconds": (completed_at - started_at).total_seconds(),
    }
    return summary


async def monarch_status(session: AsyncSession) -> dict[str, Any]:
    """Summarize the currently loaded Monarch tables for the admin dashboard.

    Reads directly from the tables (no network) so the admin page can show what
    release is loaded and how big it is before deciding to refresh.
    """
    gene_disease = (
        await session.execute(
            text(
                """
                SELECT
                    count(*) AS gene_disease_pairs,
                    count(DISTINCT hgnc_id) AS genes,
                    count(DISTINCT mondo_id) AS diseases,
                    count(*) FILTER (WHERE causal) AS causal_pairs,
                    max(release_version) AS release_version,
                    max(updated_at) AS updated_at
                FROM monarch_gene_disease
                """
            )
        )
    ).mappings().one()

    disease_phenotype = (
        await session.execute(
            text(
                """
                SELECT
                    count(*) AS disease_phenotype_pairs,
                    count(DISTINCT mondo_id) AS phenotype_diseases,
                    count(DISTINCT hpo_id) AS phenotypes,
                    max(release_version) AS release_version,
                    max(updated_at) AS updated_at
                FROM monarch_disease_phenotype
                """
            )
        )
    ).mappings().one()

    updated_candidates = [
        value
        for value in (gene_disease["updated_at"], disease_phenotype["updated_at"])
        if value is not None
    ]
    return {
        "release_version": gene_disease["release_version"]
        or disease_phenotype["release_version"],
        "gene_disease_pairs": gene_disease["gene_disease_pairs"] or 0,
        "genes": gene_disease["genes"] or 0,
        "diseases": gene_disease["diseases"] or 0,
        "causal_pairs": gene_disease["causal_pairs"] or 0,
        "disease_phenotype_pairs": disease_phenotype["disease_phenotype_pairs"] or 0,
        "phenotype_diseases": disease_phenotype["phenotype_diseases"] or 0,
        "phenotypes": disease_phenotype["phenotypes"] or 0,
        "last_updated_at": max(updated_candidates) if updated_candidates else None,
    }


async def list_monarch_gene_disease(
    session: AsyncSession, *, symbol: str
) -> list[dict[str, Any]]:
    """Return Monarch disease associations for a gene symbol (case-insensitive)."""
    result = await session.execute(
        text(
            """
            SELECT
                hgnc_id,
                gene_symbol,
                mondo_id,
                disease_label,
                predicate,
                predicates,
                sources,
                causal,
                release_version
            FROM monarch_gene_disease
            WHERE upper(gene_symbol) = upper(:symbol)
            ORDER BY causal DESC, lower(coalesce(disease_label, mondo_id))
            """
        ),
        {"symbol": symbol},
    )
    return [dict(row) for row in result.mappings().all()]


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so user text is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Per-disease caps on the returned overview lists. Counts are always the true totals;
# these only bound the payload (a disease can carry hundreds of expected phenotypes).
_SEARCH_GENE_CAP = 200
_SEARCH_PHENOTYPE_CAP = 60
# Cap on the aggregated gene overview (a broad phenotype can touch thousands of genes).
_SEARCH_GENE_OVERVIEW_CAP = 300


def _empty_search(query: str) -> dict[str, Any]:
    return {
        "query": query,
        "total": 0,
        "diseases": [],
        "gene_overview": {"total": 0, "genes": []},
    }


async def _aggregate_gene_overview(
    session: AsyncSession, mondo_ids: list[str]
) -> dict[str, Any]:
    """De-duplicated gene list across every matched disease, strongest links first.

    ``disease_count`` is how many of the matched diseases each gene is linked to and
    ``causal`` is true when any of those links is direct causation.
    """
    if not mondo_ids:
        return {"total": 0, "genes": []}
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    gene_symbol,
                    hgnc_id,
                    bool_or(causal) AS causal,
                    count(DISTINCT mondo_id) AS disease_count
                FROM monarch_gene_disease
                WHERE mondo_id = ANY(:ids)
                GROUP BY gene_symbol, hgnc_id
                ORDER BY causal DESC, disease_count DESC, lower(gene_symbol)
                """
            ),
            {"ids": mondo_ids},
        )
    ).mappings().all()
    genes = [
        {
            "gene_symbol": row["gene_symbol"],
            "hgnc_id": row["hgnc_id"],
            "causal": bool(row["causal"]),
            "disease_count": row["disease_count"],
        }
        for row in rows
    ]
    return {"total": len(genes), "genes": genes[:_SEARCH_GENE_OVERVIEW_CAP]}


async def search_monarch_associations(
    session: AsyncSession, *, query: str, limit: int = 25
) -> dict[str, Any]:
    """Search Monarch diseases by name/MONDO id or by a linked phenotype.

    A disease surfaces either because its own label/MONDO id matches the query
    (``match_type`` ``disease``) or because it presents a phenotype that matches
    (``match_type`` ``phenotype``); ``both`` when it matches on both. Phenotype
    matching is HPO-closure aware: the query is resolved to seed HPO terms (by id,
    label, or synonym) which are then expanded to all of their descendant terms, so
    a disease annotated only with a more specific child term still surfaces.

    Returns the matched diseases (each with their linked genes and an expected-
    phenotype overview, matched phenotypes flagged first) plus ``gene_overview`` —
    a de-duplicated list of every gene linked across *all* matched diseases.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return _empty_search("")

    pattern = f"%{_escape_like(cleaned)}%"
    needle = cleaned.lower()

    # Diseases whose own label or MONDO id matches (drawn from both tables so a
    # disease with only phenotype rows still matches by name).
    name_rows = (
        await session.execute(
            text(
                r"""
                SELECT mondo_id, max(disease_label) AS disease_label
                FROM (
                    SELECT mondo_id, disease_label
                    FROM monarch_gene_disease
                    WHERE disease_label ILIKE :pattern ESCAPE '\'
                       OR mondo_id ILIKE :pattern ESCAPE '\'
                    UNION ALL
                    SELECT mondo_id, disease_label
                    FROM monarch_disease_phenotype
                    WHERE disease_label ILIKE :pattern ESCAPE '\'
                       OR mondo_id ILIKE :pattern ESCAPE '\'
                ) matches
                GROUP BY mondo_id
                """
            ),
            {"pattern": pattern},
        )
    ).mappings().all()
    name_labels = {row["mondo_id"]: row["disease_label"] for row in name_rows}

    # Seed HPO terms for a phenotype match: resolve the query against the ontology
    # (id / label / synonym) and Monarch's own phenotype labels. Seeding from the
    # ontology lets a high-level term resolve even when no disease is annotated with
    # it directly — its descendants carry the disease links.
    seed_ids = set(
        (
            await session.execute(
                text(
                    r"""
                    SELECT hpo_id FROM hpo_term
                    WHERE hpo_id ILIKE :pattern ESCAPE '\'
                       OR label ILIKE :pattern ESCAPE '\'
                    UNION
                    SELECT hpo_id FROM hpo_synonym
                    WHERE synonym ILIKE :pattern ESCAPE '\'
                    UNION
                    SELECT hpo_id FROM monarch_disease_phenotype
                    WHERE negated = FALSE
                      AND (hpo_id ILIKE :pattern ESCAPE '\'
                           OR phenotype_label ILIKE :pattern ESCAPE '\')
                    """
                ),
                {"pattern": pattern},
            )
        ).scalars().all()
    )

    # Expand seeds to themselves plus every descendant term via the HPO closure.
    # Membership in this set is what flags a disease's phenotype as "matched".
    expanded_ids = set(seed_ids)
    if seed_ids:
        expanded_ids.update(
            (
                await session.execute(
                    text(
                        """
                        SELECT DISTINCT hpo_id
                        FROM hpo_closure
                        WHERE ancestor_id = ANY(:seeds)
                        """
                    ),
                    {"seeds": list(seed_ids)},
                )
            ).scalars().all()
        )

    # Diseases that present any seed-or-descendant phenotype.
    pheno_ids: set[str] = set()
    if expanded_ids:
        pheno_ids = set(
            (
                await session.execute(
                    text(
                        """
                        SELECT DISTINCT mondo_id
                        FROM monarch_disease_phenotype
                        WHERE negated = FALSE
                          AND hpo_id = ANY(:ids)
                        """
                    ),
                    {"ids": list(expanded_ids)},
                )
            ).scalars().all()
        )

    all_ids = set(name_labels) | pheno_ids
    total = len(all_ids)
    if not all_ids:
        return _empty_search(cleaned)

    # Gene overview spans the full match set, not just the displayed page.
    gene_overview = await _aggregate_gene_overview(session, list(all_ids))

    def _rank(mondo_id: str) -> tuple[int, str]:
        # Name matches rank ahead of phenotype-only matches; exact then prefix first.
        label = (name_labels.get(mondo_id) or "").lower()
        if mondo_id in name_labels:
            if label == needle:
                score = 0
            elif label.startswith(needle):
                score = 1
            else:
                score = 2
        else:
            score = 3
        return (score, label or mondo_id.lower())

    ordered = sorted(all_ids, key=_rank)[: max(1, limit)]

    gene_rows = (
        await session.execute(
            text(
                """
                SELECT mondo_id, gene_symbol, hgnc_id, predicate, causal
                FROM monarch_gene_disease
                WHERE mondo_id = ANY(:ids)
                ORDER BY causal DESC, lower(gene_symbol)
                """
            ),
            {"ids": ordered},
        )
    ).mappings().all()

    pheno_rows = (
        await session.execute(
            text(
                """
                SELECT mondo_id, hpo_id, phenotype_label, disease_label
                FROM monarch_disease_phenotype
                WHERE mondo_id = ANY(:ids)
                  AND negated = FALSE
                ORDER BY lower(coalesce(phenotype_label, hpo_id))
                """
            ),
            {"ids": ordered},
        )
    ).mappings().all()

    genes_by_disease: dict[str, list[dict[str, Any]]] = {}
    for row in gene_rows:
        genes_by_disease.setdefault(row["mondo_id"], []).append(
            {
                "gene_symbol": row["gene_symbol"],
                "hgnc_id": row["hgnc_id"],
                "predicate": row["predicate"],
                "causal": bool(row["causal"]),
            }
        )

    phenos_by_disease: dict[str, list[dict[str, Any]]] = {}
    pheno_labels: dict[str, str] = {}
    for row in pheno_rows:
        mondo_id = row["mondo_id"]
        if row["disease_label"]:
            pheno_labels.setdefault(mondo_id, row["disease_label"])
        # A phenotype counts as matched when it is the seed term or a descendant.
        matched = row["hpo_id"] in expanded_ids
        phenos_by_disease.setdefault(mondo_id, []).append(
            {
                "hpo_id": row["hpo_id"],
                "phenotype_label": row["phenotype_label"],
                "matched": matched,
            }
        )

    diseases: list[dict[str, Any]] = []
    for mondo_id in ordered:
        genes = genes_by_disease.get(mondo_id, [])
        phenos = phenos_by_disease.get(mondo_id, [])
        # Stable sort keeps the label ordering within the matched / unmatched groups.
        phenos_sorted = sorted(phenos, key=lambda item: not item["matched"])
        matched_count = sum(1 for item in phenos if item["matched"])
        in_name = mondo_id in name_labels
        in_pheno = mondo_id in pheno_ids
        match_type = (
            "both" if in_name and in_pheno else ("disease" if in_name else "phenotype")
        )
        diseases.append(
            {
                "mondo_id": mondo_id,
                "disease_label": name_labels.get(mondo_id) or pheno_labels.get(mondo_id),
                "match_type": match_type,
                "gene_count": len(genes),
                "genes": genes[:_SEARCH_GENE_CAP],
                "phenotype_count": len(phenos),
                "matched_phenotype_count": matched_count,
                "phenotypes": phenos_sorted[:_SEARCH_PHENOTYPE_CAP],
            }
        )

    return {
        "query": cleaned,
        "total": total,
        "diseases": diseases,
        "gene_overview": gene_overview,
    }


async def family_observed_phenotype_closure(
    session: AsyncSession, *, family_uuid: str
) -> set[str]:
    """Return the set of HPO ids that count as observed for a family.

    This is the family's *present* annotations plus all their ancestors, so a
    disease's general expected phenotype matches when the patient has that term or
    any more specific descendant of it.
    """
    present_result = await session.execute(
        text(
            """
            SELECT DISTINCT hpo_id
            FROM individual_hpo
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND status = 'present'
            """
        ),
        {"family_uuid": family_uuid},
    )
    present = {row["hpo_id"] for row in present_result.mappings().all()}
    if not present:
        return set()

    closure_result = await session.execute(
        text(
            """
            SELECT DISTINCT ancestor_id
            FROM hpo_closure
            WHERE hpo_id = ANY(:present)
            """
        ),
        {"present": list(present)},
    )
    observed = {row["ancestor_id"] for row in closure_result.mappings().all()}
    observed.update(present)  # ensure exact terms count even without self-closure rows
    return observed


async def phenotype_closure(
    session: AsyncSession, hpo_ids: Iterable[str]
) -> set[str]:
    """Expand a set of HPO ids to include all of their ancestors.

    A disease/gene phenotype term counts as observed by the family when the family
    has that term or any more specific descendant of it, so we match against the
    ancestor closure of the family's present terms.
    """
    ids = sorted({hpo_id for hpo_id in hpo_ids if hpo_id})
    if not ids:
        return set()
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ancestor_id
            FROM hpo_closure
            WHERE hpo_id = ANY(:ids)
            """
        ),
        {"ids": ids},
    )
    closure = {row["ancestor_id"] for row in result.mappings().all()}
    closure.update(ids)  # exact terms count even without self-closure rows
    return closure


async def gene_phenotype_breakdown(
    session: AsyncSession,
    *,
    symbols: Iterable[str],
    observed_closure: set[str],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Split each gene's Monarch phenotypes into family-matching vs extra.

    For every symbol, unions the expected phenotypes across the gene's Monarch
    diseases and partitions them by whether the term is in ``observed_closure`` (the
    family's observed phenotypes plus their ancestors). Returns
    ``{UPPER_SYMBOL: {"matching": [{hpo_id, label}], "extra": [...]}}`` with each
    list sorted by label.
    """
    upper_symbols = sorted({symbol.upper() for symbol in symbols if symbol})
    out: dict[str, dict[str, list[dict[str, Any]]]] = {
        symbol: {"matching": [], "extra": []} for symbol in upper_symbols
    }
    if not upper_symbols:
        return out

    result = await session.execute(
        text(
            """
            SELECT DISTINCT gd.gene_symbol, dp.hpo_id, dp.phenotype_label
            FROM monarch_gene_disease gd
            JOIN monarch_disease_phenotype dp ON dp.mondo_id = gd.mondo_id
            WHERE upper(gd.gene_symbol) = ANY(:symbols)
              AND dp.negated = FALSE
            """
        ),
        {"symbols": upper_symbols},
    )
    seen: dict[str, set[str]] = {symbol: set() for symbol in upper_symbols}
    for row in result.mappings().all():
        key = (row["gene_symbol"] or "").upper()
        bucket = out.get(key)
        if bucket is None:
            continue
        hpo_id = row["hpo_id"]
        if hpo_id in seen[key]:
            continue
        seen[key].add(hpo_id)
        term = {"hpo_id": hpo_id, "label": row["phenotype_label"]}
        bucket["matching" if hpo_id in observed_closure else "extra"].append(term)
    for bucket in out.values():
        for terms in bucket.values():
            terms.sort(key=lambda item: (item["label"] or item["hpo_id"]).lower())
    return out


async def summarize_disease_phenotypes(
    session: AsyncSession,
    *,
    mondo_ids: Iterable[str],
    observed_closure: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-disease expected-phenotype summary for the given MONDO ids.

    Returns ``{mondo_id: {"phenotype_count": int, "matched": [{"hpo_id", "label"}]}}``.
    ``matched`` lists the disease's expected phenotypes the patient exhibits (only
    populated when ``observed_closure`` is provided), sorted by label.
    """
    ids = list({mondo_id for mondo_id in mondo_ids if mondo_id})
    summary: dict[str, dict[str, Any]] = {
        mondo_id: {"phenotype_count": 0, "matched": []} for mondo_id in ids
    }
    if not ids:
        return summary

    result = await session.execute(
        text(
            """
            SELECT mondo_id, hpo_id, phenotype_label
            FROM monarch_disease_phenotype
            WHERE mondo_id = ANY(:ids)
              AND negated = FALSE
            """
        ),
        {"ids": ids},
    )
    for row in result.mappings().all():
        entry = summary[row["mondo_id"]]
        entry["phenotype_count"] += 1
        if observed_closure and row["hpo_id"] in observed_closure:
            entry["matched"].append(
                {"hpo_id": row["hpo_id"], "label": row["phenotype_label"]}
            )
    for entry in summary.values():
        entry["matched"].sort(key=lambda item: (item["label"] or item["hpo_id"]).lower())
    return summary
