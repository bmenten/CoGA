"""Monogenic NIPT packages: the per-sample SNV callset.

A monogenic NIPT pipeline calls the maternal plasma (cfDNA) and the father's sample one
file each. With Mutect2 in tumour-only mode, the lab's caller, every call's quality is
TLOD and its GT says 0/1 whatever the allele fraction, so the genotype is read from the
allele depths (see nipt_analysis). A package lists the two files under
``datasets.snv.per_sample``. CoGA keeps them as one callset, source ``nipt``: one row per
variant, holding each sample's call where its file has one. A sample without a call at a
variant had no alt read there that the caller reported; the analysis reads its depth there
from the sample's per-target coverage (see nipt_target_coverage).

The paternal file of an emit-everything Mutect2 run on genomic DNA is mostly low-level
noise: nearly all of its calls sit below 20% VAF. Two kinds of paternal call matter to the
analysis: a genotype call (VAF 0.20 or more) and any call at a
position where the plasma has one (a low-level paternal signal at a de novo candidate). So
the importer keeps the paternal calls at a plasma call position or at
``NIPT_PATERNAL_KEEP_MIN_VAF`` or more, and counts the others (``skipped_by_filter``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from .clickhouse_variant_records import SmallVariantCall
from .data_scope import normalize_chromosome
from .family_package_common import FamilyPackageBundle, ParsedPed, _display_path, package_text_handle, read_vcf_sample_columns
from .nipt import NIPT_CFDNA_ASSAY, SAMPLE_ASSAY_KEY

if TYPE_CHECKING:
    from .variant_upload_service import SmallVariantRecordFilter

# A paternal call below this VAF is kept only at a plasma call position. It sits below the
# 0.20 het floor of the paternal genotype classes, so no paternal genotype is lost.
NIPT_PATERNAL_KEEP_MIN_VAF = 0.15


def call_alt_fraction(call: SmallVariantCall) -> float | None:
    """The call's alt-allele fraction from its allele depths: alt reads over all reads at
    the record; the caller's AF when the call has no AD; None without either."""
    if len(call.ad) > 1:
        total = sum(depth for depth in call.ad if depth is not None)
        if total > 0:
            return (call.ad[1] or 0) / total
        return None
    if call.af:
        return call.af[0]
    return None


def scan_vcf_positions(path: Path) -> set[tuple[str, int]]:
    """The (chromosome, position) of every record of a VCF, chromosomes normalised, and of
    every base an MNV (REF and ALT of one length) spans: the father's VCF can hold its
    alleles as SNVs."""
    positions: set[tuple[str, int]] = set()
    with package_text_handle(path) as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t", 5)
            if len(fields) < 2:
                continue
            try:
                position = int(fields[1])
            except ValueError:
                continue
            chrom = normalize_chromosome(fields[0])
            positions.add((chrom, position))
            if len(fields) > 4 and len(fields[3]) > 1:
                ref = fields[3]
                if any(len(alt) == len(ref) for alt in fields[4].split(",")):
                    positions.update((chrom, position + offset) for offset in range(1, len(ref)))
    return positions


def paternal_record_filter(
    plasma_positions: set[tuple[str, int]],
    *,
    min_vaf: float = NIPT_PATERNAL_KEEP_MIN_VAF,
) -> SmallVariantRecordFilter:
    """Keep a paternal record over a plasma call position (an MNV over any of its bases), or
    when a call of it reaches ``min_vaf``."""

    def keep(chrom: str, start: int, end: int, calls: Sequence[SmallVariantCall]) -> bool:
        if any((chrom, position) in plasma_positions for position in range(start, max(start, end) + 1)):
            return True
        return any(
            fraction is not None and fraction >= min_vaf
            for fraction in (call_alt_fraction(call) for call in calls)
        )

    return keep


def _manifest_sample_entries(bundle: FamilyPackageBundle) -> dict[str, dict[str, Any]]:
    samples = bundle.manifest.samples
    if isinstance(samples, dict):
        return {str(key): value for key, value in samples.items() if isinstance(value, dict)}
    entries: dict[str, dict[str, Any]] = {}
    for item in samples or []:
        if isinstance(item, dict) and item.get("sample_id"):
            entries[str(item["sample_id"])] = item
    return entries


def nipt_package_cfdna_sample(bundle: FamilyPackageBundle) -> str | None:
    """The package's maternal-plasma sample: the one tagged ``assay: nipt_cfdna``, else the
    PED's mother of a child (as ``resolve_nipt_trio`` falls back to the mother)."""
    for sample_id, entry in _manifest_sample_entries(bundle).items():
        if str(entry.get(SAMPLE_ASSAY_KEY) or "").strip() == NIPT_CFDNA_ASSAY:
            return sample_id
    for member in bundle.ped.members:
        if member.mid not in ("", "0"):
            return member.mid
    return None


def nipt_import_order(cfdna_sample_id: str | None, sample_ids: Iterable[str]) -> list[str]:
    """The per-sample files in import order: the plasma first, so every paternal file is
    filtered against its positions, then the others as listed."""
    ordered = list(dict.fromkeys(sample_ids))
    if cfdna_sample_id is not None and cfdna_sample_id in ordered:
        ordered.remove(cfdna_sample_id)
        ordered.insert(0, cfdna_sample_id)
    return ordered


# Where Discover looks for a monogenic NIPT package's per-sample files, beside the PED or
# one folder down. ``{sample}`` is a PED sample id.
_NIPT_VCF_GLOBS: tuple[str, ...] = (
    "{sample}.vcf.gz",
    "{sample}.*.vcf.gz",
    "*/{sample}.vcf.gz",
    "*/{sample}.*.vcf.gz",
)
_NIPT_COVERAGE_GLOBS: tuple[str, ...] = (
    "coverage_{sample}.txt",
    "coverage_{sample}.tsv",
    "*/coverage_{sample}.txt",
    "*/coverage_{sample}.tsv",
)


@dataclass(slots=True)
class NiptPackageFiles:
    """A monogenic NIPT pair Discover found: the plasma (mother) and paternal sample, each
    with its one-sample VCF, and the per-target coverage tables there are."""

    cfdna_sample_id: str
    father_sample_id: str
    fetus_sample_id: str
    vcfs: dict[str, str] = field(default_factory=dict)  # sample id -> package path
    coverage_tables: dict[str, str] = field(default_factory=dict)


def _first_match(root: Path, patterns: Sequence[str], sample_id: str) -> Path | None:
    for pattern in patterns:
        matches = sorted(path for path in root.glob(pattern.format(sample=sample_id)) if path.is_file())
        if matches:
            return matches[0]
    return None


def discover_nipt_package_files(root: Path, ped: ParsedPed) -> NiptPackageFiles | None:
    """A monogenic NIPT pair in a folder without a joint VCF: a PED child without a VCF
    of its own (the fetus) whose mother and father each have a one-sample VCF named after
    them. The mother is taken as the plasma sample. None when the folder is no such pair.
    """
    for member in ped.members:
        father, mother = member.pid, member.mid
        if father in ("", "0") or mother in ("", "0") or father == mother:
            continue
        if _first_match(root, _NIPT_VCF_GLOBS, member.iid) is not None:
            continue
        vcfs: dict[str, str] = {}
        for sample_id in (mother, father):
            path = _first_match(root, _NIPT_VCF_GLOBS, sample_id)
            if path is None or len(read_vcf_sample_columns(path)) != 1:
                break
            vcfs[sample_id] = _display_path(root, path)
        if len(vcfs) != 2:
            continue
        coverage_tables = {}
        for sample_id in (mother, father):
            table = _first_match(root, _NIPT_COVERAGE_GLOBS, sample_id)
            if table is not None:
                coverage_tables[sample_id] = _display_path(root, table)
        return NiptPackageFiles(
            cfdna_sample_id=mother,
            father_sample_id=father,
            fetus_sample_id=member.iid,
            vcfs=vcfs,
            coverage_tables=coverage_tables,
        )
    return None
