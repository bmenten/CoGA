from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from functools import partial
import json
from math import log2
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.object_storage import join_remote_uri, remote_object_exists
from ..schemas import (
    FamilyImportDatasetSummary,
)
from .bed_service import BED_UPLOAD_SOURCE, upload_bed_data
from .clickhouse_column_bounds import UINT32, column_int
from .coverage_content import COVERAGE_CONTENT_KEY, declared_coverage_content
from .clickhouse_interval_tracks import (
    count_interval_track_rows,
    delete_interval_track_sources,
    delete_interval_tracks,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .clickhouse_variant_storage import (
    StructuralVariantsNotRestored,
    count_family_small_variants,
    count_family_structural_variants,
    delete_family_small_variants,
    fetch_family_structural_variant_rows,
    replace_family_structural_variants,
    rewrite_family_structural_variants,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
)
from .family_variant_write_lock import STRUCTURAL_VARIANTS, lock_family_variant_writes
from .hpo_service import (
    PhenotypeImport,
    import_family_hpo_annotations,
)
from .repeat_expansion_pg import (
    decode_repeat_upload_text,
    ingest_family_trgt_text,
    ingest_trgt_text,
    read_trgt_sample_calls,
)
from .upload_safety import read_path_text_bounded
from .annotation_table_parser import parse_mutserve_annotation_path
from .variant_upload_service import (
    NIPT_SMALL_VARIANT_SOURCE,
    SmallVariantWriteState,
    _detect_small_variant_format_from_upload,
    upload_family_small_variant_file,
)

from .family_package_bigwig import autosomal_median, open_bigwig
from .family_package_common import APCAD_PCF_SOURCE, APCAD_PCF_TRACK_TYPE, APCAD_SOURCE, CNV_SOURCE, HAPLOTYPE_ORIGIN_ROLES, QC_FAMILY_ROLES, DatasetLeftMissing, DatasetProgressCallback, FamilyPackageBundle, ManifestDataset, MITO_SOURCE, MITO_SV_SOURCE, _display_path, _read_package_text, _resolve_package_path, _run_with_periodic_progress, known_vcf_sample_ids, per_sample_vcf_column, read_vcf_sample_columns, resolve_vcf_sample_id, vcf_holds_a_data_line, vcf_sample_alias_map
from .family_package_manifest import _ped_embryo_sample_ids
from .family_package_nipt import (
    nipt_import_order,
    nipt_package_cfdna_sample,
    paternal_record_filter,
    scan_vcf_positions,
)
from .nipt_target_coverage import (
    TARGET_COVERAGE_SOURCE,
    TARGET_COVERAGE_TRACK_TYPE,
    TargetCoverageFormatError,
    parse_target_coverage_table,
    target_row_metadata,
)
from .family_package_qc import (
    extract_pipeline_versions,
    parse_ado_adi_text,
    parse_haplotype_origin_text,
    parse_king_kin0_text,
    parse_mosdepth_summary_text,
    parse_nanostats_text,
    parse_ngsbits_sample_gender_text,
    parse_pipeline_params,
    parse_qualimap_genome_results_text,
    parse_sample_value_csv,
    record_family_haplotype_origin as _record_family_haplotype_origin,
    record_family_pipeline_metadata as _record_family_pipeline_metadata,
    record_family_pipeline_qc as _record_family_pipeline_qc,
    record_sample_alignment_metadata as _record_sample_alignment_metadata,
    record_sample_mtdna_metadata,
    record_sample_signal_tracks as _record_sample_signal_tracks,
    record_sample_qc_metadata as _record_sample_qc_metadata,
)
from .family_package_registration import UNRESOLVED_SNV_SOURCE, _interval_track_count, _paraphase_count, _register_only, _repeat_expansion_count
from .family_package_tracks import _delete_sample_interval_source, _import_apcad_track_file, _import_bigwig_interval_track, _import_copy_number_track, _import_pcf_segment_file, _import_wisecondorx_track
from .family_package_validation import _manifest_hpo_rows, _manifest_individuals, _pcf_role_path
from .structural_variant_ingest import SkippedRecords
from .family_package_variants import ParaphaseValueError, _iter_cnv_structural_records, _iter_mito_sv_records, _iter_needlr_per_sample_records, _iter_needlr_structural_records, _merge_sv_records_by_id, _mito_sv_rows_after_import, _paraphase_rows_for_sample, _replace_sample_paraphase_rows, _update_sv_file_metadata, needlr_query_ids
from .import_progress import FilesReadInTurn
from .nipt import MONOGENIC_NIPT_ANALYSIS_TYPE
from .per_sample_small_variants import (
    PER_SAMPLE_CALLSET_SOURCE,
    PerSampleVcf,
    upload_family_per_sample_small_variant_files,
)


# What a per-sample file refused by ``per_sample_vcf_column`` asks of the operator.
_PER_SAMPLE_COLUMN_REMEDY = (
    "Point the manifest entry at this sample's own file, or set vcf_sample on the entry "
    "to confirm the column."
)


@dataclass(frozen=True, slots=True)
class DatasetImportJob:
    """What a dataset importer is handed: the package, the dataset's manifest entry, the
    summary to fill in, the family and sample contexts, how to treat data already there,
    and where to report progress. Each importer reads the fields it needs.

    ``skipped_samples`` is where an importer that goes sample by sample records each sample
    an update left as it was, its rows being there (all of them, or one of its tracks): an
    import that imported the dataset for other samples counts it as imported again for
    those only (``imported_dataset_scopes``), so a skip completes no earlier failure
    (SAFE-20). A dataset an update skips whole ends ``skipped``, and counts for none.

    ``scope`` is what the import's bookkeeping records the dataset as replacing, from
    before its first write (``dataset_scopes``), or None for an import without it. The SNV
    importer stores its family VCF as the callset the scope names, and nothing else
    (SAFE-23)."""

    session: AsyncSession
    bundle: FamilyPackageBundle
    dataset: ManifestDataset
    summary: FamilyImportDatasetSummary
    family_context: FamilyMetadataContext
    sample_contexts: dict[str, SampleMetadataContext]
    conflict_mode: str = "overwrite"
    progress: DatasetProgressCallback | None = None
    skipped_samples: set[str] = field(default_factory=set)
    scope: Mapping[str, Any] | None = None


DatasetImporter = Callable[[DatasetImportJob], Awaitable[FamilyImportDatasetSummary]]

# One importer per manifest dataset type, registered where it is defined (#528). A type
# in SUPPORTED_DATASETS with no importer here is a bug; the registry test and
# _import_dataset both say so.
DATASET_IMPORTERS: dict[str, DatasetImporter] = {}


def _dataset_importer(dataset_type: str) -> Callable[[DatasetImporter], DatasetImporter]:
    def register(importer: DatasetImporter) -> DatasetImporter:
        if dataset_type in DATASET_IMPORTERS:
            raise RuntimeError(f"Two importers are registered for dataset type '{dataset_type}'")
        DATASET_IMPORTERS[dataset_type] = importer
        return importer

    return register


@asynccontextmanager
async def _local_upload(path: Path):
    handle = path.open("rb")
    upload = UploadFile(file=handle, filename=path.name)
    try:
        yield upload
    finally:
        await upload.close()


async def _stored_track_rows(
    sample_context: SampleMetadataContext, *, track_type: str, source: str
) -> int:
    """The rows of one sample's ``track_type`` track of ``source`` in the store: what an
    update reads to see whether the sample holds the track, as the small-variant and SV
    loaders read their own rows. Not what Postgres records of the track
    (``sample_interval_track_sources``): a failed overwrite's restore that stopped part-way
    can leave that record for rows it deleted (SAFE-20). Only the dataset's own source: a
    sample can hold several callers' tracks of one type, and another caller's track is no
    reason to skip this one's (SAFE-22). A family with no single assembly has no track
    rows."""
    if not sample_context.assembly_name:
        return 0
    return await count_interval_track_rows(
        sample_context.assembly_name,
        family_uuid=sample_context.family_uuid,
        sample_uuid=sample_context.sample_uuid,
        track_type=track_type,
        source=source,
    )


async def _stored_imputed_blocks(family_context: FamilyMetadataContext) -> int:
    """The family's haplotype blocks made from imputed genotypes, as the store holds them:
    what an update of the imputed genotypes reads beside their small variants, as the
    glimpse2 loader writes both and either is the dataset's data. Not what Postgres records
    of the track, which a failed overwrite's restore that stopped part-way can leave for rows
    it deleted (SAFE-20, SAFE-18). A family with no single assembly has no track rows."""
    if not family_context.assembly_name:
        return 0
    return await count_interval_track_rows(
        family_context.assembly_name,
        family_uuid=family_context.family_uuid,
        sample_uuid=None,
        track_type="haplotype",
        source="glimpse2",
    )


async def _skip_stored_sample(
    job: DatasetImportJob,
    sample_id: str,
    sample_context: SampleMetadataContext,
    *,
    track_type: str,
    source: str,
) -> dict[str, Any] | None:
    """An update's skip of a sample whose ``track_type`` track of ``source`` is stored:
    ``{"skipped": True, "existing": <rows>}``, the sample recorded in
    ``job.skipped_samples``. None when the sample is to be imported: not an update, or no
    row of its track from that source is stored."""
    if job.conflict_mode != "update":
        return None
    existing = await _stored_track_rows(sample_context, track_type=track_type, source=source)
    if not existing:
        return None
    job.skipped_samples.add(sample_id)
    return {"skipped": True, "existing": existing}


def _is_update_skip(result: Any) -> bool:
    """Whether a sample's result, or one of its tracks', is an update's skip of rows that
    were there (``{"skipped": True, "existing": <rows>}``), not a loader's count of the
    lines it left out (``{"skipped": 3}``)."""
    return isinstance(result, dict) and result.get("existing") is not None


def _sample_by_sample_outcome(
    summary: FamilyImportDatasetSummary,
    sample_results: dict[str, Any],
    *,
    skipped: Sequence[str],
    imported_any: bool,
    imported: str,
    partly: str,
    skipped_all: str,
) -> FamilyImportDatasetSummary:
    """How a dataset imported sample by sample ends, when an update may have skipped some
    of its samples, or of their tracks (``skipped``), their rows being there: ``imported``
    when it imported any of them, ``skipped`` when it imported none, never ``imported`` for
    a dataset an update skipped whole (SAFE-20). The message is ``imported`` when nothing
    was skipped, else ``partly`` or ``skipped_all``, followed by what was skipped."""
    names = ", ".join(skipped)
    if not skipped:
        status, message = "imported", imported
    elif imported_any:
        status, message = "imported", f"{partly} {names}"
    else:
        status, message = "skipped", f"{skipped_all} {names}"
    return summary.model_copy(
        update={"status": status, "message": message, "summary": sample_results}
    )


def _exclude_filters(dataset: ManifestDataset) -> Sequence[Any] | None:
    """The SNV dataset's ``exclude_filters``, one value or a list: FILTER values whose
    records carry no variant to review. DeepVariant marks reference blocks RefCall and
    zero-depth sites NoCall; on a whole-genome long-read callset those are half the file."""
    value = (dataset.model_extra or {}).get("exclude_filters")
    if isinstance(value, str):
        return [value]
    return value if isinstance(value, (list, tuple)) else None


def _small_variant_callset(upload: UploadFile) -> str:
    """The callset the small-variant loader stores the family VCF in ``upload`` as when
    its format is ``auto``: ``glimpse2`` (imputed genotypes) when the first record's FORMAT
    has genotype posteriors, or phased genotypes in a file whose header or name says it was
    phased; else ``clair3``. The loader's own reading of the file, from its start; raises as
    the loader does on a file it cannot read, or one without a record."""
    return _detect_small_variant_format_from_upload(upload, "auto")


def snv_dataset_callset(bundle: FamilyPackageBundle) -> str:
    """The callset a package's ``snv`` dataset is stored as, as ``_import_snv_dataset``
    stores it: a monogenic NIPT's per-sample files as ``nipt``, a per-sample germline
    callset as the family's primary callset (``clair3``), and a family VCF as the
    ``source_format`` its dataset declares or, with ``auto`` (the default), as the callset
    the small-variant loader works out from the file (``_small_variant_callset``):
    ``glimpse2`` for imputed genotypes, else ``clair3``. The import's bookkeeping names it
    before anything is written (``dataset_scopes``), so it is read from the file here, as
    the loader reads it (SAFE-23). ``auto`` (``UNRESOLVED_SNV_SOURCE``) when it cannot be
    told: no family VCF, or one that cannot be read, which the import then registers only,
    or fails on before writing any of it."""
    dataset = bundle.manifest.datasets.get("snv")
    if dataset is None:
        return UNRESOLVED_SNV_SOURCE
    if dataset.per_sample and not dataset.family_vcf:
        if (bundle.manifest.analysis_type or "").strip() == MONOGENIC_NIPT_ANALYSIS_TYPE:
            return NIPT_SMALL_VARIANT_SOURCE
        return PER_SAMPLE_CALLSET_SOURCE
    source_format = str((dataset.model_extra or {}).get("source_format") or "auto")
    if source_format != "auto":
        return source_format
    try:
        vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
        if vcf_path is None:
            return UNRESOLVED_SNV_SOURCE
        with vcf_path.open("rb") as handle:
            return _small_variant_callset(UploadFile(file=handle, filename=vcf_path.name))
    except Exception:  # noqa: BLE001 - the loader reads the file again, and says why it cannot
        return UNRESOLVED_SNV_SOURCE


def _declares_no_file(dataset: ManifestDataset) -> bool:
    return not dataset.family_vcf and not dataset.per_sample


def _declares_no_sample(dataset: ManifestDataset) -> bool:
    return not dataset.per_sample


def _extra_declares_none(dataset: ManifestDataset, keys: Sequence[str]) -> bool:
    extra = dataset.model_extra or {}
    return not any(extra.get(key) for key in keys)


# A package's dataset that carries nothing CoGA imports, by the test its importer applies
# before it registers the dataset only: no file and no sample entry, or only a file CoGA does
# not import (a per-sample GLIMPSE2 BCF, a family-level APCAD BED, a pipeline run record
# without its versions or parameters). Such a dataset brings nothing, so nothing of it can be
# missing: its scope says so (``dataset_scopes``), and an import that registers it brings it
# again (SAFE-17). Only what the manifest declares is read: a dataset registered because a
# file it declares could not be found or read, or because the family has no single assembly,
# may lack data, and does not carry nothing. Validation refuses most datasets that declare
# no file at all (and skips a PCF dataset without one, which is then not imported); each
# test here follows its importer all the same.
_CARRIES_NOTHING: dict[str, Callable[[ManifestDataset], bool]] = {
    "snv": _declares_no_file,
    "sv_needlr": _declares_no_file,
    "repeats_trgt": _declares_no_file,
    "apcad": _declares_no_file,
    "haplotypes": lambda dataset: not dataset.family_vcf,
    "coverage": _declares_no_sample,
    "pcf": _declares_no_sample,
    "cnv": _declares_no_sample,
    "mito": _declares_no_sample,
    "alignments": _declares_no_sample,
    "qc": lambda dataset: _declares_no_sample(dataset) and _extra_declares_none(dataset, QC_FAMILY_ROLES),
    "pipeline_info": lambda dataset: _extra_declares_none(dataset, ("versions", "params")),
}


def dataset_carries_nothing(bundle: FamilyPackageBundle, dataset_type: str) -> bool:
    """Whether the package's ``dataset_type`` carries nothing CoGA imports, so that its
    importer registers it only (``_CARRIES_NOTHING``): the phenotypes when the package holds
    no HPO row (and no row with an error), any other dataset by what its manifest entry
    declares. False whenever that cannot be told (the HPO rows cannot be read): the safe
    side, as such a dataset may lack data."""
    if dataset_type == "phenotypes":
        try:
            rows, issues, _files, fatal_errors = _manifest_hpo_rows(
                root=bundle.root, manifest=bundle.manifest, family_id=bundle.manifest.family_id or ""
            )
        except Exception:  # noqa: BLE001 - the importer reads them again, and says why it cannot
            return False
        return not rows and not issues and not fatal_errors
    dataset = bundle.manifest.datasets.get(dataset_type)
    carries_nothing = _CARRIES_NOTHING.get(dataset_type)
    return dataset is not None and carries_nothing is not None and carries_nothing(dataset)


class SnvCallsetChanged(RuntimeError):
    """The family VCF of an ``snv`` dataset no longer reads as the callset its import
    recorded for it when it began: refused before anything of it was written."""


def _snv_callset_to_store(upload: UploadFile, source_format: str, scope: Mapping[str, Any] | None) -> str:
    """The callset the SNV loader is to store the family VCF in ``upload`` as: the
    ``source_format`` the dataset declares, or with ``auto`` the one the loader would work
    out from the file, read on the handle it then reads. The import recorded a callset for
    the dataset before its first write (``scope``, from ``snv_dataset_callset``); a file
    that no longer reads as that callset changed while the import ran, and is refused, so
    the import never stores a callset its bookkeeping does not name. Without a recorded
    callset (``auto``: the file could not be read then, or an import without bookkeeping)
    the file's own is stored, as before."""
    if source_format != "auto":
        return source_format
    callset = _small_variant_callset(upload)
    recorded = scope.get("source") if isinstance(scope, Mapping) else None
    if recorded not in (None, UNRESOLVED_SNV_SOURCE) and recorded != callset:
        raise SnvCallsetChanged(
            f"The SNV family VCF now reads as the {callset} callset, not the {recorded} callset "
            "this import recorded for it when it began: the file changed while the import ran. "
            "Nothing of it was imported; import the package again."
        )
    return callset


def _snv_dataset_source(job: DatasetImportJob, vcf_path: Path, source_format: str) -> str:
    """The callset the SNV importer stores its family VCF as: the rows its update check
    counts, and the only rows its clean-up after a failure removes (SAFE-27). The
    ``source_format`` the dataset declares, or with ``auto`` the callset the import recorded
    for it before its first write (``job.scope``, from ``snv_dataset_callset``): never
    assumed, so an update of imputed genotypes reads the family's imputed genotypes, not its
    direct calls, and a failed import of one callset removes none of another. An import
    that recorded none (``auto``: it could not read the file when it began, or an import
    without its bookkeeping) reads it from the file here, as the loader reads it, and raises
    as the loader does on a file it cannot read: before anything is written or removed."""
    if source_format != "auto":
        return source_format
    recorded = job.scope.get("source") if isinstance(job.scope, Mapping) else None
    if isinstance(recorded, str) and recorded and recorded != UNRESOLVED_SNV_SOURCE:
        return recorded
    with vcf_path.open("rb") as handle:
        return _small_variant_callset(UploadFile(file=handle, filename=vcf_path.name))


@_dataset_importer("snv")
async def _import_snv_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context, sample_contexts = job.family_context, job.sample_contexts
    conflict_mode, progress = job.conflict_mode, job.progress
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    if dataset.per_sample and not dataset.family_vcf:
        # One VCF per sample: a monogenic NIPT's plasma and father, or the long-read
        # pipeline's per-sample callsets (a couple screened for carriership).
        if (bundle.manifest.analysis_type or "").strip() == MONOGENIC_NIPT_ANALYSIS_TYPE:
            return await _import_nipt_per_sample_snv(job)
        return await _import_per_sample_snv(job)
    vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if vcf_path is None:
        return await _register_only(summary, "Registered only; family_vcf path is unavailable")
    extra = dataset.model_extra or {}
    source_format = str(extra.get("source_format") or "auto")
    # A family holds more than one small-variant callset, apart by source: the update check
    # and the clean-up below read only the callset this dataset stores, so neither touches
    # another. With auto that is the callset the import recorded from the file, never an
    # assumed clair3: assuming it skipped an update of imputed genotypes when the family
    # held direct calls, replaced the stored imputed genotypes in an update when it held
    # none, and made the clean-up of a failed import of imputed genotypes delete the direct
    # calls (SAFE-27). A declared source_format is used verbatim (hard-coding clair3 for
    # anything not glimpse2 once wiped the family's direct calls when a differently-sourced
    # dataset failed).
    snv_source = _snv_dataset_source(job, vcf_path, source_format)
    exclude_filters = _exclude_filters(dataset)
    if conflict_mode == "update":
        existing_count = await count_family_small_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=snv_source,
        )
        # Imputed genotypes come with the haplotype blocks made from them, which the loader
        # writes too: either is the dataset's data, as the haplotypes dataset's update reads
        # it, so blocks stored without their genotypes are left as they are (SAFE-18).
        existing_blocks = await _stored_imputed_blocks(family_context) if snv_source == "glimpse2" else 0
        if existing_count or existing_blocks:
            what = (
                f"small variants of the {snv_source} callset"
                if existing_count
                else "haplotype blocks made from imputed genotypes"
            )
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": f"Skipped SNV import in update mode because {what} already exist for this family",
                    "summary": {
                        "existing": existing_count,
                        **({"existing_haplotypes": existing_blocks} if snv_source == "glimpse2" else {}),
                    },
                }
            )
    annotation_path = _resolve_package_path(bundle.root, dataset.annotation_tsv)
    progress_lock = asyncio.Lock()

    async def report_snv_progress(stats: dict[str, Any]) -> None:
        if progress is None:
            return
        async with progress_lock:
            await progress(
                summary.model_copy(
                    update={
                        "status": "running",
                        "message": "Importing SNV VCF and VEP annotations",
                        "summary": stats,
                    }
                )
            )

    if progress is not None:
        await report_snv_progress(
            {
                "stage": "starting",
                "family_vcf": _display_path(bundle.root, vcf_path),
                "annotation_tsv": _display_path(bundle.root, annotation_path) if annotation_path else None,
            }
        )

    # Callers name the VCF's sample column after their own input file rather than the
    # sample; resolve those to family sample ids up front so the loader does not reject
    # the file outright.
    sample_aliases, unresolved_samples = vcf_sample_alias_map(
        read_vcf_sample_columns(vcf_path),
        set(sample_contexts),
        declared=extra.get("vcf_sample") or extra.get("sample_name"),
    )
    if unresolved_samples:
        raise RuntimeError(
            f"SNV VCF sample column(s) {unresolved_samples} match no sample in the family"
        )

    # With overwrite the VCF is read once, not first checked whole before the callset it
    # replaces is deleted (check_before_replacing): the import has snapshotted an existing
    # family and puts it back when a dataset fails, and a whole-genome callset is the
    # import's longest read. An update takes no snapshot, and reads the file whole first.
    check_before_replacing = conflict_mode != "overwrite"
    # Whether the loader began writing ClickHouse: the clean-up below deletes the callset
    # only then. A file the loader refused before it replaced anything (an annotation table
    # it cannot read, say) leaves the stored callset as it was, which an overwrite whose
    # snapshot could not be taken has nothing else to put back from (SAFE-32).
    write_state = SmallVariantWriteState()

    async def run_upload() -> dict[str, Any]:
        async with _local_upload(vcf_path) as upload:
            # Stored as the callset the import recorded for it before its first write, and
            # nothing else (SAFE-23): the one the update check counted and the clean-up
            # removes. One read from the file above (none was recorded) must still be it.
            callset = _snv_callset_to_store(upload, source_format, job.scope)
            if callset != snv_source:
                raise SnvCallsetChanged(
                    f"The SNV family VCF now reads as the {callset} callset, not the {snv_source} "
                    "callset it read as when its dataset began: the file changed while the import "
                    "ran. Nothing of it was imported; import the package again."
                )
            if annotation_path is not None:
                async with _local_upload(annotation_path) as annotation_upload:
                    return await upload_family_small_variant_file(
                        session,
                        context=family_context,
                        sample_contexts=sample_contexts,
                        file=upload,
                        annotation_file=annotation_upload,
                        overwrite=True,
                        format_hint=callset,  # type: ignore[arg-type]
                        progress=report_snv_progress,
                        sample_aliases=sample_aliases,
                        exclude_filters=exclude_filters,
                        check_before_replacing=check_before_replacing,
                        write_state=write_state,
                    )
            return await upload_family_small_variant_file(
                session,
                context=family_context,
                sample_contexts=sample_contexts,
                file=upload,
                overwrite=True,
                format_hint=callset,  # type: ignore[arg-type]
                progress=report_snv_progress,
                sample_aliases=sample_aliases,
                exclude_filters=exclude_filters,
                check_before_replacing=check_before_replacing,
                write_state=write_state,
            )

    try:
        result = await _run_with_periodic_progress(
            run_upload(),
            report=report_snv_progress if progress is not None else None,
            stats={
                "family_vcf": _display_path(bundle.root, vcf_path),
                "annotation_tsv": _display_path(bundle.root, annotation_path) if annotation_path else None,
            },
        )
    except SnvCallsetChanged:
        raise  # refused before the loader wrote anything: there is nothing to clean up
    except Exception:
        # Only the callset this dataset stores, which the loader replaces and writes (the
        # loader's own clean-up removes the haplotype blocks of imputed genotypes it was
        # writing): a failed import of imputed genotypes leaves the direct calls, and one of
        # direct calls the imputed genotypes (SAFE-27). The flag the import leaves names
        # this callset, the dataset's scope. And only once the loader began writing it
        # (SAFE-32): before that it replaced nothing.
        if write_state.began:
            with suppress(Exception):
                await delete_family_small_variants(
                    family_context.assembly_name,
                    family_context.family_uuid,
                    source=snv_source,
                )
        raise
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported through existing family small-variant loader",
            "summary": result,
        }
    )


@_dataset_importer("haplotypes")
async def _import_haplotypes_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context, sample_contexts = job.family_context, job.sample_contexts
    conflict_mode, progress = job.conflict_mode, job.progress
    if not dataset.family_vcf:
        return await _register_only(
            summary,
            "Registered only; direct per-sample GLIMPSE2 BCF haplotype import is not implemented yet",
        )
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if vcf_path is None:
        return await _register_only(summary, "Registered only; family_vcf path is unavailable")
    if conflict_mode == "update":
        existing_count = await count_family_small_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source="glimpse2",
        )
        # The stored blocks, not Postgres's record of them, as every update reads (SAFE-18).
        existing_haplotype_count = await _stored_imputed_blocks(family_context)
        if existing_count or existing_haplotype_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped the phased family VCF in update mode because small variants or haplotypes already exist",
                    "summary": {
                        "existing_small_variants": existing_count,
                        "existing_haplotypes": existing_haplotype_count,
                    },
                }
            )

    progress_lock = asyncio.Lock()

    async def report_haplotype_progress(stats: dict[str, Any]) -> None:
        if progress is None:
            return
        async with progress_lock:
            await progress(
                summary.model_copy(
                    update={
                        "status": "running",
                        "message": "Importing the phased family VCF and haplotype blocks",
                        "summary": stats,
                    }
                )
            )

    # With overwrite read once, as the SNV dataset's VCF: the import's snapshot puts an
    # existing family back when a dataset fails (check_before_replacing). The clean-up
    # below runs once the loader began writing, as the SNV dataset's (SAFE-32).
    write_state = SmallVariantWriteState()

    async def run_upload() -> dict[str, Any]:
        async with _local_upload(vcf_path) as upload:
            return await upload_family_small_variant_file(
                session,
                context=family_context,
                sample_contexts=sample_contexts,
                file=upload,
                annotation_file=None,
                overwrite=True,
                format_hint="glimpse2",
                progress=report_haplotype_progress,
                check_before_replacing=conflict_mode != "overwrite",
                write_state=write_state,
            )

    try:
        result = await _run_with_periodic_progress(
            run_upload(),
            report=report_haplotype_progress if progress is not None else None,
            stats={"family_vcf": _display_path(bundle.root, vcf_path)},
        )
    except Exception:
        # The glimpse2 loader owns the imputed small-variant source and the haplotype
        # interval tracks, so scope the small-variant cleanup to glimpse2 (leaving the
        # annotated clair3 SNVs intact) while still clearing its own haplotype blocks,
        # of its own source as every track loader's (SAFE-22). Only once the loader began
        # writing: a file it refused before that replaced nothing (SAFE-32).
        if write_state.began:
            with suppress(Exception):
                await delete_family_small_variants(
                    family_context.assembly_name,
                    family_context.family_uuid,
                    source="glimpse2",
                )
            with suppress(Exception):
                await delete_interval_tracks(
                    family_context.assembly_name,
                    family_uuid=family_context.family_uuid,
                    track_type="haplotype",
                    source="glimpse2",
                )
            with suppress(Exception):
                await delete_interval_track_sources(
                    session,
                    family_uuid=family_context.family_uuid,
                    track_type="haplotype",
                    source="glimpse2",
                )
        raise
    haplotype_origin = await _record_pipeline_haplotype_origin(job)
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported the phased family VCF as small variants and haplotype blocks"
                + ("; the pipeline's haplotype-origin reading on the family" if haplotype_origin else "")
            ),
            "summary": {**result, **({"haplotype_origin": haplotype_origin} if haplotype_origin else {})},
        }
    )


# The PGT pipeline names its haplotype-origin files after the affected parent.
_HAPLOTYPE_ORIGIN_SUFFIXES = ("_affected_normal_haplotype.csv", "_haplotype_conclusion.txt")
_HAPLOTYPE_CONCLUSION_MAX_CHARS = 2000


async def _record_pipeline_haplotype_origin(job: DatasetImportJob) -> dict[str, Any] | None:
    """Record the pipeline's reading of which haplotype of the affected parent is the
    affected one, when the haplotypes dataset declares it. The table counts, per
    haplotype of the parent, the sites it shares with the index's affected and normal
    haplotypes; the conclusion is the pipeline's own sentence. Both are kept verbatim
    as evidence: CoGA infers the risk haplotype itself, from the members' states."""
    extra = job.dataset.model_extra or {}
    root = job.bundle.root
    record: dict[str, Any] = {}
    files: dict[str, str] = {}
    for role in HAPLOTYPE_ORIGIN_ROLES:
        path = _resolve_package_path(root, extra.get(role))
        if path is None or not path.is_file():
            continue
        files[role] = _display_path(root, path)
        text_value = read_path_text_bounded(path, kind="Haplotype origin")
        if role == "haplotype_origin":
            rows = parse_haplotype_origin_text(text_value)
            if rows:
                record["haplotypes"] = rows
        else:
            conclusion = text_value.strip()[:_HAPLOTYPE_CONCLUSION_MAX_CHARS]
            if conclusion:
                record["conclusion"] = conclusion
        for suffix in _HAPLOTYPE_ORIGIN_SUFFIXES:
            if path.name.endswith(suffix) and len(path.name) > len(suffix):
                record.setdefault("parent", path.name[: -len(suffix)])
    if not record:
        return None
    record["files"] = files
    await _record_family_haplotype_origin(
        job.session,
        family_uuid=job.family_context.family_uuid,
        haplotype_origin=record,
    )
    return record


async def _import_bins_and_segments(
    job: DatasetImportJob,
    *,
    caller: str,
    source: str,
    role_paths: Callable[[dict[str, Any]], dict[str, Any]],
    track_importer: Callable[..., Awaitable[dict[str, int]]],
) -> FamilyImportDatasetSummary:
    """A per-sample copy-number caller's files (WisecondorX, QDNAseq): each sample's bins
    as its ``coverage`` track and its segments as its ``segments`` track, under
    ``source``. ``role_paths`` gives a sample entry's two manifest paths, and
    ``track_importer`` reads one file into its track."""
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts, conflict_mode = job.sample_contexts, job.conflict_mode
    progress = job.progress
    sample_results: dict[str, Any] = {}
    tracks: list[tuple[str, SampleMetadataContext, str, str, Callable[..., Awaitable[dict[str, int]]]]] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        sample_results[sample_id] = {}

        # sample_id is bound now, so the callback reports the sample it was made for.
        async def report_track(
            role: str, stats: dict[str, int], *, sample_id: str = sample_id
        ) -> None:
            sample_results.setdefault(sample_id, {})[role] = stats
            if progress is not None:
                await progress(
                    summary.model_copy(
                        update={
                            "status": "running",
                            "message": f"Importing {caller} {role} for {sample_id}",
                            "summary": sample_results,
                        }
                    )
                )

        # bins carry a per-bin ratio (a coverage axis); segments carry the called
        # level. Both go through the shared guard so a re-import owns its whole
        # (track_type, source) pair rather than only the filename it happens to read.
        paths = role_paths(raw_entry)
        for role, track_type in (("bins", "coverage"), ("segments", "segments")):
            path = _resolve_package_path(bundle.root, paths[role])
            if path is None:
                continue
            importer = partial(
                track_importer,
                session,
                sample_context=sample_context,
                path=path,
                track_type=track_type,
                progress=partial(report_track, role),
            )
            tracks.append((sample_id, sample_context, role, track_type, importer))
    for _sample_id, sample_context, _role, track_type, importer in tracks:
        await _check_track_unless_present(
            sample_context=sample_context,
            track_type=track_type,
            source=source,
            conflict_mode=conflict_mode,
            importer=importer,
        )
    for sample_id, sample_context, role, track_type, importer in tracks:
        sample_results[sample_id][role] = await _import_interval_track_unless_present(
            session,
            sample_context=sample_context,
            track_type=track_type,
            source=source,
            conflict_mode=conflict_mode,
            importer=importer,
        )
        if _is_update_skip(sample_results[sample_id][role]):
            # A sample whose bins or segments were skipped is not imported again whole.
            job.skipped_samples.add(sample_id)
    track_results = [
        (f"{sample_id}:{role}", stats)
        for sample_id, roles in sample_results.items()
        for role, stats in roles.items()
    ]
    return _sample_by_sample_outcome(
        summary,
        sample_results,
        skipped=[name for name, stats in track_results if _is_update_skip(stats)],
        imported_any=any(not _is_update_skip(stats) for _name, stats in track_results),
        imported=f"Imported {caller} bins as coverage and segments as segment interval tracks",
        partly=f"Imported {caller} data; skipped existing tracks in update mode:",
        skipped_all=f"Skipped {caller} import in update mode because these tracks already exist:",
    )


@_dataset_importer("wisecondorx")
async def _import_wisecondorx_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    return await _import_bins_and_segments(
        job,
        caller="WisecondorX",
        source="wisecondorx",
        role_paths=lambda entry: {"bins": entry.get("bins"), "segments": entry.get("segments")},
        track_importer=_import_wisecondorx_track,
    )


@_dataset_importer("qdnaseq")
async def _import_qdnaseq_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    return await _import_bins_and_segments(
        job,
        caller="QDNAseq",
        source="qdnaseq",
        role_paths=lambda entry: {
            "bins": entry.get("bins") or entry.get("file"),
            "segments": entry.get("segments"),
        },
        track_importer=partial(_import_copy_number_track, source="qdnaseq"),
    )


@_dataset_importer("sv_needlr")
async def _import_sv_needlr_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context, sample_contexts = job.family_context, job.sample_contexts
    conflict_mode = job.conflict_mode
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    per_sample = not dataset.family_vcf and bool(dataset.per_sample)
    vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if vcf_path is None and not per_sample:
        return await _register_only(summary, "Registered only; family_vcf path is unavailable")
    if conflict_mode == "update":
        existing_count = await count_family_structural_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source="needlr",
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped Needlr SV import in update mode because Needlr SVs already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )
    # The files read: the family VCF, or one VCF per sample (the long-read pipeline's).
    texts: dict[str, str] = {}
    filenames: str | dict[str, str]
    skipped = SkippedRecords()
    if per_sample:
        sample_files = await _needlr_sample_files(job)
        if not sample_files:
            return await _register_only(summary, "Registered only; no per-sample NeedlR file is readable")
        texts = {_display_path(bundle.root, path): text_value for _sample_id, path, text_value in sample_files}
        filenames = {sample_id: path.name for sample_id, path, _text in sample_files}
        records = _iter_needlr_per_sample_records(
            [(sample_id, text_value) for sample_id, _path, text_value in sample_files],
            ped=bundle.ped,
            sample_contexts=sample_contexts,
            skipped=skipped,
        )
    else:
        assert vcf_path is not None
        text_value = _read_package_text(vcf_path)
        texts = {_display_path(bundle.root, vcf_path): text_value}
        filenames = vcf_path.name
        records = _iter_needlr_structural_records(
            text_value,
            ped=bundle.ped,
            sample_contexts=sample_contexts,
            skipped=skipped,
        )
    if not records:
        raise RuntimeError(
            "No Needlr structural variants with PED sample calls were found" + _without_extent_clause(skipped)
        )
    # Held until the commit that records the SV file (the import holds it for its whole run).
    await lock_family_variant_writes(
        session,
        family_context.family_uuid,
        [STRUCTURAL_VARIANTS],
        samples=[sample.sample_uuid for sample in sample_contexts.values()],
    )
    await replace_family_structural_variants(
        family_context.assembly_name,
        family_context.family_uuid,
        family_context.project_ids,
        records,
        source="needlr",
    )
    await _update_sv_file_metadata(
        session,
        sample_contexts=sample_contexts,
        source="needlr",
        filename=filenames,
    )
    # Capture SV provenance into the family's annotation manifest (best-effort;
    # joins the import transaction). The NeedlR SV VCF carries no structured version
    # header lines — its annotation-database releases (GENCODE/OMIM/GenCC/gnomAD/
    # GIAB) live in the ``##INFO`` descriptions — so mine those too.
    from .annotation_manifest_service import merge_vcf_header_provenance
    from .vcf_header_provenance import (
        extract_header_provenance,
        extract_info_description_provenance,
        merge_module_maps,
    )

    sv_modules: dict[str, dict[str, Any]] = {}
    for text_value in texts.values():
        sv_lines = [line for line in text_value.splitlines() if line.startswith("#")]
        sv_modules = merge_module_maps(
            sv_modules,
            merge_module_maps(
                extract_header_provenance(sv_lines, modality="sv").as_modules(),
                extract_info_description_provenance(sv_lines),
            ),
        )
    await merge_vcf_header_provenance(
        session,
        family_uuid=family_context.family_uuid,
        assembly_id=getattr(family_context, "assembly_id", None),
        modules=sv_modules,
        modality="sv",
    )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported the per-sample Needlr SV VCFs into structural variant storage"
                if per_sample
                else "Imported Needlr family SV VCF into structural variant storage"
            )
            + _without_extent_note(skipped),
            "summary": {
                "processed": len(records),
                "source": "needlr",
                **({"per_sample": sorted(texts)} if per_sample else {}),
                **_without_extent_count(skipped),
            },
        }
    )


def _without_extent_count(skipped: SkippedRecords) -> dict[str, int]:
    """A dataset's or a sample's count of the records left out as their extent is not stated
    (``SkippedRecords``), when there are any."""
    return {"skipped_without_extent": skipped.without_extent} if skipped.without_extent else {}


def _without_extent_clause(skipped: SkippedRecords) -> str:
    """What a refusal says of the records left out as their extent is not stated."""
    if not skipped.without_extent:
        return ""
    return (
        f": {skipped.without_extent} record(s) of a deletion, duplication, inversion or CNV state "
        "neither END nor SVLEN, so how far they reach is not known, and were left out"
    )


def _without_extent_note(skipped: SkippedRecords) -> str:
    """What a dataset's message says of the records left out as their extent is not stated
    (CLIN-39, the owner's decision, 2026-10-27: left out and counted)."""
    if not skipped.without_extent:
        return ""
    return (
        f"; left out {skipped.without_extent} record(s) of a deletion, duplication, inversion or "
        "CNV that state neither END nor SVLEN"
    )


async def _needlr_sample_files(job: DatasetImportJob) -> list[tuple[str, Path, str]]:
    """Each per-sample NeedlR entry's ``(sample id, path, text)``, checked to hold that
    sample's calls before anything is written.

    NeedlR has no sample column: each record names its query sample in ``Query_ID``,
    after the caller's input file (``<sample>_sv_phased``). A file whose query names
    another sample of CoGA -- a family member's file under this entry, or another
    family's -- is refused, as a per-sample VCF column is (``per_sample_vcf_column``); a
    query that names no known sample is the entry's.

    So is a file without a record the reader reads (SAFE-29): a sample's structural
    variants called across its genome are never none, and such a file used to replace the
    sample's NeedlR calls with none while the other samples' files imported."""
    bundle, family_context = job.bundle, job.family_context
    files: list[tuple[str, Path, str]] = []
    for sample_id, raw_entry in job.dataset.per_sample.items():
        if sample_id not in job.sample_contexts or not isinstance(raw_entry, dict):
            continue
        path = _resolve_package_path(bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if path is None:
            continue
        text_value = _read_package_text(path)
        skipped = SkippedRecords()
        if not _iter_needlr_per_sample_records(
            [(sample_id, text_value)], ped=bundle.ped, sample_contexts=job.sample_contexts, skipped=skipped
        ):
            raise RuntimeError(
                f"The NeedlR file of {sample_id} ({path.name}) holds no structural variant"
                + _without_extent_clause(skipped)
                + "; no NeedlR call was replaced"
            )
        queries = sorted(needlr_query_ids(text_value))
        known = await known_vcf_sample_ids(
            job.session, family_uuid=family_context.family_uuid, header_samples=queries
        )
        for query in queries:
            named = resolve_vcf_sample_id(query, known | {sample_id})
            if named is not None and named != sample_id:
                raise RuntimeError(
                    f"The NeedlR file of {sample_id} holds another sample's calls: its query "
                    f"'{query}' is {named}. {_PER_SAMPLE_COLUMN_REMEDY}"
                )
        files.append((sample_id, path, text_value))
    return files


@_dataset_importer("apcad")
async def _import_apcad_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts, conflict_mode = job.sample_contexts, job.conflict_mode
    progress = job.progress
    sample_results: dict[str, Any] = {}

    async def report_apcad_progress(stats: dict[str, Any]) -> None:
        # An APCAD VCF holds every SNV the embryo was called at, millions per embryo,
        # so one sample can take minutes: report, and keep the job's heartbeat fresh.
        if progress is None:
            return
        await progress(
            summary.model_copy(
                update={
                    "status": "running",
                    "message": "Importing APCAD into embryo APCAD tracks",
                    "summary": {**sample_results, **stats},
                }
            )
        )

    reporter = report_apcad_progress if progress is not None else None
    if dataset.family_vcf:
        vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
        if vcf_path is None:
            return await _register_only(summary, "Registered only; family_vcf path is unavailable")
        embryo_sample_ids = _ped_embryo_sample_ids(bundle.ped)
        target_sample_contexts = (
            {
                sample_id: sample_context
                for sample_id, sample_context in sample_contexts.items()
                if sample_id in embryo_sample_ids
            }
            or sample_contexts
        )
        if conflict_mode == "update":
            # One file for every embryo: skipped whole when any embryo's track is stored.
            # This dataset's track, not HiFiCNV's minor allele fraction on the same track
            # type, nor a BED upload's (SAFE-22).
            existing_by_sample = {
                sample_id: await _stored_track_rows(sample_context, track_type="apcad", source=APCAD_SOURCE)
                for sample_id, sample_context in target_sample_contexts.items()
            }
            if any(existing_by_sample.values()):
                return summary.model_copy(
                    update={
                        "status": "skipped",
                        "message": "Skipped APCAD import in update mode because APCAD tracks already exist",
                        "summary": {"existing": existing_by_sample},
                    }
                )
        family_vcf_read = FilesReadInTurn([vcf_path])

        async def report_family_vcf_read(stats: dict[str, Any]) -> None:
            await report_apcad_progress(family_vcf_read.stats(stats))

        sample_results = await _run_with_periodic_progress(
            _import_apcad_track_file(
                session,
                sample_contexts=target_sample_contexts,
                path=vcf_path,
                ped=bundle.ped,
                progress=report_family_vcf_read if reporter is not None else None,
            ),
            report=reporter,
            stats={"family_vcf": _display_path(bundle.root, vcf_path)},
        )
        return summary.model_copy(
            update={
                "status": "imported",
                "message": "Imported APCAD VCF into embryo APCAD interval tracks",
                "summary": sample_results,
            }
        )
    if not dataset.per_sample:
        return await _register_only(
            summary,
            "Registered only; this manifest uses a family-level APCAD BED and existing loaders are sample-scoped",
        )
    # The files to read, known before the first is, so their bytes add up to the dataset's
    # progress.
    sample_files: list[tuple[str, dict[str, Any], Path]] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        bed_path = _resolve_package_path(
            bundle.root,
            raw_entry.get("bed") or raw_entry.get("file") or raw_entry.get("vcf"),
        )
        if bed_path is None:
            continue
        skip = await _skip_stored_sample(
            job, sample_id, sample_context, track_type="apcad", source=APCAD_SOURCE
        )
        if skip is not None:
            sample_results[sample_id] = skip
            continue
        sample_files.append((sample_id, raw_entry, bed_path))
    files_read = FilesReadInTurn(path for _sample_id, _entry, path in sample_files)

    async def report_file_read(stats: dict[str, Any]) -> None:
        await report_apcad_progress(files_read.stats(stats))

    async def import_sample_file(
        sample_id: str, raw_entry: dict[str, Any], bed_path: Path, *, check_only: bool = False
    ) -> Any:
        """The embryo's file into its ``apcad`` track; with ``check_only``, read as it is
        read before the track is cleared, and nothing deleted or stored."""
        # A per-embryo trio VCF (nf-cmgg/copgtm) carries the embryo's column beside the
        # parents' columns, which give each site its parental origin.
        import_result = await _run_with_periodic_progress(
            _import_apcad_track_file(
                session,
                sample_contexts=sample_contexts,
                path=bed_path,
                ped=bundle.ped,
                selected_sample_id=sample_id,
                selected_vcf_sample=raw_entry.get("sample_name") or raw_entry.get("vcf_sample"),
                progress=report_file_read if reporter is not None else None,
                check_only=check_only,
            ),
            report=reporter,
            stats={
                "checking" if check_only else "importing": sample_id,
                "file": _display_path(bundle.root, bed_path),
            },
        )
        if import_result:
            return (
                import_result.get(sample_id, import_result)
                if isinstance(import_result, dict)
                else import_result
            )
        # Read as a BED, into this dataset's own track: a BED upload's APCAD stays. Its
        # record says a package loaded it, as every package loader's does (TRACE-17).
        async with _local_upload(bed_path) as upload:
            return await upload_bed_data(
                session,
                sample_context=sample_contexts[sample_id],
                bed_type="apcad",
                file=upload,
                overwrite=True,
                source=APCAD_SOURCE,
                uploaded_from="family_package",
                check_only=check_only,
            )

    # Every embryo's file is checked, as it is checked before its embryo's track is cleared,
    # before any embryo's track is: a later embryo's file without a value for it failed the
    # dataset with the earlier embryos' tracks replaced (SAFE-3). The check reads a file up to
    # the embryo's first value (a VCF without a record, read as a BED, whole), so a file is
    # read twice only that far.
    for sample_id, raw_entry, bed_path in sample_files:
        await import_sample_file(sample_id, raw_entry, bed_path, check_only=True)
    for sample_id, raw_entry, bed_path in sample_files:
        sample_results[sample_id] = await import_sample_file(sample_id, raw_entry, bed_path)
        files_read.next_file()
        if reporter is not None:
            await reporter({})
    return _sample_by_sample_outcome(
        summary,
        sample_results,
        skipped=[sample_id for sample_id, stats in sample_results.items() if _is_update_skip(stats)],
        imported_any=any(not _is_update_skip(stats) for stats in sample_results.values()),
        imported="Imported APCAD data into interval tracks",
        partly="Imported APCAD data; skipped existing samples in update mode:",
        skipped_all="Skipped APCAD import in update mode because APCAD tracks already exist for",
    )


async def _import_nipt_per_sample_snv(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Import a monogenic NIPT's SNV files, one per sample (``datasets.snv.per_sample``),
    as one callset with source ``nipt`` (see family_package_nipt).

    The plasma file goes first, then every other file merges its calls into the stored
    rows (``overwrite_scope="samples"``), so each variant is one row holding each sample's
    call. A paternal file keeps only its calls at a plasma call position or at
    ``NIPT_PATERNAL_KEEP_MIN_VAF`` or more; the others are counted per sample
    (``skipped_by_filter``). Each file replaces its own sample's earlier calls, so a
    re-import of one file leaves the other sample's calls as they were.
    """
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context, sample_contexts = job.family_context, job.sample_contexts
    assembly_name = family_context.assembly_name or ""
    if job.conflict_mode == "update":
        existing_count = await count_family_small_variants(
            assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=NIPT_SMALL_VARIANT_SOURCE,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped the NIPT SNV import in update mode because its calls already exist",
                    "summary": {"existing": existing_count},
                }
            )
    cfdna_sample_id = nipt_package_cfdna_sample(bundle)
    plasma_positions: set[tuple[str, int]] = set()
    sample_results: dict[str, Any] = {}
    sample_files: list[tuple[str, Path]] = []
    for sample_id in nipt_import_order(cfdna_sample_id, dataset.per_sample):
        raw_entry = dataset.per_sample.get(sample_id)
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if vcf_path is not None:
            sample_files.append((sample_id, vcf_path))
    # Every file is checked, its one sample column and a record, before the plasma's calls
    # are written: the father's file was checked after them, so a refused one failed the
    # dataset with the plasma's calls replaced (SAFE-3). The loader refuses a file without
    # a record as well; here it is found before anything is written.
    sample_columns: dict[str, list[str]] = {}
    for sample_id, vcf_path in sample_files:
        vcf_columns = read_vcf_sample_columns(vcf_path)
        if len(vcf_columns) != 1:
            raise RuntimeError(
                f"The NIPT SNV file of {sample_id} must hold one sample; it has {len(vcf_columns)}"
            )
        if not vcf_holds_a_data_line(vcf_path):
            raise RuntimeError(
                f"The NIPT SNV file of {sample_id} ({vcf_path.name}) holds no record; "
                "no NIPT call was replaced"
            )
        sample_columns[sample_id] = vcf_columns
    files_read = FilesReadInTurn(path for _sample_id, path in sample_files)

    async def report_file_progress(stats: dict[str, Any], *, sample_id: str) -> None:
        if job.progress is None:
            return
        await job.progress(
            summary.model_copy(
                update={
                    "status": "running",
                    "message": "Importing the NIPT per-sample SNV files",
                    "summary": {"importing": sample_id, **stats, **files_read.stats(stats)},
                }
            )
        )

    for sample_id, vcf_path in sample_files:
        aliases, _unresolved = vcf_sample_alias_map(
            sample_columns[sample_id], set(sample_contexts), target_sample_id=sample_id
        )
        is_plasma = sample_id == cfdna_sample_id
        if is_plasma:
            plasma_positions = await asyncio.to_thread(scan_vcf_positions, vcf_path)
        async with _local_upload(vcf_path) as upload:
            result = await upload_family_small_variant_file(
                session,
                context=family_context,
                sample_contexts=sample_contexts,
                file=upload,
                overwrite=True,
                format_hint=NIPT_SMALL_VARIANT_SOURCE,
                sample_aliases=aliases,
                overwrite_scope="samples",
                record_filter=None if is_plasma else paternal_record_filter(plasma_positions),
                progress=(
                    partial(report_file_progress, sample_id=sample_id)
                    if job.progress is not None
                    else None
                ),
            )
        files_read.next_file()
        sample_results[sample_id] = {
            **result,
            "role": "cfdna" if is_plasma else "paired_sample",
            "vcf": _display_path(bundle.root, vcf_path),
        }
    if not sample_results:
        return await _register_only(summary, "Registered only; no per-sample SNV file is readable")
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported the NIPT per-sample SNV files as one callset",
            "summary": sample_results,
        }
    )


async def _import_per_sample_snv(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Import ``datasets.snv.per_sample`` of a family that is not a monogenic NIPT: the
    long-read pipeline's per-sample callsets, read side by side as the family's primary
    callset (see per_sample_small_variants).

    Every file is checked to hold its entry's sample (``per_sample_vcf_column``) before
    anything is written. With ``update`` the dataset is skipped when the family holds a
    primary callset; with ``overwrite`` the files replace it.
    """
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context, sample_contexts = job.family_context, job.sample_contexts
    assembly_name = family_context.assembly_name or ""
    if job.conflict_mode == "update":
        existing_count = await count_family_small_variants(
            assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=PER_SAMPLE_CALLSET_SOURCE,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped SNV import in update mode because small variants already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )
    exclude_filters = _exclude_filters(dataset)
    files: list[PerSampleVcf] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        if sample_id not in sample_contexts or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if vcf_path is None:
            continue
        header = read_vcf_sample_columns(vcf_path)
        if len(header) != 1:
            raise RuntimeError(f"The SNV file of {sample_id} must hold one sample; it has {len(header)}")
        column = per_sample_vcf_column(
            header,
            target_sample_id=sample_id,
            known_sample_ids=await known_vcf_sample_ids(
                session,
                family_uuid=family_context.family_uuid,
                header_samples=header,
            ),
            declared=raw_entry.get("vcf_sample") or raw_entry.get("sample_name"),
            label=f"The SNV file of {sample_id}",
            remedy=_PER_SAMPLE_COLUMN_REMEDY,
        )
        files.append(PerSampleVcf(sample_id=sample_id, path=vcf_path, column=column))
    if not files:
        return await _register_only(summary, "Registered only; no per-sample SNV file is readable")
    progress_lock = asyncio.Lock()

    async def report_progress(stats: dict[str, Any]) -> None:
        if job.progress is None:
            return
        async with progress_lock:
            await job.progress(
                summary.model_copy(
                    update={
                        "status": "running",
                        "message": "Importing the per-sample SNV files as one callset",
                        "summary": stats,
                    }
                )
            )

    paths = {file.sample_id: _display_path(bundle.root, file.path) for file in files}
    if job.progress is not None:
        await report_progress({"stage": "starting", "per_sample": paths})
    result = await _run_with_periodic_progress(
        upload_family_per_sample_small_variant_files(
            session,
            context=family_context,
            sample_contexts=sample_contexts,
            files=files,
            overwrite=True,
            exclude_filters=exclude_filters,
            progress=report_progress if job.progress is not None else None,
        ),
        report=report_progress if job.progress is not None else None,
        stats={"per_sample": paths},
    )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported the per-sample SNV files as one callset",
            "summary": {**result, "per_sample": paths},
        }
    )


async def _import_target_coverage_table(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    table_path: Path,
    bundle_root: Path,
    check_only: bool = False,
) -> dict[str, Any]:
    """Store a per-target coverage table as the sample's ``target_coverage`` track, in
    place of the one stored before (see nipt_target_coverage). The table is read and
    parsed whole first: one that cannot be, or holds no target, is refused with the track
    as it was. ``check_only`` stops there, deleting and storing nothing."""
    assembly_name = sample_context.assembly_name or ""
    text_value = await asyncio.to_thread(read_path_text_bounded, table_path, kind="Coverage table")
    try:
        targets = parse_target_coverage_table(text_value.splitlines())
    except TargetCoverageFormatError as exc:
        raise RuntimeError(f"{_display_path(bundle_root, table_path)}: {exc}") from exc
    if not targets:
        raise RuntimeError(f"{_display_path(bundle_root, table_path)} holds no target")
    if check_only:
        return {}
    await _delete_sample_interval_source(
        session,
        sample_context=sample_context,
        track_type=TARGET_COVERAGE_TRACK_TYPE,
        source=TARGET_COVERAGE_SOURCE,
    )
    rows = [
        {
            "sample_id": sample_context.sample_uuid,
            "family_id": sample_context.family_uuid,
            "track_type": TARGET_COVERAGE_TRACK_TYPE,
            "source": TARGET_COVERAGE_SOURCE,
            "filename": table_path.name,
            "chr": target.chrom,
            "start": target.start,
            "end": target.end,
            "record_id": target.gene or None,
            "value": target.mean,
            "metadata_json": target_row_metadata(target),
        }
        for target in targets
    ]
    await insert_interval_track_rows(assembly_name, rows)
    await upsert_interval_track_source(
        session,
        sample_context=sample_context,
        track_type=TARGET_COVERAGE_TRACK_TYPE,
        source=TARGET_COVERAGE_SOURCE,
        filename=table_path.name,
        row_count=len(rows),
        # Where the table came from, as every package loader's record says (TRACE-17).
        metadata={
            "format": "per_target_coverage_table",
            "targets": len(rows),
            "uploaded_from": "family_package",
        },
    )
    return {"inserted": len(rows), "track_type": TARGET_COVERAGE_TRACK_TYPE}


@_dataset_importer("coverage")
async def _import_coverage_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Import coverage: a BED (``bed``) into the ``coverage`` track, or a capture panel's
    per-target coverage table (``target_table``, monogenic NIPT) into the
    ``target_coverage`` track.

    A BED is stored as a BED upload stores it (``upload_bed_data``), under source ``web``:
    it replaces that source's coverage of the sample, and no other caller's (the bins of
    WisecondorX and QDNAseq, imported before it, and HiFiCNV's depth; SAFE-22). The entry's
    ``content`` declares what the BED holds, ``read_depth`` or ``log2_ratio``, and its
    track's record keeps it; an entry without it is stored as ``unknown``, which is never
    read as depth (TRACE-9). The sample's result names it. A BED of the coverage dataset
    used to be read as depth whatever it held.

    With ``update`` a sample whose track from this dataset's source is stored is skipped, as
    the store holds it: not as Postgres records it, which a failed overwrite's restore that
    stopped part-way can leave for rows it deleted. A dataset that skipped every sample ends
    ``skipped`` (SAFE-20).

    Every sample's file is read and checked, as it is before its track is replaced, before
    any sample's track is: a later sample's refused file failed the dataset with the earlier
    samples' tracks replaced (SAFE-3)."""
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts = job.sample_contexts
    if not dataset.per_sample:
        return await _register_only(
            summary, "Registered only; coverage dataset has no per_sample entries"
        )
    sample_results: dict[str, Any] = {}
    # Each sample's file and the track it goes into: a per-target table, or a BED with what
    # it declares it holds.
    sample_files: list[tuple[str, SampleMetadataContext, str, Path, str]] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        table_path = _resolve_package_path(bundle.root, raw_entry.get("target_table"))
        if table_path is not None:
            track_type, source = TARGET_COVERAGE_TRACK_TYPE, TARGET_COVERAGE_SOURCE
            path = table_path
        else:
            bed_path = _resolve_package_path(
                bundle.root, raw_entry.get("bed") or raw_entry.get("file")
            )
            if bed_path is None:
                continue
            track_type, source, path = "coverage", BED_UPLOAD_SOURCE, bed_path
        skip = await _skip_stored_sample(
            job, sample_id, sample_context, track_type=track_type, source=source
        )
        if skip is not None:
            sample_results[sample_id] = skip
            continue
        sample_files.append(
            (
                sample_id,
                sample_context,
                track_type,
                path,
                declared_coverage_content(raw_entry.get(COVERAGE_CONTENT_KEY)),
            )
        )

    async def import_sample_file(
        sample_context: SampleMetadataContext,
        track_type: str,
        path: Path,
        content: str,
        *,
        check_only: bool = False,
    ) -> dict[str, Any]:
        """The sample's file into its track; with ``check_only``, read and checked as it is
        before the track is replaced, and nothing deleted or stored."""
        if track_type == TARGET_COVERAGE_TRACK_TYPE:
            stored = await _import_target_coverage_table(
                session,
                sample_context=sample_context,
                table_path=path,
                bundle_root=bundle.root,
                check_only=check_only,
            )
            if not check_only:
                await session.commit()
            return stored
        async with _local_upload(path) as upload:
            stored = await upload_bed_data(
                session,
                sample_context=sample_context,
                bed_type="coverage",
                file=upload,
                overwrite=True,
                content=content,
                # Its record says a package loaded it, as the other package tracks' records do:
                # the single-file upload shares its source (`web`).
                uploaded_from="family_package",
                check_only=check_only,
            )
        return {**stored, COVERAGE_CONTENT_KEY: content}

    # Each file is read twice: the check decodes a BED whole, as its loader does, and stops at
    # its first record; a per-target table holds a row per capture target.
    for _sample_id, sample_context, track_type, path, content in sample_files:
        await import_sample_file(sample_context, track_type, path, content, check_only=True)
    for sample_id, sample_context, track_type, path, content in sample_files:
        sample_results[sample_id] = await import_sample_file(sample_context, track_type, path, content)
    return _sample_by_sample_outcome(
        summary,
        sample_results,
        skipped=[sample_id for sample_id, stats in sample_results.items() if _is_update_skip(stats)],
        imported_any=any(not _is_update_skip(stats) for stats in sample_results.values()),
        imported="Imported coverage into interval tracks",
        partly="Imported coverage; skipped existing samples in update mode:",
        skipped_all="Skipped coverage import in update mode because coverage already exists for",
    )


@_dataset_importer("pcf")
async def _import_pcf_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts = job.sample_contexts
    if not dataset.per_sample:
        return await _register_only(summary, "No PCF segment files were provided")
    sample_results: dict[str, Any] = {}
    samples: list[tuple[str, SampleMetadataContext, list[tuple[str, Path]]]] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        role_paths: list[tuple[str, Path]] = []
        for role, origin in (("maternal", "maternal"), ("paternal", "paternal")):
            path = _resolve_package_path(bundle.root, _pcf_role_path(raw_entry, role))
            if path is not None:
                role_paths.append((origin, path))
        if not role_paths:
            continue

        skip = await _skip_stored_sample(
            job, sample_id, sample_context, track_type=APCAD_PCF_TRACK_TYPE, source=APCAD_PCF_SOURCE
        )
        if skip is not None:
            sample_results[sample_id] = skip
            continue
        samples.append((sample_id, sample_context, role_paths))

    # Each table of every sample is read up to its first segment before any sample's track is
    # deleted: one without a segment fails the dataset with every track as it was (SAFE-29).
    # A later sample's table failed it with the earlier samples' tracks replaced (SAFE-3).
    for _sample_id, sample_context, role_paths in samples:
        for origin, path in role_paths:
            await _import_pcf_segment_file(
                session, sample_context=sample_context, path=path, origin=origin, check_only=True
            )
    for sample_id, sample_context, role_paths in samples:
        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type=APCAD_PCF_TRACK_TYPE,
            source=APCAD_PCF_SOURCE,
        )
        sample_results[sample_id] = {}
        for origin, path in role_paths:
            sample_results[sample_id][origin] = await _import_pcf_segment_file(
                session,
                sample_context=sample_context,
                path=path,
                origin=origin,
            )

    if not sample_results:
        return summary.model_copy(
            update={
                "status": "skipped",
                "message": "No PCF segment files were imported",
                "summary": sample_results,
            }
        )
    return _sample_by_sample_outcome(
        summary,
        sample_results,
        skipped=[sample_id for sample_id, stats in sample_results.items() if _is_update_skip(stats)],
        imported_any=any(not _is_update_skip(stats) for stats in sample_results.values()),
        imported="Imported PCF APCAD segment overlays into interval tracks",
        partly="Imported PCF data; skipped existing samples in update mode:",
        skipped_all="Skipped PCF import in update mode because PCF segment overlays already exist for",
    )


@_dataset_importer("repeats_trgt")
async def _import_repeats_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts, conflict_mode = job.sample_contexts, job.conflict_mode
    if conflict_mode == "update":
        existing_count = await _repeat_expansion_count(session, sample_contexts=sample_contexts)
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped TRGT repeat import in update mode because repeat expansions already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )
    family_vcf_path = _resolve_package_path(bundle.root, dataset.family_vcf)
    if family_vcf_path is not None:
        async with _local_upload(family_vcf_path) as upload:
            text_value = await decode_repeat_upload_text(upload)
            result = await ingest_family_trgt_text(
                session,
                sample_contexts=sample_contexts,
                text_value=text_value,
                metadata={
                    "source": "trgt_family",
                    "filename": family_vcf_path.name,
                    "uploaded_from": "family_package",
                    "family_vcf": _display_path(bundle.root, family_vcf_path),
                },
            )
        return summary.model_copy(
            update={
                "status": "imported",
                "message": "Imported family TRGT VCF through existing repeat-expansion storage",
                "summary": result,
            }
        )
    if not dataset.per_sample:
        return await _register_only(
            summary,
            "Registered only; no family VCF or per-sample TRGT files were provided",
        )
    entries: list[tuple[str, SampleMetadataContext, Path, str | None]] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("file") or raw_entry.get("vcf"))
        if vcf_path is None:
            continue
        declared = raw_entry.get("vcf_sample") or raw_entry.get("sample_name")
        entries.append((sample_id, sample_context, vcf_path, declared))

    def metadata_of(vcf_path: Path) -> dict[str, Any]:
        return {"source": "trgt", "filename": vcf_path.name, "uploaded_from": "family_package"}

    # Every sample's file is read whole, and refused or accepted, before any sample's calls
    # are replaced: a later sample's refused file failed the dataset with the earlier
    # samples' calls already replaced (SAFE-3). Each file is read again to be stored, so only
    # one sample's calls are held at a time: a genome-wide TRGT catalogue is large.
    for _sample_id, sample_context, vcf_path, declared in entries:
        async with _local_upload(vcf_path) as upload:
            text_value = await decode_repeat_upload_text(upload)
        await read_trgt_sample_calls(
            session,
            sample_context=sample_context,
            text_value=text_value,
            metadata=metadata_of(vcf_path),
            declared=declared,
        )
    sample_results: dict[str, Any] = {}
    for sample_id, sample_context, vcf_path, declared in entries:
        async with _local_upload(vcf_path) as upload:
            text_value = await decode_repeat_upload_text(upload)
            # The sample's stored calls are deleted once its file has been read whole: a
            # file with no record for it fails the dataset with them as they were (SAFE-29).
            sample_results[sample_id] = await ingest_trgt_text(
                session,
                sample_context=sample_context,
                text_value=text_value,
                metadata=metadata_of(vcf_path),
                declared=declared,
                replace=True,
            )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Imported sample-scoped TRGT files through existing repeat-expansion loader",
            "summary": sample_results,
        }
    )


@_dataset_importer("paraphase")
async def _import_paraphase_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts, conflict_mode = job.sample_contexts, job.conflict_mode
    sample_results: dict[str, Any] = {}
    # Every sample's JSON is read, and refused or accepted, before any sample's results are
    # replaced, as a package's TRGT files are (SAFE-3): a later sample's refused JSON failed
    # the dataset with the earlier samples' results already replaced (DATA-7).
    checked: list[tuple[str, SampleMetadataContext, Path, list[dict[str, Any]]]] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        # Paraphase results are stored in Postgres: the table read here is their store.
        if conflict_mode == "update" and (
            existing_count := await _paraphase_count(session, sample_context=sample_context)
        ):
            sample_results[sample_id] = {"skipped": True, "existing": existing_count}
            job.skipped_samples.add(sample_id)
            continue
        json_path = _resolve_package_path(bundle.root, raw_entry.get("json"))
        if json_path is None:
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Paraphase JSON for {sample_id} must contain an object")
        try:
            rows = _paraphase_rows_for_sample(
                sample_context=sample_context,
                path=json_path,
                payload=payload,
            )
        except ParaphaseValueError as exc:
            # Refused as a TRGT value that cannot be read as a number is (#985): a copy number
            # its integer column cannot hold failed the insert with a database error, after the
            # sample's results were deleted (DATA-7).
            raise RuntimeError(
                f"Paraphase JSON for {sample_id} holds a value that cannot be read where a number "
                f"is expected ({exc}); nothing was stored"
            ) from exc
        if not rows:
            # Paraphase reports every region it analysed: a JSON without one is no result.
            # It used to replace the sample's results with none (SAFE-29).
            raise RuntimeError(
                f"Paraphase JSON for {sample_id} holds no gene result; its results were not replaced"
            )
        checked.append((sample_id, sample_context, json_path, rows))
    for sample_id, sample_context, json_path, rows in checked:
        await _replace_sample_paraphase_rows(
            session,
            sample_context=sample_context,
            rows=rows,
        )
        sample_results[sample_id] = {
            "genes": len(rows),
            "filename": json_path.name,
        }
    return _sample_by_sample_outcome(
        summary,
        sample_results,
        skipped=[sample_id for sample_id, stats in sample_results.items() if _is_update_skip(stats)],
        imported_any=any(not _is_update_skip(stats) for stats in sample_results.values()),
        imported="Imported Paraphase JSON into sample paraphase result storage",
        partly="Imported Paraphase JSON; skipped existing samples in update mode:",
        skipped_all="Skipped Paraphase import in update mode because Paraphase results already exist for",
    )


async def _record_mtdna_sample_metadata(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    annotations: Any,
) -> None:
    """Store the sample's mtDNA haplogroup on the sample record.

    The mtDNA workspace reads ``samples.metadata["mtdna"]["haplogroup"]`` and otherwise
    reports that no haplogroup is available. mutserve assigns haplogroups per *variant*
    (each row lists the Phylotree clades that variant defines), so the sample-level
    haplogroup is taken as the most frequently reported one across the callset.
    """
    if annotations is None or annotations.conn is None:
        return
    counts: dict[str, int] = {}
    for (payload,) in annotations.conn.execute("SELECT annotation_json FROM annotations"):
        try:
            entry = json.loads(payload)
        except json.JSONDecodeError:
            continue
        haplogroup = entry.get("haplogroup")
        if not haplogroup:
            continue
        for token in str(haplogroup).split(","):
            name = token.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return
    haplogroup = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    await record_sample_mtdna_metadata(
        session,
        sample_context=sample_context,
        mtdna={"haplogroup": haplogroup, "haplogroup_support": counts[haplogroup]},
    )


def _autosomal_median_depth(path: Path) -> float | None:
    """Autosomal median of a depth bigWig, for normalising it to a log2 ratio.

    Its own pass over the file: the median has to be known before the first row is
    written, and a second streaming pass costs well under a second even for a
    whole-genome track.
    """

    reader = open_bigwig(path)
    try:
        return autosomal_median(reader)
    finally:
        reader.close()


def _log2_copy_number_transform(copy_number: float) -> float:
    """Integer copy number -> log2 ratio against a diploid baseline.

    CN 2 -> 0, CN 1 -> -1, CN 4 -> +1: the same scale the ratio-reporting callers
    use, so a HiFiCNV segment can be read against a WisecondorX or QDNAseq one and
    against the coverage bins underneath it.

    A homozygous deletion is CN 0, and log2(0) is -inf; it takes the same floor as
    the depth track, which sits far below any single-copy loss.
    """

    if copy_number <= 0:
        return _MIN_LOG2_RATIO
    return max(log2(copy_number / 2.0), _MIN_LOG2_RATIO)


def _log2_ratio_transform(normaliser: float | None) -> Callable[[float], float] | None:
    """Convert read depth to log2(depth / ``normaliser``), or don't convert at all.

    Returns ``None`` when there is no usable normaliser, which leaves the values
    raw rather than inventing a baseline -- a track drawn against a made-up
    reference would look like a genome-wide gain or loss. The track's record then
    says ``normalization: none``, so nothing reads the raw values as a log2 ratio.
    """

    if not normaliser or normaliser <= 0:
        return None

    def transform(value: float) -> float:
        # log2(0) is -inf, which ClickHouse cannot store and no axis can draw. The
        # floor also catches the near-zero depths a smoothed assembly gap leaves
        # behind (0.0005x against a 20x median is -15.3), which would otherwise
        # stretch the plotted range by an order of magnitude to show nothing.
        if value <= 0:
            return _MIN_LOG2_RATIO
        return max(log2(value / normaliser), _MIN_LOG2_RATIO)

    return transform


# A 2^-10 floor: far below any real single-copy loss (-1), so anything at or below
# it reads as "no coverage" rather than as a measurement.
_MIN_LOG2_RATIO = -10.0


async def _import_interval_track_unless_present(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    conflict_mode: str,
    importer: Callable[..., Awaitable[dict[str, int]]],
) -> dict[str, Any]:
    """Run ``importer`` unless update mode must leave existing rows alone. ``importer`` is
    one of the track importers with its file bound, which takes ``check_only``.

    ``update`` means "add what is missing, touch nothing that is there"; the
    importers themselves always replace their (track_type, source, filename)
    triple, so the guard has to sit outside them. Shared by the WisecondorX and
    QDNAseq bins and segments and the three HiFiCNV signal tracks, which differ
    only in what they read and where it lands. What is there is what the store
    holds (``_stored_track_rows``): Postgres's record of a track can outlive the
    rows a failed overwrite's restore deleted, and an update imports such a track
    again (SAFE-20).

    When the import does run, the whole (track_type, source) pair is cleared
    first rather than just the incoming filename. A source owns a track for a
    sample; a filename-scoped replace silently accumulates rows when the file
    that feeds a track changes. That is not hypothetical -- HiFiCNV's `coverage`
    track used to be fed by the copy-number bedgraph and is now fed by the depth
    bigWig, so a filename-scoped replace would leave the old copy-number rows
    behind to be averaged into the new depth values. It is cleared when Postgres
    records the track, which also removes a record whose rows are gone, or when the
    store holds rows of it that no record names: such rows used to survive beside the
    new ones (SAFE-18).

    Before that, ``importer`` reads its file up to the first row it stores
    (``check_only``): a file without one, or one it cannot read, is refused there
    (``EmptyTrackFileError``), and the dataset fails with the track as it was (SAFE-29).
    It used to clear the track and store none, the dataset reading as imported.
    """

    if conflict_mode == "update":
        stored = await _stored_track_rows(sample_context, track_type=track_type, source=source)
        if stored:
            return {"skipped": True, "existing": stored}
    await importer(check_only=True)
    existing = await _interval_track_count(
        session,
        sample_context=sample_context,
        track_type=track_type,
        source=source,
    ) or await _stored_track_rows(sample_context, track_type=track_type, source=source)
    if existing:
        await _delete_sample_interval_source(
            session,
            sample_context=sample_context,
            track_type=track_type,
            source=source,
        )
    return await importer()


async def _check_track_unless_present(
    *,
    sample_context: SampleMetadataContext,
    track_type: str,
    source: str,
    conflict_mode: str,
    importer: Callable[..., Awaitable[dict[str, int]]],
) -> None:
    """``importer``'s file read up to its first row (``check_only``), as
    ``_import_interval_track_unless_present`` reads it before it clears the track, unless an
    update leaves the track as stored. A per-sample dataset checks every sample's files so
    before it clears any sample's track: a later sample's file without a row failed the
    dataset with the earlier samples' tracks replaced (SAFE-3)."""
    if conflict_mode == "update" and await _stored_track_rows(
        sample_context, track_type=track_type, source=source
    ):
        return
    await importer(check_only=True)


def _signal_track_entry(bundle: FamilyPackageBundle, paths: dict[str, Path | None]) -> dict[str, Any]:
    """Where a sample's signal files sit, for the browser to stream: their
    package-relative paths and, for a package imported from a bucket, the objects' URIs
    under ``uris``. The staged copies are deleted after the import, so the endpoint
    serves those objects (routers/signal_tracks.py). Only files the package holds are
    recorded; staging downloads these, so a staged copy shows that the object exists."""
    entry: dict[str, Any] = {
        kind: _display_path(bundle.root, path)
        for kind, path in paths.items()
        if path is not None and path.is_file()
    }
    if entry and bundle.source_uri:
        entry["uris"] = {
            kind: join_remote_uri(bundle.source_uri, relative) for kind, relative in entry.items()
        }
    return entry


@_dataset_importer("cnv")
async def _import_cnv_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Import depth-based CNV calls (HiFiCNV) as structural variants, plus the
    caller's per-bin copy-number track."""
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context, sample_contexts = job.family_context, job.sample_contexts
    conflict_mode = job.conflict_mode
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    if not dataset.per_sample:
        return await _register_only(summary, "Registered only; CNV dataset has no per_sample entries")
    if conflict_mode == "update":
        existing_count = await count_family_structural_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=CNV_SOURCE,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped CNV import in update mode because CNV calls already exist for this family",
                    "summary": {"existing": existing_count},
                }
            )

    from .annotation_manifest_service import merge_vcf_header_provenance
    from .vcf_header_provenance import extract_header_provenance, merge_module_maps

    records: list[Any] = []
    sample_results: dict[str, Any] = {}
    # HiFiCNV states its version in its VCF header (``##source=HiFiCNV v1.0.1-…``): it goes
    # into the family's annotation manifest, as the other callers' versions do (TRACE-8).
    cnv_modules: dict[str, dict[str, Any]] = {}
    # Every sample's VCF is read and checked (its column, its records), and each of its
    # signal files up to its first value, before anything of the dataset is written: a later
    # sample's refused file failed the dataset with the earlier samples' tracks already
    # replaced (SAFE-3). The calls are few, and kept.
    entries: list[tuple[str, SampleMetadataContext, dict[str, Any]]] = []
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if vcf_path is None:
            continue
        text_value = _read_package_text(vcf_path)
        cnv_modules = merge_module_maps(
            cnv_modules,
            extract_header_provenance(
                [line for line in text_value.splitlines() if line.startswith("#")], modality="cnv"
            ).as_modules(),
        )
        vcf_columns = next(
            (
                line.rstrip("\r").split("\t")[9:]
                for line in text_value.splitlines()
                if line.startswith("#CHROM")
            ),
            [],
        )
        # HiFiCNV names its column after its own sample slot (``Sample0``), which binds to
        # the entry's sample; a column naming another sample fails the dataset.
        sample_column = per_sample_vcf_column(
            vcf_columns,
            target_sample_id=sample_id,
            known_sample_ids=await known_vcf_sample_ids(
                session,
                family_uuid=family_context.family_uuid,
                header_samples=vcf_columns,
            ),
            declared=raw_entry.get("vcf_sample") or raw_entry.get("sample_name"),
            label=f"CNV VCF of {sample_id}",
            remedy=_PER_SAMPLE_COLUMN_REMEDY,
        )
        skipped = SkippedRecords()
        sample_records = _iter_cnv_structural_records(
            text_value,
            sample_id=sample_id,
            source=CNV_SOURCE,
            sample_column=sample_column,
            skipped=skipped,
        )
        if not sample_records and skipped.without_extent:
            # Records were called, none of them readable: no answer that no CNV was called,
            # which would remove the sample's earlier calls.
            raise RuntimeError(
                f"No HiFiCNV call was replaced: the CNV VCF of {sample_id} ({vcf_path.name}) "
                f"holds no call it can read{_without_extent_clause(skipped)}"
            )
        records.extend(sample_records)
        sample_results[sample_id] = {
            "calls": len(sample_records),
            "filename": vcf_path.name,
            **_without_extent_count(skipped),
        }
        for role, track_type, reader in (
            ("copy_number_bedgraph", "segments", _import_copy_number_track),
            ("depth_bigwig", "coverage", partial(_import_bigwig_interval_track, skip_zero=True)),
            ("maf_bigwig", "apcad", _import_bigwig_interval_track),
        ):
            signal_path = _resolve_package_path(bundle.root, raw_entry.get(role))
            if signal_path is None:
                continue
            await _check_track_unless_present(
                sample_context=sample_context,
                track_type=track_type,
                source=CNV_SOURCE,
                conflict_mode=conflict_mode,
                importer=partial(
                    reader,
                    session,
                    sample_context=sample_context,
                    path=signal_path,
                    track_type=track_type,
                    source=CNV_SOURCE,
                ),
            )
        entries.append((sample_id, sample_context, raw_entry))

    for sample_id, sample_context, raw_entry in entries:
        # HiFiCNV ships two signal files per sample and they measure different
        # things, so they land on different tracks:
        #
        #   depth.bw          read depth per 2 kb bin  -> `coverage`
        #   copynum.bedgraph  called integer copy number -> `segments`
        #
        # Before this split the bedgraph was the `coverage` track, which put copy
        # number on an axis every other caller uses for depth or log2 ratio -- so
        # a HiFiCNV track could not be compared with the WisecondorX or QDNAseq
        # track stacked beside it.
        bedgraph_path = _resolve_package_path(bundle.root, raw_entry.get("copy_number_bedgraph"))
        if bedgraph_path is not None:
            sample_results[sample_id]["copy_number_track"] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type="segments",
                source=CNV_SOURCE,
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_copy_number_track,
                    session,
                    sample_context=sample_context,
                    path=bedgraph_path,
                    track_type="segments",
                    source=CNV_SOURCE,
                    # The bedGraph holds an integer copy number; the segments track
                    # holds a log2 ratio, which is what WisecondorX and QDNAseq write
                    # and what the chart's axis is calibrated for. Stored raw, a
                    # normal CN of 2 plotted above the top of a +-1.5 axis and drew a
                    # solid line across the genome at the clip boundary.
                    value_transform=_log2_copy_number_transform,
                    extra_metadata={"normalization": "log2_ratio_to_diploid"},
                ),
            )

        depth_path = _resolve_package_path(bundle.root, raw_entry.get("depth_bigwig"))
        if depth_path is not None:
            # Stored as a log2 ratio against the sample's own autosomal median, not as
            # raw depth. The coverage track is drawn on one axis per sample and
            # compared with the WisecondorX and QDNAseq tracks stacked beside it,
            # which are themselves log2 ratios; a 1-copy loss then sits at -1 in all
            # three instead of at a depth that means nothing without knowing the
            # sample's own baseline. The raw bigWig is untouched and is what IGV gets.
            # A file without autosomal depth gives no median, and its depths are stored
            # as read rather than against an invented baseline (_log2_ratio_transform).
            normaliser = await asyncio.to_thread(_autosomal_median_depth, depth_path)
            depth_transform = _log2_ratio_transform(normaliser)
            sample_results[sample_id]["depth_track"] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type="coverage",
                source=CNV_SOURCE,
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_bigwig_interval_track,
                    session,
                    sample_context=sample_context,
                    path=depth_path,
                    track_type="coverage",
                    source=CNV_SOURCE,
                    # A depth bigWig spans the whole genome, so ~half its bins are
                    # the zero-depth telomeric/centromeric gaps. They plot as a
                    # flat line on the axis and cost ~600k rows per sample.
                    skip_zero=True,
                    value_transform=depth_transform,
                    # The normaliser is part of what the stored numbers mean, so it is
                    # recorded with the track rather than left to be re-derived. The record
                    # names the transform the stored values went through, not the one
                    # intended: depths stored as read record `none`, which the views read
                    # as not a log2 ratio (TRACE-7). The track once recorded a log2 ratio
                    # with a null median over raw depths, and was drawn as one.
                    extra_metadata={
                        "normalization": (
                            "log2_ratio_to_autosomal_median" if depth_transform is not None else "none"
                        ),
                        "autosomal_median_depth": normaliser,
                    },
                ),
            )

        # Minor allele fraction is BAF-like, so it belongs on the APCAD track the
        # views already draw. It carries no parent-of-origin -- bigWig has nowhere
        # to record one -- hence `und`, which the readers treat as unphased rather
        # than as a missing paternal/maternal call.
        maf_path = _resolve_package_path(bundle.root, raw_entry.get("maf_bigwig"))
        if maf_path is not None:
            sample_results[sample_id]["maf_track"] = await _import_interval_track_unless_present(
                session,
                sample_context=sample_context,
                track_type="apcad",
                source=CNV_SOURCE,
                conflict_mode=conflict_mode,
                importer=partial(
                    _import_bigwig_interval_track,
                    session,
                    sample_context=sample_context,
                    path=maf_path,
                    track_type="apcad",
                    source=CNV_SOURCE,
                    origin="und",
                ),
            )
        if any(_is_update_skip(stats) for stats in sample_results[sample_id].values()):
            # Its calls are imported again, but not the tracks an update left as stored.
            job.skipped_samples.add(sample_id)

        # Where the signal files sit, for the browser to stream directly. Recorded
        # rather than re-derived at serve time: HiFiCNV names them after its own run
        # (`HG002.Sample0.depth.bw`, `HG002.HG002.maf.bw`), which no fixed path
        # pattern predicts. Recorded whether or not the file produced ClickHouse
        # rows -- the binned track and the file IGV streams are different artefacts,
        # and the raw depth here is absolute where the binned copy is a log2 ratio.
        signal_tracks = _signal_track_entry(
            bundle,
            {
                "depth_bigwig": depth_path,
                "maf_bigwig": maf_path,
                "copy_number_bedgraph": bedgraph_path,
            },
        )
        if signal_tracks:
            await _record_sample_signal_tracks(
                session,
                sample_context=sample_context,
                signal_tracks={CNV_SOURCE: signal_tracks},
            )

    if not sample_results:
        return await _register_only(summary, "Registered only; no CNV VCF of a family sample could be found")
    # A VCF that calls no CNV is the caller's answer for its sample, not a file that carries
    # nothing: the dataset is imported, its tracks with it, and the family's earlier HiFiCNV
    # calls go, as a VCF with calls replaces them. It used to end registered, its tracks not
    # recorded as imported and the earlier calls left in place (SAFE-18).
    # Held until the commit that records the SV files (the import holds it for its whole run).
    await lock_family_variant_writes(
        session,
        family_context.family_uuid,
        [STRUCTURAL_VARIANTS],
        samples=[sample.sample_uuid for sample in sample_contexts.values()],
    )
    # Replace only this source: a family can carry NeedlR SVs and HiFiCNV calls at once. One
    # event in two samples' VCFs is one SV with both calls: stored as two records under one
    # id, a part merge kept one of them (CLIN-39).
    await replace_family_structural_variants(
        family_context.assembly_name,
        family_context.family_uuid,
        family_context.project_ids,
        _merge_sv_records_by_id(records),
        source=CNV_SOURCE,
    )
    await _update_sv_file_metadata(
        session,
        sample_contexts=sample_contexts,
        source=CNV_SOURCE,
        filename=", ".join(
            str(result.get("filename")) for result in sample_results.values() if result.get("filename")
        ),
    )
    await merge_vcf_header_provenance(
        session,
        family_uuid=family_context.family_uuid,
        assembly_id=getattr(family_context, "assembly_id", None),
        modules=cnv_modules,
        modality="cnv",
    )
    skipped_tracks = [
        f"{sample_id}:{track}"
        for sample_id, result in sample_results.items()
        for track, stats in result.items()
        if _is_update_skip(stats)
    ]
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Imported CNV calls into structural variant storage"
                if records
                else "Imported the CNV VCFs, which call no CNV"
            )
            + (
                f"; skipped existing tracks in update mode: {', '.join(skipped_tracks)}"
                if skipped_tracks
                else ""
            )
            + _without_extent_note(
                SkippedRecords(
                    without_extent=sum(
                        int(result.get("skipped_without_extent") or 0) for result in sample_results.values()
                    )
                )
            ),
            "summary": {"processed": len(records), "source": CNV_SOURCE, "samples": sample_results},
        }
    )


@_dataset_importer("mito")
async def _import_mito_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Import mitochondrial calls.

    chrM SNVs go into the ordinary small-variant store under a dedicated ``mito``
    source, which is what the existing mtDNA workspace reads (it queries by
    chromosome, not source) and what keeps a nuclear re-import from deleting them.
    The mutserve annotation TSV supplies heteroplasmy and haplogroup context.

    Each sample's file replaces that sample's calls only: every other sample's stay as
    stored, one row per variant holding every sample's call, which is what the maternal
    transmission reads. A file without a variant record (a header alone, records none of
    which can be read, nothing at all) is a failed analysis, not a sample without
    mitochondrial variants: every sample's file is checked for a record before any call is
    replaced, and one without fails the dataset with the family's mitochondrial calls as
    they were (``_mito_files_without_a_record``). It used to remove the sample's
    earlier calls and end ``imported``, which counts as importing the dataset again
    (SAFE-29, the owner's decision).

    The chrM SV calls (``sv_vcf``, Sniffles2 ``--mosaic``) go into the structural-variant
    store under their own ``mito_sv`` source, where the mtDNA workspace and the SV views
    read them. Discovery and validation always accepted the file, but nothing read it:
    a large heteroplasmic mtDNA deletion was dropped without a word.

    Only the sample's own column is read (``per_sample_vcf_column``), in the chrM VCF and
    in the chrM SV VCF alike: a file whose column names another sample -- a family member,
    or another family's -- fails the dataset before anything of it is written, unless the
    entry's ``vcf_sample`` confirms it, and a joint chrM VCF gives each member's entry that
    member's calls only.
    """
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context, sample_contexts = job.family_context, job.sample_contexts
    conflict_mode = job.conflict_mode
    if not family_context.assembly_name:
        return await _register_only(summary, "Registered only; family is not linked to a single assembly")
    if not dataset.per_sample:
        return await _register_only(summary, "Registered only; mito dataset has no per_sample entries")
    if conflict_mode == "update":
        existing_count = await count_family_small_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            project_ids=family_context.project_ids,
            source=MITO_SOURCE,
        )
        if existing_count:
            return summary.model_copy(
                update={
                    "status": "skipped",
                    "message": "Skipped mito import in update mode because mitochondrial variants already exist",
                    "summary": {"existing": existing_count},
                }
            )

    # Every file's sample column -- the chrM calls and the chrM SVs of every sample -- is
    # checked before anything is written, so a refused file fails the dataset with none
    # of it stored (it used to be checked file by file, after the earlier files' writes).
    columns = await _mito_sample_columns(job)
    # And that every chrM VCF holds a record: a file without one fails the dataset before
    # any sample's calls are replaced (SAFE-29).
    empty = _mito_files_without_a_record(job, columns)
    if empty:
        raise RuntimeError(
            "No mitochondrial call was replaced: "
            + "; ".join(
                f"the chrM VCF of {sample_id} ({name}) holds no variant record" for sample_id, name in empty
            )
        )
    # And every chrM SV VCF is read before any chrM call is replaced: a file that cannot be
    # read, or one whose records all lie on other chromosomes, failed the dataset after its
    # SNV files were written, or removed the sample's chrM SVs as if it called none (SAFE-3).
    sv_read = _read_mito_structural_variants(
        job,
        sv_columns={
            sample_id: int(entry["sv_vcf"]) for sample_id, entry in columns.items() if "sv_vcf" in entry
        },
    )
    sample_results: dict[str, Any] = {}
    imported_any = False
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if vcf_path is None:
            continue
        sample_column = str(columns[sample_id]["vcf"])
        aliases = {sample_column: sample_id}
        # The annotation is an SQLite file in the temporary directory, which on Cloud Run is
        # memory. The upload leaves it open for the haplogroup read below, and the finally
        # removes it whatever happens.
        annotation_path = _resolve_package_path(bundle.root, raw_entry.get("annotation_tsv"))
        mutserve_annotations = (
            parse_mutserve_annotation_path(annotation_path) if annotation_path is not None else None
        )
        try:
            # A file whose records the check above passed but the loader stores none of
            # (400) fails the dataset here, as any refused file does.
            async with _local_upload(vcf_path) as upload:
                result = await upload_family_small_variant_file(
                    session,
                    context=family_context,
                    sample_contexts=sample_contexts,
                    file=upload,
                    overwrite=True,
                    format_hint=MITO_SOURCE,  # type: ignore[arg-type]
                    sample_aliases=aliases,
                    sample_column=sample_column,
                    vep_annotations=mutserve_annotations,
                    overwrite_scope="samples",
                )
            imported_any = True
            sample_results[sample_id] = result
            await _record_mtdna_sample_metadata(
                session,
                sample_context=sample_context,
                annotations=mutserve_annotations,
            )
        finally:
            if mutserve_annotations is not None:
                mutserve_annotations.close()

    sv_summary = await _import_mito_structural_variants(job, sv_read)
    if sv_summary is not None:
        sample_results = {**sample_results, "structural_variants": sv_summary}
    sv_count = int(sv_summary["processed"]) if sv_summary else 0
    if not imported_any and not sv_count:
        return summary.model_copy(
            update={
                "status": "imported",
                "message": "No mitochondrial variants were called for this family",
                "summary": sample_results,
            }
        )
    message = "Imported mitochondrial variants into small-variant storage" if imported_any else (
        "No mitochondrial small variants were called for this family"
    )
    if sv_count:
        message += f"; {sv_count} chrM structural variant(s) into structural-variant storage"
    if sv_summary:
        message += _without_extent_note(
            SkippedRecords(
                without_extent=sum(
                    int(result.get("skipped_without_extent") or 0)
                    for result in (sv_summary.get("samples") or {}).values()
                    if isinstance(result, dict)
                )
            )
        )
    return summary.model_copy(
        update={"status": "imported", "message": message, "summary": sample_results}
    )


def _vcf_holds_a_record(text_value: str) -> bool:
    """Whether a one-sample VCF's text holds a record the small-variant loader stores
    (``upload_family_small_variant_file``): a data line with the nine fixed columns and a
    sample column for each one its ``#CHROM`` line names, at a position the POS column holds.
    The loader reads the same lines; this reads them up to the first such record."""
    header_columns: int | None = None
    for line in text_value.splitlines():
        if line.startswith("#CHROM"):
            header_columns = len(line.rstrip().split("\t")[9:])
            continue
        if not line or line.startswith("#"):
            continue
        fields = line.rstrip().split("\t")
        if len(fields) < 10:
            continue
        if header_columns is not None and len(fields) - 9 != header_columns:
            continue
        if column_int(fields[1], UINT32) is None:
            continue
        return True
    return False


def _mito_files_without_a_record(
    job: DatasetImportJob, columns: dict[str, dict[str, str | int]]
) -> list[tuple[str, str]]:
    """Each (sample, file name) of a ``mito`` entry whose chrM VCF holds no record the loader
    stores (``_vcf_holds_a_record``), checked before any sample's calls are replaced; each file
    is read whole, as a chrM VCF is small (the loader reads it again).

    The chrM callers report every mitochondrial variant of a sample against the reference
    (rCRS), and practically every person carries some, so a file without a record is an
    analysis that failed or a file cut short, not a sample without mitochondrial variants
    (the owner's decision, SAFE-29). Such a file used to remove the sample's earlier calls."""
    empty: list[tuple[str, str]] = []
    for sample_id, raw_entry in (job.dataset.per_sample or {}).items():
        if sample_id not in columns or not isinstance(raw_entry, dict):
            continue
        vcf_path = _resolve_package_path(job.bundle.root, raw_entry.get("vcf") or raw_entry.get("file"))
        if vcf_path is None:
            continue
        if not _vcf_holds_a_record(_read_package_text(vcf_path)):
            empty.append((sample_id, vcf_path.name))
    return empty


async def _mito_sample_columns(job: DatasetImportJob) -> dict[str, dict[str, str | int]]:
    """Each mito entry's sample column, checked by ``per_sample_vcf_column``: the chrM
    VCF's column name (``vcf``) and the chrM SV VCF's column index (``sv_vcf``).

    The entry's ``vcf_sample`` confirms the SV file only where it names that file's column
    or the entry's own sample; it was written for the chrM VCF, whose column the SV caller
    may name differently."""
    bundle, family_context = job.bundle, job.family_context
    columns: dict[str, dict[str, str | int]] = {}
    for sample_id, raw_entry in (job.dataset.per_sample or {}).items():
        if sample_id not in job.sample_contexts or not isinstance(raw_entry, dict):
            continue
        declared = raw_entry.get("vcf_sample") or raw_entry.get("sample_name")
        entry: dict[str, str | int] = {}
        for role, label in (("vcf", "Mito VCF"), ("sv_vcf", "Mito SV VCF")):
            value = raw_entry.get(role) or (raw_entry.get("file") if role == "vcf" else None)
            path = _resolve_package_path(bundle.root, value)
            if path is None:
                continue
            header = read_vcf_sample_columns(path)
            confirmed = declared if role == "vcf" or (declared and (declared in header or declared == sample_id)) else None
            index = per_sample_vcf_column(
                header,
                target_sample_id=sample_id,
                known_sample_ids=await known_vcf_sample_ids(
                    job.session,
                    family_uuid=family_context.family_uuid,
                    header_samples=header,
                ),
                declared=confirmed,
                label=f"{label} of {sample_id}",
                remedy=_PER_SAMPLE_COLUMN_REMEDY,
            )
            entry[role] = header[index] if role == "vcf" else index
        columns[sample_id] = entry
    return columns


@dataclass
class _MitoStructuralVariantsRead:
    """Every sample's chrM SV calls, read before anything of the mito dataset is written."""

    records: list[Any]
    per_sample: dict[str, Any]
    header_lines: list[str]


def _records_off_chrM(text_value: str) -> int:
    """How many records of a VCF lie on another chromosome than chrM, or 0 when one lies
    on chrM: a chrM SV file holds the calls of chrM alone."""
    off = 0
    for line in text_value.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", 1)
        if normalize_chromosome(parts[0]).upper() in {"M", "MT"}:
            return 0
        off += 1
    return off


def _read_mito_structural_variants(
    job: DatasetImportJob,
    *,
    sv_columns: dict[str, int] | None = None,
) -> _MitoStructuralVariantsRead | None:
    """Each sample's chrM SV VCF, read and checked before any of the mito dataset is written;
    None when no sample declares one.

    A file with no call (no record on chrM, or none Sniffles called) is the sample's answer:
    no chrM SV. A file whose records all lie on other chromosomes is no chrM callset, and
    fails the dataset: read as one without a call, it removed the sample's chrM SVs (SAFE-3).
    """
    bundle, dataset = job.bundle, job.dataset
    if not job.family_context.assembly_name:
        return None
    records: list[Any] = []
    per_sample: dict[str, Any] = {}
    sv_lines: list[str] = []
    for sample_id, raw_entry in (dataset.per_sample or {}).items():
        if sample_id not in job.sample_contexts or not isinstance(raw_entry, dict):
            continue
        sv_path = _resolve_package_path(bundle.root, raw_entry.get("sv_vcf"))
        if sv_path is None:
            continue
        text_value = _read_package_text(sv_path)
        elsewhere = _records_off_chrM(text_value)
        if elsewhere:
            raise RuntimeError(
                f"No mitochondrial call was replaced: the chrM SV VCF of {sample_id} ({sv_path.name}) "
                f"holds {elsewhere} record(s), none on chrM, so it is no chrM SV callset"
            )
        skipped = SkippedRecords()
        sample_records = _iter_mito_sv_records(
            text_value, sample_id=sample_id, sample_column=(sv_columns or {}).get(sample_id, 0), skipped=skipped
        )
        if not sample_records and skipped.without_extent:
            # Records were called, none of them readable: no answer that the sample carries no
            # chrM SV, which would remove its earlier ones.
            raise RuntimeError(
                f"No mitochondrial call was replaced: the chrM SV VCF of {sample_id} ({sv_path.name}) "
                f"holds no call it can read{_without_extent_clause(skipped)}"
            )
        records.extend(sample_records)
        per_sample[sample_id] = {
            "calls": len(sample_records),
            "filename": sv_path.name,
            **_without_extent_count(skipped),
        }
        if not sv_lines:
            sv_lines = [line for line in text_value.splitlines() if line.startswith("#")]
    if not per_sample:
        return None
    return _MitoStructuralVariantsRead(records=records, per_sample=per_sample, header_lines=sv_lines)


async def _import_mito_structural_variants(
    job: DatasetImportJob,
    read: _MitoStructuralVariantsRead | None,
) -> dict[str, Any] | None:
    """Each sample's chrM SV calls, as ``_read_mito_structural_variants`` read them, into the
    SV store under the ``mito_sv`` source.

    None when no sample declares a file. Only the samples with a file are replaced; the
    others' calls stay as stored. A declared file with no call removes that sample's
    earlier chrM SVs.
    """
    session = job.session
    family_context, sample_contexts = job.family_context, job.sample_contexts
    if read is None or not family_context.assembly_name:
        return None
    records, per_sample, sv_lines = read.records, read.per_sample, read.header_lines
    merged = _merge_sv_records_by_id(records)
    # Held from the read below until the commit that records the SV files (the import
    # holds it for its whole run), so no other SV write falls between read and rewrite.
    await lock_family_variant_writes(
        session,
        family_context.family_uuid,
        [STRUCTURAL_VARIANTS],
        samples=[sample.sample_uuid for sample in sample_contexts.values()],
    )
    stored_rows = await fetch_family_structural_variant_rows(
        family_context.assembly_name,
        family_context.family_uuid,
        source=MITO_SV_SOURCE,
    )
    replaced_samples = {
        value
        for sample_id in per_sample
        for value in (sample_id, sample_contexts[sample_id].sample_uuid)
        if value
    }
    rows = _mito_sv_rows_after_import(
        stored_rows,
        merged,
        replaced_samples=replaced_samples,
        project_ids=list(family_context.project_ids),
    )
    try:
        # The rewrite deletes every sample's chrM SVs before it writes them back, and puts
        # the stored rows back when it fails after that delete (SAFE-28): an update takes no
        # snapshot of the family.
        await rewrite_family_structural_variants(
            family_context.assembly_name,
            family_context.family_uuid,
            rows,
            source=MITO_SV_SOURCE,
            stored=stored_rows,
        )
    except StructuralVariantsNotRestored as exc:
        # The chrM SVs of the samples this dataset does not name may be missing too: the
        # dataset is flagged for them as well.
        raise DatasetLeftMissing(
            "The chrM structural variants could not be written, nor put back as they were: "
            "those of every sample may be partly written or missing",
            samples={
                family_context.sample_uuid_to_name.get(call.sample, call.sample)
                for row in (*stored_rows, *rows)
                for call in row.record.calls
            },
        ) from exc
    await _update_sv_file_metadata(
        session,
        sample_contexts={sample_id: sample_contexts[sample_id] for sample_id in per_sample},
        source=MITO_SV_SOURCE,
        filename=", ".join(str(entry["filename"]) for entry in per_sample.values()),
    )
    from .annotation_manifest_service import merge_vcf_header_provenance
    from .vcf_header_provenance import extract_header_provenance

    await merge_vcf_header_provenance(
        session,
        family_uuid=family_context.family_uuid,
        assembly_id=getattr(family_context, "assembly_id", None),
        modules=extract_header_provenance(sv_lines, modality="sv").as_modules(),
        modality="sv",
    )
    return {"processed": len(merged), "source": MITO_SV_SOURCE, "samples": per_sample}


@_dataset_importer("qc")
async def _import_qc_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Record sequencing QC.

    The read-level numbers (NanoPlot/NanoStats) and per-chromosome depth (mosdepth)
    are parsed at import and stored on the sample, so the workspace shows them without
    re-reading pipeline output. The rendered HTML report is recorded by path only --
    it is untrusted pipeline output and is never inlined into the application.

    The PGT pipeline (nf-cmgg/copgtm) adds, per sample, a Qualimap summary, a mean
    coverage and an ngs-bits sex check, and per family the tables covering every embryo
    (allele drop-out/drop-in, Mendelian concordance before and after imputation) and the
    KING kinship of every pair. The embryo rows go onto each embryo's QC; the kinship
    table onto the family.
    """
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts = job.sample_contexts
    extra = dataset.model_extra or {}
    family_tables = _read_family_qc_tables(bundle.root, extra)
    pgt_metrics = _pgt_metrics_by_sample(family_tables)
    if not dataset.per_sample and not family_tables:
        return await _register_only(summary, "Registered only; QC dataset has no per_sample entries")
    sample_results: dict[str, Any] = {}
    sample_ids = list(dict.fromkeys([*dataset.per_sample, *pgt_metrics]))
    for sample_id in sample_ids:
        sample_context = sample_contexts.get(sample_id)
        raw_entry = dataset.per_sample.get(sample_id, {})
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        metrics = _sample_qc_metrics(bundle.root, sample_id, raw_entry)
        if sample_id in pgt_metrics:
            metrics["pgt"] = pgt_metrics[sample_id]
        if not metrics:
            continue
        await _record_sample_qc_metadata(
            session,
            sample_context=sample_context,
            metrics=metrics,
        )
        sample_results[sample_id] = metrics
    family_result: dict[str, Any] = {}
    kinship = family_tables.get("kinship")
    if kinship:
        family_result = {
            "kinship": kinship["pairs"],
            "files": {role: table["file"] for role, table in family_tables.items()},
        }
        await _record_family_pipeline_qc(
            session,
            family_uuid=job.family_context.family_uuid,
            pipeline_qc=family_result,
        )
    if not sample_results and not family_result:
        return await _register_only(summary, "Registered only; no QC artefacts were readable")
    return summary.model_copy(
        update={
            "status": "imported",
            "message": (
                "Recorded sequencing QC metrics and report location on each sample"
                + ("; the pipeline's kinship table on the family" if family_result else "")
            ),
            "summary": {
                **sample_results,
                # Beside the per-sample entries, under a key no sample id takes.
                **({"pipeline_qc": {"kinship_pairs": len(family_result["kinship"])}} if family_result else {}),
            },
        }
    )


def _sample_qc_metrics(root: Path, sample_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """One sample's QC from its per-sample artefacts."""
    metrics: dict[str, Any] = {}
    read_stats_path = _resolve_package_path(root, entry.get("read_stats"))
    if read_stats_path is not None and read_stats_path.is_file():
        metrics["reads"] = parse_nanostats_text(
            read_path_text_bounded(read_stats_path, kind="NanoStats")
        )
    depth_summary_path = _resolve_package_path(root, entry.get("depth_summary"))
    if depth_summary_path is not None and depth_summary_path.is_file():
        metrics["depth"] = parse_mosdepth_summary_text(
            read_path_text_bounded(depth_summary_path, kind="mosdepth summary")
        )
    qualimap_path = _resolve_package_path(root, entry.get("qualimap_summary"))
    if qualimap_path is not None and qualimap_path.is_file():
        alignment = parse_qualimap_genome_results_text(
            read_path_text_bounded(qualimap_path, kind="Qualimap summary")
        )
        if alignment:
            metrics["alignment"] = alignment
    mean_coverage_path = _resolve_package_path(root, entry.get("mean_coverage"))
    mean_coverage: float | None = None
    if mean_coverage_path is not None and mean_coverage_path.is_file():
        values = parse_sample_value_csv(read_path_text_bounded(mean_coverage_path, kind="Mean coverage"))
        # The file is per sample; a single row under another name is still this sample's.
        mean_coverage = values.get(sample_id) if sample_id in values else (
            next(iter(values.values())) if len(values) == 1 else None
        )
    if mean_coverage is None:
        qualimap_mean = metrics.get("alignment", {}).get("mean_coverage")
        mean_coverage = qualimap_mean if isinstance(qualimap_mean, (int, float)) else None
    if mean_coverage is not None and "mean_depth" not in metrics.get("depth", {}):
        metrics.setdefault("depth", {})["mean_depth"] = mean_coverage
    sex_check_path = _resolve_package_path(root, entry.get("sex_check"))
    if sex_check_path is not None and sex_check_path.is_file():
        sex_check = parse_ngsbits_sample_gender_text(
            read_path_text_bounded(sex_check_path, kind="ngs-bits sex check")
        )
        if sex_check:
            metrics["sex_check"] = sex_check
    report_path = _resolve_package_path(root, entry.get("report"))
    if report_path is not None and report_path.is_file():
        metrics["report"] = _display_path(root, report_path)
    return metrics


def _read_family_qc_tables(root: Path, extra: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The family-level QC tables a ``qc`` dataset declares, parsed, each with its
    package-relative file."""
    parsers: dict[str, tuple[str, Callable[[str], Any]]] = {
        "ado_adi": ("ADO/ADI summary", parse_ado_adi_text),
        "concordance": ("Mendelian concordance", parse_sample_value_csv),
        "imputed_concordance": ("Mendelian concordance", parse_sample_value_csv),
        "kinship": ("KING kinship", parse_king_kin0_text),
    }
    tables: dict[str, dict[str, Any]] = {}
    for role in QC_FAMILY_ROLES:
        path = _resolve_package_path(root, extra.get(role))
        if path is None or not path.is_file():
            continue
        kind, parser = parsers[role]
        parsed = parser(read_path_text_bounded(path, kind=kind))
        if parsed:
            key = "pairs" if role == "kinship" else "values"
            tables[role] = {key: parsed, "file": _display_path(root, path)}
    return tables


def _pgt_metrics_by_sample(tables: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
    """The per-embryo numbers of the family-level tables, keyed by embryo, under the
    metric names ``sequencing_qc.pgt`` holds (percentages, as the pipeline reports)."""
    metrics: dict[str, dict[str, float]] = {}
    for sample_id, rates in (tables.get("ado_adi", {}).get("values") or {}).items():
        metrics.setdefault(sample_id, {}).update(rates)
    for role, metric in (
        ("concordance", "mendelian_concordance"),
        ("imputed_concordance", "mendelian_concordance_imputed"),
    ):
        for sample_id, value in (tables.get(role, {}).get("values") or {}).items():
            metrics.setdefault(sample_id, {})[metric] = value
    return metrics


def _local_alignment_entry(
    root: Path, alignment_path: Path, index_path: Path | None
) -> dict[str, str] | None:
    if not alignment_path.is_file():
        return None
    entry = {
        "path": _display_path(root, alignment_path),
        "format": "cram" if alignment_path.name.endswith(".cram") else "bam",
    }
    if index_path is not None and index_path.is_file():
        entry["index_path"] = _display_path(root, index_path)
    return entry


def _remote_alignment_entry(
    root: Path, source_uri: str, alignment_path: Path, index_path: Path | None
) -> dict[str, str] | None:
    """The entry for a package staged from a bucket. Staging left the alignment in the
    store, so the store is asked whether it exists, and its URI is recorded: the staged
    copy of the package is deleted after the import. Blocking (store requests)."""
    relative = _display_path(root, alignment_path)
    uri = join_remote_uri(source_uri, relative)
    if not remote_object_exists(uri):
        return None
    entry = {
        "path": relative,
        "format": "cram" if alignment_path.name.endswith(".cram") else "bam",
        "uri": uri,
    }
    if index_path is not None:
        index_relative = _display_path(root, index_path)
        index_uri = join_remote_uri(source_uri, index_relative)
        if remote_object_exists(index_uri):
            entry.update(index_path=index_relative, index_uri=index_uri)
    return entry


@_dataset_importer("alignments")
async def _import_alignments_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Record each sample's aligned-reads file so the CRAM/IGV endpoint can find it.

    The alignment itself is never copied or re-read. The package-relative path is
    recorded on the sample; for a package imported from a bucket, so is the object's
    URI, which is where the endpoint looks first in gcs/s3 mode (routers/cram.py) --
    the package keeps its reads under ``bams/<sample>.cram`` below the import root,
    not where the endpoint's layout probes look. A local package's file is found by
    the endpoint's data-directory layouts, as before.
    """
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    sample_contexts = job.sample_contexts
    if not dataset.per_sample:
        return await _register_only(summary, "Registered only; alignments dataset has no per_sample entries")
    sample_results: dict[str, Any] = {}
    for sample_id, raw_entry in dataset.per_sample.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None or not isinstance(raw_entry, dict):
            continue
        alignment_path = _resolve_package_path(bundle.root, raw_entry.get("file"))
        if alignment_path is None:
            continue
        index_path = _resolve_package_path(bundle.root, raw_entry.get("index"))
        if bundle.source_uri:
            entry = await asyncio.to_thread(
                _remote_alignment_entry, bundle.root, bundle.source_uri, alignment_path, index_path
            )
        else:
            entry = _local_alignment_entry(bundle.root, alignment_path, index_path)
        if entry is None:
            continue
        await _record_sample_alignment_metadata(
            session,
            sample_context=sample_context,
            alignment=entry,
        )
        sample_results[sample_id] = entry
    if not sample_results:
        return await _register_only(summary, "Registered only; no alignment files were found")
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Recorded aligned-read file locations for the genome browser",
            "summary": sample_results,
        }
    )


@_dataset_importer("pipeline_info")
async def _import_pipeline_info_dataset(job: DatasetImportJob) -> FamilyImportDatasetSummary:
    """Capture the Nextflow run record into the family's annotation manifest.

    Every tool version behind the callset lives in ``software_versions.yaml``, and the
    run's parameters (reference build, callers, VEP cache, repeat catalogue) in
    ``params_*.json``. Recording them per family is what makes a released report
    traceable back to the exact pipeline that produced its evidence.
    """
    session, bundle, dataset, summary = job.session, job.bundle, job.dataset, job.summary
    family_context = job.family_context
    from .annotation_manifest_service import merge_vcf_header_provenance

    extra = dataset.model_extra or {}
    modules: dict[str, Any] = {}
    captured: dict[str, Any] = {}

    versions_path = _resolve_package_path(bundle.root, extra.get("versions"))
    if versions_path is not None and versions_path.is_file():
        modules = extract_pipeline_versions(
            read_path_text_bounded(versions_path, kind="Pipeline versions")
        )
        captured["versions"] = _display_path(bundle.root, versions_path)
        captured["modules"] = sorted(modules)

    params_path = _resolve_package_path(bundle.root, extra.get("params"))
    parameters: dict[str, Any] = {}
    if params_path is not None and params_path.is_file():
        parameters = parse_pipeline_params(
            read_path_text_bounded(params_path, kind="Pipeline params")
        )
        captured["params"] = _display_path(bundle.root, params_path)
        captured["parameters"] = parameters

    if not modules and not parameters:
        return await _register_only(summary, "Registered only; no pipeline run record was readable")

    if modules:
        await merge_vcf_header_provenance(
            session,
            family_uuid=family_context.family_uuid,
            assembly_id=getattr(family_context, "assembly_id", None),
            modules=modules,
            modality="pipeline",
            source="manifest",
        )
    if parameters:
        await _record_family_pipeline_metadata(
            session,
            family_uuid=family_context.family_uuid,
            parameters=parameters,
        )
    return summary.model_copy(
        update={
            "status": "imported",
            "message": "Recorded pipeline tool versions and run parameters for traceability",
            "summary": captured,
        }
    )


def _phenotype_files(bundle: FamilyPackageBundle, tsv_files: Sequence[str]) -> tuple[str, ...]:
    """The package's files the phenotypes are read from: its phenotype TSV, and its manifest
    when a member's HPO terms are written in it."""
    individuals = _manifest_individuals(bundle.manifest)
    inline = isinstance(individuals, Mapping) and any(
        isinstance(entry, Mapping) and entry.get("hpo") is not None for entry in individuals.values()
    )
    manifest = [_display_path(bundle.root, bundle.manifest_path)] if inline else []
    return tuple(dict.fromkeys([*tsv_files, *manifest]))


async def _import_phenotypes_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    phenotype_import: PhenotypeImport | None = None,
) -> FamilyImportDatasetSummary:
    """Import the package's phenotypes (its phenotype TSV and the HPO terms its manifest gives
    members). What they change is recorded on the family's clinical audit trail as made by
    ``phenotype_import``, the import that runs this: without it nothing is written."""
    rows, issues, tsv_files, fatal_errors = _manifest_hpo_rows(
        root=bundle.root,
        manifest=bundle.manifest,
        family_id=family_context.family_id,
    )
    if fatal_errors:
        return summary.model_copy(
            update={
                "status": "failed",
                "message": "; ".join(error.message for error in fatal_errors),
                "summary": {"errors": [error.model_dump() for error in fatal_errors]},
            }
        )
    if not rows and not issues:
        # Nothing to import: registered, as a dataset that carries nothing is, which an
        # import counts as brought again (SAFE-17; before: skipped, as an update's skip).
        return await _register_only(summary, "No HPO phenotype annotation rows were imported")
    if phenotype_import is None:
        # A clinical audit event names who changed a family's phenotypes: none is written
        # without one.
        raise RuntimeError("A phenotype import needs the import that runs it, for its audit events")
    result = await import_family_hpo_annotations(
        session,
        family_uuid=family_context.family_uuid,
        family_id=family_context.family_id,
        sample_uuids_by_id={
            sample_id: sample_context.sample_uuid
            for sample_id, sample_context in sample_contexts.items()
        },
        rows=rows,
        issues=issues,
        origin=replace(phenotype_import, files=_phenotype_files(bundle, tsv_files)),
    )
    status = "imported" if result["imported"] else "skipped"
    if result["errors"]:
        status = "warning" if result["imported"] else "skipped"
    return summary.model_copy(
        update={
            "status": status,
            "message": (
                f"Imported {result['imported']} HPO phenotype annotation row(s)"
                if result["imported"]
                else "No HPO phenotype annotation rows were imported"
            ),
            "summary": {
                **result,
                "assumption": "PED phenotype remains coarse affected/unaffected status; detailed HPO phenotypes are stored in individual_hpo.",
            },
        }
    )


async def _import_dataset(
    session: AsyncSession,
    *,
    bundle: FamilyPackageBundle,
    summary: FamilyImportDatasetSummary,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    conflict_mode: str = "overwrite",
    progress: DatasetProgressCallback | None = None,
    skipped_samples: set[str] | None = None,
    scope: Mapping[str, Any] | None = None,
    phenotype_import: PhenotypeImport | None = None,
) -> FamilyImportDatasetSummary:
    """Import one dataset of the package through its registered importer. Each sample an
    update leaves as it was, its rows being there, is added to ``skipped_samples``
    (``DatasetImportJob.skipped_samples``); ``scope`` is what the import records the
    dataset as replacing (``DatasetImportJob.scope``); ``phenotype_import`` is the import
    the phenotypes' audit events name."""
    if summary.dataset_type == "phenotypes":
        # Not a manifest dataset: the phenotypes come from the package's HPO rows.
        return await _import_phenotypes_dataset(
            session,
            bundle=bundle,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
            phenotype_import=phenotype_import,
        )
    dataset = bundle.manifest.datasets.get(summary.dataset_type)
    if dataset is None or not dataset.enabled:
        return summary
    importer = DATASET_IMPORTERS.get(summary.dataset_type)
    if importer is None:
        # A validated, enabled dataset with no importer would otherwise report success
        # while importing nothing. Fail loudly instead: adding a dataset type to
        # SUPPORTED_DATASETS without an importer is a bug, not a runtime condition.
        raise RuntimeError(
            f"No importer is registered for dataset type '{summary.dataset_type}'"
        )
    return await importer(
        DatasetImportJob(
            session=session,
            bundle=bundle,
            dataset=dataset,
            summary=summary,
            family_context=family_context,
            sample_contexts=sample_contexts,
            conflict_mode=conflict_mode,
            progress=progress,
            skipped_samples=skipped_samples if skipped_samples is not None else set(),
            scope=scope,
        )
    )
