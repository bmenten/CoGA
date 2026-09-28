"""Local Phenomizer-style gene -> phenotype similarity scoring.

Scores how well a gene's Monarch phenotype profile matches a patient's observed
HPO terms, computed entirely from data already in CoGA (the Phase 1 gene->disease
and Phase 2 disease->phenotype tables plus `hpo_closure`). Unlike the live Monarch
semsim API (which only returns the top ~50 genes), this covers *every* gene with a
candidate variant, which is what Exomiser-style variant ranking needs.

Method (Phenomizer / Resnik best-match-average):
  - Information content IC(t) = -ln(p(t)), where p(t) is the fraction of diseases
    annotated to term t or any of its descendants (propagated via `hpo_closure`).
  - Pairwise similarity resnik(a, b) = IC of the most informative common ancestor.
  - Gene score = symmetric best-match average between the patient's term set and the
    union of the gene's diseases' phenotypes, normalized to [0, 1] by the maximum IC.

See docs/monarch-integration.md.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# IC is a function of the Monarch release only, so cache it process-wide with a TTL.
# The lock makes the (heavy, ~265k-row) aggregate single-flight: concurrent first-hits
# on a cold cache wait for one computation instead of all running it (thundering herd).
_IC_CACHE_TTL_SECONDS = 3600.0
_ic_cache: dict[str, Any] = {"expires_at": 0.0, "ic": None, "max_ic": 0.0}
_ic_lock = asyncio.Lock()

# Bounds to keep per-request scoring cheap even for genes with large phenotype sets.
_MAX_GENE_TERMS = 200
_MAX_PATIENT_TERMS = 60


@dataclass(slots=True)
class GenePhenotypeScore:
    score: float  # normalized to [0, 1]
    matched: list[dict[str, Any]] = field(default_factory=list)  # [{hpo_id, label, ic}]


async def _load_information_content(session: AsyncSession) -> tuple[dict[str, float], float]:
    """IC per HPO term, propagated to ancestors via the closure. Cached with a TTL."""
    if _ic_cache["ic"] is not None and time.monotonic() < _ic_cache["expires_at"]:
        return _ic_cache["ic"], _ic_cache["max_ic"]

    async with _ic_lock:
        # Re-check after acquiring: another coroutine may have populated the cache while
        # we waited, in which case we skip the expensive aggregate.
        now = time.monotonic()
        if _ic_cache["ic"] is not None and now < _ic_cache["expires_at"]:
            return _ic_cache["ic"], _ic_cache["max_ic"]

        total_result = await session.execute(
            text(
                "SELECT count(DISTINCT mondo_id) FROM monarch_disease_phenotype WHERE negated = FALSE"
            )
        )
        total_diseases = int(total_result.scalar() or 0)
        ic: dict[str, float] = {}
        max_ic = 0.0
        if total_diseases > 0:
            # Disease count per term, propagated up the ontology: a disease "has"
            # ancestor a when it is annotated to any descendant of a.
            rows = await session.execute(
                text(
                    """
                    SELECT c.ancestor_id AS hpo_id, count(DISTINCT dp.mondo_id) AS disease_count
                    FROM monarch_disease_phenotype dp
                    JOIN hpo_closure c ON c.hpo_id = dp.hpo_id
                    WHERE dp.negated = FALSE
                    GROUP BY c.ancestor_id
                    """
                )
            )
            for row in rows.mappings().all():
                count = int(row["disease_count"] or 0)
                if count <= 0:
                    continue
                value = -math.log(count / total_diseases)
                ic[row["hpo_id"]] = value
                if value > max_ic:
                    max_ic = value

        # Don't cache an empty result (e.g. the table not yet populated) — otherwise the
        # first call on a fresh/empty database would disable phenotype scoring for the
        # full TTL even after a refresh fills the table.
        if ic:
            _ic_cache.update(
                {"expires_at": now + _IC_CACHE_TTL_SECONDS, "ic": ic, "max_ic": max_ic}
            )
        return ic, max_ic


def reset_information_content_cache() -> None:
    """Drop the cached IC map (e.g. after a Monarch refresh)."""
    _ic_cache.update({"expires_at": 0.0, "ic": None, "max_ic": 0.0})


async def _term_ancestors(
    session: AsyncSession, terms: Iterable[str]
) -> dict[str, set[str]]:
    term_list = sorted({term for term in terms if term})
    if not term_list:
        return {}
    rows = await session.execute(
        text(
            """
            SELECT hpo_id, ancestor_id
            FROM hpo_closure
            WHERE hpo_id = ANY(:terms)
            """
        ),
        {"terms": term_list},
    )
    ancestors: dict[str, set[str]] = {term: {term} for term in term_list}
    for row in rows.mappings().all():
        ancestors.setdefault(row["hpo_id"], {row["hpo_id"]}).add(row["ancestor_id"])
    return ancestors


async def _gene_phenotype_terms(
    session: AsyncSession, symbols: Iterable[str]
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Map each gene symbol (upper) to the set of its diseases' expected HPO ids."""
    upper = sorted({s.upper() for s in symbols if s})
    if not upper:
        return {}, {}
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT upper(gd.gene_symbol) AS symbol, dp.hpo_id, dp.phenotype_label
            FROM monarch_gene_disease gd
            JOIN monarch_disease_phenotype dp ON dp.mondo_id = gd.mondo_id
            WHERE upper(gd.gene_symbol) = ANY(:symbols)
              AND dp.negated = FALSE
            """
        ),
        {"symbols": upper},
    )
    by_gene: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    for row in rows.mappings().all():
        by_gene.setdefault(row["symbol"], set()).add(row["hpo_id"])
        if row["phenotype_label"]:
            labels[row["hpo_id"]] = row["phenotype_label"]
    return by_gene, labels


def _resnik(
    a: str,
    b: str,
    ancestors: dict[str, set[str]],
    ic: dict[str, float],
) -> tuple[float, str | None]:
    """IC of the most informative common ancestor of terms a and b."""
    common = ancestors.get(a, {a}) & ancestors.get(b, {b})
    best = 0.0
    best_term: str | None = None
    for term in common:
        value = ic.get(term, 0.0)
        if value > best:
            best = value
            best_term = term
    return best, best_term


def phenomizer_score(
    patient_terms: list[str],
    gene_terms: list[str],
    *,
    ancestors: dict[str, set[str]],
    ic: dict[str, float],
    max_ic: float,
) -> GenePhenotypeScore:
    """Symmetric best-match-average similarity, normalized to [0, 1]."""
    if not patient_terms or not gene_terms or max_ic <= 0:
        return GenePhenotypeScore(score=0.0)

    patient_best: dict[str, float] = {}
    gene_best: dict[str, float] = {}
    for p in patient_terms:
        for g in gene_terms:
            value, _ = _resnik(p, g, ancestors, ic)
            if value > patient_best.get(p, -1.0):
                patient_best[p] = value
            if value > gene_best.get(g, -1.0):
                gene_best[g] = value

    patient_avg = sum(patient_best.values()) / len(patient_terms)
    gene_avg = sum(gene_best.values()) / len(gene_terms)
    raw = (patient_avg + gene_avg) / 2.0
    score = max(0.0, min(1.0, raw / max_ic))

    # Explanation: the patient terms that matched the gene best.
    matched = sorted(
        ({"hpo_id": term, "ic": value} for term, value in patient_best.items() if value > 0),
        key=lambda item: item["ic"],
        reverse=True,
    )[:5]
    return GenePhenotypeScore(score=score, matched=matched)


def _cap_terms_by_ic(
    terms: Iterable[str], ic: dict[str, float], limit: int
) -> list[str]:
    """Keep the ``limit`` most-informative terms, deterministically.

    ``terms`` may be a set, so IC ties are broken on the term id (ascending) to keep
    the selection reproducible run-to-run — without the id tiebreaker the survivors of
    the cap depend on set-iteration order (PYTHONHASHSEED), which would perturb the
    phenotype score and the variant ranking frozen into the cache/report. ``-ic`` keeps
    the primary order IC-descending (equivalent to the previous ``reverse=True``); the
    ``ic.get(t, 0.0)`` default is preserved so a term with unknown IC sinks to the bottom.
    """
    return sorted(terms, key=lambda t: (-ic.get(t, 0.0), t))[:limit]


async def score_genes_for_hpo(
    session: AsyncSession,
    *,
    gene_symbols: Iterable[str],
    patient_hpo_ids: Iterable[str],
) -> dict[str, GenePhenotypeScore]:
    """Phenotype-similarity score per gene symbol (keyed upper-case).

    Genes without Monarch phenotype data are simply absent from the result (the
    caller treats them as "phenotype unknown", not "phenotype score 0").
    """
    patient_all = sorted({t for t in patient_hpo_ids if t})
    symbols = {s.upper() for s in gene_symbols if s}
    if not patient_all or not symbols:
        return {}

    ic, max_ic = await _load_information_content(session)
    if not ic:
        return {}

    # Keep the most informative patient terms (highest IC), not the lexicographically
    # first ones, when bounding the set — a deeply phenotyped patient shouldn't have
    # the scoring driven by HPO-id ordering.
    patient = _cap_terms_by_ic(patient_all, ic, _MAX_PATIENT_TERMS)

    gene_terms_map, _label_map = await _gene_phenotype_terms(session, symbols)
    if not gene_terms_map:
        return {}

    # Cap each gene's term set to the most informative terms to bound cost
    # (deterministically — see _cap_terms_by_ic).
    capped: dict[str, list[str]] = {
        symbol: _cap_terms_by_ic(terms, ic, _MAX_GENE_TERMS)
        for symbol, terms in gene_terms_map.items()
    }

    all_terms = set(patient)
    for terms in capped.values():
        all_terms.update(terms)
    ancestors = await _term_ancestors(session, all_terms)

    # The per-gene similarity is what makes a prioritised ranking take seconds; it is pure
    # CPU work, so it runs in a worker thread and other requests are served meanwhile (#527).
    return await asyncio.to_thread(_score_gene_terms, patient, capped, ancestors, ic, max_ic)


def _score_gene_terms(
    patient: list[str],
    gene_terms: dict[str, list[str]],
    ancestors: Any,
    ic: Any,
    max_ic: float,
) -> dict[str, GenePhenotypeScore]:
    return {
        symbol: phenomizer_score(patient, terms, ancestors=ancestors, ic=ic, max_ic=max_ic)
        for symbol, terms in gene_terms.items()
    }
