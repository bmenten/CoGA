from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Collection, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
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
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyImportValidationIssue,
    FamilyPackageValidationOut,
)
from .coverage_content import COVERAGE_CONTENT_KEY, declared_coverage_content
from .clickhouse_interval_tracks import (
    count_interval_track_source_rows,
    delete_interval_tracks,
)
from .clickhouse_variant_storage import (
    SmallVariantsNotRestored,
    StructuralVariantsNotRestored,
    delete_family_small_variants,
    delete_family_structural_variants,
)
from .data_scope import normalize_chromosome
from .family_identifiers import identifier_problem
from .family_structure_audit import (
    IMPORT_FAILED,
    PACKAGE_IMPORT,
    audit_actor,
    lock_family_pedigree,
    read_structure_snapshot,
    record_family_deleted,
    record_structure_change,
)
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
)
from .family_variant_write_lock import VARIANT_TYPES, lock_family_variant_writes
from .hpo_service import PHENOTYPES_ROLLED_BACK, PhenotypeImport, record_phenotypes_deleted_with_family
from .access_control import CurrentUser
from . import ped_service
from .raw_import_files_pg import record_raw_import_file

from .family_package_common import HAPLOTYPE_ORIGIN_ROLES, LONG_READ_DATASET_ROLES, PCF_ROLE_KEYS, QC_FAMILY_ROLES, FamilyPackageBundle, ManifestDataset, _display_path, _issue, _metadata_dict, _resolve_package_path
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


# The keys under which a dataset of the manifest names a package file, per sample or at
# the dataset's top level: each file named under one gets its raw_import_files row and is
# listed in its sample's package record. A key validation checks a file under that this set
# lacks lets the file validate and import with neither, and the traceability record would
# not name a file a released interpretation rests on; test_family_package_raw_files.py
# explores every key validation reads as a file. The role lists are the ones discovery,
# validation and the importers use.
_PROVENANCE_PATH_KEYS = frozenset(
    {
        # ManifestDataset's own file fields (``json`` is its ``json_path``), which the
        # per-sample entries use too.
        "family_vcf",
        "annotation_tsv",
        "index",
        "bed",
        "vcf",
        "file",
        "json",
        # The copy-number callers' bins and segments, and a GLIMPSE2 BCF's index.
        "bins",
        "segments",
        "bcf_index",
        # A capture panel's per-target coverage table (monogenic NIPT), which gives the
        # NIPT analysis its depths.
        "target_table",
        # Each parent's PCF segment table, under any of its names.
        *(key for keys in PCF_ROLE_KEYS.values() for key in keys),
        # The Paraphase BAM Discover lists beside the JSON.
        "bam",
        # The long-read datasets' files, and each sample's QC files of either pipeline.
        *(role for required, optional in LONG_READ_DATASET_ROLES.values() for role in (*required, *optional)),
        # The PGT pipeline's (nf-cmgg/copgtm) family files: its QC tables and its reading
        # of the affected haplotype.
        *QC_FAMILY_ROLES,
        *HAPLOTYPE_ORIGIN_ROLES,
        # The Nextflow run record.
        "params",
        "versions",
        "execution_trace",
        "execution_report",
    }
)


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
    family_identifier: str,
    user: CurrentUser,
) -> None:
    """Record the package's provenance on the family and refresh its members' affected and
    carrier status from the PED and the manifest. A refresh that changes the pedigree is
    stored as the family's next structure version and recorded on its clinical audit
    trail as made by ``user`` (SEC-7)."""
    now = datetime.now(timezone.utc).isoformat()
    await lock_family_pedigree(session, family_identifier)
    pedigree_before = await read_structure_snapshot(session, family_uuid)
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
    await record_structure_change(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        before_snapshot=pedigree_before,
        user=user,
        source=PACKAGE_IMPORT,
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
# replacing it (`overwrite`), what that import had not finished -- the same SNV callset (as
# stored: `auto` as each resolved it), the same samples -- removes the entry. An `update`
# cannot: it skips a dataset that already holds data, partial data included.
#
# A failed overwrite's restore is the exception. It rewrites the family's rows in every
# variant and track table, whichever import or single-file upload wrote them, so once it
# begins, its import's entry lists every dataset the family holds in ClickHouse
# (`imported_scopes`: ``merged_imported_scopes``, which the uploads add to as the imports
# do) besides its own, under `whole_family`, each with its scope (``whole_family_entry``). A restore that puts
# the family back, or that refuses to start, removes the entry as before. One that fails
# part-way, or whose process stops in it, leaves it: any dataset of the family may then be
# partly written or missing, whichever import or upload wrote it. Each later import that
# completes with `overwrite` removes from it what it imported again, and each upload with
# `overwrite` what it brought again (``record_family_upload``); the entry goes with the
# last: the family has then been imported again in full. Nothing else removes it.
IMPORT_UNFINISHED_KEY = "import_unfinished"
WHOLE_FAMILY_KEY = "whole_family"

# A dataset an import ended with one of these left nothing partly written: it imported
# completely, or with warnings (rows its input got wrong left out), skipped (update mode:
# data was there), or registered its files only. This says only what the import's own
# entry no longer lists as pending: what counts as importing a dataset again is
# ``datasets_brought_again``, which a skip never is.
_FINISHED_DATASET_STATUSES = frozenset({"imported", "warning", "skipped", "registered"})

# A dataset an import ended with one of these brought its data again: completely, or with
# warnings (the rows its input got wrong left out, each named in the job's record; SAFE-17).
_IMPORTED_DATASET_STATUSES = frozenset({"imported", "warning"})

# The key of a scope (``dataset_scopes``) whose dataset carries nothing CoGA imports: no file
# or sample entry, or only a file CoGA does not import (``dataset_carries_nothing``). Such a
# dataset brings nothing, so nothing of it can be missing: a failure or entry of it is
# completed by any import or upload that brings the dataset again, its registration included,
# and its registration completes nothing else (SAFE-17). Set only when true.
EMPTY_SCOPE_KEY = "empty"


def _carries_nothing(scope: Any) -> bool:
    return isinstance(scope, Mapping) and scope.get(EMPTY_SCOPE_KEY) is True


def datasets_brought_again(endings: Iterable[tuple[str, str]], scopes: Mapping[str, Any]) -> list[str]:
    """The datasets an import brought again, by how it ended each (``endings``: the dataset
    and its status): imported, or imported with warnings; and registered when its package
    carries nothing for it (its scope ``empty``, ``scopes``), so nothing of it can be missing
    (SAFE-17). Never one it registered for want of an assembly or of a file it could find or
    read, which may lack data, nor one an update skipped, its rows being there (SAFE-20)."""
    return sorted(
        {
            str(name)
            for name, status in endings
            if status in _IMPORTED_DATASET_STATUSES
            or (status == "registered" and _carries_nothing(scopes.get(name)))
        }
    )

# The source of an SNV scope whose callset was never resolved: what the manifest's
# `source_format: auto` (the default) says before the file is read. A scope records the
# callset the import stores, `auto` as it resolves (SAFE-23); one that still reads `auto`
# names no callset: written before imports resolved it, or by an import that could not
# read its family VCF when it began. It covers nothing, and nothing covers it: two scopes
# that read `auto` may stand for different callsets, and either may have stored another
# callset than an import or upload that names one.
UNRESOLVED_SNV_SOURCE = "auto"


def dataset_scopes(
    bundle: FamilyPackageBundle, dataset_types: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """What an overwrite of each dataset replaces, as its loader replaces it: ``source``,
    the callset the SNV dataset is stored as (its rows of other callsets stay; every other
    dataset has one source of its own), and ``samples``, the samples a per-sample dataset
    names (its other samples' rows stay), or None for one family-level file.

    The SNV callset is the one the import stores (``snv_dataset_callset``): the
    ``source_format`` the dataset declares, or with ``auto`` the callset the loader works
    out from the family VCF, read from the file here, before the import's first write.
    Two imports that leave ``source_format`` at ``auto`` then replace each other's rows only
    when their files are the same callset, and their scopes say so (SAFE-23).

    A dataset that carries nothing CoGA imports (``dataset_carries_nothing``) is marked
    ``empty`` (``EMPTY_SCOPE_KEY``): it brings nothing, and nothing of it can be missing
    (SAFE-17)."""
    # Imported here: family_package_datasets imports this module.
    from .family_package_datasets import dataset_carries_nothing, snv_dataset_callset

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
            source = snv_dataset_callset(bundle)
        scope: dict[str, Any] = {"source": source, "samples": samples}
        if dataset_type == "coverage" and dataset is not None and samples:
            # What each sample's coverage BED declares it holds (TRACE-9), as the import
            # stores it: `unknown` without the manifest's `content`. A per-target table
            # declares nothing.
            contents = {
                str(sample_id): declared_coverage_content(entry.get(COVERAGE_CONTENT_KEY))
                for sample_id, entry in dataset.per_sample.items()
                if isinstance(entry, dict)
                and not entry.get("target_table")
                and (entry.get("bed") or entry.get("file"))
            }
            if contents:
                scope[COVERAGE_CONTENT_KEY] = contents
        if dataset_carries_nothing(bundle, dataset_type):
            scope[EMPTY_SCOPE_KEY] = True
        scopes[dataset_type] = scope
    return scopes


def _scope_covers(replaced: Any, wanted: Any) -> bool:
    """Whether an overwrite that replaced ``replaced`` replaced all of ``wanted``: the same
    source, and every sample of a per-sample dataset (a family-level file covers only a
    family-level file). Never for a scope not in the shape ``dataset_scopes`` gives, nor
    for an SNV scope that names no callset (``UNRESOLVED_SNV_SOURCE``), on either side.

    A dataset that carried nothing (``empty``) is covered by any scope of it brought again,
    as nothing of it can be missing; one that brings nothing covers only such a dataset,
    never data another import or upload brought (SAFE-17)."""
    if not isinstance(replaced, Mapping) or not isinstance(wanted, Mapping):
        return False
    if _carries_nothing(wanted):
        return True
    if _carries_nothing(replaced):
        return False
    source = replaced.get("source")
    if source == UNRESOLVED_SNV_SOURCE or source != wanted.get("source"):
        return False
    have, want = replaced.get("samples"), wanted.get("samples")
    if have is None or want is None:
        return have is None and want is None
    if not isinstance(have, list) or not isinstance(want, list):
        return False
    return {str(sample) for sample in want} <= {str(sample) for sample in have}


def imported_dataset_scopes(
    imported: Iterable[str],
    scopes: Mapping[str, Any],
    skipped: Mapping[str, Collection[str]],
) -> dict[str, Any]:
    """What an import imported again: each dataset it brought again (``imported``, as
    ``datasets_brought_again`` gives them), with its scope (``scopes``, as ``dataset_scopes``
    gives it) less the samples an update left as they were, their rows being there
    (``skipped``). A skip imports nothing again, so it completes no failure the flag records
    and no entry: a dataset left with no sample, or one whose scope is not split by sample
    and that skipped any, is not listed (SAFE-20). A dataset an update skipped whole ends
    ``skipped``, and is not in ``imported``."""
    result: dict[str, Any] = {}
    for name in imported:
        scope = scopes.get(name)
        left_out = {str(sample) for sample in skipped.get(name, ())}
        if not left_out:
            result[name] = scope
            continue
        samples = scope.get("samples") if isinstance(scope, Mapping) else None
        if not isinstance(samples, list):
            continue
        kept = sorted({str(sample) for sample in samples if sample} - left_out)
        if kept:
            result[name] = {**scope, "samples": kept}
    return result


def scope_with_samples(scope: Any, samples: Iterable[str]) -> Any:
    """``scope`` (as ``dataset_scopes`` gives it) with ``samples`` added to the samples it
    names: what a failed dataset is flagged for when its failure may have removed the data
    of samples it does not name (``DatasetLeftMissing``), so that the flag holds until the
    dataset has been imported again for them too. A family-level scope (no samples) covers
    every sample already, and a scope not in that shape is kept as it is."""
    if not isinstance(scope, Mapping) or not isinstance(scope.get("samples"), list):
        return scope
    named = {str(sample) for sample in scope["samples"] if sample}
    return {**scope, "samples": sorted(named | {str(sample) for sample in samples if sample})}


def _failure_imported_again(imported: Mapping[str, Any], name: str, scope: Any) -> bool:
    """Whether a failure of a dataset was imported again: an import that imported the
    dataset (in any mode -- the failing loader's rows were rolled back or cleaned up, so an
    update finds none and imports it whole, while one whose clean-up left rows is skipped)
    for the scope the failure records. A failure that records no scope (written before
    flags did) compares the dataset only."""
    if name not in imported:
        return False
    return scope is None or _scope_covers(imported[name], scope)


# `import_incomplete.failed_scopes`: for each failed dataset, every scope it failed for --
# each callset of the small variants, each set of samples of a per-sample dataset -- oldest
# first, each with the import job whose record holds its error (None: an upload, or an
# import run outside a job). A later failure of the same dataset under another scope never
# replaces an earlier one: each leaves the flag only once an import or upload has brought
# that scope again (SAFE-26). A flag written before keeps one scope per dataset (`scopes`,
# its job in `failed_jobs`), and reads as that one failure.
FAILED_SCOPES_KEY = "failed_scopes"


def _job_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _flag_failures(flag: Any) -> dict[str, list[dict[str, Any]]] | None:
    """The datasets a stored ``import_incomplete`` flag names as failed, each with every
    failure of it the flag records, oldest first: its scope, and the import job whose record
    holds its error (``failed_scopes``). A flag written before flags kept every scope reads
    as one failure per dataset: its scope (``scopes``) and its job (``failed_jobs``, else the
    flag's). None when the flag is not in the shape an import writes: also a
    ``failed_scopes`` that does not list each failed dataset's failures as written."""
    if not isinstance(flag, Mapping):
        return None
    failed = _dataset_names(flag.get("failed_datasets"))
    if failed is None:
        return None
    listed = flag.get(FAILED_SCOPES_KEY)
    failures: dict[str, list[dict[str, Any]]] = {}
    if listed is not None:
        if not isinstance(listed, Mapping):
            return None
        for name in failed:
            items = listed.get(name)
            if not (
                isinstance(items, list)
                and items
                and all(isinstance(item, Mapping) and "scope" in item for item in items)
            ):
                return None
            failures[name] = [
                {"job_id": _job_or_none(item.get("job_id")), "scope": item["scope"]} for item in items
            ]
        return failures
    job = _job_or_none(flag.get("job_id"))
    jobs = flag.get("failed_jobs")
    scopes = flag.get("scopes")
    for name in failed:
        dataset_job = _job_or_none(jobs.get(name)) if isinstance(jobs, Mapping) else None
        failures[name] = [
            {
                "job_id": dataset_job or job,
                "scope": scopes.get(name) if isinstance(scopes, Mapping) else None,
            }
        ]
    return failures


def _flag_payload(
    failures: Mapping[str, list[dict[str, Any]]],
    *,
    at: Any,
    job_id: Any,
    imported: Iterable[str],
) -> dict[str, Any]:
    """The ``import_incomplete`` flag naming ``failures`` (each failed dataset with every
    failure of it, oldest first). ``failed_jobs`` names, for each, the job of its latest
    failure: the one job the family pages and a sign-out name for it."""
    names = sorted(failures)
    return {
        "at": at,
        "failed_datasets": names,
        "imported_datasets": sorted({str(name) for name in imported}),
        "job_id": job_id,
        "failed_jobs": {name: failures[name][-1]["job_id"] for name in names},
        FAILED_SCOPES_KEY: {
            name: [{"scope": item["scope"], "job_id": item["job_id"]} for item in failures[name]]
            for name in names
        },
    }


def _failures_not_imported_again(
    failures: Mapping[str, list[dict[str, Any]]], imported_scopes: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """``failures`` without each one an import or upload brought again
    (``imported_scopes``: the scope of each dataset it brought), and without a dataset once
    none of its failures is left."""
    left: dict[str, list[dict[str, Any]]] = {}
    for name, items in failures.items():
        kept = [
            item for item in items if not _failure_imported_again(imported_scopes, name, item["scope"])
        ]
        if kept:
            left[name] = kept
    return left


def merged_import_failure(
    stored: Any,
    *,
    at: str,
    job_id: str | None,
    failed: Iterable[str],
    imported: Iterable[str],
    scopes: Mapping[str, Any],
    imported_scopes: Mapping[str, Any],
) -> Any:
    """The ``import_incomplete`` flag after an import that failed for ``failed``: its own
    failures, and those of the flag already stored that it did not import again
    (``imported_scopes``: the scope of each dataset it imported). A later failure never
    drops an earlier one, of another dataset or of the same dataset under another scope
    (another callset, other samples): each failure keeps its scope and the job whose record
    holds its error, and leaves the flag only once its scope has been brought again
    (SAFE-26). The same scope failing again is one failure, with its latest job.

    A stored flag not in the shape an import writes (``true``, a list) is kept as it is: it
    says the family is incomplete without naming what would complete it, as a sign-out reads
    it, and a completed import leaves it too (``failures_left_after``). Replacing it would
    let the family read as complete once this import's own failures had been imported again
    (SAFE-18). This import's failures are then named in its job's record alone."""
    if stored is not None and stored is not False and _flag_failures(stored) is None:
        return stored
    failures = _failures_not_imported_again(_flag_failures(stored) or {}, imported_scopes)
    for name in sorted({str(name) for name in failed}):
        scope = scopes.get(name)
        earlier = [
            item for item in failures.get(name, []) if _canonical(item["scope"]) != _canonical(scope)
        ]
        failures[name] = [*earlier, {"job_id": job_id, "scope": scope}]
    return _flag_payload(failures, at=at, job_id=job_id, imported=imported)


@dataclass(frozen=True)
class FailuresLeft:
    """What of an ``import_incomplete`` flag a completed import, or an upload, leaves: the
    flag to store (None: none), whether that changes the flag stored (``changed``), and the
    failed datasets still to bring again, each with the job of its latest failure
    (``failed``) and the job of each of its failures, oldest first (``jobs``)."""

    flag: Any
    failed: dict[str, str | None]
    unreadable: bool = False
    jobs: dict[str, list[str | None]] = field(default_factory=dict)
    changed: bool = False


def failures_left_after(stored: Any, imported_scopes: Mapping[str, Any]) -> FailuresLeft:
    """The ``import_incomplete`` flag after an import that completed, or an upload, having
    brought ``imported_scopes``: each failure it brought again, for the scope the failure
    records, leaves the flag; a dataset leaves it with its last failure, and the flag goes
    with the last dataset. A flag nothing left stays as it was stored. A flag not in the
    shape an import writes stays as it is: nothing shows what would complete it.

    When the failures left are all one job's (or all of no job: uploads), that job becomes
    the flag's ``job_id``, the job the family pages and a sign-out name when one job holds
    every error: the failure that set the flag may have been brought again, and its job then
    holds none of them. Its time (``at``) stays."""
    if stored is None:
        return FailuresLeft(flag=None, failed={})
    failures = _flag_failures(stored)
    if failures is None or not isinstance(stored, Mapping):
        return FailuresLeft(flag=stored, failed={}, unreadable=True)
    left = _failures_not_imported_again(failures, imported_scopes)
    if not left:
        return FailuresLeft(flag=None, failed={}, changed=True)
    changed = sum(map(len, left.values())) != sum(map(len, failures.values()))
    jobs_left = {item["job_id"] for items in left.values() for item in items}
    flag = (
        _flag_payload(
            left,
            at=stored.get("at"),
            job_id=next(iter(jobs_left)) if len(jobs_left) == 1 else stored.get("job_id"),
            imported=_dataset_names(stored.get("imported_datasets")) or (),
        )
        if changed
        else stored
    )
    return FailuresLeft(
        flag=flag,
        failed={name: left[name][-1]["job_id"] for name in sorted(left)},
        jobs={name: [item["job_id"] for item in left[name]] for name in sorted(left)},
        changed=changed,
    )


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


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _scope_items(dataset: str, scope: Any) -> list[dict[str, Any]]:
    """A dataset's scope as ``imported_scopes`` and a whole-family entry list it: a
    per-sample scope one sample at a time, as an overwrite of each sample replaces it. A
    scope not in the shape ``dataset_scopes`` gives is kept as it is (None when there is
    none): no import can be shown to cover it. A dataset that carries nothing stays marked
    so (``EMPTY_SCOPE_KEY``).

    A coverage BED's scope also says what each sample's BED declared it holds
    (``content``: one value for an upload's sample, or sample -> value for a package's
    ``coverage`` dataset, TRACE-9), and each sample's item keeps its own. It says what the
    data is, not what an overwrite replaces: ``_scope_covers`` does not read it."""
    if not isinstance(scope, Mapping):
        return [{"dataset": dataset, "scope": None}]
    source, samples = scope.get("source"), scope.get("samples")
    empty = {EMPTY_SCOPE_KEY: True} if _carries_nothing(scope) else {}
    content = scope.get(COVERAGE_CONTENT_KEY)

    def item(item_samples: Any, name: str | None = None) -> dict[str, Any]:
        item_scope: dict[str, Any] = {"source": source, "samples": item_samples, **empty}
        declared = content.get(name) if isinstance(content, Mapping) else content
        if declared is not None and not isinstance(declared, Mapping):
            item_scope[COVERAGE_CONTENT_KEY] = declared
        return {"dataset": dataset, "scope": item_scope}

    if isinstance(samples, list):
        names = sorted({str(sample) for sample in samples if sample})
        if names:
            return [item([name], name) for name in names]
    return [item(samples)]


def _scope_identity(scope: Any) -> Any:
    """A scope as an overwrite replaces it: without what its data was declared to hold
    (``content``), so that a later record of the same dataset and sample replaces an
    earlier one's declaration rather than standing beside it."""
    if isinstance(scope, Mapping) and COVERAGE_CONTENT_KEY in scope:
        return {key: value for key, value in scope.items() if key != COVERAGE_CONTENT_KEY}
    return scope


def merged_imported_scopes(
    stored: Any, additions: Iterable[tuple[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """``families.metadata.imported_scopes`` with each (dataset, scope) of ``additions``
    added: by dataset, each scope once, one sample at a time, sorted.

    The key holds what the family's imports imported, and its single-file uploads brought,
    each dataset with its scope as ``dataset_scopes`` gives it: what a restore may remove
    that its own import did not carry. An import adds what it imported when it ends, under
    the family row's lock, with its flag or its clear, and what an earlier import that
    stopped had finished once that one's entry goes; an upload adds what it brought with
    its own commit (``record_family_upload``). An addition without a scope (an import run
    without its bookkeeping) adds nothing, nor does one of a dataset that carried nothing
    (``EMPTY_SCOPE_KEY``): the family holds nothing of it (SAFE-17). A stored value not in
    this shape is read as holding nothing. A coverage BED's item says what the BED declared
    it holds (``content``), and a later addition for the same sample replaces it (TRACE-9)."""
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            stored = None
    pairs: list[tuple[str, Any]] = []
    if isinstance(stored, Mapping):
        for dataset, scopes in stored.items():
            if isinstance(scopes, list):
                pairs.extend((str(dataset), scope) for scope in scopes)
    pairs.extend(
        (str(dataset), scope)
        for dataset, scope in additions
        if scope is not None and not _carries_nothing(scope)
    )
    merged: dict[str, dict[str, Any]] = {}
    for dataset, scope in pairs:
        for item in _scope_items(dataset, scope):
            merged.setdefault(dataset, {})[_canonical(_scope_identity(item["scope"]))] = item["scope"]
    return {
        dataset: [scopes[key] for key in sorted(scopes)]
        for dataset, scopes in sorted(merged.items())
    }


def whole_family_entry(
    mark: ImportMark,
    *,
    imported: Any,
    unfinished: Mapping[str, Any],
    family_samples: Collection[str] | None = None,
) -> dict[str, Any]:
    """This import's ``import_unfinished`` entry once its restore begins: the restore
    rewrites the family's rows in every variant and track table, whichever import or upload
    wrote them, so until the family has been imported again in full, any of its datasets may
    be partly written or missing. It lists, under ``whole_family``, every dataset the family
    holds -- what its imports imported and its single-file uploads brought (``imported``:
    ``imported_scopes``) and what the other imports that have not finished set out to
    import -- and every dataset of this import, each with its scope, one sample at a time;
    ``datasets`` names them all and none is finished. A sample that is no longer the
    family's (``family_samples``) is left out: no import of the family can name it.

    Of the datasets this import did not carry, only those ClickHouse holds are listed
    (``dataset_clickhouse_stores``): the restore rewrites ClickHouse table by table, but puts
    Postgres back in one transaction, which a failure rolls back whole, so it cannot leave a
    dataset held in Postgres alone (repeats, Paraphase, QC, phenotypes) partly written. Those
    of this import are all listed, as its flag names each as failed. They used to be listed
    all the same, so the whole family had to be imported again for them too (SAFE-18)."""
    pairs: list[tuple[str, Any]] = [(name, mark.scopes.get(name)) for name in mark.datasets]
    for dataset, held in merged_imported_scopes(imported, ()).items():
        pairs.extend((dataset, scope) for scope in held if dataset_clickhouse_stores(dataset))
    for key, other in unfinished.items():
        if key == mark.key or not isinstance(other, Mapping):
            continue
        listed = other.get(WHOLE_FAMILY_KEY)
        if isinstance(listed, list):
            pairs.extend(
                (str(item["dataset"]), item.get("scope"))
                for item in listed
                if isinstance(item, Mapping)
                and item.get("dataset")
                and dataset_clickhouse_stores(str(item["dataset"]))
            )
            continue
        scopes = other.get("scopes")
        pairs.extend(
            (name, scopes.get(name) if isinstance(scopes, Mapping) else None)
            for name in sorted(_dataset_names(other.get("datasets")) or ())
            if dataset_clickhouse_stores(name)
        )
    members = {str(sample) for sample in family_samples} if family_samples else None
    items: dict[str, dict[str, Any]] = {}
    for dataset, scope in pairs:
        for item in _scope_items(dataset, scope):
            samples = item["scope"].get("samples") if isinstance(item["scope"], Mapping) else None
            if members is not None and isinstance(samples, list) and samples and not {
                str(sample) for sample in samples
            } <= members:
                continue
            items[_canonical(item)] = item
    listed_items = [items[key] for key in sorted(items)]
    entry = mark.entry()
    entry["datasets"] = sorted({item["dataset"] for item in listed_items})
    entry[WHOLE_FAMILY_KEY] = listed_items
    return entry


def whole_family_left(entry: Mapping[str, Any], replaced: Mapping[str, Any]) -> dict[str, Any] | None:
    """A whole-family entry after an import that completed with ``overwrite``, or a
    single-file upload with ``overwrite``, replacing ``replaced`` (each dataset it imported
    or brought, with its scope): without what it covered (``_scope_covers``), or None when
    it covered all that was left. A list not in the shape ``whole_family_entry`` writes
    stays as it is: nothing can show it complete."""
    listed = entry.get(WHOLE_FAMILY_KEY)
    if not isinstance(listed, list):
        return dict(entry)
    left = [
        item
        for item in listed
        if not (
            isinstance(item, Mapping)
            and _scope_covers(replaced.get(str(item.get("dataset"))), item.get("scope"))
        )
    ]
    if not left:
        return None
    return {
        **entry,
        "datasets": sorted(
            {str(item["dataset"]) for item in left if isinstance(item, Mapping) and item.get("dataset")}
        ),
        "finished_datasets": [],
        WHOLE_FAMILY_KEY: left,
    }


def is_whole_family_entry(entry: Any) -> bool:
    """Whether an ``import_unfinished`` entry is the whole-family one a restore leaves."""
    return isinstance(entry, Mapping) and WHOLE_FAMILY_KEY in entry


async def _mark_family_import_unfinished(
    session: AsyncSession,
    *,
    family_uuid: str,
    mark: ImportMark,
    finished: Iterable[str] = (),
    entry: Mapping[str, Any] | None = None,
) -> None:
    """Write this import's entry into ``import_unfinished`` (created if the map or the
    entry is missing), and commit: ``mark``'s entry with ``finished``, or ``entry`` as
    given. Raises: the caller decides whether the import may go on without it."""
    await _write_unfinished_entry(
        session,
        family_uuid=family_uuid,
        key=mark.key,
        entry=dict(entry) if entry is not None else mark.entry(finished),
    )


async def _write_unfinished_entry(
    session: AsyncSession,
    *,
    family_uuid: str,
    key: str,
    entry: Mapping[str, Any],
) -> None:
    """Write ``entry`` under ``key`` into the family's ``import_unfinished`` (created if the
    map or the entry is missing), and commit. Raises."""
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
            "import_key": key,
            "entry": json.dumps(dict(entry)),
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


# The row an import's failure (or its restore) is written to, read under its lock: by the
# family's uuid, or by its identifier (an import that failed before it had the family's
# context).
_FAILURE_ROW_BY_UUID = """
SELECT id::text AS family_uuid,
       metadata -> 'import_incomplete' AS flag,
       metadata -> 'import_unfinished' AS unfinished,
       metadata -> 'imported_scopes' AS imported_scopes
FROM families
WHERE id = CAST(:family_uuid AS uuid)
FOR UPDATE
"""
_FAILURE_ROW_BY_FAMILY_ID = """
SELECT id::text AS family_uuid,
       metadata -> 'import_incomplete' AS flag,
       metadata -> 'import_unfinished' AS unfinished,
       metadata -> 'imported_scopes' AS imported_scopes
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
    keep: ImportMark | None = None,
    family_samples: Collection[str] | None = None,
) -> dict[str, Any] | None:
    """Write an import's failure into the family's ``import_incomplete`` flag, merged with
    the flag already there (``merged_import_failure``), add what it imported to
    ``imported_scopes``, and remove the import's ``import_unfinished`` entry -- or, with
    ``keep`` (its mark), write that entry as the whole-family one a restore that failed
    part-way leaves (``whole_family_entry``) -- in one transaction under the row's lock.
    With ``only_with_entry``, only where the import's entry is. Raises on a database error;
    returns the family's ``import_unfinished`` as written ({}: none), or None when there
    was nothing to write."""
    if family_uuid is not None:
        row_sql, params = _FAILURE_ROW_BY_UUID, {"family_uuid": family_uuid}
    else:
        row_sql, params = _FAILURE_ROW_BY_FAMILY_ID, {"family_id": family_id}
    row = (await session.execute(text(row_sql), params)).mappings().first()
    if row is None or (
        only_with_entry and (import_key is None or import_key not in _unfinished_entries(row["unfinished"]))
    ):
        await session.rollback()
        return None
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
    entries = _unfinished_entries(row["unfinished"])
    if keep is not None:
        entries[keep.key] = whole_family_entry(
            keep,
            imported=row.get("imported_scopes"),
            unfinished=entries,
            family_samples=family_samples,
        )
    elif import_key is not None:
        entries.pop(import_key, None)
    recorded = merged_imported_scopes(row.get("imported_scopes"), imported_scopes.items())
    await session.execute(
        text(
            f"""
            UPDATE families
            SET metadata = {_WITH_IMPORT_STATE}
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": row["family_uuid"],
            "flag": json.dumps(payload),
            "unfinished": json.dumps(entries or None),
            "imported_scopes": json.dumps(recorded or None),
        },
    )
    await session.commit()
    return entries


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
        written = await _write_import_failure(
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
        return written is not None
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
            actor=audit_actor(user),
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
        family_identifier=context.family_id,
        user=user,
    )
    await _apply_manifest_roi(session, bundle=bundle, context=context)
    return context, created


async def _delete_family_shell(
    session: AsyncSession,
    family_context: FamilyMetadataContext,
    user: CurrentUser | None = None,
    *,
    phenotype_import: PhenotypeImport,
) -> None:
    """Compensating cleanup for a failed import that created a fresh family.

    Removes the family's ClickHouse variant rows and the Postgres family shell
    (samples / members / other family-scoped rows cascade via ``ON DELETE
    CASCADE``) so a failed import leaves no orphan partial state. Mirrors the
    deletion recipe in ``ped_service``. The deletion is the family's last clinical audit
    event, as made by ``user``, the import's (SEC-7); the phenotypes the family goes with are
    recorded as removed just before it, as made by ``phenotype_import``, the failed import.
    """
    assembly_name = family_context.assembly_name
    family_uuid = family_context.family_uuid
    # The import that calls this holds the lock already; any other caller takes it here.
    await lock_family_variant_writes(session, family_uuid, VARIANT_TYPES)
    # Before anything of the family goes, in the transaction that deletes it: the
    # phenotypes it goes with, then its deletion, the last event of its chain.
    await record_phenotypes_deleted_with_family(
        session,
        family_uuid=family_uuid,
        family_identifier=family_context.family_id,
        origin=phenotype_import,
        change=PHENOTYPES_ROLLED_BACK,
    )
    await record_family_deleted(
        session,
        family_uuid=family_uuid,
        family_identifier=family_context.family_id,
        user=user,
        source=IMPORT_FAILED,
    )
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
    """Stamp a family as import-incomplete after an import that failed for some datasets
    and left the family in place.

    The family is left in place by an import that imported some datasets (a new family is
    deleted only when none imported), a failed update (it takes no snapshot: it skips what
    is there, so it replaced nothing), an overwrite whose snapshot could not be taken, and
    one whose restore was refused before its first delete (``FamilyRestoreRefused``). A
    restore that puts the family back leaves no flag, and one that fails part-way flags it
    through ``_flag_family_restore_failed``. Rather than let that state be silently
    queryable as if complete, record it in the family metadata so it is explicit and
    auditable.
    Best-effort and self-committing: a flag-write failure must not mask the original
    import failure.

    The flag names the import job (``job_id``, None for an import run outside a job):
    the job's record holds each dataset's error. The error texts are not copied here, as
    they can carry file paths and grow long. It records each failed dataset's scope (from
    ``scopes``, as ``dataset_scopes`` gives it) and, merged with a flag already there, keeps
    an earlier import's failures this import did not import again (``imported_scopes``),
    those of the same dataset under another scope too, each with the job that holds its
    error (``failed_scopes``; ``failed_jobs`` names each dataset's latest): the flag clears
    only once each failure has been imported again, for its scope (``failures_left_after``).

    The import has ended, so its ``import_unfinished`` entry (``import_key``) goes in the
    same transaction: the family is never without one or the other. If the write fails,
    the entry stays and still marks the family. What it imported is added to the family's
    ``imported_scopes`` in the same transaction.

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


def family_sample_ids(bundle: FamilyPackageBundle) -> list[str]:
    """The family's samples, as a package of an existing family must name them all."""
    return [str(sample) for sample in bundle.ped.sample_ids]


async def _mark_family_import_restoring(
    session: AsyncSession,
    family_context: FamilyMetadataContext,
    mark: ImportMark,
    *,
    family_samples: Collection[str] | None = None,
) -> None:
    """Before a failed overwrite's restore: this import's entry becomes the whole-family
    one (``whole_family_entry``), read and written under the family row's lock. The restore
    rewrites every table, whichever import wrote its rows, so a restore that fails part-way
    -- or a process that stops in it -- leaves the family marked until it has been
    imported again in full; one that puts the family back, or refuses to start, removes the
    entry. Best-effort: if it cannot be written, every dataset of this import is recorded
    as pending instead, and the flag of a restore that fails keeps the entry whole-family
    all the same (``_flag_family_restore_failed``)."""
    try:
        row = (
            await session.execute(
                text(_FAILURE_ROW_BY_UUID), {"family_uuid": family_context.family_uuid}
            )
        ).mappings().first()
        if row is None:
            raise LookupError("The family's row is gone")
        entry = whole_family_entry(
            mark,
            imported=row.get("imported_scopes"),
            unfinished=_unfinished_entries(row["unfinished"]),
            family_samples=family_samples,
        )
        await _mark_family_import_unfinished(
            session, family_uuid=family_context.family_uuid, mark=mark, entry=entry
        )
    except Exception:  # every dataset of the import pending instead: as before
        logger.warning(
            "Failed to mark the restore of family %s as rewriting all of its data",
            family_context.family_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass
        await _record_family_import_finished(session, family_context, mark, ())


async def _flag_family_restore_failed(
    session: AsyncSession,
    family_context: FamilyMetadataContext,
    *,
    failed_datasets: list[str],
    job_id: str | None,
    mark: ImportMark,
    scopes: Mapping[str, Any] | None = None,
    family_samples: Collection[str] | None = None,
) -> list[str] | None:
    """Flag a family whose failed overwrite's restore failed part-way (any way but
    refusing to start): ``failed_datasets`` (every dataset of the import) as failed and
    none as imported, and keep the import's ``import_unfinished`` entry as the
    whole-family one -- the restore may have removed the family's rows of any dataset,
    whichever import wrote them -- in one transaction under the row's lock. The gate then
    holds until each dataset the entry lists has been imported again with ``overwrite``.

    Returns the datasets the entry lists, or None when nothing could be written; the entry
    written before the restore (``_mark_family_import_restoring``) then stays. Best-effort,
    as ``_flag_family_import_incomplete``: a write that fails must not mask the import's
    failure."""
    try:
        entries = await _write_import_failure(
            session,
            family_uuid=family_context.family_uuid,
            failed_datasets=failed_datasets,
            imported_datasets=[],
            job_id=job_id,
            import_key=mark.key,
            scopes=scopes or {},
            imported_scopes={},
            keep=mark,
            family_samples=family_samples,
        )
    except Exception:  # the flag write must not mask the import failure
        logger.warning(
            "Failed to flag family %s as import-incomplete after its restore failed",
            family_context.family_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass
        return None
    entry = (entries or {}).get(mark.key)
    return sorted(_dataset_names(entry.get("datasets")) or ()) if isinstance(entry, Mapping) else None


# The family's metadata with each import-state key set to its new value: :flag for
# import_incomplete and :unfinished for import_unfinished (a JSON null removes the key), and
# :imported_scopes for imported_scopes (a JSON null leaves it as it is).
_WITH_IMPORT_STATE = """
CASE
    WHEN CAST(:unfinished AS jsonb) = 'null'::jsonb THEN {recorded} - 'import_unfinished'
    ELSE jsonb_set({recorded}, '{{import_unfinished}}', CAST(:unfinished AS jsonb), true)
END
""".format(
    recorded="""(
    CASE
        WHEN CAST(:imported_scopes AS jsonb) = 'null'::jsonb THEN {flagged}
        ELSE jsonb_set({flagged}, '{{imported_scopes}}', CAST(:imported_scopes AS jsonb), true)
    END
)""".format(
        flagged="""(
    CASE
        WHEN CAST(:flag AS jsonb) = 'null'::jsonb
            THEN COALESCE(metadata, '{}'::jsonb) - 'import_incomplete'
        ELSE jsonb_set(COALESCE(metadata, '{}'::jsonb), '{import_incomplete}', CAST(:flag AS jsonb), true)
    END
)"""
    )
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

    The flag shrinks by each failure this import imported again, in any mode, for the
    scope the failure records (``imported``: the scope of each dataset it imported, as
    ``dataset_scopes`` gives it), and goes with the last (``failures_left_after``); an
    import that does not import a failed dataset, or not for each scope it failed for,
    leaves it flagged.

    An earlier import's entry goes when this import completed what that one may have left
    partly written: every dataset it had not finished is one this import imported again,
    replacing all of what that one was writing (``rewritten``: as ``imported``, but only
    for an ``overwrite``; an ``update`` replaces nothing, so passes none), or it had
    finished them all. Any other entry stays. The whole-family entry of an import whose
    restore did not finish shrinks instead by what this import replaced, one dataset (and
    sample) at a time, and goes with the last (``whole_family_left``). Returns what is
    left, so the import can say why the family stays marked, or None when nothing could be
    cleared.

    What this import imported, and what an entry it removes had finished, is added to the
    family's ``imported_scopes``.

    Read and written under the row's lock, so no other mark written meanwhile is lost.
    Best-effort: a flag or an entry left in place keeps the family marked, the safe side.
    """
    replaced = dict(rewritten or {})
    try:
        row = (
            await session.execute(
                text(
                    "SELECT metadata -> 'import_incomplete' AS flag, "
                    "metadata -> 'import_unfinished' AS unfinished, "
                    "metadata -> 'imported_scopes' AS imported_scopes "
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
        recorded: list[tuple[str, Any]] = list((imported or {}).items())
        for key, entry in _unfinished_entries(row["unfinished"] if row is not None else None).items():
            if key == import_key:
                continue
            if is_whole_family_entry(entry):
                left = whole_family_left(entry, replaced)
                if left is not None:
                    remaining[key] = left
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
                # What it had finished stays the family's: this import replaced the rest.
                recorded.extend(
                    (name, scopes.get(name) if isinstance(scopes, Mapping) else None)
                    for name in sorted(_dataset_names(entry.get("finished_datasets")) or ())
                )
                continue
            remaining[key] = entry
        failures = failures_left_after(stored_flag, dict(imported or {}))
        holds = merged_imported_scopes(row.get("imported_scopes") if row is not None else None, recorded)
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
                "imported_scopes": json.dumps(holds or None),
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


# A single-file upload records what it brought in `imported_scopes`, as an import records
# what it imported: a restore that fails part-way rewrites the family's rows whichever
# import or upload wrote them, so its whole-family entry must name an upload's data too.
# Each upload records under the dataset, and with the scope, that a package import of the
# same data records: the package dataset whose loader replaces the same rows, where there
# is one, so that an overwrite of either brings again what the other brought; a name of its
# own where there is none.


def small_variant_upload_scope(callset: str) -> tuple[str, dict[str, Any]]:
    """What a family small-variant upload of ``callset`` brought (the source its rows are
    stored under, ``auto`` as the upload resolved it). Imputed genotypes (``glimpse2``),
    with the haplotype blocks made from them, are what a package ``haplotypes`` dataset
    brings; any other callset is what a package ``snv`` dataset stored as that callset
    brings (its ``source_format``, or ``auto`` as the import resolved it:
    ``dataset_scopes``). Each loads its family VCF through the same loader, which replaces
    the family's rows of that callset."""
    if callset == "glimpse2":
        return "haplotypes", {"source": None, "samples": None}
    return "snv", {"source": callset, "samples": None}


def structural_variant_upload_scope(record_format: str, sample_id: str) -> tuple[str, dict[str, Any]]:
    """What an upload of one sample's structural variants from one caller (``sniffles``,
    ``spectre``, ``manual``) brought. No package dataset stores that caller's calls, so it
    has a dataset of its own, ``sv_<caller>``."""
    return f"sv_{record_format}", {"source": None, "samples": [sample_id]}


def repeat_upload_scope(sample_id: str) -> tuple[str, dict[str, Any]]:
    """What a TRGT upload of one sample's calls brought: what a package ``repeats_trgt``
    dataset's file for that sample brings, which replaces the sample's TRGT calls as the
    upload does."""
    return "repeats_trgt", {"source": None, "samples": [sample_id]}


def interval_track_upload_scope(
    bed_type: str, sample_id: str, *, content: str | None = None
) -> tuple[str, dict[str, Any]]:
    """What a BED upload of one sample's ``bed_type`` track brought; it replaces the
    sample's track of that type that a BED brought (source ``web``), and no other caller's
    (SAFE-22). A package dataset that writes such a track may read another kind of file, or
    write another track (a coverage dataset's target table, an APCAD VCF), so neither can
    show that it replaced what the other wrote: the upload has a dataset of its own,
    ``bed_<type>``. A coverage BED's scope also says what it declared it holds
    (``content``, TRACE-9), which no overwrite compares."""
    scope: dict[str, Any] = {"source": None, "samples": [sample_id]}
    if content is not None:
        scope[COVERAGE_CONTENT_KEY] = content
    return f"bed_{bed_type}", scope


# A write of the family's data made outside a package import marks the family as an import
# does (SAFE-28). ClickHouse has no transactions, and such a write deletes rows before it
# writes them again: a process that stops in between -- a restart, a crash, running out of
# memory -- runs no clean-up, and a failure that cannot put back what it deleted leaves it
# missing. So the write leaves an entry in ``import_unfinished`` (``FamilyWriteMark``), under
# a key of its own whose prefix says what wrote it, listing each dataset it may leave partly
# written or missing, with its scope, one sample at a time, in the shape of the whole-family
# entry a restore that did not finish leaves (``WHOLE_FAMILY_KEY``): sign-out is gated as for
# an import that stopped, and each item goes once an import or an upload with overwrite has
# brought it again (``whole_family_left``), the entry with the last. A later restore lists
# its items among the family's (``whole_family_entry``).
#
# A single-file upload that writes ClickHouse -- the small-variant, structural-variant and
# interval-track uploads; the repeat-expansion upload writes Postgres alone, in the
# transaction that deletes what it replaces -- writes its mark before its first ClickHouse
# write, committed on a connection of its own (``mark_family_upload``). The upload's commit
# removes it (``record_family_upload``), and so does a failure that left nothing missing
# (``end_family_write_mark``); an upload that flags its failure turns it into the flag
# (``record_family_upload_failure``).
#
# So does every deletion of the family's data that deletes or rewrites ClickHouse rows
# (SAFE-31; ``FamilyDeletion``): an admin's deletion of a sample with its data, of a sample's
# structural variants or of the family, a PED upload or a manual family that replaces the
# family, and a structure change that clears its data. Its mark lists what it may leave
# partly written or missing if it stops anywhere after its first ClickHouse write; it goes
# with the deletion's commit (with the family, for one that deletes the family), and a
# deletion that fails leaves it listing exactly what it may have left so, given how far it
# got (``FamilyDeletion.failed``), or removes it when that is nothing.
UPLOAD_MARK_PREFIX = "upload-"
DELETION_MARK_PREFIX = "deletion-"


def unfinished_entry_writer(key: Any) -> str:
    """What wrote an ``import_unfinished`` entry, read from its key: ``upload`` (a
    single-file upload, ``UPLOAD_MARK_PREFIX``), ``deletion`` (a deletion of the family's
    data, ``DELETION_MARK_PREFIX``: an admin's deletion of a sample, of its structural
    variants or of the family, the replacement of the family by a PED upload or a manual
    family, or a structure change that clears its data), or else ``import`` (a package
    import, keyed by its job or by a run of its own)."""
    if isinstance(key, str):
        if key.startswith(UPLOAD_MARK_PREFIX):
            return "upload"
        if key.startswith(DELETION_MARK_PREFIX):
            return "deletion"
    return "import"


# An item of a deletion's mark that stands for one sample's part of a dataset that holds every
# sample's data in one file (its scope names no sample: a family VCF), as against the whole
# dataset: the deletion of that sample removed its part, and nothing of the others'. An
# upload or an import that brings the dataset again with overwrite strikes it as any item
# (``_scope_covers`` reads the scope alone), and so does the deletion of that sample once it
# commits (``FamilyDeletion.finished``): nothing of a sample that is gone can be missing.
ITEM_SAMPLE_KEY = "sample"


def _listed_items(items: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Each item once, in a stable order."""
    listed: dict[str, dict[str, Any]] = {}
    for item in items:
        listed[_canonical(item)] = dict(item)
    return tuple(listed[key] for key in sorted(listed))


@dataclass(frozen=True)
class FamilyWriteMark:
    """The ``import_unfinished`` entry of a write of the family's data made outside a
    package import: its key (``UPLOAD_MARK_PREFIX`` or ``DELETION_MARK_PREFIX``, and an id
    of its own), when it was written, and what the write may leave partly written or
    missing (``items``), each dataset with its scope, one sample at a time."""

    key: str
    at: str
    items: tuple[Mapping[str, Any], ...]

    @classmethod
    def begin(cls, prefix: str, items: Iterable[tuple[str, Any]]) -> "FamilyWriteMark":
        return cls.listing(
            prefix, (item for dataset, scope in items for item in _scope_items(str(dataset), scope))
        )

    @classmethod
    def listing(cls, prefix: str, items: Iterable[Mapping[str, Any]]) -> "FamilyWriteMark":
        """A mark listing ``items`` as given (``{"dataset": …, "scope": …}``)."""
        return cls(
            key=f"{prefix}{uuid4().hex}",
            at=datetime.now(timezone.utc).isoformat(),
            items=_listed_items(items),
        )

    def entry(self) -> dict[str, Any]:
        return {
            "job_id": None,
            "at": self.at,
            "datasets": sorted({str(item["dataset"]) for item in self.items}),
            "finished_datasets": [],
            WHOLE_FAMILY_KEY: [dict(item) for item in self.items],
        }


async def mark_family_upload(
    *, family_uuid: str, items: Iterable[tuple[str, Any]]
) -> FamilyWriteMark:
    """Mark the family before a single-file upload's first ClickHouse write, with what the
    upload may leave partly written or missing if it stops or fails after that write
    (``items``: each dataset with its scope, as ``small_variant_upload_scope`` and the
    functions after it give them).

    Written and committed on a connection of its own, so that it stands whatever becomes of
    the upload's transaction, which holds the family's variant-write locks until it commits.
    Call it before that transaction locks the family row (``record_family_upload``): this
    write would wait for that lock. Raises: an upload that cannot mark the family writes
    nothing."""
    mark = FamilyWriteMark.begin(UPLOAD_MARK_PREFIX, items)
    await _write_mark(family_uuid, mark)
    return mark


async def _write_mark(family_uuid: str, mark: FamilyWriteMark) -> None:
    """Write ``mark`` into the family's ``import_unfinished``, committed on a connection of
    its own. Raises."""
    async with get_postgres_sessionmaker()() as session:
        await _write_unfinished_entry(
            session, family_uuid=family_uuid, key=mark.key, entry=mark.entry()
        )


async def end_family_write_mark(
    session: AsyncSession, *, family_uuid: str, family_id: str, mark: FamilyWriteMark
) -> None:
    """Remove the mark of a write that failed leaving nothing missing: what it deleted was
    put back, or it deleted nothing and what it wrote was removed again, or it failed before
    its first ClickHouse write. The caller's transaction, which failed, is rolled back first.
    Best-effort: a mark left in place keeps the family marked, the safe side."""
    try:
        await session.rollback()
        await session.execute(
            text(
                f"""
                UPDATE families
                SET metadata = {_WITHOUT_IMPORT_ENTRY}
                WHERE id = CAST(:family_uuid AS uuid)
                """
            ),
            {"family_uuid": family_uuid, "import_key": mark.key},
        )
        await session.commit()
    except Exception:  # the mark stays: the family stays marked
        logger.warning(
            "Failed to remove the mark of a failed write of family %s", family_id, exc_info=True
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass


# Where ClickHouse holds a family's data, store by store: its small variants, its structural
# variants, and its interval tracks (each sample's).
SMALL_VARIANT_DATA = "small_variants"
STRUCTURAL_VARIANT_DATA = "structural_variants"
INTERVAL_TRACK_DATA = "interval_tracks"
_CLICKHOUSE_STORES = frozenset({SMALL_VARIANT_DATA, STRUCTURAL_VARIANT_DATA, INTERVAL_TRACK_DATA})

# Which of those stores hold each dataset's data, as ``imported_scopes`` names the dataset: a
# package's dataset (an upload of small variants or imputed genotypes records ``snv`` or
# ``haplotypes``), and an upload's own (``sv_<caller>``, ``bed_<type>``:
# ``dataset_clickhouse_stores``). The repeat calls, the paraphase results, the QC, the
# alignments' registration, the pipeline's run record and the phenotypes are Postgres's alone.
_DATASET_CLICKHOUSE_STORES: dict[str, frozenset[str]] = {
    "snv": frozenset({SMALL_VARIANT_DATA}),
    # The imputed genotypes, and the haplotype blocks made from them.
    "haplotypes": frozenset({SMALL_VARIANT_DATA, INTERVAL_TRACK_DATA}),
    # The chrM calls, and the chrM structural variants.
    "mito": frozenset({SMALL_VARIANT_DATA, STRUCTURAL_VARIANT_DATA}),
    "sv_needlr": frozenset({STRUCTURAL_VARIANT_DATA}),
    # HiFiCNV's calls, and its copy-number, depth and allele-fraction tracks.
    "cnv": frozenset({STRUCTURAL_VARIANT_DATA, INTERVAL_TRACK_DATA}),
    "wisecondorx": frozenset({INTERVAL_TRACK_DATA}),
    "qdnaseq": frozenset({INTERVAL_TRACK_DATA}),
    "apcad": frozenset({INTERVAL_TRACK_DATA}),
    "pcf": frozenset({INTERVAL_TRACK_DATA}),
    "coverage": frozenset({INTERVAL_TRACK_DATA}),
    "repeats_trgt": frozenset(),
    "paraphase": frozenset(),
    "qc": frozenset(),
    "alignments": frozenset(),
    "pipeline_info": frozenset(),
    "phenotypes": frozenset(),
}


def dataset_clickhouse_stores(dataset: str) -> frozenset[str]:
    """The ClickHouse stores that hold ``dataset``'s data (``_DATASET_CLICKHOUSE_STORES``). A
    dataset of no known kind counts as held in each: a deletion marks it rather than miss
    it."""
    known = _DATASET_CLICKHOUSE_STORES.get(dataset)
    if known is not None:
        return known
    if dataset.startswith("sv_"):
        return frozenset({STRUCTURAL_VARIANT_DATA})
    if dataset.startswith("bed_"):
        return frozenset({INTERVAL_TRACK_DATA})
    return _CLICKHOUSE_STORES


@dataclass(frozen=True)
class DataReach:
    """What a deletion's write reaches of the family's data in one ClickHouse store
    (``store``): every sample's data of each dataset the store holds; with ``sample``, that
    sample's alone -- its own items, and its part of a dataset whose scope names no sample
    (``ITEM_SAMPLE_KEY``); with ``but``, every sample's but that one's own items, a
    deletion of whose data was to remove them."""

    store: str
    sample: str | None = None
    but: str | None = None


def _item_samples(item: Mapping[str, Any]) -> set[str] | None:
    """The samples an item's scope names, or None for a dataset of one family-level file (or
    a scope not in the shape ``dataset_scopes`` gives, which holds any sample's data)."""
    scope = item.get("scope")
    samples = scope.get("samples") if isinstance(scope, Mapping) else None
    if isinstance(samples, list):
        named = {str(sample) for sample in samples if sample}
        if named:
            return named
    return None


def reached_items(
    items: Iterable[Mapping[str, Any]], reaches: Iterable[DataReach]
) -> tuple[dict[str, Any], ...]:
    """Of ``items`` (as a whole-family entry lists them), what ``reaches`` reach: each item of
    a dataset held in a reached store, and of a reached sample. An item reached for one
    sample's part alone (a dataset whose scope names no sample, reached for that sample)
    names it (``ITEM_SAMPLE_KEY``); one that already names a sample's part is reached for
    that part or not at all. Never more than ``items``: what a mark lists only shrinks."""
    reaches = tuple(reaches)
    reached: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping) or not item.get("dataset"):
            continue
        dataset = str(item["dataset"])
        stores = dataset_clickhouse_stores(dataset)
        samples = _item_samples(item)
        part = item.get(ITEM_SAMPLE_KEY)
        whole = False
        parts: set[str] = set()
        for reach in reaches:
            if reach.store not in stores:
                continue
            if reach.sample is not None:
                if samples is None:
                    parts.add(reach.sample)
                elif reach.sample in samples:
                    whole = True
            elif not (reach.but is not None and samples is not None and samples <= {reach.but}):
                whole = True
        base = {"dataset": dataset, "scope": item.get("scope")}
        if part is not None:
            if whole or str(part) in parts:
                reached.append({**base, ITEM_SAMPLE_KEY: str(part)})
        elif whole:
            reached.append(base)
        else:
            reached.extend({**base, ITEM_SAMPLE_KEY: sample} for sample in sorted(parts))
    return _listed_items(reached)


def _without_sample(entry: Any, sample: str) -> Any:
    """A whole-family entry without the items of ``sample``, a sample that is deleted: its
    own items and its part of a family-level dataset (``ITEM_SAMPLE_KEY``). None when nothing
    is left of a list that had items; the entry itself when none is struck, or when it is
    not a whole-family entry (an import's own entry lists datasets, not samples)."""
    listed = entry.get(WHOLE_FAMILY_KEY) if isinstance(entry, Mapping) else None
    if not isinstance(listed, list):
        return entry
    left = [
        item
        for item in listed
        if not (
            isinstance(item, Mapping)
            and (item.get(ITEM_SAMPLE_KEY) == sample or _item_samples(item) == {sample})
        )
    ]
    if len(left) == len(listed):
        return entry
    if not left:
        return None
    return {
        **entry,
        "datasets": sorted(
            {str(item["dataset"]) for item in left if isinstance(item, Mapping) and item.get("dataset")}
        ),
        "finished_datasets": [],
        WHOLE_FAMILY_KEY: left,
    }


_UNFINISHED_ROW = """
SELECT metadata -> 'import_unfinished' AS unfinished
FROM families
WHERE id = CAST(:family_uuid AS uuid)
FOR NO KEY UPDATE
"""
# The family's metadata with import_unfinished set to :unfinished (a JSON null removes it).
_WITH_UNFINISHED = """
CASE
    WHEN CAST(:unfinished AS jsonb) = 'null'::jsonb
        THEN COALESCE(metadata, '{}'::jsonb) - 'import_unfinished'
    ELSE jsonb_set(COALESCE(metadata, '{}'::jsonb), '{import_unfinished}', CAST(:unfinished AS jsonb), true)
END
"""


@dataclass
class FamilyDeletion:
    """A deletion of a family's data that deletes or rewrites its rows in ClickHouse
    (SAFE-31), from its mark to its end.

    ``begin`` writes the deletion's mark (``FamilyWriteMark``, ``DELETION_MARK_PREFIX``)
    before its first ClickHouse write, committed on a connection of its own, listing each
    dataset the family holds (``imported_scopes``) that the deletion may leave partly
    written or missing (``DataReach``): a process that stops anywhere after that write
    leaves the family marked for all of it. As it writes, the deletion records what each
    write reached (``reach``, ``rewrite``). It removes the mark in its own transaction, which
    commits it with everything the deletion changed (``finished``; a deletion of the family
    removes the family, and the mark with it). One that fails leaves the mark listing exactly
    what it may have left partly written or missing, given how far it got, or removes it
    when that is nothing (``failed``)."""

    family_uuid: str
    family_id: str
    mark: FamilyWriteMark
    _reached: list[DataReach] = field(default_factory=list)

    @classmethod
    async def begin(
        cls,
        session: AsyncSession,
        *,
        family_uuid: str,
        family_id: str,
        reaches: Iterable[DataReach],
    ) -> "FamilyDeletion":
        """Mark the family for what the deletion may reach (``reaches``). The family's
        ``imported_scopes`` is read in the caller's transaction, which must hold the
        family's variant-write locks and must not yet have locked the family row: the mark
        is written on a connection of its own, which would wait for that lock. An item of a
        sample that is no longer the family's is left out, as a restore's entry leaves it
        out (``whole_family_entry``): nothing can bring it again. Raises: a deletion that
        cannot mark the family deletes nothing."""
        row = (
            await session.execute(
                text(
                    "SELECT f.metadata -> 'imported_scopes' AS imported_scopes, "
                    "ARRAY(SELECT s.sample_id FROM samples s WHERE s.family_id = f.id) AS sample_ids "
                    "FROM families f WHERE f.id = CAST(:family_uuid AS uuid)"
                ),
                {"family_uuid": family_uuid},
            )
        ).mappings().first()
        members = row.get("sample_ids") if row is not None else None
        family_samples = {str(sample) for sample in members} if isinstance(members, (list, tuple)) else None
        held = [
            {"dataset": dataset, "scope": scope}
            for dataset, scopes in merged_imported_scopes(
                row.get("imported_scopes") if row is not None else None, ()
            ).items()
            for scope in scopes
        ]
        if family_samples is not None:
            held = [
                item
                for item in held
                if (samples := _item_samples(item)) is None or samples & family_samples
            ]
        mark = FamilyWriteMark.listing(DELETION_MARK_PREFIX, reached_items(held, reaches))
        await _write_mark(family_uuid, mark)
        return cls(family_uuid=family_uuid, family_id=family_id, mark=mark)

    def reach(self, reach: DataReach) -> None:
        """A write that may leave ``reach`` partly written or missing begins (a delete): it
        counts from now on, whatever becomes of it."""
        self._reached.append(reach)

    @asynccontextmanager
    async def rewrite(self, *, failed: DataReach, done: DataReach | None = None) -> AsyncIterator[None]:
        """A rewrite that deletes rows and writes them back changed, putting them back when
        it fails (``rewrite_family_structural_variants``; the admin's rewrite of a sample's
        small variants). While it runs, and once it could not put them back, it may leave
        ``failed`` partly written or missing; once it completes, ``done`` (what it
        removed); once it put them back, nothing."""
        self._reached.append(failed)
        try:
            yield
        except (StructuralVariantsNotRestored, SmallVariantsNotRestored):
            raise
        except Exception:
            self._reached.remove(failed)
            raise
        self._reached.remove(failed)
        if done is not None:
            self._reached.append(done)

    async def finished(self, session: AsyncSession, *, deleted_sample: str | None = None) -> None:
        """Remove the mark, in the deletion's own transaction (no commit), which commits it
        with everything the deletion changed. With ``deleted_sample`` (a sample the deletion
        removed), its items go from every whole-family entry too (``_without_sample``):
        nothing of a sample that is gone can be missing. Raises: a deletion whose mark
        cannot be removed fails."""
        row = (
            await session.execute(text(_UNFINISHED_ROW), {"family_uuid": self.family_uuid})
        ).mappings().first()
        if row is None:  # the family is gone, and the mark with it
            return
        entries = _unfinished_entries(row["unfinished"])
        changed = entries.pop(self.mark.key, None) is not None
        if deleted_sample is not None:
            for key, entry in list(entries.items()):
                left = _without_sample(entry, deleted_sample)
                if left is entry:
                    continue
                changed = True
                if left is None:
                    del entries[key]
                else:
                    entries[key] = left
        if not changed:
            return
        await session.execute(
            text(f"UPDATE families SET metadata = {_WITH_UNFINISHED} WHERE id = CAST(:family_uuid AS uuid)"),
            {"family_uuid": self.family_uuid, "unfinished": json.dumps(entries or None)},
        )

    async def failed(self, session: AsyncSession) -> None:
        """After the deletion failed: its transaction is rolled back, and its mark lists
        exactly what its writes may have left partly written or missing (``reached_items``
        of what they reached), or goes when that is nothing (it failed before its first
        ClickHouse write, or each rewrite put back what it deleted). The mark is read and
        written under the family row's lock, and only ever shrinks, so it never lists again
        what a write since struck off. Best-effort: a mark left as written lists more, the
        safe side; the deletion's own error is what the caller raises."""
        try:
            await session.rollback()
            row = (
                await session.execute(text(_UNFINISHED_ROW), {"family_uuid": self.family_uuid})
            ).mappings().first()
            entries = _unfinished_entries(row["unfinished"] if row is not None else None)
            entry = entries.get(self.mark.key)
            if not isinstance(entry, Mapping):  # the family is gone, or the mark is
                await session.rollback()
                return
            listed = entry.get(WHOLE_FAMILY_KEY)
            items = reached_items(listed if isinstance(listed, list) else (), self._reached)
            if items:
                entries[self.mark.key] = replace(self.mark, at=str(entry.get("at") or self.mark.at), items=items).entry()
            else:
                del entries[self.mark.key]
            await session.execute(
                text(f"UPDATE families SET metadata = {_WITH_UNFINISHED} WHERE id = CAST(:family_uuid AS uuid)"),
                {"family_uuid": self.family_uuid, "unfinished": json.dumps(entries or None)},
            )
            await session.commit()
        except Exception:  # the mark stays as written: the family stays marked
            logger.warning(
                "Failed to narrow the mark of a failed deletion of family %s's data",
                self.family_id,
                exc_info=True,
            )
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001 - best-effort; nothing else to do
                pass


async def end_failed_deletions(session: AsyncSession, deletions: Iterable[FamilyDeletion]) -> None:
    """``FamilyDeletion.failed`` for each deletion a failed request began."""
    for deletion in deletions:
        await deletion.failed(session)


# The family row an upload's bookkeeping reads, locked as an UPDATE of its metadata locks
# it: every other writer of the family's metadata waits for it, and it for them (a package
# import's bookkeeping reads the row FOR UPDATE). Unlike FOR UPDATE, it does not wait for
# the key-share locks that inserts of rows referencing the family take (a sample, a repeat
# call, the annotation manifest) in transactions that have not committed.
_UPLOAD_ROW = """
SELECT metadata -> 'import_unfinished' AS unfinished,
       metadata -> 'imported_scopes' AS imported_scopes,
       metadata -> 'import_incomplete' AS flag
FROM families
WHERE id = CAST(:family_uuid AS uuid)
FOR NO KEY UPDATE
"""
# The family's metadata with imported_scopes set to :imported_scopes, and, in the second,
# import_unfinished to :unfinished (a JSON null removes it).
_WITH_IMPORTED_SCOPES = (
    "jsonb_set(COALESCE(metadata, '{}'::jsonb), '{imported_scopes}', "
    "CAST(:imported_scopes AS jsonb), true)"
)
_WITH_IMPORTED_SCOPES_AND_UNFINISHED = f"""
CASE
    WHEN CAST(:unfinished AS jsonb) = 'null'::jsonb
        THEN {_WITH_IMPORTED_SCOPES} - 'import_unfinished'
    ELSE jsonb_set({_WITH_IMPORTED_SCOPES}, '{{import_unfinished}}', CAST(:unfinished AS jsonb), true)
END
"""


def _with_import_flag(metadata: str) -> str:
    """``metadata`` (an expression of the family's metadata) with import_incomplete set to
    :flag, or removed where :flag is a JSON null."""
    return f"""
CASE
    WHEN CAST(:flag AS jsonb) = 'null'::jsonb THEN ({metadata}) - 'import_incomplete'
    ELSE jsonb_set(({metadata}), '{{import_incomplete}}', CAST(:flag AS jsonb), true)
END
"""


async def record_family_upload(
    session: AsyncSession,
    *,
    family_uuid: str,
    dataset: str,
    scope: Mapping[str, Any],
    overwrite: bool,
    mark: FamilyWriteMark | None = None,
) -> None:
    """A single-file upload's bookkeeping, as an import's: add what it brought
    (``dataset``, with its ``scope``, as ``small_variant_upload_scope`` and the functions
    after it give them) to the family's ``imported_scopes``, so that the whole-family entry
    of a restore that fails part-way later names it; and, with ``overwrite``, remove it
    from each whole-family entry already there (``whole_family_left``), which goes once
    nothing is left on it -- the marks other uploads that stopped or failed left included
    (``FamilyWriteMark``). An upload without ``overwrite`` removes nothing, as an import's
    ``update`` removes nothing. The upload's own mark (``mark``, written before its first
    ClickHouse write: ``mark_family_upload``) goes in either mode, with its commit.

    In either mode it also removes from the ``import_incomplete`` flag each failure it
    brought again, for the scope the failure records (``failures_left_after``), as an
    import that imports it again does, and the flag goes with the last: an upload that
    completes has brought its scope whole, since one without ``overwrite`` is refused while
    any of it is stored. A failed upload that had deleted what it replaces leaves such a
    failure (``record_family_upload_failure``).

    Read and written under the family row's lock, in the upload's own transaction, which
    the caller commits with what the upload wrote: the record stands or falls with the
    upload, and an upload whose record cannot be written fails. The caller holds the
    family's variant-write locks from before its first read until that commit, so the
    upload never falls between a package import's snapshot and its restore: no restore
    removes what it struck off, or misses what it recorded. Called before the upload takes
    the annotation manifest's lock, as the imputed small-variant upload takes the row (for
    its phase corrections) before it: two uploads of the family that run at once (a
    small-variant and a structural-variant upload) take the two in one order."""
    row = (
        await session.execute(text(_UPLOAD_ROW), {"family_uuid": family_uuid})
    ).mappings().first()
    if row is None:  # the family is gone, and with it what the upload brought
        return
    params: dict[str, Any] = {
        "family_uuid": family_uuid,
        "imported_scopes": json.dumps(
            merged_imported_scopes(row["imported_scopes"], [(dataset, scope)])
        ),
    }
    entries = _unfinished_entries(row["unfinished"])
    struck = False
    if mark is not None and mark.key in entries:
        # The upload is about to commit what it wrote: nothing of it is left to mark.
        del entries[mark.key]
        struck = True
    if overwrite:
        for key, entry in list(entries.items()):
            if not is_whole_family_entry(entry):
                continue
            left = whole_family_left(entry, {dataset: scope})
            if left is None:
                del entries[key]
            elif left != entry:
                entries[key] = left
            else:
                continue
            struck = True
    metadata = _WITH_IMPORTED_SCOPES
    if struck:
        metadata = _WITH_IMPORTED_SCOPES_AND_UNFINISHED
        params["unfinished"] = json.dumps(entries or None)
    stored_flag = row.get("flag")
    if isinstance(stored_flag, str):
        try:
            stored_flag = json.loads(stored_flag)
        except ValueError:
            pass
    if _flag_failures(stored_flag):
        # Rewritten once it strikes a failure off: a dataset that failed for several
        # scopes stays, for the others (SAFE-26).
        failures = failures_left_after(stored_flag, {dataset: scope})
        if failures.changed:
            metadata = _with_import_flag(metadata)
            params["flag"] = json.dumps(failures.flag)
    await session.execute(
        text(f"UPDATE families SET metadata = {metadata} WHERE id = CAST(:family_uuid AS uuid)"),
        params,
    )


async def record_family_upload_failure(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_id: str,
    dataset: str,
    scope: Mapping[str, Any],
    mark: FamilyWriteMark | None = None,
) -> None:
    """A single-file upload that failed after it had begun deleting the rows it replaces
    (SAFE-25): ClickHouse has no transactions, so what it was to bring (``dataset``, with
    ``scope``, as ``small_variant_upload_scope`` gives them) is missing or partly written.
    The family is flagged import-incomplete for it, as an import that failed for a dataset
    flags it (``_write_import_failure``): merged with a flag already there, under no import
    job, as an upload runs in none. The flag clears once the dataset has been brought
    again, by an upload (``record_family_upload``) or an import. The upload's mark
    (``mark``: ``mark_family_upload``) goes in the same transaction, as an import's entry
    goes with its flag: the family is never without one or the other.

    The upload's own transaction is rolled back first: it failed, and nothing of it is to
    commit. The flag is written, and committed, in a transaction of its own under the
    family row's lock. Best-effort: a flag that cannot be written is logged, the mark
    stays and still marks the family, and the caller raises the upload's own error."""
    try:
        await session.rollback()
        await _write_import_failure(
            session,
            family_uuid=family_uuid,
            failed_datasets=[dataset],
            imported_datasets=(),
            job_id=None,
            import_key=mark.key if mark is not None else None,
            scopes={dataset: dict(scope)},
            imported_scopes={},
        )
    except Exception:  # the upload's own error is what the caller raises
        logger.warning(
            "Failed to flag family %s as import-incomplete after a failed upload",
            family_id,
            exc_info=True,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - best-effort; nothing else to do
            pass


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
