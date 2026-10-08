"""The evidence a structural-variant / CNV classification rests on (clinical traceability,
Phase 1 — the SV/CNV counterpart of the small-variant evidence snapshot).

Saving a CNV (ClinGen 2019) scoring freezes what it was made on into
``structural_variant_reviews.cnv_evidence_snapshot``; the classification-drift check and
the sign-out drift gate compare that snapshot with the SV as the family's data holds it
now (see docs/clinical-traceability.md).

What is frozen is what the CNV classification actually reads. The criteria engine
(``frontend/src/lib/cnvAcmg/evaluate.ts``, scored again by ``cnv_acmg_points``) suggests
from the SV record alone: its type (loss or gain), the genes it overlaps and their count
(sections 1 and 3), the gene constraint (pLI, criterion 2H) and the annotated inheritance
(section 5). It reads no clinical-CNV, dosage-score or DGV reference data; the
analyst scores those criteria by hand. So the snapshot holds:

* ``evidence`` — those inputs, the event they describe (its source, locus and extent) and
  a hash of the SV's whole annotation record, which changes whenever any annotation of
  the SV changes, as the annotation-set hash does for a small variant;
* ``evidence_hash`` — the drift key: SHA-256 over ``evidence``;
* ``versions`` — the annotation and reference versions they come from: those the
  family's manifest records for its structural-variant callset (or for the pipeline run
  that produced it), and the reference assembly and gene loci CoGA loaded (the gene loci
  give a directly uploaded SV its genes). Recorded for the audit, not compared: evidence
  that is unchanged under a new release has not drifted;
* ``captured_at``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Mapping, Sequence

from clickhouse_connect.driver.exceptions import ClickHouseError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from .access_control import CurrentUser
from .annotation_manifest_service import get_family_annotation_manifest
from .clickhouse_variant_queries import (
    _collect_annotations,
    _decode_json_payload,
    _string_list,
    _structural_info_text,
    _structural_pli,
    _structural_variant_where_clauses,
)
from .clickhouse_variant_ids import _structural_table_name
from .clickhouse_variant_records import StructuralVariantRecord, _coerce_int
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext
from .family_variant_filters import StructuralVariantQueryFilters
from .hash_chain import canonical_hash, canonical_json

# The evidence fields grouped under the name a drift reports when they change, in the
# order a reviewer reads them.
_CHANGE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("source", ("source",)),
    ("sv_type", ("sv_type",)),
    ("locus", ("chrom", "start", "end", "sv_len", "remote_chrom", "remote_start")),
    ("gene_symbols", ("gene_symbols", "gene_count")),
    ("pli", ("pli",)),
    ("inheritance", ("inheritance",)),
    ("annotations", ("annotation_hash",)),
)

# The reference modules that shape an SV's evidence: the assembly its coordinates are on,
# and the gene loci that give a directly uploaded SV its genes.
_REFERENCE_MODULES = ("assembly", "gene_loci")
# The manifest modalities an SV callset's versions are recorded under, the SV VCF's own
# first (``by_modality``, #294).
_STRUCTURAL_MODALITIES = ("sv", "pipeline")


def _finite(value: float | None) -> float | None:
    """A float the snapshot can hold: JSONB has no NaN or infinity, and no -0.0."""
    if value is None or not math.isfinite(value):
        return None
    return value + 0.0


def structural_evidence(record: StructuralVariantRecord) -> dict[str, Any]:
    """The inputs a CNV classification of ``record`` reads, as the SV page serves them.

    Extracted with the same helpers the page uses (``_structural_variant_out``), so the
    frozen values are the ones the classification dialog was given.
    """
    gene_symbols = list(record.gene_symbols)
    return {
        "source": record.source,
        "sv_type": record.sv_type,
        "chrom": record.chr,
        "start": record.start,
        "end": record.end,
        "sv_len": record.sv_len,
        "remote_chrom": record.remote_chr,
        "remote_start": record.remote_start,
        "gene_symbols": gene_symbols,
        "gene_count": len(gene_symbols),
        "pli": _finite(_structural_pli(record)),
        "inheritance": _structural_info_text(record, "Inheritance"),
        "annotation_hash": canonical_hash(record.annotations),
    }


def structural_evidence_versions(modules: Sequence[Any]) -> dict[str, str]:
    """The annotation and reference versions an SV's evidence comes from, from a family's
    annotation manifest (``get_family_annotation_manifest(...)["modules"]``)."""
    versions: dict[str, str] = {}
    for module in modules:
        if not isinstance(module, Mapping):
            continue
        key = str(module.get("key") or "")
        if not key:
            continue
        if module.get("layer") == "reference":
            if key in _REFERENCE_MODULES and module.get("version"):
                versions[key] = str(module["version"])
            continue
        by_modality = module.get("by_modality")
        if not isinstance(by_modality, Mapping):
            continue
        for modality in _STRUCTURAL_MODALITIES:
            if by_modality.get(modality):
                versions[key] = str(by_modality[modality])
                break
    return versions


def build_structural_evidence_snapshot(
    record: StructuralVariantRecord, versions: Mapping[str, str], *, captured_at: datetime
) -> dict[str, Any]:
    """Freeze the evidence a CNV classification of ``record`` is saved with."""
    evidence = structural_evidence(record)
    return {
        "evidence": evidence,
        "evidence_hash": canonical_hash(evidence),
        "versions": dict(versions),
        "captured_at": captured_at.isoformat(),
    }


async def fetch_structural_variant_record(
    context: FamilyMetadataContext, variant_id: str
) -> StructuralVariantRecord | None:
    """The family's SV ``variant_id`` as the SV page reads it, or None when its data holds
    no such SV (or no SV storage for the assembly).

    The same scope as the page (``_structural_variant_where_clauses``: the family, its
    visible projects, an SV with a call in a visible sample) and the same value parsing;
    without the calls, which the classification's evidence does not include.
    """
    assembly_name = getattr(context, "assembly_name", None)
    if not assembly_name:
        return None
    where_clauses, params = _structural_variant_where_clauses(
        context, StructuralVariantQueryFilters(page=1, page_size=1)
    )
    where_clauses.append("e.variantId = %(evidence_variant_id)s")
    params["evidence_variant_id"] = variant_id
    query = f"""
        SELECT
            any(e.key) AS key,
            any(e.variantId) AS variant_id,
            any(e.chrom) AS chrom,
            any(e.start) AS start,
            any(e.end) AS "end",
            any(e.svType) AS sv_type,
            any(e.source) AS source,
            any(d.remoteChrom) AS remote_chr,
            any(d.remoteStart) AS remote_start,
            any(d.svLen) AS sv_len,
            any(d.filters) AS filters,
            any(d.annotationsJson) AS annotations_json,
            any(e.gene_symbols) AS gene_symbols
        FROM {_structural_table_name(assembly_name, "entries")} AS e
        LEFT JOIN {_structural_table_name(assembly_name, "variants/details")} AS d
          ON d.key = e.key
        WHERE {' AND '.join(where_clauses)}
        GROUP BY e.key, e.variantId
        ORDER BY key
        LIMIT 1
    """
    try:
        rows = await execute_clickhouse(query, params)
    except ClickHouseError as exc:
        message = str(exc)
        if "UNKNOWN_TABLE" in message or "doesn't exist" in message:
            return None  # no SV storage for this assembly yet: the SV is not in the data
        raise
    if not rows:
        return None
    (
        variant_key,
        stored_variant_id,
        chrom,
        start,
        end,
        sv_type,
        source,
        remote_chr,
        remote_start,
        sv_len,
        filters_raw,
        annotations_json,
        gene_symbols,
    ) = rows[0]
    return StructuralVariantRecord(
        variant_key=_coerce_int(variant_key),
        variant_id=str(stored_variant_id),
        chr=normalize_chromosome(str(chrom)),
        start=int(start),
        end=int(end),
        sv_type=str(sv_type or ""),
        source=str(source) if source is not None else None,
        remote_chr=normalize_chromosome(str(remote_chr)) if remote_chr not in (None, "") else None,
        remote_start=_coerce_int(remote_start),
        remote_end=None,
        sv_len=_coerce_int(sv_len),
        filters=_string_list(filters_raw),
        gene_symbols=_string_list(gene_symbols),
        annotations=_collect_annotations(_decode_json_payload(annotations_json)),
        calls=[],
    )


async def read_structural_evidence_versions(
    session: AsyncSession, *, context: FamilyMetadataContext, user: CurrentUser
) -> dict[str, str]:
    """The versions an SV of this family takes its evidence from, as its manifest says now."""
    manifest = await get_family_annotation_manifest(session, family_id=context.family_id, user=user)
    return structural_evidence_versions(manifest.get("modules") or [])


def _as_snapshot(value: Any) -> Any:
    """The JSONB column decodes to a dict; read a driver's JSON text the same way."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _same(left: Any, right: Any) -> bool:
    # Through the JSON encoding the stored snapshot went through, so a value read back
    # from JSONB and a fresh one compare equal when unchanged.
    return canonical_json(json.loads(json.dumps(left, default=str))) == canonical_json(
        json.loads(json.dumps(right, default=str))
    )


def _changed_groups(frozen: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    return [
        name
        for name, fields in _CHANGE_GROUPS
        if any(field in frozen and not _same(frozen.get(field), current.get(field)) for field in fields)
    ]


def diff_structural_evidence(
    snapshot: Any, current_record: StructuralVariantRecord | None
) -> dict[str, Any]:
    """Compare a frozen SV/CNV evidence snapshot with the SV as it is now.

    ``status`` is ``current`` (unchanged), ``drifted`` (``changed`` names what moved),
    ``unknown`` (the frozen evidence cannot be read, so it cannot be shown unchanged; the
    sign-out gate counts it like a drift) or ``variant_missing`` (the SV is no longer in the
    family's data). Only the fields the snapshot froze are compared, so evidence a later
    version of CoGA adds does not turn an older classification into a drift.
    """
    snapshot = _as_snapshot(snapshot)
    frozen = snapshot.get("evidence") if isinstance(snapshot, Mapping) else None
    frozen_hash = snapshot.get("evidence_hash") if isinstance(snapshot, Mapping) else None
    evidence_from = dict(frozen) if isinstance(frozen, Mapping) else None
    if current_record is None:
        return {
            "status": "variant_missing",
            "changed": [],
            "evidence_from": evidence_from,
            "evidence_to": None,
        }
    current = structural_evidence(current_record)
    if evidence_from is None or not frozen_hash:
        return {"status": "unknown", "changed": [], "evidence_from": evidence_from, "evidence_to": current}
    current_hash = canonical_hash({field: current.get(field) for field in evidence_from})
    if current_hash == frozen_hash:
        return {"status": "current", "changed": [], "evidence_from": evidence_from, "evidence_to": current}
    return {
        "status": "drifted",
        "changed": _changed_groups(evidence_from, current),
        "evidence_from": evidence_from,
        "evidence_to": current,
    }
