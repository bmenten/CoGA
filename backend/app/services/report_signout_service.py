"""Case sign-out — frozen, versioned, hashed report snapshot (clinical traceability,
Phase 3; see docs/clinical-traceability.md).

Signing out a case freezes the reported result to exactly what produced it: the
annotation/reference versions (the manifest), the reported variant list, each
classification with its frozen evidence snapshot, and the evidence-drift state at the
moment of sign-out. The snapshot is content-hashed (SHA-256) and written append-only
into ``report_signouts`` as a new version; the sign-out is recorded in the immutable
clinical audit trail.

Sign-out is gated on evidence drift: if any classification's backing annotation has
changed since it was made — a small variant's ACMG classification, or a structural
variant's / CNV's ClinGen classification — the caller must explicitly acknowledge the
drift. It is also
gated on the Sample-integrity QC and on an incomplete import (a family-package import
that partly failed); each gate is acknowledged separately, with a reason that is frozen
into the snapshot and recorded in the audit event.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import scrub_log
from ..core.config import settings
from .annotation_manifest_service import (
    REFERENCE_MODULE_KEYS,
    UNAVAILABLE_MODULE_VERSION,
    get_family_annotation_manifest,
    module_label,
)
from .assembly_scope import is_validated_assembly, off_scope_message, validated_assemblies
from .classification_drift_service import evaluate_classification_drift
from .clinical_audit_service import record_clinical_event
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .hash_chain import ChainVerification, canonical_hash, chain_row_hash, verify_chain
from .access_control import CurrentUser
from .sample_integrity_qc import (
    MIN_MATERNAL_TRANSMISSION_SITES,
    MIN_MENDEL_SITES,
    MIN_PATERNITY_SITES,
    MIN_RELATEDNESS_SITES,
    SampleIntegrityReport,
)
from .sample_integrity_service import get_family_sample_integrity_qc

logger = logging.getLogger(__name__)

_REPORT_TAG = "report"

# Sample-integrity QC statuses that BLOCK sign-out unless acknowledged with a reason.
# A hard "fail" is a DETECTED mismatch (a sample/pedigree swap — TF-06 H4, rated S5
# catastrophic). Separately, a swap can manifest as MISSING data (a sample absent from
# the callset, a whole-family genotype-load failure, too few cfDNA sites): that leaves
# the swap-relevant check unable to run, so it stays "warn"/"skip"/"pass" and never
# reaches "fail". _unverifiable_swap_checks() gates those too (#330), so an unverifiable
# ASSERTED pedigree relationship cannot sign out silently. Both are acknowledge-with-
# reason (never a hard lockout); the reason is frozen into the content hash + audit.
_QC_BLOCKING_STATUSES = {"fail"}

# Pedigree-declared first-degree relationships whose swap check, when it cannot run,
# indicates missing data for an asserted edge (vs a non-asserted/"unrelated" pair).
_ASSERTED_RELATIONSHIPS = frozenset({"parent-child", "sibling"})

# The dataset lists an incomplete-import flag records; frozen sorted so the hash is stable.
_IMPORT_FLAG_DATASET_LISTS = ("failed_datasets", "imported_datasets")

# The snapshot section holding the SV/CNV evidence drift. Its absence marks a record
# signed before SV/CNV classifications froze their evidence.
STRUCTURAL_DRIFT_SECTION = "structural_drift"


def _canonical_hash(snapshot: dict[str, Any]) -> str:
    """SHA-256 over a canonical (sorted-key) JSON encoding — stable + tamper-evident."""
    return canonical_hash(snapshot)


def _as_snapshot(snapshot: Any) -> Any:
    """The JSONB snapshot decodes to a dict via the asyncpg codec; guard against a
    driver/serialisation change handing back the raw JSON string — which would make
    every on-read content re-verification a false 'tampered' alarm on a signed report."""
    if isinstance(snapshot, str):
        return json.loads(snapshot)
    return snapshot


def _signout_chain_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Immutable identity a report_signouts row's hash chain binds. Excludes the FK
    columns the SET-NULL carve-out may null (family_id/signed_out_by_id); content_hash
    already binds the snapshot, so the snapshot itself is not re-serialised here."""
    signed_out_at = row["signed_out_at"]
    return {
        "version": row["version"],
        "content_hash": row["content_hash"],
        "signed_out_at": (
            signed_out_at.isoformat()
            if hasattr(signed_out_at, "isoformat")
            else str(signed_out_at)
        ),
        "signed_out_by": row["signed_out_by"],
        "family_identifier": row.get("family_identifier"),
    }


def _canonical_sample_qc(report: SampleIntegrityReport) -> dict[str, Any]:
    """Deterministic dict of the Sample-integrity QC, for freezing into the snapshot.

    The report is a pure function of the deterministically-ordered input genotypes
    (counts/rates/inferred labels — no timestamps, no RNG), so ``dataclasses.asdict``
    yields a structure that content-hashes reproducibly for identical clinical content.
    """
    return dataclasses.asdict(report)


def _qc_failure_summary(qc: dict[str, Any]) -> dict[str, Any]:
    """Compact summary of the concerning QC checks, for the 409 acknowledge prompt."""
    messages: list[str] = []
    for key in ("sex_checks", "relatedness_checks", "mendelian_checks"):
        for check in qc.get(key) or []:
            if check.get("status") in ("warn", "fail"):
                messages.append(check.get("message") or "")
    for key in ("paternity_check", "fetal_sex_check", "category_qc_check"):
        check = qc.get(key)
        if check and check.get("status") in ("warn", "fail"):
            messages.append(check.get("message") or "")
    return {
        "overall_status": qc.get("overall_status"),
        "application_label": qc.get("application_label"),
        "messages": [m for m in messages if m],
    }


def _unverifiable_swap_checks(qc: dict[str, Any]) -> list[str]:
    """Asserted swap-relevant QC checks that COULD NOT RUN (too few informative sites).

    A genuine mismatch rolls up to "fail" (which already blocks). But a swap that
    manifests as MISSING data — a sample absent from the callset, a whole-family
    genotype-load failure, too few cfDNA sites — leaves the relevant check unable to
    run, so it stays "warn"/"skip"/"pass" and would otherwise sign out silently. Flag
    each ASSERTED (pedigree-declared) relationship whose swap check has too few
    informative sites to verify it. Scoped to asserted edges: non-asserted pairs and
    donor/unknown-parent edges never trigger.
    """
    reasons: list[str] = []
    for check in qc.get("relatedness_checks") or []:
        if (
            check.get("expected_relationship") in _ASSERTED_RELATIONSHIPS
            and int(check.get("informative_sites") or 0) < MIN_RELATEDNESS_SITES
        ):
            reasons.append(
                check.get("message")
                or (
                    f"Relatedness of {check.get('sample_a')}–{check.get('sample_b')} "
                    f"(expected {check.get('expected_relationship')}) could not be verified."
                )
            )
    # Mendelian checks are only generated for asserted child+parent(s), so any with too
    # few informative sites is an unverifiable asserted trio — distinct from an elevated
    # but measurable error rate, which is a ran-and-concerning "warn" we leave alone.
    for check in qc.get("mendelian_checks") or []:
        if int(check.get("informative_sites") or 0) < MIN_MENDEL_SITES:
            reasons.append(
                check.get("message")
                or f"Mendelian consistency for {check.get('child')} could not be verified."
            )
    paternity = qc.get("paternity_check")
    if paternity and int(paternity.get("informative_sites") or 0) < MIN_PATERNITY_SITES:
        reasons.append(
            paternity.get("message")
            or "NIPT paternity could not be verified (too few paternal-informative sites)."
        )
    # The NIPT category QC silently reports "pass" when the maternal-transmission block
    # is skipped for too few maternal-informative sites — i.e. a wrong/absent mother is
    # not caught. Treat that unverifiable maternal lineage as gating.
    category = qc.get("category_qc_check")
    if (
        category
        and int(category.get("maternal_informative") or 0) < MIN_MATERNAL_TRANSMISSION_SITES
    ):
        reasons.append(
            "NIPT maternal lineage could not be verified (too few maternal-informative "
            "sites) — possible wrong mother."
        )

    # Samples anchored by an asserted relatedness/Mendelian edge have their identity
    # covered there. Sex is the ONLY identity signal for a sample with no asserted edge
    # (the single/couple profiles, or an added relative), so a sex call that could not be
    # made for such a sample — the sample absent from its callset, or no usable chrX — is
    # an otherwise-ungated swap gap. (A sex MISmatch already rolls up to "fail".)
    anchored: set[str] = set()
    for check in qc.get("relatedness_checks") or []:
        if check.get("expected_relationship") in _ASSERTED_RELATIONSHIPS:
            anchored.add(check.get("sample_a"))
            anchored.add(check.get("sample_b"))
    for check in qc.get("mendelian_checks") or []:
        anchored.add(check.get("child"))
        anchored.update(check.get("parents") or [])
    for check in qc.get("sex_checks") or []:
        if check.get("inferred_sex") == "indeterminate" and check.get("sample_id") not in anchored:
            reasons.append(
                check.get("message")
                or (
                    f"Sex/identity of {check.get('sample_id')} could not be verified, and no "
                    "relatedness check anchors this sample."
                )
            )

    # NIPT integrity rests entirely on the cfDNA paternity + category-QC checks. If
    # NEITHER ran — the maternal/paternal trio could not be resolved, or the cfDNA
    # analysis failed — a prenatal report would otherwise sign out with no identity
    # evidence at all (the checks are None and nothing rolls up to "fail").
    if (
        qc.get("application") == "nipt"
        and qc.get("paternity_check") is None
        and qc.get("category_qc_check") is None
    ):
        reasons.append(
            "NIPT sample integrity could not be assessed — the trio could not be resolved "
            "or the cfDNA analysis did not run (no paternity or maternal-lineage check)."
        )
    return reasons


def _qc_gate_message(qc_status: str, hard_fail: bool, unverifiable: list[str]) -> str:
    if hard_fail:
        return (
            f"Sample-integrity QC status is '{qc_status}' (possible sample or pedigree "
            "swap — TF-06 H4). Resolve it, or acknowledge with a reason to sign out anyway."
        )
    return (
        "A swap-relevant sample-integrity check could not be verified for an asserted "
        f"pedigree relationship ({'; '.join(unverifiable)}). A sample swap can present as "
        "missing data rather than a mismatch. Resolve it, or acknowledge with a reason to "
        "sign out anyway."
    )


async def _import_incomplete_state(
    session: AsyncSession, family_uuid: str
) -> dict[str, Any] | None:
    """What the family's package import left incomplete, or None when nothing is.

    A family-package import that partly fails keeps the family and the datasets that did
    import, and stamps ``families.metadata.import_incomplete`` with when it ran, which
    datasets failed, which imported and the import job whose record holds each dataset's
    error (``family_package_registration._flag_family_import_incomplete``); a later,
    fully successful import removes it. Frozen into the snapshot and gating sign-out, so
    a report on partly loaded data is never released as if it were complete.

    Fails safe: a flag that is set but not in the shape the import writes still counts
    as incomplete, with nothing to name. A flag written before the job was recorded
    reads the same, with ``job_id`` None.
    """
    raw = (
        await session.execute(
            text(
                "SELECT metadata -> 'import_incomplete' FROM families "
                "WHERE id = CAST(:family_uuid AS uuid)"
            ),
            {"family_uuid": family_uuid},
        )
    ).scalar_one_or_none()
    if isinstance(raw, str):
        # jsonb decodes through the asyncpg codec; a driver that hands back the JSON
        # text instead must read the same.
        try:
            raw = json.loads(raw)
        except ValueError:
            pass  # set, but not JSON the import wrote: still incomplete
    if raw is None or raw is False:
        return None
    flag: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    at = flag.get("at")
    state: dict[str, Any] = {"at": str(at) if at else None}
    for key in _IMPORT_FLAG_DATASET_LISTS:
        values = flag.get(key)
        state[key] = (
            sorted({str(value) for value in values if value}) if isinstance(values, list) else []
        )
    job_id = flag.get("job_id")
    state["job_id"] = job_id if isinstance(job_id, str) and job_id else None
    return state


def _import_gate_message(state: Mapping[str, Any]) -> str:
    failed = list(state.get("failed_datasets") or [])
    imported = list(state.get("imported_datasets") or [])
    when = f" ({state['at']})" if state.get("at") else ""
    if failed:
        what = f"the package import{when} failed for {', '.join(failed)}"
        if imported:
            what += f" ({', '.join(imported)} did import)"
    else:
        what = (
            f"a package import{when} did not complete, and which datasets it left out "
            "was not recorded"
        )
    job = (
        f" Each dataset's error is recorded in import job {state['job_id']}."
        if state.get("job_id")
        else ""
    )
    return (
        f"This family's data is incomplete: {what}. The report may lack data from what "
        f"failed.{job} Re-run the import to complete it, or acknowledge with a reason to "
        "sign out anyway."
    )


def _dataset_list(value: Any) -> list[str] | None:
    """A dataset list extracted from the JSONB snapshot (None when it has none)."""
    value = _as_snapshot(value)
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


async def _reported_reviews(session: AsyncSession, family_uuid: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT variant_id, acmg_class, acmg, tags, note, acmg_evidence_snapshot
                FROM small_variant_reviews
                WHERE family_id = CAST(:family_uuid AS uuid)
                  AND tags @> :report_tag
                ORDER BY variant_id
                """
            ),
            {"family_uuid": family_uuid, "report_tag": json.dumps([_REPORT_TAG])},
        )
    ).mappings().all()
    reported: list[dict[str, Any]] = []
    for row in rows:
        reported.append(
            {
                "variant_id": row["variant_id"],
                "acmg_class": row["acmg_class"],
                "acmg": row["acmg"],
                "tags": sorted(row["tags"] or []),
                "note": row["note"],
                "evidence_snapshot": row["acmg_evidence_snapshot"],
            }
        )
    return reported


async def _reported_structural_reviews(
    session: AsyncSession, family_uuid: str
) -> list[dict[str, Any]]:
    """Structural variants / CNVs tagged for the report, with their classification and the
    evidence its CNV scoring froze.

    The report page renders these beside the small variants, so the signed record must
    freeze them too — before #508 a reported CNV was printed but never signed.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT variant_id, variant_key, classification, cnv_class, cnv_point_total,
                       cnv_acmg, tags, note, cnv_evidence_snapshot
                FROM structural_variant_reviews
                WHERE family_id = CAST(:family_uuid AS uuid)
                  AND tags @> :report_tag
                ORDER BY variant_id NULLS LAST, variant_key NULLS LAST
                """
            ),
            {"family_uuid": family_uuid, "report_tag": json.dumps([_REPORT_TAG])},
        )
    ).mappings().all()
    return [
        {
            "variant_id": row["variant_id"],
            "variant_key": row["variant_key"],
            "classification": row["classification"],
            "cnv_class": row["cnv_class"],
            "cnv_point_total": row["cnv_point_total"],
            "cnv_acmg": row["cnv_acmg"],
            "tags": sorted(row["tags"] or []),
            "note": row["note"],
            "evidence_snapshot": row["cnv_evidence_snapshot"],
        }
        for row in rows
    ]


async def _canonical_sequencing_qc(
    session: AsyncSession,
    context: FamilyMetadataContext,
) -> dict[str, Any]:
    """The sequencing-QC verdicts and the cut-offs in force, for freezing.

    Deterministic: profiles and metrics are sorted, and the values are the same numbers
    the workspace showed. Best-effort — a family imported without QC outputs simply has
    nothing here, and a QC display has never been allowed to break sign-out.
    """

    from .qc_threshold_service import evaluate_sequencing_qc, resolve_family_qc_thresholds

    try:
        resolved = await resolve_family_qc_thresholds(session, family_uuid=context.family_uuid)
    except Exception:  # noqa: BLE001 — see the docstring
        logger.exception("Could not resolve QC thresholds for the report snapshot")
        # Frozen as explicitly unavailable, so the signed record does not read like a
        # family imported without QC outputs (#514). The marker appears only on failure,
        # so the hash of an ordinary snapshot is unchanged.
        return {
            "profile_key": None,
            "profile_label": None,
            "thresholds": {},
            "samples": {},
            "unavailable": "QC thresholds could not be resolved",
        }

    thresholds = resolved.get("thresholds") or {}
    samples: dict[str, Any] = {}
    for row in context.sample_rows:
        sample_id = row.get("sample_id")
        # `sample_metadata`, not `metadata` — the context query aliases it, and reading
        # the wrong key yields an empty map with no error at all.
        metadata = row.get("sample_metadata") or {}
        sequencing_qc = metadata.get("sequencing_qc") if isinstance(metadata, dict) else None
        evaluation = evaluate_sequencing_qc(sequencing_qc, thresholds)
        if sample_id and evaluation is not None:
            samples[str(sample_id)] = evaluation
    return {
        "profile_key": resolved.get("profile_key"),
        "profile_label": resolved.get("profile_label"),
        # Only the metrics that actually had a cut-off: an unconfigured metric gated
        # nothing, and listing it would imply otherwise.
        "thresholds": {key: thresholds[key] for key in sorted(thresholds)},
        "samples": {key: samples[key] for key in sorted(samples)},
    }


# The reference-layer modules the software looked up before a snapshot recorded the list
# (see REFERENCE_MODULE_KEYS): a record signed then holds no HPO release.
_LEGACY_REFERENCE_MODULES: tuple[str, ...] = ("assembly", "gene_loci", "monarch")


def _reference_modules_looked_up(snapshot: Mapping[str, Any]) -> set[str]:
    """The reference-layer modules the software that built ``snapshot`` looked up."""
    recorded = snapshot.get("reference_modules")
    if isinstance(recorded, list):
        return {str(key) for key in recorded}
    return set(_LEGACY_REFERENCE_MODULES)


def _module_keys(modules: Any) -> set[str]:
    return {
        str(module.get("key"))
        for module in (modules if isinstance(modules, list) else [])
        if isinstance(module, Mapping)
    }


def snapshot_gaps(snapshot: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """The parts a report snapshot records as unavailable, with the reason (#514).

    A lookup that failed while the snapshot was built is frozen as an explicit marker,
    never as an empty block that reads like "nothing there". This lists those markers so
    the audit event and the report page can say what the signed record lacks. It also
    lists a reference module a record signed before CoGA recorded it does not hold (the
    HPO release): the record cannot say which version its report was produced with. And,
    for a record signed before SV/CNV classifications froze their evidence, that its
    reported structural variants carry none.
    """
    if not isinstance(snapshot, Mapping):
        return []
    gaps: list[dict[str, str]] = []
    sequencing_qc = snapshot.get("sequencing_qc")
    if isinstance(sequencing_qc, Mapping) and sequencing_qc.get("unavailable"):
        gaps.append(
            {
                "section": "sequencing_qc",
                "item": "Sequencing-QC cut-offs",
                "reason": str(sequencing_qc["unavailable"]),
            }
        )
    for module in snapshot.get("modules") or []:
        if isinstance(module, Mapping) and module.get("version") == UNAVAILABLE_MODULE_VERSION:
            gaps.append(
                {
                    "section": "modules",
                    "item": str(module.get("label") or module.get("key") or "module"),
                    "reason": str(module.get("detail") or "version could not be read"),
                }
            )
    if "modules" in snapshot:
        held = _module_keys(snapshot.get("modules"))
        looked_up = _reference_modules_looked_up(snapshot)
        for key in REFERENCE_MODULE_KEYS:
            if key not in looked_up and key not in held:
                gaps.append(
                    {
                        "section": "modules",
                        "item": module_label(key),
                        "reason": "signed before CoGA recorded its version",
                    }
                )
    if STRUCTURAL_DRIFT_SECTION not in snapshot and snapshot.get("reported_structural_variants"):
        gaps.append(
            {
                "section": "reported_structural_variants",
                "item": "Evidence of the reported structural variants and CNVs",
                "reason": "signed before CoGA froze it",
            }
        )
    return gaps


async def build_report_snapshot(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Assemble (but do not persist) the frozen report snapshot for a family."""
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    manifest = await get_family_annotation_manifest(
        session, family_id=family_id, user=user, project_id=project_id
    )
    drift = await evaluate_classification_drift(
        session, family_id=family_id, user=user, project_id=project_id
    )
    qc_report = await get_family_sample_integrity_qc(
        session, family_id=family_id, user=user, project_id=project_id
    )
    reported = await _reported_reviews(session, context.family_uuid)
    reported_structural = await _reported_structural_reviews(session, context.family_uuid)
    sequencing_qc = await _canonical_sequencing_qc(session, context)
    import_incomplete = await _import_incomplete_state(session, context.family_uuid)
    # A reported classification with no frozen evidence snapshot cannot be drift-verified
    # (evaluate_classification_drift only checks reviews that HAVE a snapshot), so it would
    # otherwise clear the sign-out drift gate unchallenged. Surface each as a "no_snapshot"
    # drift entry so the gate requires acknowledgement (#332).
    unsnapshotted = [
        {
            "variant_id": r["variant_id"],
            "acmg_class": r.get("acmg_class"),
            "status": "no_snapshot",
        }
        for r in reported
        if not r.get("evidence_snapshot")
    ]
    drifted = sorted(
        [*drift["drifted"], *unsnapshotted],
        key=lambda item: item.get("variant_id") or "",
    )
    # The same for the reported structural variants and CNVs: their classification's
    # frozen evidence against the SV as it is now, and a reported one whose evidence was
    # never frozen (no CNV scoring saved, or one saved before CoGA froze it) as
    # "no_snapshot".
    structural = drift["structural"]
    structural_unsnapshotted = [
        {
            "variant_id": r["variant_id"],
            "classification": r.get("classification"),
            "cnv_class": r.get("cnv_class"),
            "status": "no_snapshot",
        }
        for r in reported_structural
        if not r.get("evidence_snapshot")
    ]
    structural_drifted = sorted(
        [*structural["drifted"], *structural_unsnapshotted],
        key=lambda item: item.get("variant_id") or "",
    )
    return {
        "family_id": context.family_id,
        "assembly": manifest.get("assembly"),
        "modules": manifest.get("modules", []),
        # The reference-layer modules looked up for `modules`: one of them missing there
        # was not loaded at sign-out. A record without this list was signed before CoGA
        # recorded the HPO release, which is how the sign-out check tells the two apart.
        "reference_modules": list(REFERENCE_MODULE_KEYS),
        # Build identity of the software that produced this snapshot, frozen into the
        # content hash so a signed report is bound to the exact code that made it.
        # These are build-time constants (no per-call/runtime-varying value), so the
        # hash stays deterministic for identical clinical content.
        "software": {"version": settings.app_version, "git_sha": settings.git_sha},
        "drift": {
            "checked": drift["checked"] + len(unsnapshotted),
            "drifted_count": len(drifted),
            # Sorted by the unique, stable variant_id so the hashed snapshot
            # (content_hash) is reproducible for identical content — json.dumps(sort_keys)
            # canonicalizes dict keys but never list-element order. Includes reported
            # classifications with no snapshot ("no_snapshot"), which can't be drift-
            # verified and so must gate. Scoped to the sign-out/hash path only — the live
            # drift endpoint keeps its most-recent-first display order.
            "drifted": drifted,
        },
        # The evidence drift of the structural-variant and CNV classifications, sorted and
        # gated like `drift`. A record signed without this section predates the SV/CNV
        # evidence snapshot: the sign-out check does not compare it (nor the evidence in
        # its reported SVs), and snapshot_gaps says it holds none.
        STRUCTURAL_DRIFT_SECTION: {
            "checked": structural["checked"] + len(structural_unsnapshotted),
            "drifted_count": len(structural_drifted),
            "drifted": structural_drifted,
        },
        # Sample-integrity QC (sample/pedigree-swap detection) frozen into the content
        # hash so the signed record proves QC was run and exactly what it found. A pure
        # function of the deterministically-ordered input genotypes, so it hashes stably.
        "sample_qc": _canonical_sample_qc(qc_report),
        # Sequencing QC and, crucially, the cut-offs it was judged against. These are
        # advisory — nothing is withheld from a report because of them — but the chip a
        # reviewer saw beside each sample said "warning" or nothing *relative to limits
        # that can be changed afterwards*. Without freezing them, a signed report cannot
        # say what its own QC display meant at the time.
        "sequencing_qc": sequencing_qc,
        # Whether the family's data loaded completely: None, or what a partly failed
        # package import left out. A report on partly loaded data can lack whole datasets,
        # so the signed record says which were missing when it was signed.
        "import_incomplete": import_incomplete,
        "reported_variants": reported,
        "reported_structural_variants": reported_structural,
    }


async def _next_version(session: AsyncSession, family_uuid: str) -> int:
    result = await session.execute(
        text(
            "SELECT COALESCE(MAX(version), 0) FROM report_signouts "
            "WHERE family_id = CAST(:family_uuid AS uuid)"
        ),
        {"family_uuid": family_uuid},
    )
    return int(result.scalar_one() or 0) + 1


def _serialize_signout(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": row["version"],
        "signed_out_by": row["signed_out_by"],
        "signed_out_at": row["signed_out_at"],
        "content_hash": row["content_hash"],
        "software_version": row.get("software_version"),
        "git_sha": row.get("git_sha"),
        "qc_status": row.get("qc_status"),
        "qc_acknowledged": row.get("qc_acknowledged"),
        "qc_acknowledgement_reason": row.get("qc_acknowledgement_reason"),
        "drift_acknowledged": row.get("drift_acknowledged"),
        "drift_acknowledgement_reason": row.get("drift_acknowledgement_reason"),
        "import_incomplete_failed_datasets": _dataset_list(
            row.get("import_incomplete_failed_datasets")
        ),
        "import_incomplete_job_id": row.get("import_incomplete_job_id"),
        "import_incomplete_acknowledged": row.get("import_incomplete_acknowledged"),
        "import_incomplete_acknowledgement_reason": row.get(
            "import_incomplete_acknowledgement_reason"
        ),
        "verified": row.get("verified"),
        "snapshot": row.get("snapshot"),
    }


async def sign_out_report(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    acknowledge_drift: bool = False,
    drift_acknowledgement_reason: str | None = None,
    acknowledge_qc: bool = False,
    qc_acknowledgement_reason: str | None = None,
    acknowledge_import_incomplete: bool = False,
    import_incomplete_acknowledgement_reason: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    # Off-scope gate (TF-06 H12), first and not overridable: a report on an assembly the
    # device is not validated on must not become a signed clinical record (#515). The
    # structured detail keeps the report page from offering the drift override for it.
    assembly_name = getattr(context, "assembly_name", None)
    if not is_validated_assembly(assembly_name):
        raise HTTPException(
            status_code=409,
            detail={
                "gate": "assembly_scope",
                "message": off_scope_message(assembly_name),
                "assembly": assembly_name,
                "validated_assemblies": validated_assemblies(),
            },
        )
    snapshot_body = await build_report_snapshot(
        session, family_id=family_id, user=user, project_id=project_id
    )

    # One gate, one acknowledgement: the small-variant and the SV/CNV drift together.
    structural_drifted_count = snapshot_body[STRUCTURAL_DRIFT_SECTION]["drifted_count"]
    drifted_count = snapshot_body["drift"]["drifted_count"] + structural_drifted_count
    if drifted_count and not acknowledge_drift:
        structural_part = (
            f" ({structural_drifted_count} of them structural-variant or CNV classifications)"
            if structural_drifted_count
            else ""
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"{drifted_count} classification(s){structural_part} have evidence that "
                "changed, or that cannot be verified (no frozen snapshot), since they were "
                "made. Re-review, or acknowledge the drift to sign out anyway."
            ),
        )
    # Overriding the drift gate is attested like the QC override: with a reason that is
    # frozen into the content hash and the audit event (#508). Before, it was a bare
    # confirm with nothing recorded about why stale evidence was signed.
    drift_reason = (drift_acknowledgement_reason or "").strip()
    if drifted_count and acknowledge_drift and not drift_reason:
        raise HTTPException(
            status_code=422,
            detail="A reason is required to acknowledge evidence drift.",
        )

    # Sample-QC gate (after the drift gate; each gate guards an independent concern and
    # is acknowledged independently). Only a hard "fail" blocks (TF-06 H4 sample/pedigree
    # swap, S5 catastrophic); acknowledging requires a non-empty reason, which — like the
    # QC verdict itself — is frozen into the content hash below.
    sample_qc = snapshot_body["sample_qc"]
    qc_status = sample_qc["overall_status"]
    qc_hard_fail = qc_status in _QC_BLOCKING_STATUSES
    # A swap that manifests as MISSING data leaves the swap-relevant check unable to run
    # (it never becomes "fail"); gate those unverifiable asserted-pedigree checks too so
    # they cannot sign out silently (#330).
    qc_unverifiable = _unverifiable_swap_checks(sample_qc)
    qc_blocks = qc_hard_fail or bool(qc_unverifiable)
    if qc_blocks and not acknowledge_qc:
        raise HTTPException(
            status_code=409,
            detail={
                "gate": "sample_qc",
                "message": _qc_gate_message(qc_status, qc_hard_fail, qc_unverifiable),
                "qc_summary": _qc_failure_summary(sample_qc),
                "unverifiable_checks": qc_unverifiable,
            },
        )
    qc_reason = (qc_acknowledgement_reason or "").strip()
    if qc_blocks and acknowledge_qc and not qc_reason:
        raise HTTPException(
            status_code=422,
            detail="A reason is required to acknowledge a sample-integrity QC concern.",
        )

    # Incomplete-import gate (after the QC gate, and like it acknowledged independently
    # with a reason frozen into the content hash). A family-package import that partly
    # failed keeps the family, and the datasets that did import, flagged import_incomplete.
    # Nothing read that flag before, so a report that could lack whole datasets signed
    # out with no warning at all.
    import_incomplete: Mapping[str, Any] | None = snapshot_body.get("import_incomplete")
    if import_incomplete is not None and not acknowledge_import_incomplete:
        raise HTTPException(
            status_code=409,
            detail={
                "gate": "import_incomplete",
                "message": _import_gate_message(import_incomplete),
                "import_incomplete": import_incomplete,
            },
        )
    import_acknowledged = import_incomplete is not None and acknowledge_import_incomplete
    import_reason = (import_incomplete_acknowledgement_reason or "").strip()
    if import_acknowledged and not import_reason:
        raise HTTPException(
            status_code=422,
            detail="A reason is required to acknowledge an incomplete import.",
        )

    # Per-family advisory lock: makes version selection + chain-head read + insert
    # atomic for this family (also closes a latent version race). Transaction-scoped;
    # released on the commit/rollback below.
    # Partition the chain on the IMMUTABLE family_identifier (not the mutable family_id,
    # which an ON DELETE SET NULL cascade nulls), so a deleted family's signed history
    # stays verifiable. family_id↔family_identifier are 1:1 so version ordering is intact.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"rs:{context.family_id}"},
    )
    prev_hash = (
        await session.execute(
            text(
                "SELECT row_hash FROM report_signouts "
                "WHERE family_identifier IS NOT DISTINCT FROM :fident "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"fident": context.family_id},
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    version = await _next_version(session, context.family_uuid)
    actor = getattr(user, "username", None) or getattr(user, "email", "") or "unknown"

    snapshot = {
        **snapshot_body,
        "version": version,
        "generated_at": now.isoformat(),
        "signed_out_by": actor,
        "acknowledged_drift": bool(drifted_count) and acknowledge_drift,
        "drift_acknowledgement_reason": (
            drift_reason if (drifted_count and acknowledge_drift) else None
        ),
        "acknowledged_qc": qc_blocks and acknowledge_qc,
        "qc_acknowledgement_reason": qc_reason if (qc_blocks and acknowledge_qc) else None,
        "acknowledged_import_incomplete": import_acknowledged,
        "import_incomplete_acknowledgement_reason": (
            import_reason if import_acknowledged else None
        ),
    }
    content_hash = _canonical_hash(snapshot)
    row_hash = chain_row_hash(
        prev_hash,
        _signout_chain_payload(
            {
                "version": version,
                "content_hash": content_hash,
                "signed_out_at": now,
                "signed_out_by": actor,
                "family_identifier": context.family_id,
            }
        ),
    )

    await session.execute(
        text(
            """
            INSERT INTO report_signouts
                (family_id, family_identifier, version, signed_out_by, signed_out_by_id,
                 signed_out_at, content_hash, snapshot, row_hash, prev_hash)
            VALUES
                (CAST(:family_id AS uuid), :family_identifier, :version, :signed_out_by,
                 CAST(:signed_out_by_id AS uuid), :signed_out_at, :content_hash,
                 CAST(:snapshot AS jsonb), :row_hash, :prev_hash)
            """
        ),
        {
            "family_id": context.family_uuid,
            "family_identifier": context.family_id,
            "version": version,
            "signed_out_by": actor,
            "signed_out_by_id": getattr(user, "id", None),
            "signed_out_at": now,
            "content_hash": content_hash,
            "snapshot": json.dumps(snapshot, default=str),
            "row_hash": row_hash,
            "prev_hash": prev_hash,
        },
    )
    gaps = snapshot_gaps(snapshot_body)
    await record_clinical_event(
        session,
        family_uuid=context.family_uuid,
        family_identifier=context.family_id,
        variant_id=None,
        actor=actor,
        actor_id=getattr(user, "id", None),
        action="sign_out",
        summary=(
            f"Report signed out (v{version}) — {len(snapshot_body['reported_variants'])} "
            f"reported variant(s), "
            f"{len(snapshot_body['reported_structural_variants'])} reported structural "
            f"variant(s){', drift acknowledged' if snapshot['acknowledged_drift'] else ''}"
            f"{', QC override acknowledged' if snapshot['acknowledged_qc'] else ''}"
            f"{', incomplete import acknowledged' if import_acknowledged else ''}"
            f"{f', {len(gaps)} part(s) not captured' if gaps else ''}"
        ),
        after={
            "version": version,
            "content_hash": content_hash,
            "software_version": snapshot_body["software"]["version"],
            "git_sha": snapshot_body["software"]["git_sha"],
            "reported_count": len(snapshot_body["reported_variants"]),
            "reported_structural_count": len(snapshot_body["reported_structural_variants"]),
            # Every classification the drift gate counted, and how many are SVs or CNVs.
            "drifted_count": drifted_count,
            "structural_drifted_count": structural_drifted_count,
            "drift_acknowledgement_reason": snapshot["drift_acknowledgement_reason"],
            "qc_status": qc_status,
            "acknowledged_qc": snapshot["acknowledged_qc"],
            "qc_acknowledgement_reason": snapshot["qc_acknowledgement_reason"],
            "qc_unverifiable": qc_unverifiable,
            # What the family's import left out when it was signed (None: nothing), and
            # the override of that gate.
            "import_incomplete": import_incomplete,
            "acknowledged_import_incomplete": import_acknowledged,
            "import_incomplete_acknowledgement_reason": snapshot[
                "import_incomplete_acknowledgement_reason"
            ],
            # What the signed record could not capture (a failed lookup), so the trail
            # says the record is incomplete rather than leaving it to be noticed (#514).
            "not_captured": gaps,
        },
    )
    await session.commit()
    return {
        "version": version,
        "signed_out_by": actor,
        "signed_out_at": now,
        "content_hash": content_hash,
        # Surface the frozen identity at top level so the POST response matches the
        # GET list/detail contract (which extracts it from the JSONB snapshot).
        "software_version": snapshot_body["software"]["version"],
        "git_sha": snapshot_body["software"]["git_sha"],
        "drift_acknowledged": snapshot["acknowledged_drift"],
        "drift_acknowledgement_reason": snapshot["drift_acknowledgement_reason"],
        "import_incomplete_failed_datasets": (
            import_incomplete.get("failed_datasets") if import_incomplete is not None else None
        ),
        "import_incomplete_job_id": (
            import_incomplete.get("job_id") if import_incomplete is not None else None
        ),
        "import_incomplete_acknowledged": import_acknowledged,
        "import_incomplete_acknowledgement_reason": snapshot[
            "import_incomplete_acknowledgement_reason"
        ],
        "snapshot": snapshot,
        # As a detail read returns it: what the new record could not capture.
        "not_captured": gaps,
    }


async def list_report_signouts(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    rows = (
        await session.execute(
            text(
                """
                SELECT version, signed_out_by, signed_out_at, content_hash,
                       snapshot->'software'->>'version' AS software_version,
                       snapshot->'software'->>'git_sha'  AS git_sha,
                       snapshot->'sample_qc'->>'overall_status' AS qc_status,
                       (snapshot->>'acknowledged_qc')::boolean   AS qc_acknowledged,
                       snapshot->>'qc_acknowledgement_reason'    AS qc_acknowledgement_reason,
                       (snapshot->>'acknowledged_drift')::boolean AS drift_acknowledged,
                       snapshot->>'drift_acknowledgement_reason' AS drift_acknowledgement_reason,
                       snapshot->'import_incomplete'->'failed_datasets'
                           AS import_incomplete_failed_datasets,
                       snapshot->'import_incomplete'->>'job_id' AS import_incomplete_job_id,
                       (snapshot->>'acknowledged_import_incomplete')::boolean
                           AS import_incomplete_acknowledged,
                       snapshot->>'import_incomplete_acknowledgement_reason'
                           AS import_incomplete_acknowledgement_reason
                FROM report_signouts
                WHERE family_id = CAST(:family_uuid AS uuid)
                ORDER BY version DESC
                """
            ),
            {"family_uuid": context.family_uuid},
        )
    ).mappings().all()
    signouts = [_serialize_signout(dict(row)) for row in rows]
    return {
        "family_id": context.family_id,
        "latest": signouts[0] if signouts else None,
        "signouts": signouts,
    }


async def get_report_signout(
    session: AsyncSession,
    *,
    family_id: str,
    version: int,
    user: CurrentUser,
    project_id: str | None = None,
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    row = (
        await session.execute(
            text(
                """
                SELECT version, signed_out_by, signed_out_at, content_hash, snapshot,
                       snapshot->'software'->>'version' AS software_version,
                       snapshot->'software'->>'git_sha'  AS git_sha,
                       snapshot->'sample_qc'->>'overall_status' AS qc_status,
                       (snapshot->>'acknowledged_qc')::boolean   AS qc_acknowledged,
                       snapshot->>'qc_acknowledgement_reason'    AS qc_acknowledgement_reason,
                       (snapshot->>'acknowledged_drift')::boolean AS drift_acknowledged,
                       snapshot->>'drift_acknowledgement_reason' AS drift_acknowledgement_reason,
                       snapshot->'import_incomplete'->'failed_datasets'
                           AS import_incomplete_failed_datasets,
                       snapshot->'import_incomplete'->>'job_id' AS import_incomplete_job_id,
                       (snapshot->>'acknowledged_import_incomplete')::boolean
                           AS import_incomplete_acknowledged,
                       snapshot->>'import_incomplete_acknowledgement_reason'
                           AS import_incomplete_acknowledgement_reason
                FROM report_signouts
                WHERE family_id = CAST(:family_uuid AS uuid) AND version = :version
                """
            ),
            {"family_uuid": context.family_uuid, "version": version},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Sign-out version not found")
    record = dict(row)
    # Served as the object it was verified as, even from a driver that hands back text.
    record["snapshot"] = _as_snapshot(record["snapshot"])
    # Re-verify the stored content hash against the snapshot on read (tamper-evidence).
    # A mismatch means the immutable snapshot was altered out-of-band; surface it as a
    # non-fatal flag — the record must still be returned so an auditor can inspect it.
    recomputed = _canonical_hash(record["snapshot"])
    record["verified"] = recomputed == record["content_hash"]
    if not record["verified"]:
        logger.error(
            "report_signout content hash mismatch on read (possible tampering)",
            extra={
                "family_id": scrub_log(context.family_id),
                "signout_version": version,
                "stored_content_hash": record["content_hash"],
                "recomputed_content_hash": recomputed,
            },
        )
    detail = _serialize_signout(record)
    # The report page renders a signed version from its snapshot alone, so it must be able
    # to say what that record lacks — for any version, not only the latest (#514).
    detail["not_captured"] = snapshot_gaps(record["snapshot"])
    return detail


# The snapshot sections that make up the report's clinical content. The build identity
# ("software") is left out on purpose: a newer build over identical content has not
# changed what was signed. The per-sign-out fields (version, signer, timestamp,
# acknowledgements) are not report content either. The import state is: a family
# re-imported since sign-out, completing it or leaving it incomplete, no longer holds the
# data that was signed.
REPORT_CONTENT_SECTIONS = (
    "assembly",
    "modules",
    "reported_variants",
    "reported_structural_variants",
    "drift",
    STRUCTURAL_DRIFT_SECTION,
    "sample_qc",
    "sequencing_qc",
    "import_incomplete",
)
# A sign-out made before a section was frozen has no value for it. For the reported
# structural variants that means none were signed — the page could show them but the
# snapshot could not hold them — so the absent section compares as an empty list. Any
# other absent section cannot be compared and is reported as such.
_ABSENT_SECTION_DEFAULTS: dict[str, Any] = {"reported_structural_variants": []}


def _section_fingerprint(value: Any) -> str:
    # Normalise through the JSON encoding the stored snapshot went through, so a value
    # read back from JSONB and a freshly built one compare equal when unchanged.
    return _canonical_hash(json.loads(json.dumps(value, default=str)))


def _modules_comparable_with(
    signed: Mapping[str, Any], current: list[Any]
) -> tuple[list[Any], list[str]]:
    """The current modules to compare with a signed record's, and the keys left out.

    A reference-layer module the record neither holds nor looked up postdates it: the HPO
    release, for a record signed before CoGA recorded it. Like a snapshot section an older
    record predates, it is not compared, instead of turning every such record into a
    change to modules nothing in it froze. One the record looked up and does not hold
    was not loaded then, so its arrival is a change; and a pipeline module is always
    compared, as one the family's pipeline declared since came with a re-import.
    """
    held = _module_keys(signed.get("modules"))
    looked_up = _reference_modules_looked_up(signed)
    comparable: list[Any] = []
    postdating: list[str] = []
    for module in current:
        key = module.get("key") if isinstance(module, Mapping) else None
        if (
            key is not None
            and module.get("layer") == "reference"
            and str(key) not in held
            and str(key) not in looked_up
        ):
            postdating.append(str(key))
        else:
            comparable.append(module)
    return comparable, postdating


def _structural_reviews_comparable_with(
    signed: Mapping[str, Any], signed_value: Any, current: list[Any]
) -> tuple[list[Any], list[str]]:
    """The current reported SVs to compare with a signed record's, and what is left out.

    A record signed before SV/CNV classifications froze their evidence (it has no
    STRUCTURAL_DRIFT_SECTION) holds none in its reported SVs. Their evidence is then not
    compared, like a section the record predates, rather than turning every such record
    with a reported SV into a change; everything else about them still is.
    """
    if STRUCTURAL_DRIFT_SECTION in signed:
        return current, []
    comparable = [
        {key: value for key, value in entry.items() if key != "evidence_snapshot"}
        if isinstance(entry, Mapping)
        else entry
        for entry in current
    ]
    return comparable, (["reported_structural_variants.evidence_snapshot"] if signed_value else [])


async def compare_report_with_latest_signout(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Does the report, as it would be signed now, still match the latest sign-out?

    The report page renders a signed version from its frozen snapshot and the live report
    from current data. A change after sign-out (a review edit, a new report tag, a
    re-import, a QC cut-off change) leaves the signed version as it was, so the page uses
    this check to say so: on the live report, whether it still matches the latest signed
    version; on that version, whether the family's data changed since (#508). This
    rebuilds the snapshot body and compares it, section by section, with the frozen one. A
    section or reference module the signed record predates is listed in ``not_compared``
    (a module as ``modules.<key>``, the evidence of its reported SVs as
    ``reported_structural_variants.evidence_snapshot``) rather than reported as changed.
    """
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    checked_at = datetime.now(timezone.utc)
    row = (
        await session.execute(
            text(
                """
                SELECT version, content_hash, snapshot
                FROM report_signouts
                WHERE family_id = CAST(:family_uuid AS uuid)
                ORDER BY version DESC
                LIMIT 1
                """
            ),
            {"family_uuid": context.family_uuid},
        )
    ).mappings().first()
    if row is None:
        return {
            "family_id": context.family_id,
            "version": None,
            "content_hash": None,
            "matches": None,
            "changed_sections": [],
            "not_compared": [],
            "not_captured": [],
            "checked_at": checked_at,
        }
    signed = _as_snapshot(row["snapshot"]) or {}
    current = await build_report_snapshot(
        session, family_id=family_id, user=user, project_id=project_id
    )
    changed: list[str] = []
    not_compared: list[str] = []
    for section in REPORT_CONTENT_SECTIONS:
        if section in signed:
            signed_value = signed[section]
        elif section in _ABSENT_SECTION_DEFAULTS:
            signed_value = _ABSENT_SECTION_DEFAULTS[section]
        else:
            not_compared.append(section)
            continue
        current_value = current.get(section)
        if section == "modules" and isinstance(current_value, list):
            current_value, postdating = _modules_comparable_with(signed, current_value)
            not_compared.extend(f"modules.{key}" for key in postdating)
        if section == "reported_structural_variants" and isinstance(current_value, list):
            current_value, left_out = _structural_reviews_comparable_with(
                signed, signed_value, current_value
            )
            not_compared.extend(left_out)
        if _section_fingerprint(signed_value) != _section_fingerprint(current_value):
            changed.append(section)
    return {
        "family_id": context.family_id,
        "version": row["version"],
        "content_hash": row["content_hash"],
        "matches": not changed,
        "changed_sections": changed,
        "not_compared": not_compared,
        "not_captured": snapshot_gaps(signed),
        "checked_at": checked_at,
    }


async def verify_report_signout_chain(
    session: AsyncSession, family_identifier: str
) -> ChainVerification:
    """Re-walk a family's report-signout hash chain AND re-verify each row's content
    hash against its snapshot; report whether the signed-report history is intact.

    Partitioned on the immutable ``family_identifier`` so a family's signed history
    stays verifiable after the family row is deleted (the cascade nulls ``family_id``).
    """
    rows = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT version, version::text AS id, content_hash, signed_out_at, "
                    "signed_out_by, family_identifier, snapshot, row_hash, prev_hash "
                    "FROM report_signouts "
                    "WHERE family_identifier IS NOT DISTINCT FROM :fident "
                    "AND row_hash IS NOT NULL "
                    "ORDER BY version ASC"
                ),
                {"fident": family_identifier},
            )
        )
        .mappings()
        .all()
    ]
    result = verify_chain(rows, _signout_chain_payload)
    if not result.verified:
        return result
    for position, row in enumerate(rows, start=1):
        if _canonical_hash(_as_snapshot(row["snapshot"])) != row["content_hash"]:
            return ChainVerification(
                False,
                position,
                str(row["version"]),
                "content_hash does not match snapshot (snapshot tampered)",
            )
    return result
