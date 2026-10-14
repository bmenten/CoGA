"""The gene panels a family's analysis applied, on its clinical audit trail (REQ-CARR-003).

A search of a family's variants that applies a gene panel is part of the analysis that a
sign-out closes, so the signed record must say which panel, at which version, the analysis
applied. Nothing recorded it: the HTTP audit log keeps only the query's keys by default
(``AUDIT_LOG_QUERY_STRING_MODE=keys``), is written in batches after the response, and can
be switched off, so it cannot anchor a signed record.

A family search that applies a panel therefore records it on the family's clinical audit
trail (``panel_applied``), before the search runs: the panel's id, name and local version
and the release of its source (PanelApp's version of the panel, or the Monarch release of
the Mendeliome) as they are at that moment. Each panel version is recorded once per family,
the first time a search applies it: a panel edited or imported again since (#809 archives
each version) is recorded again when a search next applies it. The events are hash-chained
with the family's other clinical events, and a sign-out freezes them
(``report_signout_service``, section ``gene_panels``).

A search the event cannot be recorded for fails with it: a panel the record would not name
is not applied.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import canonical_uuid
from .access_control import CurrentUser
from .clinical_audit_service import record_clinical_event
from .family_metadata_context import FamilyMetadataContext

PANEL_APPLIED_ACTION = "panel_applied"

# The searches that apply a panel to a family's data, as the event names them.
PANEL_SEARCHES: dict[str, str] = {
    "small_variants": "the small-variant search",
    "small_variants_export": "the small-variant export",
    "structural_variants": "the structural-variant search",
    "structural_variants_export": "the structural-variant export",
    "nipt_variants": "the NIPT variant search",
    "nipt_coverage": "the NIPT coverage",
    "tracks": "the genome tracks",
}

_PANEL_SQL = text(
    """
    SELECT gp.id::text AS panel_id,
           gp.name,
           gp.version,
           gp.source,
           gp.external_id,
           gp.external_version,
           gp.source_updated_at,
           gp.source_metadata,
           (SELECT count(*) FROM gene_panel_genes g WHERE g.panel_id = gp.id) AS gene_count
    FROM gene_panels gp
    WHERE gp.id = CAST(:panel_id AS uuid)
    """
)

_RECORDED_SQL = text(
    """
    SELECT 1
    FROM clinical_audit_events
    WHERE family_id = CAST(:family_uuid AS uuid)
      AND action = :action
      AND after ->> 'panel_id' = :panel_id
      AND after ->> 'version' = :version
      AND COALESCE(after ->> 'external_version', '') = :external_version
    LIMIT 1
    """
)

_APPLIED_SQL = text(
    """
    SELECT created_at, actor, after
    FROM clinical_audit_events
    WHERE family_id = CAST(:family_uuid AS uuid)
      AND action = :action
    ORDER BY created_at, id
    """
)


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    text_value = str(value).strip() if value is not None else ""
    return text_value or None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def panel_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    """What identifies the gene set a search applied: the panel, its local version and the
    release of its source, as the panel row holds them now."""
    metadata = _record(row.get("source_metadata"))
    options = _record(metadata.get("import_options"))
    levels = options.get("confidence_levels")
    return {
        "panel_id": str(row["panel_id"]),
        "name": str(row.get("name") or ""),
        "version": int(row.get("version") or 0),
        "source": _text(row.get("source")),
        "gene_count": int(row.get("gene_count") or 0),
        # PanelApp's panel id and its version of the panel, or the Mendeliome's Monarch
        # release: the release CoGA read the panel from.
        "external_id": _text(row.get("external_id")),
        "external_version": _text(row.get("external_version")),
        "external_version_created": _text(metadata.get("version_created")),
        "confidence_levels": sorted(str(level) for level in levels) if isinstance(levels, list) else None,
        "source_updated_at": _iso(row.get("source_updated_at")),
    }


def _describe(panel: Mapping[str, Any]) -> str:
    name = panel.get("name") or panel.get("panel_id")
    described = f"Gene panel {name} version {panel.get('version')}"
    release = panel.get("external_version")
    if panel.get("source") == "panelapp":
        described += f" (PanelApp panel {panel.get('external_id')}, version {release or 'not stated'})"
    elif release:
        described += f" ({panel.get('source')} release {release})"
    return described


async def record_family_panel_use(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    panel_id: str | None,
    user: CurrentUser,
    search: str,
) -> None:
    """Record that a search of the family applies ``panel_id``, the first time it applies
    this version of the panel, and commit. Call it before the search runs.

    A value that names no panel records nothing: the search itself refuses it.
    """
    panel_uuid = canonical_uuid(panel_id) if panel_id else None
    if panel_uuid is None:
        return
    row = (await session.execute(_PANEL_SQL, {"panel_id": panel_uuid})).mappings().first()
    if row is None:
        return
    panel = panel_identity(dict(row))
    lookup = {
        "family_uuid": context.family_uuid,
        "action": PANEL_APPLIED_ACTION,
        "panel_id": panel["panel_id"],
        "version": str(panel["version"]),
        "external_version": panel["external_version"] or "",
    }
    if (await session.execute(_RECORDED_SQL, lookup)).first() is not None:
        return
    # The family's audit-chain lock, which record_clinical_event takes too, before the
    # second look: two searches applying the panel at once record it once.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"cae:{context.family_id or 'orphan'}"},
    )
    if (await session.execute(_RECORDED_SQL, lookup)).first() is not None:
        await session.commit()
        return
    label = PANEL_SEARCHES.get(search, search)
    await record_clinical_event(
        session,
        family_uuid=context.family_uuid,
        family_identifier=context.family_id,
        variant_id=None,
        actor=getattr(user, "username", None) or getattr(user, "email", "") or "unknown",
        actor_id=getattr(user, "id", None),
        action=PANEL_APPLIED_ACTION,
        summary=f"{_describe(panel)} first applied to {label}",
        before=None,
        after={**panel, "search": search},
    )
    await session.commit()


async def applied_gene_panels(session: AsyncSession, family_uuid: str) -> list[dict[str, Any]]:
    """Each panel version the family's searches applied, with when and by whom it was first
    applied, as its clinical audit trail records them; sorted, for a stable content hash."""
    rows = (
        await session.execute(
            _APPLIED_SQL, {"family_uuid": family_uuid, "action": PANEL_APPLIED_ACTION}
        )
    ).mappings().all()
    seen: set[tuple[str, int, str]] = set()
    applied: list[dict[str, Any]] = []
    for row in rows:
        after = _record(row.get("after"))
        panel_id = _text(after.get("panel_id"))
        if panel_id is None:
            continue
        version = int(after.get("version") or 0)
        key = (panel_id, version, _text(after.get("external_version")) or "")
        if key in seen:
            continue
        seen.add(key)
        levels = after.get("confidence_levels")
        applied.append(
            {
                "panel_id": panel_id,
                "name": _text(after.get("name")),
                "version": version,
                "source": _text(after.get("source")),
                "gene_count": int(after.get("gene_count") or 0),
                "external_id": _text(after.get("external_id")),
                "external_version": _text(after.get("external_version")),
                "external_version_created": _text(after.get("external_version_created")),
                "confidence_levels": (
                    sorted(str(level) for level in levels) if isinstance(levels, list) else None
                ),
                "source_updated_at": _text(after.get("source_updated_at")),
                "first_applied_at": _iso(row.get("created_at")),
                "first_applied_by": _text(row.get("actor")),
                "first_search": _text(after.get("search")),
            }
        )
    applied.sort(
        key=lambda panel: (
            str(panel["name"] or "").lower(),
            panel["panel_id"],
            panel["version"],
            panel["external_version"] or "",
        )
    )
    return applied
