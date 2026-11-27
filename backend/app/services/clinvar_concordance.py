"""Each import's check of CoGA's ClinVar matching against the pipeline's.

A callset annotated with ClinVar's VCF (VEP ``--custom``) carries, per allele, the
classification, review status and VariationID the pipeline matched in that ClinVar release.
When CoGA holds the same release (the current one or the one before it), an import looks up the
callset's stored alleles in it, by the keys ``entries.allele_keys`` holds, and compares them: an
allele agrees when both sides name the same classification terms (``_status_terms``), review
status and VariationID. Every other allele that either side classifies is a difference, with its
reason. The import keeps the counts and the differences with the dataset
(``FamilyImportDatasetSummary.clinvar_concordance``), and the import page shows them. A callset
whose ClinVar release CoGA does not hold, or that names none, is recorded as not compared, with
the reason.

Only the ClinVar file's values count: the classification of VEP's cache
(``clinvar_from_vep_cache``) is ClinVar at rsID level, which may be another allele's.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error
from ..schemas import (
    ClinvarConcordanceDifferenceOut,
    ClinvarConcordanceOut,
    ClinvarConcordanceReason,
    FamilyImportDatasetSummary,
)
from .clickhouse_variant_ids import minimal_allele_id, small_allele_key
from .clickhouse_variant_records import SmallVariantRecord, _status_terms
from .clinvar_release_store import ClinvarStoredAllele, clinvar_alleles_of_callset
from .clinvar_releases import ClinvarRelease, annotated_clinvar_release, recorded_clinvar_releases
from .variant_annotation_parser import ALT_ALLELE_KEY, CLINVAR_FROM_VEP_CACHE_KEY

logger = logging.getLogger(__name__)

# The key of a loader's result that carries the concordance (``ClinvarConcordanceOut`` as JSON).
CONCORDANCE_KEY = "clinvar_concordance"
# The differences kept, the first by variant: the counts cover them all.
MAX_DIFFERENCES = 100


@dataclass(frozen=True, slots=True)
class _Annotated:
    """An allele's ClinVar as the pipeline annotated it, with the record it is of and the allele
    in its minimal form."""

    variant_id: str
    allele: str
    classification: str
    terms: frozenset[str]
    review_status: str
    variation_id: int | None


class ClinvarConcordance:
    """The annotated ClinVar of each allele an import stores, gathered batch by batch, and its
    comparison with CoGA's release once the callset is stored."""

    def __init__(self, assembly_name: str) -> None:
        self._assembly = assembly_name
        self._annotated: dict[int, _Annotated] = {}

    def observe(self, records: Iterable[SmallVariantRecord]) -> None:
        """Note the ClinVar file's values of each ALT of ``records``, from the first annotation
        of each allele: every annotation of one allele carries the same."""
        for record in records:
            alts = record.alt.split(",")
            seen: set[str] = set()
            for annotation in record.annotations:
                alt = str(annotation.get(ALT_ALLELE_KEY) or (alts[0] if len(alts) == 1 else ""))
                if not alt or alt in seen:
                    continue
                seen.add(alt)
                clinvar = annotation.get("clinvar")
                if not clinvar or annotation.get(CLINVAR_FROM_VEP_CACHE_KEY):
                    continue
                allele = minimal_allele_id(record.chr, record.start, record.ref, alt)
                if allele is None:
                    continue
                variation_id = str(annotation.get("clinvar_variation_id") or "").strip()
                self._annotated[small_allele_key(self._assembly, allele)] = _Annotated(
                    variant_id=record.variant_id,
                    allele=allele,
                    classification=str(clinvar),
                    terms=frozenset(_status_terms(clinvar)),
                    review_status=str(annotation.get("clinvar_review_status") or ""),
                    variation_id=int(variation_id) if variation_id.isdigit() else None,
                )

    async def compare(
        self,
        *,
        modules: Mapping[str, Mapping[str, Any]],
        kept: Sequence[ClinvarRelease],
        family_uuid: str,
        source: str,
    ) -> ClinvarConcordanceOut:
        """The stored callset's concordance with CoGA's release of the ClinVar file its
        annotation names (``modules``, its manifest), when CoGA keeps it (``kept``: the current
        release and the one before it)."""
        release = annotated_clinvar_release(modules)
        if release is None:
            return ClinvarConcordanceOut(
                compared=False,
                reason="Not compared: the annotation names no release of a ClinVar file.",
            )
        if release not in {kept_release.release for kept_release in kept}:
            return ClinvarConcordanceOut(
                annotated_release=release,
                compared=False,
                reason=f"Not compared: ClinVar {release.isoformat()}, the annotation's release, is not loaded in CoGA.",
            )
        stored = await clinvar_alleles_of_callset(self._assembly, release, family_uuid, source)
        return concordance(release, self._annotated, stored)


    async def report(
        self,
        session: AsyncSession,
        *,
        modules: Mapping[str, Mapping[str, Any]],
        family_uuid: str,
        source: str,
    ) -> dict[str, Any]:
        """The concordance as a loader's result reports it (``CONCORDANCE_KEY``), as JSON. Never
        fails the import: a comparison that fails is reported as not compared, by the error's
        kind."""
        try:
            kept = await recorded_clinvar_releases(session, self._assembly)
            result = await self.compare(modules=modules, kept=kept, family_uuid=family_uuid, source=source)
        except Exception as exc:  # noqa: BLE001 - reported by kind; the callset is stored
            logger.error("ClinVar concordance of an import not compared (%s)", describe_error(exc))
            result = ClinvarConcordanceOut(
                compared=False, reason=f"Not compared: the comparison failed ({describe_error(exc)})."
            )
        return result.model_dump(mode="json")


def _words(classification: str, review_status: str, variation_id: int | None) -> str:
    parts = [classification.replace("_", " ") or "no classification"]
    if review_status:
        parts.append(review_status)
    if variation_id is not None:
        parts.append(f"VariationID {variation_id}")
    return "; ".join(parts)


def _reason(annotated: _Annotated | None, coga: ClinvarStoredAllele | None) -> ClinvarConcordanceReason | None:
    """Why the allele's two values differ; None when they agree, or neither classifies it."""
    if annotated is None:
        return "not in the annotation" if coga is not None and coga.classification_terms else None
    if coga is None:
        return "not in CoGA's release"
    if annotated.terms != frozenset(coga.classification_terms):
        return "classification"
    if annotated.review_status != coga.review_status:
        return "review status"
    if annotated.variation_id != coga.variation_id:
        return "VariationID"
    return None


def concordance(
    release: date, annotated: Mapping[int, _Annotated], stored: Mapping[int, ClinvarStoredAllele]
) -> ClinvarConcordanceOut:
    """The comparison of the annotated alleles with the release's, by allele key."""
    agree = 0
    differences: list[ClinvarConcordanceDifferenceOut] = []
    for key in set(annotated) | set(stored):
        ours, theirs = annotated.get(key), stored.get(key)
        reason = _reason(ours, theirs)
        if reason is None:
            agree += ours is not None
            continue
        differences.append(
            ClinvarConcordanceDifferenceOut(
                variant_id=ours.variant_id if ours is not None else None,
                allele=theirs.variant_id if theirs is not None else ours.allele if ours is not None else "",
                reason=reason,
                annotated=_words(ours.classification, ours.review_status, ours.variation_id) if ours else None,
                coga=(
                    _words(theirs.classification, theirs.review_status, theirs.variation_id) if theirs else None
                ),
            )
        )
    differences.sort(key=lambda difference: (difference.variant_id or "", difference.allele))
    return ClinvarConcordanceOut(
        annotated_release=release,
        compared=True,
        agree=agree,
        differ=len(differences),
        differences=differences[:MAX_DIFFERENCES],
    )


def with_clinvar_concordance(summary: FamilyImportDatasetSummary) -> FamilyImportDatasetSummary:
    """A dataset's summary with the concordance its small-variant loader reported
    (``CONCORDANCE_KEY`` of its result) as a field of its own."""
    reported = summary.summary.get(CONCORDANCE_KEY)
    if reported is None:
        return summary
    return summary.model_copy(
        update={
            "summary": {key: value for key, value in summary.summary.items() if key != CONCORDANCE_KEY},
            "clinvar_concordance": ClinvarConcordanceOut.model_validate(reported),
        }
    )
