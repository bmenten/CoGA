"""Sequencing-QC, alignment and pipeline-run records for a family package.

The long-read pipeline emits per-sample QC (NanoPlot/MultiQC + mosdepth), the aligned
reads, and a Nextflow run record. None of it is variant data, so it does not belong in
the variant stores -- but all of it is needed: QC numbers to judge whether a callset is
interpretable, the alignment path so the genome browser can show reads, and the run
record so a released report traces back to the exact pipeline that produced it.

Parsing happens once at import and the results are stored as sample/family metadata, so
nothing downstream has to re-read pipeline output.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .family_metadata_context import SampleMetadataContext
from .family_package_common import _coerce_finite_float, _coerce_int, _jsonb_safe, _metadata_dict


logger = logging.getLogger(__name__)


# NanoStats "General summary" labels -> the metric names stored on the sample. Values
# are thousands-separated ("11,981.4"), so they are cleaned before parsing.
_NANOSTATS_METRICS = {
    "mean read length": "mean_read_length",
    "median read length": "median_read_length",
    "mean read quality": "mean_read_quality",
    "median read quality": "median_read_quality",
    "read length n50": "read_length_n50",
    "number of reads": "read_count",
    "total bases": "total_bases",
    "total bases aligned": "total_bases_aligned",
    "mean percent identity": "mean_percent_identity",
    "median percent identity": "median_percent_identity",
    "average percent identity": "mean_percent_identity",
    "fraction of bases aligned": "fraction_bases_aligned",
    "stdev read length": "stdev_read_length",
}

_NANOSTATS_QUALITY_CUTOFF = re.compile(r"^>Q(\d+):\s*(\d+)\s*\(([\d.]+)%\)")


def parse_nanostats_text(text_value: str) -> dict[str, Any]:
    """Read-level metrics from a NanoPlot ``NanoStats.txt``.

    Only the "General summary" block and the ``>QN`` cutoff table are read; the
    top-5 read listings below them are per-read detail with no summary value.
    """
    metrics: dict[str, Any] = {}
    quality_cutoffs: dict[str, Any] = {}
    for line in text_value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cutoff = _NANOSTATS_QUALITY_CUTOFF.match(stripped)
        if cutoff:
            quality_cutoffs[f"q{cutoff.group(1)}"] = {
                "reads": _coerce_int(cutoff.group(2)),
                "percent": _coerce_finite_float(cutoff.group(3)),
            }
            continue
        if ":" not in stripped:
            continue
        label, _, raw_value = stripped.partition(":")
        key = _NANOSTATS_METRICS.get(label.strip().lower())
        if key is None:
            continue
        value = _coerce_finite_float(raw_value.replace(",", "").strip())
        if value is None:
            continue
        metrics[key] = value
    if quality_cutoffs:
        metrics["quality_cutoffs"] = quality_cutoffs
    return metrics


def parse_mosdepth_summary_text(text_value: str) -> dict[str, Any]:
    """Per-chromosome and genome-wide mean depth from a mosdepth ``.summary.txt``.

    mosdepth emits both ``<chrom>`` and ``<chrom>_region`` rows; the ``_region`` rows
    duplicate the plain ones when no target BED was used, so they are skipped. The
    ``total`` row is lifted out as the genome-wide mean depth, which is the single
    number an interpreter looks at first.
    """
    per_chromosome: dict[str, Any] = {}
    result: dict[str, Any] = {}
    for line in text_value.splitlines():
        parts = line.rstrip("\n\r").split("\t")
        if len(parts) < 4 or parts[0] in {"", "chrom"}:
            continue
        chrom = parts[0]
        if chrom.endswith("_region"):
            continue
        mean_depth = _coerce_finite_float(parts[3])
        if mean_depth is None:
            continue
        if chrom == "total":
            result["mean_depth"] = mean_depth
            result["total_bases"] = _coerce_int(parts[2])
            continue
        per_chromosome[chrom] = mean_depth
    if per_chromosome:
        result["mean_depth_by_chromosome"] = per_chromosome
        mito_depth = next(
            (
                depth
                for name, depth in per_chromosome.items()
                if name.upper().lstrip("CHR") in {"M", "MT"}
            ),
            None,
        )
        if mito_depth is not None:
            result["mito_mean_depth"] = mito_depth
    return result


# A process heading in software_versions.yaml: an all-caps Nextflow process name (or
# the trailing "Workflow" block) on its own line with no value after the colon.
_PIPELINE_PROCESS_HEADING = re.compile(r"^([A-Za-z][A-Za-z0-9_./-]*):\s*$")
_PIPELINE_TOOL_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_./+-]*):\s*(\S.*)$")


# Banner lines inside a tool's block can look like `key: value` (mutserve prints
# "https://github.com/seppinho/mutserve"). They are not tools.
_PIPELINE_NON_TOOL_KEYS = frozenset({"http", "https", "ftp", "c"})


def _merge_pipeline_module(
    modules: dict[str, dict[str, str]], tool: str, version: str, process: str
) -> None:
    key = tool.strip().lower()
    value = version.strip()
    if not key or key in _PIPELINE_NON_TOOL_KEYS or value.startswith("//"):
        return
    existing = modules.get(key)
    if existing is None:
        modules[key] = {"version": value, "detail": process}
        return
    if process and process not in existing["detail"]:
        existing["detail"] = f"{existing['detail']}, {process}"
    # One tool can legitimately run at two versions in a single pipeline (this run
    # annotates SNVs with VEP 115.2 and CNVs with 116.0). Keep both rather than letting
    # whichever process was listed first define the traceability record.
    if value and value != existing["version"] and value not in existing["version"]:
        existing["version"] = f"{existing['version']}, {value}"


def extract_pipeline_versions(text_value: str) -> dict[str, dict[str, str]]:
    """``{tool: {version, detail}}`` from a Nextflow ``software_versions.yaml``.

    The file is keyed by *process* (``DEEPVARIANT_RUNDEEPVARIANT``) with a
    ``{tool: version}`` map under each, and the same tool appears under several
    processes. Collapse to one entry per tool and record which processes reported it,
    matching the shape the annotation manifest already stores for VCF-header
    provenance.

    Parsing is line-based rather than YAML-based on purpose: the file is *not* reliably
    valid YAML. Tools that print a banner instead of a bare version (mutserve emits a
    URL and a copyright line) leave colon-less lines inside the mapping, which makes
    ``yaml.safe_load`` reject the whole document -- and losing every tool version
    because one tool is chatty would silently break the traceability record. A
    well-formed nested file parses identically here, since the shape is recovered from
    the ``KEY:``/``tool: version`` line pattern and not from indentation.
    """
    modules: dict[str, dict[str, str]] = {}
    process = ""
    for raw_line in text_value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        heading = _PIPELINE_PROCESS_HEADING.match(line)
        if heading:
            process = heading.group(1)
            continue
        tool_line = _PIPELINE_TOOL_LINE.match(line)
        if tool_line:
            _merge_pipeline_module(modules, tool_line.group(1), tool_line.group(2), process)
            continue
        # A banner/continuation line (no `key: value`): it belongs to the tool that was
        # just recorded, and carries no version, so there is nothing to keep.
    return modules


# Run parameters worth keeping: what was analysed and with which references. The rest of
# a Nextflow params dump is scheduler/executor configuration with no bearing on results.
_PIPELINE_PARAM_KEYS = (
    "genome",
    "assembly",
    "fasta",
    "snv_caller",
    "sv_caller",
    "cnv_caller",
    "mito_caller",
    "vep_cache_version",
    "vep_genome",
    "vep_species",
    "trgt_repeats",
    "exomiser",
    "paraphase",
    "repeats",
    "mito",
    "sv",
    "cnv",
    "snv",
    "annotate",
    "align",
    "methyl",
    "kit",
    "clair3_model_pacbio",
    "clair3_model_ont",
    "qdnaseq_bin_size",
)


def parse_pipeline_params(text_value: str) -> dict[str, Any]:
    """Analysis-relevant entries from a Nextflow ``params_*.json``.

    Absolute paths on the compute cluster are reduced to their basename: the file name
    identifies the reference/catalogue used, while the full path is site-specific and
    would be recorded as if it were meaningful provenance.
    """
    try:
        payload = json.loads(text_value)
    except json.JSONDecodeError:
        logger.warning("Pipeline params JSON does not parse; skipping parameter capture")
        return {}
    if not isinstance(payload, dict):
        return {}
    parameters: dict[str, Any] = {}
    for key in _PIPELINE_PARAM_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, str) and ("/" in value or "\\" in value):
            value = Path(value).name
        parameters[key] = value
    return parameters


async def _merge_sample_metadata(
    session: AsyncSession,
    *,
    sample_uuid: str,
    patch: dict[str, Any],
) -> None:
    """Shallow-merge ``patch`` into ``samples.metadata`` (top-level keys replaced)."""
    result = await session.execute(
        text("SELECT metadata FROM samples WHERE id = CAST(:sample_id AS uuid)"),
        {"sample_id": sample_uuid},
    )
    metadata = _metadata_dict(result.scalar_one_or_none())
    metadata.update(_jsonb_safe(patch))
    await session.execute(
        text(
            """
            UPDATE samples
            SET metadata = CAST(:metadata_json AS jsonb)
            WHERE id = CAST(:sample_id AS uuid)
            """
        ),
        {"sample_id": sample_uuid, "metadata_json": json.dumps(metadata)},
    )
    await session.commit()


async def record_sample_qc_metadata(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    metrics: dict[str, Any],
) -> None:
    await _merge_sample_metadata(
        session,
        sample_uuid=sample_context.sample_uuid,
        patch={"sequencing_qc": metrics},
    )


async def record_sample_alignment_metadata(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    alignment: dict[str, Any],
) -> None:
    await _merge_sample_metadata(
        session,
        sample_uuid=sample_context.sample_uuid,
        patch={"alignment": alignment},
    )


async def record_sample_signal_tracks(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    signal_tracks: dict[str, Any],
) -> None:
    """Record where a caller's signal files sit, under ``samples.metadata["signal_tracks"]``.

    The genome browser streams these files directly rather than reading the binned
    rows out of ClickHouse, so it needs the path. Probing for them the way the
    alignment endpoints probe for a CRAM does not work here: the filenames carry
    the caller's own naming (``HG002.Sample0.depth.bw``, ``HG002.HG002.maf.bw``),
    so what the import actually found is recorded instead of re-guessed later.

    Paths are package-relative, matching ``alignment``; they are resolved against
    the family package root and containment-checked at serve time.
    """

    await _merge_sample_metadata(
        session,
        sample_uuid=sample_context.sample_uuid,
        patch={"signal_tracks": signal_tracks},
    )


async def record_sample_mtdna_metadata(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    mtdna: dict[str, Any],
) -> None:
    await _merge_sample_metadata(
        session,
        sample_uuid=sample_context.sample_uuid,
        patch={"mtdna": mtdna},
    )


async def record_family_pipeline_metadata(
    session: AsyncSession,
    *,
    family_uuid: str,
    parameters: dict[str, Any],
) -> None:
    """Store the pipeline run parameters under ``families.metadata["pipeline"]``."""
    result = await session.execute(
        text("SELECT metadata FROM families WHERE id = CAST(:family_id AS uuid)"),
        {"family_id": family_uuid},
    )
    metadata = _metadata_dict(result.scalar_one_or_none())
    metadata["pipeline"] = _jsonb_safe(parameters)
    await session.execute(
        text(
            """
            UPDATE families
            SET metadata = CAST(:metadata_json AS jsonb)
            WHERE id = CAST(:family_id AS uuid)
            """
        ),
        {"family_id": family_uuid, "metadata_json": json.dumps(metadata)},
    )
    await session.commit()
