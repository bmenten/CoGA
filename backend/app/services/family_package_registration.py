from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.object_storage import (
    join_remote_uri,
    remote_object_identity,
)
from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyImportValidationIssue,
    FamilyPackageValidationOut,
)
from .clickhouse_interval_tracks import (
    count_interval_track_source_rows,
    delete_interval_tracks,
)
from .clickhouse_variant_storage import (
    delete_family_small_variants,
    delete_family_structural_variants,
)
from .data_scope import normalize_chromosome
from .family_identifiers import identifier_problem
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
)
from .family_variant_write_lock import VARIANT_TYPES, lock_family_variant_writes
from .access_control import CurrentUser
from . import ped_service
from .raw_import_files_pg import record_raw_import_file

from .family_package_common import FamilyPackageBundle, ManifestDataset, _display_path, _issue, _metadata_dict, _resolve_package_path
from .family_package_manifest import _manifest_carrier_types, _manifest_member_status_overrides, _manifest_pgt_metadata, _manifest_relationships, _manifest_roi_value, _normalize_manifest_samples, _ped_carrier_type, _ped_is_carrier, _ped_members_for_import


logger = logging.getLogger(__name__)


async def existing_family_sample_ids(
    session: AsyncSession,
    family_id: str | None,
) -> list[str]:
    """Sample IDs of an already-configured family, or ``[]`` when it does not
    exist. Used so manifest discovery can scan per-sample dataset files for an
    incremental import without requiring a PED file."""
    family_id = (family_id or "").strip()
    if identifier_problem(family_id) is not None:
        # No family is stored under such an ID (and a NUL could not even be sent to
        # Postgres): Discover reports it as family_id_invalid.
        return []
    existing = await _fetch_existing_family(session, family_id=family_id)
    if existing is None:
        return []
    return [str(sample_id) for sample_id in existing.get("sample_ids", []) if sample_id]


def _family_sample_contexts(context: FamilyMetadataContext) -> dict[str, SampleMetadataContext]:
    return {
        row["sample_id"]: SampleMetadataContext(
            sample_uuid=row["sample_uuid"],
            sample_id=row["sample_id"],
            family_uuid=context.family_uuid,
            family_id=context.family_id,
            sex=row["sex"],
            project_ids=context.project_ids,
            assembly_id=context.assembly_id,
            assembly_name=context.assembly_name,
        )
        for row in context.sample_rows
    }


async def _fetch_existing_family(
    session: AsyncSession,
    *,
    family_id: str,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT
                f.id::text AS family_uuid,
                f.metadata,
                COALESCE(
                    ARRAY_AGG(DISTINCT s.sample_id) FILTER (WHERE s.sample_id IS NOT NULL),
                    '{}'::text[]
                ) AS sample_ids
            FROM families f
            LEFT JOIN samples s ON s.family_id = f.id
            WHERE f.family_id = :family_id
            GROUP BY f.id
            """
        ),
        {"family_id": family_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


def _dataset_provenance(validation: FamilyPackageValidationOut) -> dict[str, Any]:
    return {
        summary.dataset_type: {
            "enabled": summary.enabled,
            "status": summary.status,
            "files": summary.files,
            "samples": summary.samples,
            "summary": summary.summary,
            "message": summary.message,
        }
        for summary in validation.datasets
        if summary.enabled
    }


def _sample_provenance(bundle: FamilyPackageBundle) -> dict[str, dict[str, Any]]:
    sample_payloads: dict[str, dict[str, Any]] = {}
    for dataset_type, dataset in bundle.manifest.datasets.items():
        if not dataset.enabled:
            continue
        for sample_id, raw_entry in dataset.per_sample.items():
            if not isinstance(raw_entry, dict):
                continue
            files = {
                key: _display_path(bundle.root, resolved)
                for key, value in raw_entry.items()
                if key in _PROVENANCE_PATH_KEYS
                for resolved in [_resolve_package_path(bundle.root, str(value))]
                if resolved is not None
            }
            sample_payloads.setdefault(sample_id, {})[dataset_type] = files
    return sample_payloads


_PROVENANCE_PATH_KEYS = {
    "bins",
    "segments",
    "file",
    "index",
    "bcf_index",
    "json",
    "bed",
    "vcf",
    "family_vcf",
    "annotation_tsv",
    "maternal",
    "paternal",
    "mat",
    "pat",
    # Long-read roles. Without these the files validate and import but leave no
    # raw-file provenance row, so the traceability record would not name the CNV
    # callset, the mitochondrial annotation, the alignment or the QC report a
    # released interpretation rests on.
    "bam",
    "sv_vcf",
    "sv_index",
    "sv_annotation_tsv",
    "copy_number_bedgraph",
    "depth_bigwig",
    "maf_bigwig",
    "summary_html",
    "report",
    "read_stats",
    "depth_summary",
    "depth_regions",
    "depth_global_dist",
    "params",
    "versions",
    "execution_trace",
    "execution_report",
    # PGT pipeline (nf-cmgg/copgtm) roles: the per-sample QC files, the family's QC
    # tables, and its reading of the affected haplotype.
    "qualimap_summary",
    "mean_coverage",
    "sex_check",
    "ado_adi",
    "concordance",
    "imputed_concordance",
    "kinship",
    "haplotype_origin",
    "haplotype_conclusion",
}


def _dataset_top_level_files(dataset: ManifestDataset) -> dict[str, str]:
    """Top-level (family-scoped) file references on a dataset, excluding per-sample
    entries. Includes manifest extras so non-standard keys are still captured."""
    payload: dict[str, Any] = dict(dataset.model_extra or {})
    payload.update(
        {
            "family_vcf": dataset.family_vcf,
            "annotation_tsv": dataset.annotation_tsv,
            "index": dataset.index,
            "bed": dataset.bed,
            "vcf": dataset.vcf,
            "file": dataset.file,
            "json": dataset.json_path,
        }
    )
    return {
        key: str(value)
        for key, value in payload.items()
        if key in _PROVENANCE_PATH_KEYS and isinstance(value, str) and value.strip()
    }


async def _record_package_raw_files(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    family_uuid: str,
) -> None:
    """Record provenance rows for every raw file referenced by the package manifest,
    grouped into family-level and individual-level scope. Files are referenced in
    place (not copied). Best-effort: never fails the import.

    Each row identifies its file's content. A local file is hashed where it lies. For a
    package staged from a bucket the row names the object's URI -- the staging copy is
    deleted after the import -- and carries the store's own record of the object
    (``remote_object_identity``) under ``metadata.store_object``, which Verify compares
    with the object later. A staged file is also hashed from its staged copy; a file
    staging left in the store (an alignment) is not: its checksums are the store's, not
    a SHA-256, so ``sha256`` stays empty."""
    try:
        result = await session.execute(
            text(
                "SELECT id::text AS sample_uuid, sample_id "
                "FROM samples WHERE family_id = CAST(:family_uuid AS uuid)"
            ),
            {"family_uuid": family_uuid},
        )
        sample_uuid_by_id = {
            str(row["sample_id"]): str(row["sample_uuid"]) for row in result.mappings().all()
        }

        def _provenance_path(resolved: Path) -> str:
            # For an S3-staged package, record the durable s3:// URI (the staging
            # temp dir is deleted after the import); otherwise the local path.
            if bundle.source_uri:
                try:
                    relative = resolved.relative_to(bundle.root)
                except ValueError:
                    return str(resolved)
                return join_remote_uri(bundle.source_uri, str(relative))
            return str(resolved)

        def _held_where(resolved: Path) -> str | None:
            # "staged" (a file in the package folder), "store" (one staging left in the
            # object store -- a remote package's alignment, which the traceability
            # record must still name) or None (not part of the package).
            if resolved.is_file():
                return "staged"
            if _display_path(bundle.root, resolved) in bundle.remote_only_files:
                return "store"
            return None

        async def _record(
            resolved: Path,
            held: str,
            *,
            scope: str,
            dataset_type: str,
            sample_uuid: str | None,
        ) -> None:
            storage_path = _provenance_path(resolved)
            content: dict[str, Any] = {}  # how the row identifies the file's content
            if bundle.source_uri:
                # The store's record of the object, for Verify to compare with later
                # without re-hashing. A store error costs this row its identity, not
                # the package its record.
                try:
                    identity = await asyncio.to_thread(remote_object_identity, storage_path)
                except Exception:
                    logger.warning(
                        "Could not read the object store's record of %s for provenance",
                        storage_path,
                        exc_info=True,
                    )
                    identity = None
                content["metadata"] = {"store_object": identity} if identity else None
                if held == "staged":
                    # The URI is not a local file: hash the staged copy.
                    content["checksum_path"] = resolved
                else:
                    # Left in the store: its checksums are the store's, never a SHA-256.
                    content["compute_checksum"] = False
                    content["file_size"] = identity.get("size") if identity else None
            await record_raw_import_file(
                session,
                family_uuid=family_uuid,
                sample_uuid=sample_uuid,
                scope=scope,
                dataset=dataset_type,
                file_name=resolved.name,
                storage_path=storage_path,
                managed=False,
                source="family_package",
                **content,
            )

        for dataset_type, dataset in bundle.manifest.datasets.items():
            if not dataset.enabled:
                continue
            for value in _dataset_top_level_files(dataset).values():
                resolved = _resolve_package_path(bundle.root, value)
                held = _held_where(resolved) if resolved is not None else None
                if resolved is None or held is None:
                    continue
                await _record(
                    resolved, held, scope="family", dataset_type=dataset_type, sample_uuid=None
                )
            for sample_id, raw_entry in dataset.per_sample.items():
                if not isinstance(raw_entry, dict):
                    continue
                sample_uuid = sample_uuid_by_id.get(str(sample_id))
                for key, value in raw_entry.items():
                    if (
                        key not in _PROVENANCE_PATH_KEYS
                        or not isinstance(value, str)
                        or not value.strip()
                    ):
                        continue
                    resolved = _resolve_package_path(bundle.root, value)
                    held = _held_where(resolved) if resolved is not None else None
                    if resolved is None or held is None:
                        continue
                    await _record(
                        resolved,
                        held,
                        scope="individual",
                        dataset_type=dataset_type,
                        sample_uuid=sample_uuid,
                    )
    except Exception:  # pragma: no cover - provenance is non-critical
        logger.warning("Failed to record raw import file provenance", exc_info=True)


async def _register_package_provenance(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    validation: FamilyPackageValidationOut,
    family_uuid: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    # The keys this writes, merged into the family's metadata as it is stored rather than
    # written back whole from a copy read here first: that copy would put back what
    # another writer changed in between, such as the import-state keys an import of the
    # family records (`import_unfinished`, `import_incomplete`).
    family_metadata: dict[str, Any] = {}
    pgt_metadata = _manifest_pgt_metadata(bundle.manifest)
    family_metadata["package_import"] = {
        "source": "family_package",
        # The folder the package was imported from: for a bucket, its gs:// or s3://
        # URI, since the staging copy is deleted after the import.
        "folder_path": bundle.source_uri or str(bundle.root),
        "manifest_path": _display_path(bundle.root, bundle.manifest_path),
        "ped_path": _display_path(bundle.root, bundle.ped_path) if bundle.ped_path is not None else None,
        "schema_version": bundle.manifest.schema_version,
        "family_id": validation.family_id,
        "metadata": bundle.manifest.metadata,
        "datasets": _dataset_provenance(validation),
        "registered_at": now,
    }
    # Promote a declared analysis type (e.g. monogenic_nipt) to the top level so
    # the workspace surfaces the matching analysis section.
    analysis_type = bundle.manifest.analysis_type
    if isinstance(analysis_type, str) and analysis_type.strip():
        family_metadata["analysis_type"] = analysis_type.strip()
    # The manifest's PGT context is merged into the family's, key by key.
    await session.execute(
        text(
            """
            UPDATE families
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                    || CAST(:metadata AS jsonb)
                    || CASE
                        WHEN CAST(:pgt AS jsonb) IS NULL THEN '{}'::jsonb
                        ELSE jsonb_build_object(
                            'pgt',
                            CASE
                                WHEN jsonb_typeof(metadata -> 'pgt') = 'object'
                                    THEN metadata -> 'pgt'
                                ELSE '{}'::jsonb
                            END || CAST(:pgt AS jsonb)
                        )
                    END,
                pedigree = :pedigree
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": family_uuid,
            "metadata": json.dumps(family_metadata),
            "pgt": json.dumps(pgt_metadata) if pgt_metadata else None,
            "pedigree": bundle.ped.text,
        },
    )

    sample_metadata = _normalize_manifest_samples(bundle.manifest.samples)
    sample_provenance = _sample_provenance(bundle)
    manifest_carrier_types = _manifest_carrier_types(bundle.manifest)
    member_overrides = _manifest_member_status_overrides(bundle.manifest, bundle.ped)
    ped_member_state: dict[str, dict[str, Any]] = {}
    for member in bundle.ped.members:
        carrier_type = manifest_carrier_types.get(member.iid) or _ped_carrier_type(member)
        override = member_overrides.get(member.iid, {})
        clinical_status = override.get("clinical_status") or member.clinical_status
        carrier_type = override.get("carrier_type") or carrier_type
        carrier_status = override.get("carrier_status") or (
            "carrier" if carrier_type or member.iid in manifest_carrier_types or _ped_is_carrier(member) else "unknown"
        )
        ped_member_state[member.iid] = {
            "clinical_status": clinical_status,
            "carrier_status": carrier_status,
            "carrier_type": carrier_type,
            "carrier_evidence": override.get("carrier_evidence") or {},
            "role": override.get("role") or member.role_hint,
        }
    if sample_metadata or sample_provenance or ped_member_state:
        result = await session.execute(
            text(
                """
                SELECT id::text AS sample_uuid, sample_id, metadata
                FROM samples
                WHERE family_id = CAST(:family_uuid AS uuid)
                """
            ),
            {"family_uuid": family_uuid},
        )
        for row in result.mappings().all():
            sample_id = str(row["sample_id"])
            metadata = _metadata_dict(row.get("metadata"))
            if sample_id in ped_member_state:
                state = ped_member_state[sample_id]
                await session.execute(
                    text(
                        """
                        UPDATE family_members
                        SET clinical_status = :clinical_status,
                            carrier_status = :carrier_status,
                            carrier_type = :carrier_type,
                            carrier_evidence = CAST(:carrier_evidence AS jsonb),
                            affected = :affected,
                            updated_at = timezone('utc', now())
                        WHERE family_id = CAST(:family_uuid AS uuid)
                          AND sample_id = CAST(:sample_uuid AS uuid)
                        """
                    ),
                    {
                        "family_uuid": family_uuid,
                        "sample_uuid": str(row["sample_uuid"]),
                        "clinical_status": state["clinical_status"],
                        "carrier_status": state["carrier_status"],
                        "carrier_type": state.get("carrier_type"),
                        "carrier_evidence": json.dumps(state.get("carrier_evidence") or {}),
                        "affected": state["clinical_status"] == "affected",
                    },
                )
            if sample_id in sample_metadata:
                metadata["package_sample_metadata"] = sample_metadata[sample_id]
                # Promote a per-sample assay (e.g. nipt_cfdna) to the top level so
                # resolve_nipt_trio can identify the maternal-plasma cfDNA sample, and
                # its capture panel, which scopes the NIPT artifact list (nipt.py).
                for key in ("assay", "assay_panel"):
                    value = sample_metadata[sample_id].get(key)
                    if isinstance(value, str) and value.strip():
                        metadata[key] = value.strip()
            if sample_id in sample_provenance:
                metadata["package_import"] = {
                    "source": "family_package",
                    "datasets": sample_provenance[sample_id],
                    "registered_at": now,
                }
            await session.execute(
                text(
                    """
                    UPDATE samples
                    SET metadata = CAST(:metadata AS jsonb)
                    WHERE id = CAST(:sample_uuid AS uuid)
                    """
                ),
                {
                    "sample_uuid": str(row["sample_uuid"]),
                    "metadata": json.dumps(metadata),
                },
            )
    await _record_package_raw_files(session, bundle=bundle, family_uuid=family_uuid)
    await session.commit()


_ROI_REGION_PATTERN = re.compile(
    r"^(?P<chrom>(?:chr)?[A-Za-z0-9_]+):(?P<start>[0-9,]+)(?:-(?P<end>[0-9,]+))?$",
    re.IGNORECASE,
)


async def _resolve_manifest_roi(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    query: str,
) -> dict[str, Any] | None:
    if not assembly_id:
        return None
    region_match = _ROI_REGION_PATTERN.match(query.strip())
    if region_match:
        chrom = normalize_chromosome(region_match.group("chrom"))
        start = int(region_match.group("start").replace(",", ""))
        end_value = region_match.group("end")
        end = int(end_value.replace(",", "")) if end_value else start
        if end < start:
            start, end = end, start
        return {
            "query": query,
            "label": query,
            "source": "region",
            "assembly_id": assembly_id,
            "chr": chrom,
            "start": start,
            "end": end,
        }
    gene_result = await session.execute(
        text(
            """
            SELECT hgnc_symbol, gene_id, chr, start, "end"
            FROM genes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND (
                lower(hgnc_symbol) = lower(:query)
                OR lower(gene_id) = lower(:query)
                -- Same identifier set as the family gene search: Ensembl transcript and
                -- gene ids, plus the RefSeq accessions the old refGene table was keyed on.
                OR lower(COALESCE(extra->>'ensembl_transcript_id', '')) = lower(:query)
                OR lower(COALESCE(extra->>'ensembl_gene_id', '')) = lower(:query)
                OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(extra->'refseq_accessions', '[]'::jsonb)
                    ) AS accession
                    WHERE lower(accession) = lower(:query)
                       OR lower(split_part(accession, '.', 1)) = lower(:query)
                )
              )
            ORDER BY ("end" - start) DESC, hgnc_symbol
            LIMIT 1
            """
        ),
        {"assembly_id": assembly_id, "query": query},
    )
    gene_row = gene_result.mappings().first()
    if gene_row is None:
        return None
    return {
        "query": query,
        "label": gene_row.get("hgnc_symbol") or gene_row.get("gene_id") or query,
        "source": "gene",
        "assembly_id": assembly_id,
        "chr": normalize_chromosome(str(gene_row["chr"])),
        "start": int(gene_row["start"]),
        "end": int(gene_row["end"]),
    }


async def _apply_manifest_roi(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    context: FamilyMetadataContext,
) -> None:
    roi_query = _manifest_roi_value(bundle.manifest)
    if not roi_query:
        return
    roi = await _resolve_manifest_roi(
        session,
        assembly_id=context.assembly_id,
        query=roi_query,
    )
    if roi is None:
        # Merged where it is stored, as the provenance is (_register_package_provenance).
        await session.execute(
            text(
                """
                UPDATE families
                SET metadata = jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{unresolved_roi}',
                    CAST(:unresolved_roi AS jsonb),
                    true
                )
                WHERE id = CAST(:family_uuid AS uuid)
                """
            ),
            {
                "family_uuid": context.family_uuid,
                "unresolved_roi": json.dumps({"query": roi_query, "source": "manifest"}),
            },
        )
        await session.commit()
        return
    await session.execute(
        text(
            """
            UPDATE families
            SET roi_query = :query,
                roi_label = :label,
                roi_source = :source,
                roi_assembly_id = CAST(:assembly_id AS uuid),
                roi_chr = :chr,
                roi_start = :start,
                roi_end = :end
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": context.family_uuid,
            "query": roi["query"],
            "label": roi["label"],
            "source": roi["source"],
            "assembly_id": roi["assembly_id"],
            "chr": roi["chr"],
            "start": roi["start"],
            "end": roi["end"],
        },
    )
    await session.commit()


def _package_family_id(validation: FamilyPackageValidationOut, bundle: FamilyPackageBundle) -> str:
    """The family a validated package writes: the manifest's family (by default its
    folder's), which every PED row matches."""
    return validation.family_id or bundle.ped.family_ids[0]


# `families.metadata.import_unfinished`: the package imports that have begun writing the
# family and not finished, one entry per import (``ImportMark``). An import records its
# entry before it writes anything of the family -- a new family is created with it -- and
# removes it when it ends: completed, failed and flagged `import_incomplete`, or put back
# to its state before the import. An entry that outlives its import marks one that stopped
# part-way, its process ended by a restart, a crash or running out of memory: what it was
# importing may be partly written. Only a later import that completes and imports again,
# replacing it (`overwrite`), what that import had not finished -- the same SNV source, the
# same samples -- removes the entry. An `update` cannot: it skips a dataset that already
# holds data, partial data included.
IMPORT_UNFINISHED_KEY = "import_unfinished"

# A dataset an import ended with one of these left nothing partly written: it imported
# completely, skipped (update mode: data was there), or registered its files only.
_FINISHED_DATASET_STATUSES = frozenset({"imported", "skipped", "registered"})


def dataset_scopes(
    bundle: FamilyPackageBundle, dataset_types: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """What an overwrite of each dataset replaces, as its loader replaces it: ``source``,
    the SNV callset's declared source (its rows of other sources stay; every other dataset
    has one source of its own), and ``samples``, the samples a per-sample dataset names
    (its other samples' rows stay), or None for one family-level file."""
    scopes: dict[str, dict[str, Any]] = {}
    for dataset_type in dataset_types:
        dataset = bundle.manifest.datasets.get(dataset_type)
        samples: list[str] | None = None
        if dataset is not None and not dataset.family_vcf and dataset.per_sample:
            samples = sorted(
                str(sample_id)
                for sample_id, entry in dataset.per_sample.items()
                if isinstance(entry, dict)
            )
        source: str | None = None
        if dataset_type == "snv" and dataset is not None:
            source = str((dataset.model_extra or {}).get("source_format") or "auto")
        scopes[dataset_type] = {"source": source, "samples": samples}
    return scopes


def _scope_covers(replaced: Any, wanted: Any) -> bool:
    """Whether an overwrite that replaced ``replaced`` replaced all of ``wanted``: the same
    source, and every sample of a per-sample dataset (a family-level file covers only a
    family-level file). Never for a scope not in the shape ``dataset_scopes`` gives."""
    if not isinstance(replaced, Mapping) or not isinstance(wanted, Mapping):
        return False
    if replaced.get("source") != wanted.get("source"):
        return False
    have, want = replaced.get("samples"), wanted.get("samples")
    if have is None or want is None:
        return have is None and want is None
    if not isinstance(have, list) or not isinstance(want, list):
        return False
    return {str(sample) for sample in want} <= {str(sample) for sample in have}


def _failure_imported_again(imported: Mapping[str, Any], name: str, scope: Any) -> bool:
    """Whether a failed dataset was imported again: an import that imported it (in any
    mode -- the failing loader's rows were rolled back or cleaned up, so an update finds
    none and imports it whole, while one whose clean-up left rows is skipped) for the
    scope the flag records. A flag that records no scope for it (written before flags
    did) compares the dataset only."""
    if name not in imported:
        return False
    return scope is None or _scope_covers(imported[name], scope)


def _flag_failures(flag: Any) -> dict[str, dict[str, Any]] | None:
    """The datasets a stored ``import_incomplete`` flag names as failed, each with the
    import job whose record holds its error (``failed_jobs``, else the flag's job) and its
    scope; None when the flag is not in the shape an import writes."""
    if not isinstance(flag, Mapping):
        return None
    failed = _dataset_names(flag.get("failed_datasets"))
    if failed is None:
        return None
    job = flag.get("job_id")
    jobs = flag.get("failed_jobs")
    scopes = flag.get("scopes")
    failures: dict[str, dict[str, Any]] = {}
    for name in failed:
        dataset_job = jobs.get(name) if isinstance(jobs, Mapping) else None
        if not (isinstance(dataset_job, str) and dataset_job):
            dataset_job = job if isinstance(job, str) and job else None
        failures[name] = {
            "job_id": dataset_job,
            "scope": scopes.get(name) if isinstance(scopes, Mapping) else None,
        }
    return failures


def _flag_payload(
    failures: Mapping[str, Mapping[str, Any]],
    *,
    at: Any,
    job_id: Any,
    imported: Iterable[str],
) -> dict[str, Any]:
    names = sorted(failures)
    return {
        "at": at,
        "failed_datasets": names,
        "imported_datasets": sorted({str(name) for name in imported}),
        "job_id": job_id,
        "failed_jobs": {name: failures[name]["job_id"] for name in names},
        "scopes": {
            name: failures[name]["scope"] for name in names if failures[name]["scope"] is not None
        },
    }


def merged_import_failure(
    stored: Any,
    *,
    at: str,
    job_id: str | None,
    failed: Iterable[str],
    imported: Iterable[str],
    scopes: Mapping[str, Any],
    imported_scopes: Mapping[str, Any],
) -> dict[str, Any]:
    """The ``import_incomplete`` flag after an import that failed for ``failed``: its own
    failures, and those of the flag already stored that it did not import again
    (``imported_scopes``: the scope of each dataset it imported). A later failure never
    drops an earlier one. Each failed dataset keeps the job whose record holds its error
    and its scope. A stored flag not in the shape an import writes names nothing to keep."""
    failures: dict[str, dict[str, Any]] = {
        name: earlier
        for name, earlier in (_flag_failures(stored) or {}).items()
        if not _failure_imported_again(imported_scopes, name, earlier["scope"])
    }
    for name in {str(name) for name in failed}:
        failures[name] = {"job_id": job_id, "scope": scopes.get(name)}
    return _flag_payload(failures, at=at, job_id=job_id, imported=imported)


@dataclass(frozen=True)
class FailuresLeft:
    """What of an ``import_incomplete`` flag a completed import leaves: the flag to store
    (None: none), and the failed datasets still to import again, each with its job."""

    flag: Any
    failed: dict[str, str | None]
    unreadable: bool = False


def failures_left_after(stored: Any, imported_scopes: Mapping[str, Any]) -> FailuresLeft:
    """The ``import_incomplete`` flag after an import that completed, having imported
    ``imported_scopes``: each failed dataset it imported again leaves the flag; the flag
    goes with the last. A flag not in the shape an import writes stays as it is: nothing
    shows what would complete it."""
    if stored is None:
        return FailuresLeft(flag=None, failed={})
    failures = _flag_failures(stored)
    if failures is None:
        return FailuresLeft(flag=stored, failed={}, unreadable=True)
    left = {
        name: failure
        for name, failure in failures.items()
        if not _failure_imported_again(imported_scopes, name, failure["scope"])
    }
    if not left or not isinstance(stored, Mapping):
        return FailuresLeft(flag=None, failed={})
    flag = _flag_payload(
        left,
        at=stored.get("at"),
        job_id=stored.get("job_id"),
        imported=_dataset_names(stored.get("imported_datasets")) or (),
    )
    return FailuresLeft(flag=flag, failed={name: left[name]["job_id"] for name in sorted(left)})


@dataclass(frozen=True)
class ImportStateLeft:
    """What a completed import leaves marking the family: other imports' unfinished
    entries, and what of the ``import_incomplete`` flag it did not import again."""

    unfinished: dict[str, Any]
    failures: FailuresLeft


@dataclass(frozen=True)
class ImportMark:
    """One import's entry in ``families.metadata.import_unfinished``.

    Keyed by its job's id, or, run outside a job, by an id of its own run. ``at`` is when
    it began writing the family, ``datasets`` what it set out to import and ``scopes``
    what an overwrite of each replaces (``dataset_scopes``); the entry also records the
    datasets it has finished (``finished_datasets``), so after a stop the others
    (``pending_datasets``) are those that may be partly written or missing.
    """

    key: str
    job_id: str | None
    at: str
    datasets: tuple[str, ...]
    scopes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict, hash=False)

    @classmethod
    def begin(
        cls,
        *,
        job_id: str | None,
        datasets: Iterable[str],
        scopes: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> "ImportMark":
        return cls(
            key=job_id or f"run-{uuid4().hex}",
            job_id=job_id,
            at=datetime.now(timezone.utc).isoformat(),
            datasets=tuple(sorted(set(datasets))),
            scopes=dict(scopes or {}),
        )

    def entry(self, finished: Iterable[str] = ()) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "at": self.at,
            "datasets": list(self.datasets),
            "finished_datasets": sorted(set(finished)),
            "scopes": {name: dict(scope) for name, scope in sorted(self.scopes.items())},
        }


def _dataset_names(value: Any) -> set[str] | None:
    """The dataset names an entry lists, or None when the list is not one CoGA writes."""
    if not isinstance(value, list):
        return None
    return {str(item) for item in value if item}


def pending_datasets(entry: Any) -> set[str] | None:
    """What an unfinished import had not finished, so may have left partly written or
    missing: the datasets it set out to import less those it finished. None when the
    entry does not say (not in the shape an import writes): then nothing is known to be
    complete, and no later import can show it rewrote what the entry left pending."""
    if not isinstance(entry, Mapping):
        return None
    datasets = _dataset_names(entry.get("datasets"))
    finished = _dataset_names(entry.get("finished_datasets"))
    if datasets is None or finished is None:
        return None
    return datasets - finished


def _unfinished_entries(raw: Any) -> dict[str, Any]:
    """The `import_unfinished` map as stored; a value that is set but is not a map is
    kept, under a key of its own, so it still marks the family (it is never dropped)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            pass
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    return {"unreadable": raw}


async def _mark_family_import_unfinished(
    session: AsyncSession,
    *,
    family_uuid: str,
    mark: ImportMark,
    finished: Iterable[str] = (),
) -> None:
    """Write this import's entry into ``import_unfinished`` (created if the map or the
    entry is missing), and commit. Raises: the caller decides whether the import may go
    on without it."""
    await session.execute(
        text(
            """
            UPDATE families
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{import_unfinished}',
                CASE
                    WHEN metadata -> 'import_unfinished' IS NULL
                         OR jsonb_typeof(metadata -> 'import_unfinished') = 'null'
                        THEN '{}'::jsonb
                    WHEN jsonb_typeof(metadata -> 'import_unfinished') = 'object'
                        THEN metadata -> 'import_unfinished'
                    -- Set, but not a map: kept, so it still marks the family.
                    ELSE jsonb_build_object('unreadable', metadata -> 'import_unfinished')
                END || jsonb_build_object(CAST(:import_key AS text), CAST(:entry AS jsonb)),
                true
            )
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": family_uuid,
            "import_key": mark.key,
            "entry": json.dumps(mark.entry(finished)),
        },
    )
    await session.commit()


async def _record_family_import_finished(
    session: AsyncSession,
    family_context: FamilyMetadataContext,
    mark: ImportMark,
    finished: Iterable[str],
) -> None:
    """Record in this import's entry the datasets it has finished. Best-effort: one it
    fails to record counts as not finished, which only keeps more of the import pending."""
    try:
        await _mark_family_import_unfinished(
            session, family_uuid=family_context.family_uuid, mark=mark, finished=finished
        )
    except Exception:  # an unrecorded dataset stays pending: the safe side
        logger.warning(
            "Failed to record the finished datasets of the import of family %s",
            family_context.family_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass


# `metadata` without the entry `:import_key` in import_unfinished, and without the map once
# that empties it; unchanged when there is no such entry (or no key: an import outside
# the bookkeeping, as a test runs one).
_WITHOUT_IMPORT_ENTRY = """
CASE
    WHEN CAST(:import_key AS text) IS NULL
         OR jsonb_typeof(metadata -> 'import_unfinished') IS DISTINCT FROM 'object'
         OR NOT ((metadata -> 'import_unfinished') ? CAST(:import_key AS text))
        THEN COALESCE(metadata, '{}'::jsonb)
    WHEN (metadata -> 'import_unfinished') - CAST(:import_key AS text) = '{}'::jsonb
        THEN metadata - 'import_unfinished'
    ELSE jsonb_set(
        metadata,
        '{import_unfinished}',
        (metadata -> 'import_unfinished') - CAST(:import_key AS text)
    )
END
"""


# The row an import's failure is written to, read under its lock: by the family's uuid, or
# by its identifier (an import that failed before it had the family's context).
_FAILURE_ROW_BY_UUID = """
SELECT id::text AS family_uuid,
       metadata -> 'import_incomplete' AS flag,
       metadata -> 'import_unfinished' AS unfinished
FROM families
WHERE id = CAST(:family_uuid AS uuid)
FOR UPDATE
"""
_FAILURE_ROW_BY_FAMILY_ID = """
SELECT id::text AS family_uuid,
       metadata -> 'import_incomplete' AS flag,
       metadata -> 'import_unfinished' AS unfinished
FROM families
WHERE family_id = :family_id
FOR UPDATE
"""


async def _write_import_failure(
    session: AsyncSession,
    *,
    family_uuid: str | None = None,
    family_id: str | None = None,
    failed_datasets: Iterable[str],
    imported_datasets: Iterable[str],
    job_id: str | None,
    import_key: str | None,
    scopes: Mapping[str, Any],
    imported_scopes: Mapping[str, Any],
    only_with_entry: bool = False,
) -> bool:
    """Write an import's failure into the family's ``import_incomplete`` flag, merged with
    the flag already there (``merged_import_failure``), and remove the import's
    ``import_unfinished`` entry, in one transaction under the row's lock. With
    ``only_with_entry``, only where the import's entry is. Raises on a database error;
    False when there was nothing to write."""
    if family_uuid is not None:
        row_sql, params = _FAILURE_ROW_BY_UUID, {"family_uuid": family_uuid}
    else:
        row_sql, params = _FAILURE_ROW_BY_FAMILY_ID, {"family_id": family_id}
    row = (await session.execute(text(row_sql), params)).mappings().first()
    if row is None or (
        only_with_entry and (import_key is None or import_key not in _unfinished_entries(row["unfinished"]))
    ):
        await session.rollback()
        return False
    flag = row["flag"]
    if isinstance(flag, str):
        try:
            flag = json.loads(flag)
        except ValueError:
            pass
    payload = merged_import_failure(
        flag,
        at=datetime.now(timezone.utc).isoformat(),
        job_id=job_id,
        failed=failed_datasets,
        imported=imported_datasets,
        scopes=scopes,
        imported_scopes=imported_scopes,
    )
    await session.execute(
        text(
            f"""
            UPDATE families
            SET metadata = jsonb_set(
                {_WITHOUT_IMPORT_ENTRY},
                '{{import_incomplete}}',
                CAST(:payload AS jsonb),
                true
            )
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": row["family_uuid"],
            "payload": json.dumps(payload),
            "import_key": import_key,
        },
    )
    await session.commit()
    return True


async def _end_import_failed_before_datasets(
    session: AsyncSession, *, family_id: str, mark: ImportMark
) -> bool:
    """Swap this import's entry for the ``import_incomplete`` flag, naming every dataset it
    set out to import as failed: it failed after it marked the family and before its first
    dataset, so it wrote none of them. Merged with a flag already there. Only where its
    entry is -- not when it failed before marking the family. Best-effort: an entry left
    in place keeps the family marked. True when the swap was made."""
    try:
        # What failed may have left a failed transaction behind.
        await session.rollback()
        return await _write_import_failure(
            session,
            family_id=family_id,
            failed_datasets=mark.datasets,
            imported_datasets=(),
            job_id=mark.job_id,
            import_key=mark.key,
            scopes=mark.scopes,
            imported_scopes={},
            only_with_entry=True,
        )
    except Exception:  # the entry stays: the family stays marked
        logger.warning(
            "Failed to record the failed import of family %s", family_id, exc_info=True
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass
        return False


async def _end_family_import_unfinished(
    session: AsyncSession, family_context: FamilyMetadataContext, *, import_key: str
) -> None:
    """Remove this import's entry: it ended with the family as it was before it (a failed
    overwrite put back). Best-effort: an entry left in place keeps the family marked,
    the safe side."""
    try:
        await session.execute(
            text(
                f"""
                UPDATE families
                SET metadata = {_WITHOUT_IMPORT_ENTRY}
                WHERE id = CAST(:family_uuid AS uuid)
                """
            ),
            {"family_uuid": family_context.family_uuid, "import_key": import_key},
        )
        await session.commit()
    except Exception:  # the entry stays: the family stays marked
        logger.warning(
            "Failed to remove the unfinished-import entry of family %s",
            family_context.family_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass


async def _ensure_family_from_ped(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    project_id: str | None,
    user: CurrentUser,
    validation: FamilyPackageValidationOut,
    conflict_mode: str = "cancel",
    import_mark: ImportMark | None = None,
) -> tuple[FamilyMetadataContext, bool]:
    """Create the package's family, or check the existing one may take the import, then
    register the package (provenance, ROI). Returns the family and whether it was created.

    ``import_mark``, when given, is recorded before anything of the family is written: a
    new family is created with it, an existing one marked (committed) once its checks
    pass. If that cannot be done the import stops here, having written nothing."""
    resolved_project_id = await ped_service._resolve_accessible_project_id(session, user, project_id)
    family_id = _package_family_id(validation, bundle)
    existing = await _fetch_existing_family(session, family_id=family_id)
    created = existing is None
    if existing is None:
        await ped_service._ensure_sample_ids_are_available(session, bundle.ped.sample_ids)
        members = _ped_members_for_import(
            bundle.ped,
            carrier_types=_manifest_carrier_types(bundle.manifest),
            member_overrides=_manifest_member_status_overrides(bundle.manifest, bundle.ped),
        )
        relationships = ped_service._relationships_from_members(members)
        seen_relationships = {
            ped_service._relationship_key(
                relationship["relationship_type"],
                relationship["sample_id_a"],
                relationship["sample_id_b"],
                relationship.get("role_a"),
                relationship.get("role_b"),
            )
            for relationship in relationships
        }
        for relationship in _manifest_relationships(bundle.manifest):
            key = ped_service._relationship_key(
                relationship["relationship_type"],
                relationship["sample_id_a"],
                relationship["sample_id_b"],
                relationship.get("role_a"),
                relationship.get("role_b"),
            )
            if key not in seen_relationships:
                seen_relationships.add(key)
                relationships.append(relationship)
        await ped_service._create_family(
            session,
            family_id=family_id,
            pedigree=bundle.ped.text,
            members=members,
            relationships=relationships,
            project_id=resolved_project_id,
            # Created with the import's entry: the family never exists without it.
            family_metadata=(
                {IMPORT_UNFINISHED_KEY: {import_mark.key: import_mark.entry()}}
                if import_mark is not None
                else None
            ),
            created_by=user.id,
        )
        await session.commit()
    else:
        if conflict_mode == "cancel":
            raise RuntimeError(
                f"Family '{family_id}' already exists; choose update or overwrite to import data."
            )
        existing_samples = set(str(sample_id) for sample_id in existing.get("sample_ids", []) if sample_id)
        requested_samples = set(bundle.ped.sample_ids)
        if existing_samples != requested_samples:
            raise RuntimeError(
                "Existing family has different sample IDs; refusing to attach package import "
                f"to {family_id}"
            )
        if import_mark is not None:
            # Before the first write of the family.
            await _mark_family_import_unfinished(
                session, family_uuid=str(existing["family_uuid"]), mark=import_mark
            )
        if resolved_project_id is not None:
            await session.execute(
                text(
                    """
                    INSERT INTO family_projects (family_id, project_id)
                    VALUES (CAST(:family_uuid AS uuid), CAST(:project_id AS uuid))
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"family_uuid": existing["family_uuid"], "project_id": resolved_project_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO sample_projects (sample_id, project_id)
                    SELECT id, CAST(:project_id AS uuid)
                    FROM samples
                    WHERE family_id = CAST(:family_uuid AS uuid)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"family_uuid": existing["family_uuid"], "project_id": resolved_project_id},
            )
            await session.commit()

    context = await build_family_metadata_context(
        session,
        family_identifier=family_id,
        user=user,
        project_id=resolved_project_id,
    )
    await _register_package_provenance(
        session,
        bundle=bundle,
        validation=validation,
        family_uuid=context.family_uuid,
    )
    await _apply_manifest_roi(session, bundle=bundle, context=context)
    return context, created


async def _delete_family_shell(
    session: AsyncSession, family_context: FamilyMetadataContext
) -> None:
    """Compensating cleanup for a failed import that created a fresh family.

    Removes the family's ClickHouse variant rows and the Postgres family shell
    (samples / members / other family-scoped rows cascade via ``ON DELETE
    CASCADE``) so a failed import leaves no orphan partial state. Mirrors the
    deletion recipe in ``ped_service``.
    """
    assembly_name = family_context.assembly_name
    family_uuid = family_context.family_uuid
    # The import that calls this holds the lock already; any other caller takes it here.
    await lock_family_variant_writes(session, family_uuid, VARIANT_TYPES)
    if assembly_name:
        try:
            await delete_family_small_variants(assembly_name, family_uuid)
            await delete_family_structural_variants(assembly_name, family_uuid)
            # Coverage/segment/haplotype interval tracks are keyed by family_guid in
            # a separate ClickHouse table; without this they survive the family delete
            # as orphan rows pointing at a now-deleted family.
            await delete_interval_tracks(assembly_name, family_uuid=family_uuid)
        except Exception:  # best-effort store cleanup
            logger.warning(
                "Failed to clear ClickHouse rows during import compensation for %s",
                family_context.family_id,
                exc_info=True,
            )
    await session.execute(
        text("DELETE FROM families WHERE id = CAST(:family_uuid AS uuid)"),
        {"family_uuid": family_uuid},
    )


async def _flag_family_import_incomplete(
    session: AsyncSession,
    family_context: FamilyMetadataContext,
    *,
    failed_datasets: list[str],
    imported_datasets: list[str],
    job_id: str | None = None,
    import_key: str | None = None,
    scopes: Mapping[str, Any] | None = None,
    imported_scopes: Mapping[str, Any] | None = None,
) -> None:
    """Stamp a pre-existing family as import-incomplete after a failed update/overwrite.

    We do not snapshot/restore existing family data, so a partial update/overwrite can
    leave the family with some datasets (over)written and others missing (overwrite
    even pre-clears before insert). Rather than let that state be silently queryable as
    if complete, record it in the family metadata so it is explicit and auditable.
    Best-effort and self-committing: a flag-write failure must not mask the original
    import failure.

    The flag names the import job (``job_id``, None for an import run outside a job):
    the job's record holds each dataset's error. The error texts are not copied here, as
    they can carry file paths and grow long. It records each failed dataset's scope
    (``scopes``, from ``dataset_scopes``) and, merged with a flag already there, keeps an
    earlier import's failures this import did not import again (``imported_scopes``),
    each with the job that holds its error (``failed_jobs``): the flag clears only once
    every failed dataset has been imported again (``failures_left_after``).

    The import has ended, so its ``import_unfinished`` entry (``import_key``) goes in the
    same transaction: the family is never without one or the other. If the write fails,
    the entry stays and still marks the family.

    Read back by report sign-out (``report_signout_service._import_incomplete_state``),
    which refuses a flagged family unless the signer acknowledges it with a reason, and
    by the family pages, which warn while it is set. Keep the payload's keys in step.
    """
    try:
        await _write_import_failure(
            session,
            family_uuid=family_context.family_uuid,
            failed_datasets=failed_datasets,
            imported_datasets=imported_datasets,
            job_id=job_id,
            import_key=import_key,
            scopes=scopes or {},
            imported_scopes=imported_scopes or {},
        )
    except Exception:  # flag write must not mask the import failure
        logger.warning(
            "Failed to flag family %s as import-incomplete",
            family_context.family_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass


# The family's metadata with each import-state key set to its new value, or removed (a JSON
# null): :flag for import_incomplete, :unfinished for import_unfinished.
_WITH_IMPORT_STATE = """
CASE
    WHEN CAST(:unfinished AS jsonb) = 'null'::jsonb THEN {flagged} - 'import_unfinished'
    ELSE jsonb_set({flagged}, '{{import_unfinished}}', CAST(:unfinished AS jsonb), true)
END
""".format(
    flagged="""(
    CASE
        WHEN CAST(:flag AS jsonb) = 'null'::jsonb
            THEN COALESCE(metadata, '{}'::jsonb) - 'import_incomplete'
        ELSE jsonb_set(COALESCE(metadata, '{}'::jsonb), '{import_incomplete}', CAST(:flag AS jsonb), true)
    END
)"""
)


async def _clear_family_import_incomplete(
    session: AsyncSession,
    family_context: FamilyMetadataContext,
    *,
    import_key: str | None = None,
    rewritten: Mapping[str, Any] | None = None,
    imported: Mapping[str, Any] | None = None,
) -> ImportStateLeft | None:
    """After a fully-successful (re)import: remove what of the ``import_incomplete`` flag
    it imported again, and this import's ``import_unfinished`` entry (``import_key``).

    The flag shrinks by each failed dataset this import imported again, in any mode, for
    the scope the flag records (``imported``: the scope of each dataset it imported, as
    ``dataset_scopes`` gives it), and goes with the last (``failures_left_after``); an
    import that does not import a failed dataset leaves it flagged.

    An earlier import's entry goes when this import completed what that one may have left
    partly written: every dataset it had not finished is one this import imported again,
    replacing all of what that one was writing (``rewritten``: as ``imported``, but only
    for an ``overwrite``; an ``update`` replaces nothing, so passes none), or it had
    finished them all. Any other entry stays. Returns what is left, so the import can say
    why the family stays marked, or None when nothing could be cleared.

    Read and written under the row's lock, so no other mark written meanwhile is lost.
    Best-effort: a flag or an entry left in place keeps the family marked, the safe side.
    """
    replaced = dict(rewritten or {})
    try:
        row = (
            await session.execute(
                text(
                    "SELECT metadata -> 'import_incomplete' AS flag, "
                    "metadata -> 'import_unfinished' AS unfinished "
                    "FROM families WHERE id = CAST(:family_uuid AS uuid) FOR UPDATE"
                ),
                {"family_uuid": family_context.family_uuid},
            )
        ).mappings().first()
        stored_flag = row["flag"] if row is not None else None
        if isinstance(stored_flag, str):
            try:
                stored_flag = json.loads(stored_flag)
            except ValueError:
                pass
        remaining: dict[str, Any] = {}
        for key, entry in _unfinished_entries(row["unfinished"] if row is not None else None).items():
            if key == import_key:
                continue
            pending = pending_datasets(entry)
            scopes = entry.get("scopes") if isinstance(entry, Mapping) else None
            if pending is not None and all(
                _scope_covers(
                    replaced.get(name),
                    scopes.get(name) if isinstance(scopes, Mapping) else None,
                )
                for name in pending
            ):
                continue
            remaining[key] = entry
        failures = failures_left_after(stored_flag, dict(imported or {}))
        await session.execute(
            text(
                f"""
                UPDATE families
                SET metadata = {_WITH_IMPORT_STATE}
                WHERE id = CAST(:family_uuid AS uuid)
                """
            ),
            {
                "family_uuid": family_context.family_uuid,
                "flag": json.dumps(failures.flag),
                "unfinished": json.dumps(remaining or None),
            },
        )
        await session.commit()
        return ImportStateLeft(unfinished=remaining, failures=failures)
    except Exception:  # best-effort flag clear
        logger.warning(
            "Failed to clear import-incomplete flag for family %s",
            family_context.family_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass
        return None


def _enabled_dataset_summaries(validation: FamilyPackageValidationOut) -> list[FamilyImportDatasetSummary]:
    return [
        summary
        for summary in validation.datasets
        if summary.enabled and summary.status in {"valid", "warning"}
    ]


async def _register_only(summary: FamilyImportDatasetSummary, message: str) -> FamilyImportDatasetSummary:
    return summary.model_copy(
        update={
            "status": "registered",
            "message": message,
        }
    )


def _normalized_conflict_mode(value: str | None) -> str:
    return value if value in {"cancel", "update", "overwrite"} else "cancel"


def _execution_metadata(
    *,
    requested_family_id: str | None,
    conflict_mode: str,
) -> dict[str, Any]:
    return {
        "requested_family_id": requested_family_id,
        "conflict_mode": conflict_mode,
    }


def _merge_validation_metadata(
    validation: FamilyPackageValidationOut,
    metadata: dict[str, Any],
) -> FamilyPackageValidationOut:
    return validation.model_copy(
        update={
            "metadata": {
                **validation.metadata,
                **metadata,
            }
        }
    )


async def _existing_sample_ids(
    session: AsyncSession,
    sample_ids: list[str],
) -> list[str]:
    if not sample_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT sample_id
            FROM samples
            WHERE sample_id IN :sample_ids
            ORDER BY sample_id
            """
        ).bindparams(bindparam("sample_ids", expanding=True)),
        {"sample_ids": list(dict.fromkeys(sample_ids))},
    )
    return [str(row["sample_id"]) for row in result.mappings().all()]


async def _existing_package_entity_warnings(
    session: AsyncSession,
    *,
    family_id: str | None,
    sample_ids: list[str],
) -> list[FamilyImportValidationIssue]:
    warnings: list[FamilyImportValidationIssue] = []
    if family_id:
        existing_family = await _fetch_existing_family(session, family_id=family_id)
        if existing_family is not None:
            warnings.append(
                _issue(
                    "existing_family",
                    f"Family '{family_id}' already exists. Choose update, overwrite, or cancel before importing data.",
                )
            )
    existing_samples = await _existing_sample_ids(session, sample_ids)
    if existing_samples:
        preview = ", ".join(existing_samples[:10])
        suffix = "" if len(existing_samples) <= 10 else f", and {len(existing_samples) - 10} more"
        warnings.append(
            _issue(
                "existing_samples",
                f"Sample ID(s) already exist in the system: {preview}{suffix}.",
            )
        )
    return warnings


async def _interval_track_count(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str | None = None,
) -> int:
    return await count_interval_track_source_rows(
        session,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
    )


async def _repeat_expansion_count(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
) -> int:
    sample_uuids = [context.sample_uuid for context in sample_contexts.values()]
    if not sample_uuids:
        return 0
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM repeat_expansions
            WHERE sample_id::text IN :sample_uuids
              AND source = 'trgt'
            """
        ).bindparams(bindparam("sample_uuids", expanding=True)),
        {"sample_uuids": sample_uuids},
    )
    return int(result.scalar_one() or 0)


async def _paraphase_count(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
) -> int:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM sample_paraphase_results
            WHERE sample_id = CAST(:sample_id AS uuid)
            """
        ),
        {"sample_id": sample_context.sample_uuid},
    )
    return int(result.scalar_one() or 0)
