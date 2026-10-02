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

import csv
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


def _csv_rows(text_value: str) -> list[list[str]]:
    """Non-empty rows of a small comma-separated file, cells stripped of blanks and quotes."""
    rows: list[list[str]] = []
    for row in csv.reader(text_value.splitlines()):
        cells = [cell.strip() for cell in row]
        if any(cells):
            rows.append(cells)
    return rows


def parse_sample_value_csv(text_value: str) -> dict[str, float]:
    """``{sample: value}`` from a headerless ``sample,value`` file.

    The PGT pipeline (nf-cmgg/copgtm) writes several of its QC numbers this way: the
    mean coverage per sample (``mean/<sample>_coverage.csv``) and the per-embryo
    Mendelian concordance before and after imputation (``rtgtools/<family>_*_concordance.csv``).
    A header row, if one is ever added, is skipped because its value is not a number.
    """
    values: dict[str, float] = {}
    for row in _csv_rows(text_value):
        if len(row) < 2 or not row[0]:
            continue
        value = _coerce_finite_float(row[1])
        if value is None:
            continue
        values[row[0]] = value
    return values


def parse_ado_adi_text(text_value: str) -> dict[str, dict[str, float]]:
    """``{offspring: {"allele_dropout_rate": .., "allele_dropin_rate": ..}}`` from the
    pipeline's Picard Mendelian-violation summary (``picard/<family>_ADO_ADI.csv``,
    header ``FAMILY_ID,OFFSPRING,ADO,ADI``; both rates are percentages)."""
    rows = _csv_rows(text_value)
    if not rows:
        return {}
    header = [cell.lower() for cell in rows[0]]
    try:
        offspring_index = header.index("offspring")
        ado_index = header.index("ado")
        adi_index = header.index("adi")
    except ValueError:
        logger.warning("ADO/ADI summary has no OFFSPRING/ADO/ADI header; skipping it")
        return {}
    result: dict[str, dict[str, float]] = {}
    for row in rows[1:]:
        if len(row) <= max(offspring_index, ado_index, adi_index) or not row[offspring_index]:
            continue
        rates: dict[str, float] = {}
        ado = _coerce_finite_float(row[ado_index])
        adi = _coerce_finite_float(row[adi_index])
        if ado is not None:
            rates["allele_dropout_rate"] = ado
        if adi is not None:
            rates["allele_dropin_rate"] = adi
        if rates:
            result[row[offspring_index]] = rates
    return result


def parse_king_kin0_text(text_value: str) -> list[dict[str, Any]]:
    """Sample pairs from a KING ``.kin0`` table (between-family kinship).

    The PGT pipeline runs KING on every pair of the family's samples, each sample as its
    own family, so the file is a ``.kin0``; the columns are read by name.
    """
    lines = [line for line in text_value.splitlines() if line.strip()]
    if not lines:
        return []
    header = [cell.strip().lower() for cell in lines[0].split()]
    columns = {name: index for index, name in enumerate(header)}
    if "id1" not in columns or "id2" not in columns or "kinship" not in columns:
        logger.warning("KING table has no ID1/ID2/Kinship header; skipping it")
        return []
    pairs: list[dict[str, Any]] = []
    for line in lines[1:]:
        cells = line.split()
        if len(cells) < len(header):
            continue
        kinship = _coerce_finite_float(cells[columns["kinship"]])
        if kinship is None:
            continue
        pair: dict[str, Any] = {
            "sample_a": cells[columns["id1"]],
            "sample_b": cells[columns["id2"]],
            "kinship": kinship,
        }
        if "n_snp" in columns:
            pair["n_snp"] = _coerce_int(cells[columns["n_snp"]])
        if "hethet" in columns:
            pair["het_het"] = _coerce_finite_float(cells[columns["hethet"]])
        if "ibs0" in columns:
            pair["ibs0"] = _coerce_finite_float(cells[columns["ibs0"]])
        pairs.append(pair)
    return pairs


def parse_ngsbits_sample_gender_text(text_value: str) -> dict[str, Any]:
    """The sex ngs-bits ``SampleGender`` read off the chrY/chrX read ratio.

    The file is a two-line TSV, ``#file gender reads_chry reads_chrx ratio_chry_chrx``.
    The result is the pipeline's *inferred* sex; CoGA keeps it apart from the recorded
    sex, which is what the sample-integrity check compares against.
    """
    lines = [line for line in text_value.splitlines() if line.strip()]
    if len(lines) < 2:
        return {}
    header = [cell.strip().lstrip("#").lower() for cell in lines[0].split("\t")]
    cells = [cell.strip() for cell in lines[1].split("\t")]
    row = dict(zip(header, cells))
    inferred = (row.get("gender") or "").strip().lower()
    if not inferred:
        return {}
    result: dict[str, Any] = {
        "method": "ngs-bits SampleGender",
        "inferred_sex": inferred if inferred in {"male", "female"} else "indeterminate",
    }
    for key in ("reads_chry", "reads_chrx"):
        value = _coerce_int(row.get(key))
        if value is not None:
            result[key] = value
    ratio = _coerce_finite_float(row.get("ratio_chry_chrx"))
    if ratio is not None:
        result["ratio_chry_chrx"] = ratio
    return result


# Qualimap ``genome_results.txt`` labels -> the metric names stored under
# ``sequencing_qc.alignment``. Values carry thousands separators, units and a
# parenthesised percentage ("217,392,633 (99.11%)"), so they are cleaned per label.
_QUALIMAP_COUNT_LABELS = {
    "number of reads": "read_count",
    "number of mapped reads": "mapped_reads",
    "number of duplicated reads (flagged)": "duplicated_reads",
}
_QUALIMAP_VALUE_LABELS = {
    "mean coveragedata": "mean_coverage",
    "std coveragedata": "std_coverage",
    "mean mapping quality": "mean_mapping_quality",
    "median insert size": "median_insert_size",
    "general error rate": "general_error_rate",
    "gc percentage": "gc_percent",
}
_QUALIMAP_PERCENT = re.compile(r"\(([\d.]+)%\)")
_QUALIMAP_NUMBER = re.compile(r"-?[\d,]*\.?\d+")


def parse_qualimap_genome_results_text(text_value: str) -> dict[str, Any]:
    """Headline alignment metrics from a Qualimap ``bamqc`` ``genome_results.txt``."""
    metrics: dict[str, Any] = {}
    for line in text_value.splitlines():
        label, separator, raw_value = line.partition("=")
        if not separator:
            continue
        key = label.strip().lower()
        value_text = raw_value.strip()
        if key in _QUALIMAP_COUNT_LABELS:
            number = _QUALIMAP_NUMBER.search(value_text)
            count = _coerce_int(number.group(0).replace(",", "")) if number else None
            if count is not None:
                metrics[_QUALIMAP_COUNT_LABELS[key]] = count
            percent = _QUALIMAP_PERCENT.search(value_text)
            if key == "number of mapped reads" and percent:
                mapped_percent = _coerce_finite_float(percent.group(1))
                if mapped_percent is not None:
                    metrics["mapped_reads_percent"] = mapped_percent
        elif key in _QUALIMAP_VALUE_LABELS:
            number = _QUALIMAP_NUMBER.search(value_text)
            value = _coerce_finite_float(number.group(0).replace(",", "")) if number else None
            if value is not None:
                metrics[_QUALIMAP_VALUE_LABELS[key]] = value
    read_count = metrics.get("read_count")
    duplicated = metrics.get("duplicated_reads")
    if isinstance(read_count, int) and read_count > 0 and isinstance(duplicated, int):
        metrics["duplicated_reads_percent"] = round(100.0 * duplicated / read_count, 2)
    return metrics


def parse_haplotype_origin_text(text_value: str) -> list[dict[str, Any]]:
    """Rows of the pipeline's affected/normal haplotype table
    (``phasing/haplotype_origin/<parent>_affected_normal_haplotype.csv``): per haplotype
    of the affected parent, how many sites it shares with the index's affected and
    normal haplotypes."""
    rows = _csv_rows(text_value)
    if not rows:
        return []
    header = [cell.lower() for cell in rows[0]]
    if "haplotype" not in header:
        logger.warning("Haplotype-origin table has no 'haplotype' column; skipping it")
        return []
    result: list[dict[str, Any]] = []
    for row in rows[1:]:
        entry: dict[str, Any] = {}
        for name, cell in zip(header, row):
            if name == "haplotype":
                entry["haplotype"] = cell
                continue
            value = _coerce_int(cell)
            entry[name] = value if value is not None else cell
        if entry.get("haplotype"):
            result.append(entry)
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
    # PGT (nf-cmgg/copgtm): the parent whose disease haplotype the run traces, the
    # region it was run for, the QDNAseq bin size (kb), whether APCAD used the imputed
    # parents, and the callset and phasing panel the run started from.
    "affected_parent",
    "roi",
    "bin_size",
    "apcad_imputation",
    "cohort_vcf",
    "shapeit_reference_panel",
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
    """Record where a sample's aligned reads lie, under ``samples.metadata["alignment"]``:
    ``format`` (cram/bam), the package-relative ``path``/``index_path`` and, for a
    package imported from a bucket, the objects' ``uri``/``index_uri``. The CRAM
    endpoint serves from the URIs only after checking them (routers/cram.py)."""
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
    the family package root and containment-checked at serve time. For a package
    imported from a bucket, ``uris`` maps each kind to its object's gs:// or s3:// URI,
    which the endpoint serves from in gcs/s3 mode after checking it
    (routers/signal_tracks.py).
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
    await _set_family_metadata_key(session, family_uuid=family_uuid, key="pipeline", value=parameters)


async def record_family_pipeline_qc(
    session: AsyncSession,
    *,
    family_uuid: str,
    pipeline_qc: dict[str, Any],
) -> None:
    """Store the QC the pipeline reported for the family as a whole -- the KING kinship
    of every pair -- under ``families.metadata["pipeline_qc"]``."""
    await _set_family_metadata_key(session, family_uuid=family_uuid, key="pipeline_qc", value=pipeline_qc)


async def record_family_haplotype_origin(
    session: AsyncSession,
    *,
    family_uuid: str,
    haplotype_origin: dict[str, Any],
) -> None:
    """Store the pipeline's reading of the affected parent's affected haplotype under
    ``families.metadata["pipeline_haplotype_origin"]``. It is evidence the report can
    cite; CoGA's own risk-haplotype inference does not read it."""
    await _set_family_metadata_key(
        session, family_uuid=family_uuid, key="pipeline_haplotype_origin", value=haplotype_origin
    )


async def _set_family_metadata_key(
    session: AsyncSession,
    *,
    family_uuid: str,
    key: str,
    value: dict[str, Any],
) -> None:
    """Replace one top-level key of ``families.metadata``, keeping the others.

    Set where it is stored, not written back whole from a copy read first, which would put
    back what another writer changed in between (the import-state keys among them)."""
    await session.execute(
        text(
            """
            UPDATE families
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                CAST(:path AS text[]),
                CAST(:value_json AS jsonb),
                true
            )
            WHERE id = CAST(:family_id AS uuid)
            """
        ),
        {"family_id": family_uuid, "path": [key], "value_json": json.dumps(_jsonb_safe(value))},
    )
    await session.commit()
