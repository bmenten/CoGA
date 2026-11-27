"""The re-analysis signal after sign-out (the ClinVar design's phase 3, §6.7; the owner's decision
B10): a read-only list, per project, of the signed analyses one of whose small variants CoGA's
current ClinVar release classifies otherwise than it was frozen.

Each family analysis is read in its latest signed version (``report_signouts``, the newest
version of each ``analysis_type``):

- a variant its record reports, its ClinVar (CoGA) as the signed version saw it: the value its
  drift section names where the classification had drifted or been noted at sign-out
  (``clinvar_coga_to``), else the one frozen with the classification (``evidence_snapshot``);
- a variant the analysis's review classifies (an ACMG classification with an evidence snapshot,
  the review the analysis reads: its own, else the shared one, ``effective_reviews``) and the
  record does not report, its value frozen with that classification.

A variant is listed when an ALT's classification differs, read as the drift check reads it
(``clinvar_coga_move``: by its terms; one where there was none, or none where there was one,
included). Another review status or other stars of the same classification is not a
reclassification (B5). CoGA changes nothing of the analysis: a signed version never changes,
and a re-analysis is a new one. Who follows the list up, and how, is the laboratory's procedure;
no mail goes out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import require_uuid
from ..schemas import ClinvarCogaOut, ClinvarReanalysisItemOut, ClinvarReanalysisOut
from .access_control import CurrentUser, RecordNotVisible, user_can_access_metadata_projects
from .analysis_reviews import effective_reviews
from .analysis_scope import AnalysisScope
from .classification_drift_service import clinvar_coga_move
from .clinvar_coga import Locus, clinvar_coga_read, clinvar_coga_values, locus_of_variant_id
from .family_analysis_types import family_analyses
from .report_code import report_code
from .report_signout_service import CLASS_SHOWN_KEY
from .review_pg_utils import review_class_label

@dataclass(frozen=True, slots=True)
class _Signed:
    """A family analysis's latest signed version: what the list reads of it."""

    family_uuid: str
    family_identifier: str
    analysis: str | None
    version: int
    signed_out_at: datetime
    report_code: str
    assembly: str | None
    snapshot: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _Frozen:
    """A variant of a signed analysis, with its class and its ClinVar (CoGA) as frozen."""

    signed: _Signed
    variant_id: str
    reported: bool
    classification: str | None
    clinvar_coga: list[Mapping[str, Any]]


async def _require_project(session: AsyncSession, project_id: str, user: CurrentUser) -> str:
    project = require_uuid(project_id, "Project not found", status_code=404)
    known = (
        await session.execute(
            text("SELECT EXISTS (SELECT 1 FROM projects WHERE id = CAST(:project AS uuid))"), {"project": project}
        )
    ).scalar_one()
    if not known or not user_can_access_metadata_projects([project], user):
        raise RecordNotVisible("project", "Project not found")
    return project


async def _latest_signed(session: AsyncSession, project: str) -> list[_Signed]:
    """Each family analysis of the project in its latest signed version."""
    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (r.family_id, r.snapshot->>'analysis_type')
                       r.family_id::text AS family_uuid, r.family_identifier, r.version,
                       r.signed_out_at, r.content_hash, r.snapshot->>'analysis_type' AS analysis,
                       r.snapshot->>'assembly' AS assembly, r.snapshot
                FROM report_signouts r
                JOIN family_projects fp ON fp.family_id = r.family_id
                WHERE fp.project_id = CAST(:project AS uuid)
                ORDER BY r.family_id, r.snapshot->>'analysis_type', r.version DESC
                """
            ),
            {"project": project},
        )
    ).mappings().all()
    signed: list[_Signed] = []
    for row in rows:
        code = report_code(row["family_identifier"], row["version"], row["content_hash"])
        assert code is not None  # every sign-out is made with its code (sign_out_report)
        signed.append(
            _Signed(
                family_uuid=str(row["family_uuid"]),
                family_identifier=str(row["family_identifier"]),
                analysis=row["analysis"],
                version=int(row["version"]),
                signed_out_at=row["signed_out_at"],
                report_code=code,
                assembly=row["assembly"],
                snapshot=row["snapshot"],
            )
        )
    return signed


def _reported(signed: _Signed) -> list[_Frozen]:
    """The variants the signed record reports, each with its ClinVar (CoGA) as the signed
    version saw it; a variant with no evidence frozen has none to compare, and is left out."""
    at_signout = {
        str(entry["variant_id"]): entry["clinvar_coga_to"]
        for entry in signed.snapshot["drift"]["drifted"]
        if entry.get("clinvar_coga_to") is not None
    }
    frozen: list[_Frozen] = []
    for entry in signed.snapshot["reported_variants"]:
        evidence = entry.get("evidence_snapshot")
        if evidence is None:
            continue
        variant_id = str(entry["variant_id"])
        frozen.append(
            _Frozen(
                signed=signed,
                variant_id=variant_id,
                reported=True,
                classification=entry.get(CLASS_SHOWN_KEY),
                clinvar_coga=at_signout.get(variant_id, evidence["clinvar_coga"]),
            )
        )
    return frozen


async def _classified(session: AsyncSession, signed: Sequence[_Signed]) -> list[_Frozen]:
    """The variants each signed analysis's reviews classify and its record does not report, each
    with its ClinVar (CoGA) frozen with the classification."""
    families = sorted({item.family_uuid for item in signed})
    if not families:
        return []
    rows = (
        await session.execute(
            text(
                """
                SELECT r.family_id::text AS family_uuid, r.analysis, r.variant_id, r.classification,
                       r.tags, r.acmg_evidence_snapshot, f.metadata
                FROM small_variant_reviews r
                JOIN families f ON f.id = r.family_id
                WHERE r.family_id = ANY(CAST(:families AS uuid[]))
                  AND r.acmg_evidence_snapshot IS NOT NULL
                """
            ),
            {"families": families},
        )
    ).mappings().all()
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_family[str(row["family_uuid"])].append(dict(row))
    frozen: list[_Frozen] = []
    for item in signed:
        reviews = by_family.get(item.family_uuid, [])
        if not reviews:
            continue
        reported = {str(entry["variant_id"]) for entry in item.snapshot["reported_variants"]}
        scope = AnalysisScope(analysis=item.analysis, held=family_analyses(reviews[0]["metadata"]))
        for variant_id, review in sorted(effective_reviews(reviews, scope).items()):
            if variant_id in reported:
                continue
            frozen.append(
                _Frozen(
                    signed=item,
                    variant_id=variant_id,
                    reported=False,
                    classification=review_class_label(review["classification"], review["tags"]),
                    clinvar_coga=review["acmg_evidence_snapshot"]["clinvar_coga"],
                )
            )
    return frozen


async def clinvar_reanalysis_list(session: AsyncSession, *, project_id: str, user: CurrentUser) -> ClinvarReanalysisOut:
    """The project's signed analyses with a variant CoGA's current ClinVar release classifies
    otherwise than it was frozen; 404 for a project outside the caller's, as for an unknown one."""
    project = await _require_project(session, project_id, user)
    signed = await _latest_signed(session, project)
    frozen = [variant for item in signed for variant in _reported(item)] + await _classified(session, signed)
    by_assembly: dict[str, list[tuple[_Frozen, Locus]]] = defaultdict(list)
    for variant in frozen:
        locus = locus_of_variant_id(variant.variant_id)
        if variant.signed.assembly and locus is not None:
            by_assembly[variant.signed.assembly].append((variant, locus))
    releases: dict[str, date | None] = {}
    items: list[ClinvarReanalysisItemOut] = []
    for assembly, pairs in sorted(by_assembly.items()):
        read = await clinvar_coga_read(session, assembly)
        releases[assembly] = read.release
        if read.release is None:
            continue
        now = await clinvar_coga_values(read, [locus for _variant, locus in pairs])
        for (variant, _locus), values in zip(pairs, now):
            current = [value.model_dump(mode="json") for value in values]
            if clinvar_coga_move(variant.clinvar_coga, current) != "classification":
                continue
            items.append(
                ClinvarReanalysisItemOut(
                    family_id=variant.signed.family_identifier,
                    analysis=variant.signed.analysis,
                    version=variant.signed.version,
                    signed_out_at=variant.signed.signed_out_at,
                    report_code=variant.signed.report_code,
                    variant_id=variant.variant_id,
                    reported=variant.reported,
                    classification=variant.classification,
                    clinvar_coga_frozen=[ClinvarCogaOut.model_validate(value) for value in variant.clinvar_coga],
                    clinvar_coga_now=values,
                )
            )
    items.sort(key=lambda item: (item.family_id, item.analysis or "", not item.reported, item.variant_id))
    return ClinvarReanalysisOut(project_id=project, releases=releases, items=items)
