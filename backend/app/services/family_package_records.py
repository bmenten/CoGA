"""A family's package records: one per analysis it was imported for.

A family may hold several analyses, each delivered as a package of its own: the monogenic and the
conventional NIPT of one pregnancy (the owner's decision of 2026-11-19). So the family's metadata
keeps one package record per analysis (``families.metadata.package_imports``), keyed by the
package's ``analysis_type`` (``UNSPECIFIED_ANALYSIS`` for a package that names none). An import
replaces the record of its own analysis and keeps the others: each says the folder that analysis's
data came from, the delivery it was last imported from, its reference delivery
(``package_changed_after_import``), its mode, its datasets and the members its package listed.

A sample's files that CoGA serves from a package folder are each tied to the analysis whose import
recorded them, so they are read in that analysis's folder, never in another's: its alignments and
its sequencing QC (with the QC report) are keyed by analysis (``samples.metadata.alignments``,
``samples.metadata.sequencing_qc``), and each signal track names its analysis (``PACKAGE_KEY``).

SQL that reads every record of a family joins ``PACKAGE_RECORDS_OF_A_FAMILY``: one row per record,
``r.analysis`` its key and ``r.record`` the record.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# Family.metadata["package_imports"]: the family's package records, keyed by analysis.
PACKAGE_IMPORTS_KEY = "package_imports"
# The key of the record of a package that names no analysis type.
UNSPECIFIED_ANALYSIS = "unspecified"
# The key, in a sample's signal-track record, of the analysis whose import recorded it.
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


class PackageRecordShapeError(ValueError):
    """A package record whose sources are not in the shape the import writes them:
    what reads them stops rather than guess which data are the analysis's."""


def record_sources(record: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """The storage sources a package record lists, per kind (``SOURCE_KINDS``), as the import
    writes them (``package_sources``). Raises ``PackageRecordShapeError`` for any other shape."""
    sources = record.get(SOURCES_KEY)
    if (
        not isinstance(sources, Mapping)
        or set(sources) != set(SOURCE_KINDS)
        or not all(
            isinstance(names, list) and all(isinstance(name, str) for name in names) for names in sources.values()
        )
    ):
        raise PackageRecordShapeError("A package record's sources are not in the shape the import writes.")
    return {kind: frozenset(names) for kind, names in sources.items()}


# How a message names the data of each kind.
_KIND_WORDS: dict[str, str] = {
    "small_variants": "small variants",
    "structural_variants": "structural variants",
    "tracks": "tracks",
    "repeats": "repeat calls",
    "paraphase": "Paraphase results",
}


def source_collisions(
    records: Mapping[str, Mapping[str, Any]], key: str, sources: Mapping[str, Iterable[str]]
) -> list[tuple[str, str, str]]:
    """The storage sources an import of the package record ``key`` would write (``sources``,
    per kind) that another of the family's package records (``records``, by key) lists: each
    as ``(kind, source, other key)``, sorted. Two analyses of one family never share a source:
    each kind's rows are written, replaced and scoped by source, so a shared one would make one
    analysis's import replace or skip the other's data, and each analysis's sign-out claim the
    other's (the owner's decision of 2026-11-20)."""
    collisions: set[tuple[str, str, str]] = set()
    for other_key, record in records.items():
        if other_key == key:
            continue
        held = record_sources(record)
        for kind, names in sources.items():
            for name in names:
                if name in held.get(kind, frozenset()):
                    collisions.add((kind, name, other_key))
    return sorted(collisions)


def source_collision_message(analysis: str, collisions: Sequence[tuple[str, str, str]]) -> str:
    """Why the import of a package of ``analysis`` (its record key) is refused: the sources it
    would store data under that another analysis of the family holds (``source_collisions``)."""
    label = ANALYSIS_LABELS.get(analysis)
    package = f"The {label} package" if label else "This package"
    shared = "; ".join(
        f"its {_KIND_WORDS.get(kind, kind)} as '{name}', which {analysis_data_words(other)} already holds"
        for kind, name, other in collisions
    )
    return (
        f"{package} would store {shared}. Two analyses of one family cannot store data under one "
        "source, as one import would replace or claim the other's. Leave that dataset out of this "
        "package, or, for small variants, name its caller differently (the snv dataset's "
        "source_format)."
    )


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
    """The folder a sample's signal-track record (``file_record``) was imported from: that of the
    package record its ``PACKAGE_KEY`` names, in
    the family's ``package_records`` (``families.metadata.package_imports``). None when it names
    none, or a record the family does not hold."""
    if not isinstance(package_records, Mapping):
        return None
    key = (file_record or {}).get(PACKAGE_KEY)
    if not isinstance(key, str):
        return None
    record = package_records.get(key)
    return record_folder(record) if isinstance(record, Mapping) else None
