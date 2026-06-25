"""Per-family annotation/reference version manifest (clinical traceability, Phase 0).

Consolidates which annotation modules/versions backed a family's data so a report
can state its provenance (see docs/clinical-traceability.md). It merges two layers:

* the per-family *pipeline* manifest — the upstream tools that produced this family's
  annotated VCF (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, …). Recorded explicitly in
  ``family_annotation_manifest`` (manual/admin), or captured for free from
  ``family.metadata.annotation_manifest`` when the import manifest declares it.
* the platform *reference* layer — versions of what CoGA itself loaded (the assembly,
  the Monarch release), read live from the reference tables.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .family_metadata_context import build_family_metadata_context
from .metadata_service import CurrentUser, get_family_record

# Canonical display order + labels; unknown keys fall through with a title-cased label.
_MODULE_LABELS: dict[str, str] = {
    "assembly": "Reference assembly",
    "vep": "VEP",
    "clinvar": "ClinVar",
    "gnomad": "gnomAD",
    "dbnsfp": "dbNSFP",
    "spliceai": "SpliceAI",
    "gencc": "GenCC",
    "panelapp": "PanelApp",
    "monarch": "Monarch",
    "hpo": "HPO",
}
_MODULE_ORDER = list(_MODULE_LABELS)


def _as_module(value: Any) -> dict[str, Any]:
    """Accept either ``"110"`` or ``{"version": "110", "cache": "…"}``."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"version": str(value)}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _family_manifest_row(session: AsyncSession, family_uuid: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT modules, source, recorded_by, recorded_at
                FROM family_annotation_manifest
                WHERE family_id = CAST(:family_uuid AS uuid)
                """
            ),
            {"family_uuid": family_uuid},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _platform_modules(session: AsyncSession, assembly_id: str | None) -> dict[str, dict[str, Any]]:
    """Versions of the reference layer CoGA loaded — best-effort, never fatal."""
    modules: dict[str, dict[str, Any]] = {}
    if assembly_id:
        try:
            row = (
                await session.execute(
                    text(
                        "SELECT assembly_name, version, release_date FROM assemblies "
                        "WHERE id = CAST(:assembly_id AS uuid)"
                    ),
                    {"assembly_id": assembly_id},
                )
            ).mappings().first()
            if row:
                detail = str(row["release_date"]) if row["release_date"] else (row["version"] or None)
                modules["assembly"] = {"version": row["assembly_name"], "detail": detail}
        except Exception:  # noqa: BLE001 — a missing column shouldn't break provenance
            pass
    try:
        release = (
            await session.execute(
                text(
                    "SELECT release_version FROM monarch_gene_disease "
                    "WHERE release_version IS NOT NULL AND release_version <> '' "
                    "ORDER BY updated_at DESC NULLS LAST LIMIT 1"
                )
            )
        ).scalar()
        if release:
            modules["monarch"] = {"version": str(release)}
    except Exception:  # noqa: BLE001
        pass
    return modules


def _module_list(
    pipeline_modules: dict[str, Any], platform_modules: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: dict[str, tuple[dict[str, Any], str]] = {}
    for key, value in platform_modules.items():
        merged[key] = (_as_module(value), "reference")
    # The family's own pipeline versions take precedence over platform defaults.
    for key, value in pipeline_modules.items():
        merged[key] = (_as_module(value), "pipeline")

    ordered = [k for k in _MODULE_ORDER if k in merged] + [k for k in merged if k not in _MODULE_ORDER]
    result: list[dict[str, Any]] = []
    for key in ordered:
        module, layer = merged[key]
        version = module.get("version")
        detail = (
            module.get("detail")
            or module.get("cache")
            or module.get("release")
            or module.get("release_date")
        )
        result.append(
            {
                "key": key,
                "label": _MODULE_LABELS.get(key, key.replace("_", " ").title()),
                "version": str(version) if version not in (None, "") else None,
                "detail": str(detail) if detail else None,
                "layer": layer,
            }
        )
    return result


async def get_family_annotation_manifest(
    session: AsyncSession, *, family_id: str, user: CurrentUser, project_id: str | None = None
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    row = await _family_manifest_row(session, context.family_uuid)

    pipeline_modules: dict[str, Any] = {}
    source: str | None = None
    recorded_at = None
    recorded_by: str | None = None
    if row and _as_dict(row.get("modules")):
        pipeline_modules = _as_dict(row.get("modules"))
        source = row.get("source")
        recorded_at = row.get("recorded_at")
        recorded_by = row.get("recorded_by")
    else:
        # Fall back to the manifest the import captured into family.metadata.
        family = await get_family_record(session, family_id, user)
        captured = (getattr(family, "metadata", None) or {}).get("annotation_manifest")
        if isinstance(captured, dict):
            pipeline_modules = captured
            source = "manifest"

    platform_modules = await _platform_modules(session, context.assembly_id)
    return {
        "family_id": context.family_id,
        "assembly": context.assembly_name,
        "source": source,
        "recorded_at": recorded_at,
        "recorded_by": recorded_by,
        "modules": _module_list(pipeline_modules, platform_modules),
    }


async def set_family_annotation_manifest(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    modules: dict[str, Any],
    source: str = "manual",
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user
    )
    await session.execute(
        text(
            """
            INSERT INTO family_annotation_manifest
                (family_id, assembly_id, modules, source, recorded_by, recorded_at)
            VALUES
                (CAST(:family_uuid AS uuid), CAST(:assembly_id AS uuid),
                 CAST(:modules AS jsonb), :source, :recorded_by, now())
            ON CONFLICT (family_id) DO UPDATE SET
                modules = EXCLUDED.modules,
                source = EXCLUDED.source,
                recorded_by = EXCLUDED.recorded_by,
                recorded_at = now(),
                assembly_id = EXCLUDED.assembly_id
            """
        ),
        {
            "family_uuid": context.family_uuid,
            "assembly_id": context.assembly_id,
            "modules": json.dumps(modules or {}),
            "source": source,
            "recorded_by": getattr(user, "email", None),
        },
    )
    await session.commit()
    return await get_family_annotation_manifest(session, family_id=family_id, user=user)
