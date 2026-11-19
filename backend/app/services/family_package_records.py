"""A family's package records: one per analysis it was imported for.

A family may hold several analyses, each delivered as a package of its own: the monogenic and the
conventional NIPT of one pregnancy (the owner's decision of 2026-11-19). So the family's metadata
keeps one package record per analysis (``families.metadata.package_imports``), keyed by the
package's ``analysis_type`` (``UNSPECIFIED_ANALYSIS`` for a package that names none). An import
replaces the record of its own analysis and keeps the others: each says the folder that analysis's
data came from, the delivery it was last imported from, its reference delivery
(``package_changed_after_import``), its mode, its datasets and the members its package listed.

A sample's files that CoGA serves from a package folder (its alignment, signal tracks and QC
report) each name the analysis whose import recorded them (``PACKAGE_KEY``), so they are read in
that analysis's folder, never in another's.

SQL that reads every record of a family joins ``PACKAGE_RECORDS_OF_A_FAMILY``: one row per record,
``r.analysis`` its key and ``r.record`` the record.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Family.metadata["package_imports"]: the family's package records, keyed by analysis.
PACKAGE_IMPORTS_KEY = "package_imports"
# The key of the record of a package that names no analysis type.
UNSPECIFIED_ANALYSIS = "unspecified"
# The key, in a sample's file record (alignment, signal tracks, sequencing QC), of the analysis
# whose import recorded it.
PACKAGE_KEY = "package"

# Each package record of the family ``f``: ``r.analysis`` its key, ``r.record`` the record.
PACKAGE_RECORDS_OF_A_FAMILY = (
    "CROSS JOIN LATERAL jsonb_each("
    "CASE WHEN jsonb_typeof(f.metadata -> 'package_imports') = 'object' "
    "THEN f.metadata -> 'package_imports' ELSE '{}'::jsonb END"
    ") AS r(analysis, record)"
)


# How a message names each analysis a package may name (coga_package.AnalysisType).
ANALYSIS_LABELS: dict[str, str] = {
    "long_read_wgs": "long-read WGS",
    "long_read_adaptive_sampling": "long-read adaptive sampling",
    "short_read_wgs": "short-read WGS",
    "pgt": "PGT",
    "expanded_carrier_screening": "expanded carrier screening",
    "monogenic_nipt": "monogenic NIPT",
    "conventional_nipt": "conventional NIPT",
}


def analysis_data_words(analysis: object) -> str:
    """How a message names the data of the analysis whose package record ``analysis`` is the key
    of: "this family's monogenic NIPT data", or "this family's data" for one it has no label for
    (``UNSPECIFIED_ANALYSIS``)."""
    label = ANALYSIS_LABELS.get(str(analysis or ""))
    return f"this family's {label} data" if label else "this family's data"


# The kinds of data a sign-out of one analysis is scoped by (report_signout_service), each the
# storage ``source`` of its rows: small variants and structural variants (ClickHouse), interval
# tracks, repeat calls (``repeat_expansions.source``) and Paraphase results (one source).
SOURCE_KINDS = ("small_variants", "structural_variants", "tracks", "repeats", "paraphase")
# The key of a package record that lists the sources its import wrote, per kind (``package_sources``).
SOURCES_KEY = "sources"

# The sources each dataset of a package writes, per kind; the ``snv`` dataset's small-variant
# callset is read from the package (``family_package_datasets.snv_dataset_callset``).
_DATASET_SOURCES: dict[str, dict[str, tuple[str, ...]]] = {
    "haplotypes": {"small_variants": ("glimpse2",), "tracks": ("glimpse2",)},
    "mito": {"small_variants": ("mito",), "structural_variants": ("mito_sv",)},
    "sv_needlr": {"structural_variants": ("needlr",)},
    "cnv": {"structural_variants": ("hificnv",), "tracks": ("hificnv",)},
    "wisecondorx": {"tracks": ("wisecondorx",)},
    "qdnaseq": {"tracks": ("qdnaseq",)},
    "ccnv": {"structural_variants": ("ccnv",), "tracks": ("ccnv",)},
    "apcad": {"tracks": ("apcad",)},
    "pcf": {"tracks": ("pcf",)},
    # A coverage BED (stored as an upload's, ``web``) or a NIPT per-target table.
    "coverage": {"tracks": ("web", "coverage_table")},
    "repeats_trgt": {"repeats": ("trgt",)},
    "paraphase": {"paraphase": ("paraphase",)},
}


def package_sources(dataset_types: Iterable[str], snv_callset: str | None) -> dict[str, list[str]]:
    """The storage sources a package's import writes, per kind (``SOURCE_KINDS``), from its
    enabled ``dataset_types`` and the callset its ``snv`` dataset is stored as (``snv_callset``;
    None when it has none, or one the import cannot tell): each list sorted, every kind present.
    A sign-out of the package's analysis is scoped by them."""
    sources: dict[str, set[str]] = {kind: set() for kind in SOURCE_KINDS}
    for dataset_type in dataset_types:
        if dataset_type == "snv":
            if snv_callset:
                sources["small_variants"].add(snv_callset)
            continue
        for kind, names in _DATASET_SOURCES.get(dataset_type, {}).items():
            sources[kind].update(names)
    return {kind: sorted(names) for kind, names in sources.items()}


def package_record_key(analysis_type: str | None) -> str:
    """The key of the record a package of ``analysis_type`` writes."""
    if isinstance(analysis_type, str) and analysis_type.strip():
        return analysis_type.strip()
    return UNSPECIFIED_ANALYSIS


def family_package_records(metadata: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The family's package records, by analysis; empty when it was imported from no package."""
    records = (metadata or {}).get(PACKAGE_IMPORTS_KEY)
    if not isinstance(records, Mapping):
        return {}
    return {str(key): dict(record) for key, record in records.items() if isinstance(record, Mapping)}


def record_folder(record: Mapping[str, Any] | None) -> str | None:
    """The folder a package record's import read, or None."""
    folder = (record or {}).get("folder_path")
    return folder if isinstance(folder, str) and folder else None


def family_package_folders(metadata: Mapping[str, Any] | None) -> list[str]:
    """Every folder the family's package records name, sorted, without repeats."""
    return sorted(
        {folder for record in family_package_records(metadata).values() if (folder := record_folder(record))}
    )


def package_folder_of(package_records: Any, file_record: Mapping[str, Any] | None) -> str | None:
    """The folder a sample's file record (``file_record``: its alignment, a signal track, its
    sequencing QC) was imported from: that of the package record its ``PACKAGE_KEY`` names, in
    the family's ``package_records`` (``families.metadata.package_imports``). None when it names
    none, or a record the family does not hold."""
    if not isinstance(package_records, Mapping):
        return None
    key = (file_record or {}).get(PACKAGE_KEY)
    if not isinstance(key, str):
        return None
    record = package_records.get(key)
    return record_folder(record) if isinstance(record, Mapping) else None
