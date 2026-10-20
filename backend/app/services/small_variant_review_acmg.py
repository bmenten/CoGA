from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import AcmgClassificationPayload
from . import acmg_points
from .clickhouse_variant_queries import _normalize_alpha_missense_class, _select_primary_annotation
from .clickhouse_variant_records import (
    _annotation_effect,
    _annotation_float,
    _annotation_gene,
    _annotation_gene_id,
    _annotation_int,
    _annotation_polyphen,
    _annotation_population_frequencies,
    _annotation_sift,
    _annotation_spliceai_max,
    _annotation_text,
)
from .gene_constraint_metrics import fetch_gene_constraint_metrics
from .review_pg_utils import _json_payload, _read_stored_classification

# The consequence a small-variant classification rests on, read from its primary transcript:
# the consequence term(s) the criteria for a null, missense, synonymous or length-changing
# variant read (PVS1, PM4, PP2, BP7, and the criteria a consequence rules out), the impact
# beside them, and LOFTEE's loss-of-function call the PVS1 strength reads (HC: very strong).
# Frozen like the frequencies, also without a value, so one that changes or appears later
# reads as a change.
EVIDENCE_CONSEQUENCE_FIELDS: tuple[str, ...] = ("effect", "impact", "lof")
# The population frequencies a small-variant classification rests on (CLIN-7), in the order
# a reviewer reads them: the gnomAD allele frequency the frequency criteria read (PM2, BS1,
# BA1), and the other frequencies and the gnomAD allele, homozygote and hemizygote counts
# the filters read (BS2 reads the homozygote count). Each is frozen, also without a value,
# so one that appears later reads as a change.
EVIDENCE_FREQUENCY_FIELDS: tuple[str, ...] = (
    "gnomad_af",
    "gnomad_exomes_af",
    "gnomad_genomes_af",
    "gnomad_popmax_af",
    "topmed_af",
    "gnomad_ac",
    "gnomad_hom_count",
    "gnomad_hemi_count",
)
# The in-silico scores: REVEL, SpliceAI and the AlphaMissense class the computational
# criteria read (PP3, BP4, BP7), and CADD, SIFT and PolyPhen beside them.
EVIDENCE_IN_SILICO_FIELDS: tuple[str, ...] = (
    "cadd_phred",
    "revel",
    "spliceai_max",
    "sift",
    "polyphen",
    "alpha_missense_class",
    "alpha_missense_pathogenicity",
)
# The gene's missense constraint the PP2 suggestion reads (missense in a gene with a gnomAD
# missense Z of 3.09 or more). It is the gene profile's, not the annotation's: looked up as
# the variant list looks it up (``evidence_gene_missense_z``). Frozen only for a missense
# variant, the one variant PP2 reads it for (``reads_gene_missense_z``): for any other
# variant the group is left out, so a refresh of the gene profiles is never its drift.
EVIDENCE_GENE_CONSTRAINT_FIELDS: tuple[str, ...] = ("gene_missense_z",)


def _normalize_acmg_payload(
    payload: AcmgClassificationPayload | None,
) -> tuple[dict[str, Any] | None, int | None, str | None]:
    """Validate the submitted criteria and recompute the class/points server-side.

    Returns ``(blob, point_total, class_key)`` where ``blob`` is the JSON to store
    (or ``None`` to clear). The client-supplied total is ignored.

    Each criterion must be known, listed once and applied at a strength it takes
    (``acmg_points.ALLOWED_STRENGTHS``), as the classification dialog sends them; any other
    payload is refused with 400. Only a save is checked: a stored classification is read
    back as it was written.
    """

    if payload is None or not payload.criteria:
        return None, None, None

    normalized_criteria: list[dict[str, Any]] = []
    listed: set[str] = set()
    for criterion in payload.criteria:
        code = (criterion.code or "").strip().upper()
        if not acmg_points.is_valid_code(code):
            raise HTTPException(status_code=400, detail=f"Unknown ACMG criterion: {criterion.code}")
        # Every entry is scored, so a repeated code would count twice (PVS1 sent twice
        # scores +16) and leave the record ambiguous; the dialog holds one entry per code.
        if code in listed:
            raise HTTPException(status_code=400, detail=f"Repeated ACMG criterion: {code}")
        listed.add(code)
        strength = (criterion.strength or "").strip()
        if strength not in acmg_points.VALID_STRENGTHS:
            raise HTTPException(status_code=400, detail=f"Invalid ACMG strength: {criterion.strength}")
        if not acmg_points.is_allowed_strength(code, strength):
            raise HTTPException(status_code=400, detail=f"Invalid ACMG strength for {code}: {strength}")
        normalized_criteria.append(
            {
                "code": code,
                "strength": strength,
                "accepted": bool(criterion.accepted),
                "evidence": (criterion.evidence or "").strip() or None,
                "auto_suggested": bool(criterion.auto_suggested),
            }
        )

    point_total, class_key, class_label = acmg_points.compute_classification(normalized_criteria)
    blob = {
        "criteria": normalized_criteria,
        "point_total": point_total,
        "classification": class_label,
        "vus_tier": acmg_points.vus_tier_for_points(point_total, class_key),
    }
    return blob, point_total, class_key


def _acmg_json_or_none(value: Any) -> str | None:
    """Serialize the ACMG blob for a JSONB column, or None for SQL NULL."""
    if not value:
        return None
    return _json_payload(value)


def _json_or_none(value: Any) -> str | None:
    """Serialize a JSONB column value, or None for SQL NULL."""
    return None if value is None else _json_payload(value)


def _finite(value: float | None) -> float | None:
    """A value the snapshot can hold: JSONB has no NaN or infinity, and no -0.0."""
    if value is None or not math.isfinite(value):
        return None
    return value + 0.0


def _frozen_float(annotation: dict[str, Any], *keys: str) -> float | None:
    return _finite(_annotation_float(annotation, *keys))


def _frozen_int(annotation: dict[str, Any], *keys: str) -> int | None:
    """A count the snapshot can hold; None for a value no integer reads (an infinity)."""
    try:
        return _annotation_int(annotation, *keys)
    except OverflowError:
        return None


def _primary_annotation(record: Any) -> dict[str, Any]:
    return _select_primary_annotation(list(getattr(record, "annotations", None) or []))


def reads_gene_missense_z(record: Any) -> bool:
    """Whether a classification of ``record`` reads its gene's missense Z: a missense
    variant, as the ACMG dialog tells one for PP2 (its primary transcript's consequence
    includes missense_variant, ``isMissense`` in lib/acmg/evaluate.ts)."""
    return "missense_variant" in (_annotation_effect(_primary_annotation(record)) or "").lower()


def evidence_gene(record: Any) -> tuple[str | None, str | None]:
    """The gene symbol and gene id whose constraint ``record`` is served with: its primary
    transcript's gene, else the variant's first gene symbol (``_small_variant_out``)."""
    annotation = _primary_annotation(record)
    symbols = list(getattr(record, "gene_symbols", None) or [])
    return (
        _annotation_gene(annotation) or (symbols[0] if symbols else None),
        _annotation_gene_id(annotation),
    )


async def evidence_gene_missense_z(
    session: AsyncSession, records: Mapping[str, Any]
) -> dict[str, float | None]:
    """The gene missense Z of each record, by its key: the value the variant list serves it
    with and the PP2 suggestion reads (``gene_constraint_metrics``); None when its gene's
    profile holds none, and for a variant that is not a missense variant, whose gene is not
    looked up."""
    genes: list[tuple[str, str | None, str | None]] = []
    for key, record in records.items():
        if not reads_gene_missense_z(record):
            continue
        symbol, gene_id = evidence_gene(record)
        genes.append((key, symbol, gene_id))
    found = await fetch_gene_constraint_metrics(session, genes)
    return {key: (found.get(key) or {}).get("gene_missense_z") for key in records}


def small_variant_evidence(record: Any, *, gene_missense_z: float | None) -> dict[str, Any]:
    """The consequence, population frequencies and in-silico scores a classification of
    ``record`` reads, the transcript they are read from (CLIN-7), and, for a missense
    variant, its gene's missense constraint.

    Read from the variant's primary transcript (MANE Select, then canonical, then the most
    severe impact) with the helpers the variant's fields are served with
    (``_small_variant_out``), so the frozen values are the ones the ACMG dialog was given.
    The gene's missense Z is not in the annotation: the caller looks it up in the gene
    profiles as the variant list does (``evidence_gene_missense_z``). Every field is
    present, None without a value, except ``gene_constraint``, which only a missense
    variant has (``reads_gene_missense_z``): PP2 reads it for no other variant.
    """
    annotation = _primary_annotation(record)
    population = _annotation_population_frequencies(annotation)

    def _population(key: str) -> float | None:
        return _finite(population.get(key))

    constraint = (
        {"gene_constraint": {"gene_missense_z": _finite(gene_missense_z)}}
        if reads_gene_missense_z(record)
        else {}
    )
    return {
        "transcript_id": _annotation_text(annotation, "transcript_id", "transcriptId"),
        "consequence": {
            "effect": _annotation_effect(annotation),
            "impact": _annotation_text(annotation, "impact"),
            "lof": _annotation_text(annotation, "lof"),
        },
        "frequencies": {
            "gnomad_af": _frozen_float(annotation, "gnomad_af", "gnomadAf"),
            "gnomad_exomes_af": _population("gnomad_exomes_af"),
            "gnomad_genomes_af": _population("gnomad_genomes_af"),
            "gnomad_popmax_af": _population("gnomad_popmax_af"),
            "topmed_af": _population("topmed_af"),
            "gnomad_ac": _frozen_int(annotation, "gnomad_ac"),
            "gnomad_hom_count": _frozen_int(annotation, "gnomad_hom_count", "gnomadHomCount"),
            "gnomad_hemi_count": _frozen_int(annotation, "gnomad_hemi_count"),
        },
        "in_silico": {
            "cadd_phred": _frozen_float(annotation, "cadd_phred", "caddPhred"),
            "revel": _frozen_float(annotation, "revel"),
            "spliceai_max": _finite(_annotation_spliceai_max(annotation)),
            "sift": _annotation_sift(annotation),
            "polyphen": _annotation_polyphen(annotation),
            "alpha_missense_class": _normalize_alpha_missense_class(
                _annotation_text(annotation, "alpha_missense_class", "alphaMissenseClass")
            ),
            "alpha_missense_pathogenicity": _frozen_float(
                annotation, "alpha_missense_pathogenicity", "alphaMissensePathogenicity"
            ),
        },
        **constraint,
    }


def build_evidence_snapshot(
    record: Any, captured_at: datetime, *, gene_missense_z: float | None
) -> dict[str, Any]:
    """Freeze the evidence a classification was based on (clinical traceability).

    The annotation-set identity (``annotation_set_hash``) is the cheap drift key —
    it changes whenever any annotation changes — alongside the ClinVar significance,
    the most decision-relevant value, read from the per-transcript annotations, and the
    consequence, population frequencies and in-silico scores the classification reads, with
    the transcript they are read from, and, for a missense variant, the gene's missense Z
    (``small_variant_evidence``; ``gene_missense_z`` as ``evidence_gene_missense_z`` reads
    it). The drift check compares each value the snapshot froze, so it names what moved.
    """
    clinvar = None
    for annotation in getattr(record, "annotations", None) or []:
        value = annotation.get("clinvar") or annotation.get("clinvarClinicalSignificance")
        text_value = str(value).strip() if value not in (None, "", ".", "-") else ""
        if text_value:
            clinvar = text_value
            break
    return {
        "annotation_version": getattr(record, "annotation_version", None),
        "annotation_set_hash": getattr(record, "annotation_set_hash", None),
        "clinvar": clinvar,
        **small_variant_evidence(record, gene_missense_z=gene_missense_z),
        "captured_at": captured_at.isoformat(),
    }


async def freeze_evidence_snapshot(
    session: AsyncSession, record: Any, captured_at: datetime
) -> dict[str, Any]:
    """The evidence snapshot a classification of ``record`` saved now freezes, a missense
    variant's gene missense Z looked up as the variant list looks it up."""
    gene_missense_z = (await evidence_gene_missense_z(session, {"record": record}))["record"]
    return build_evidence_snapshot(record, captured_at, gene_missense_z=gene_missense_z)


def _deserialize_acmg(value: Any) -> AcmgClassificationPayload | None:
    """The stored ACMG classification, or None (see ``_read_stored_classification``)."""
    return _read_stored_classification(value, AcmgClassificationPayload, "ACMG classification")
