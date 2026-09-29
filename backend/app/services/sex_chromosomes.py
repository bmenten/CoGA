"""Where a male carries a single copy of the genome: chrX and chrY outside the PARs (#545).

A male has one X and one Y. Outside the pseudo-autosomal regions (PAR1 and PAR2) he
carries one copy of each, so a variant there is hemizygous. A caller writes it as a
haploid ``1`` or as a diploid ``1/1``, never as ``0/1``. Inside the PARs, X and Y pair like
an autosome, and a male is diploid.

The boundaries are the Genome Reference Consortium's, 1-based and inclusive. An assembly
whose boundaries are not listed here (T2T-CHM13, another species) has no hemizygous
positions as far as this module is concerned, so the diploid rules apply there, as they
did before, rather than guessed boundaries.
"""

from __future__ import annotations

import re
from typing import Literal

from .data_scope import normalize_chromosome

SexChromosome = Literal["X", "Y"]
Interval = tuple[int, int]

_PSEUDOAUTOSOMAL_REGIONS: dict[str, dict[SexChromosome, tuple[Interval, ...]]] = {
    "GRCh38": {
        "X": ((10_001, 2_781_479), (155_701_383, 156_030_895)),
        "Y": ((10_001, 2_781_479), (56_887_903, 57_217_415)),
    },
    "GRCh37": {
        "X": ((60_001, 2_699_520), (154_931_044, 155_260_560)),
        "Y": ((10_001, 2_649_520), (59_034_050, 59_363_566)),
    },
}

_ASSEMBLY_ALIASES = {
    "grch38": "GRCh38",
    "hg38": "GRCh38",
    "grch37": "GRCh37",
    "hg19": "GRCh37",
}

# Some pipelines number the sex chromosomes 23 and 24.
XY_CHROMOSOME_NAMES: dict[SexChromosome, tuple[str, ...]] = {
    "X": ("X", "23"),
    "Y": ("Y", "24"),
}

_PATCH_SUFFIX = re.compile(r"\.p\d+$")


def pseudoautosomal_regions(assembly_name: str | None) -> dict[SexChromosome, tuple[Interval, ...]] | None:
    """The PARs of an assembly, per sex chromosome, or None when they are not known."""
    key = _PATCH_SUFFIX.sub("", str(assembly_name or "").strip().lower())
    canonical = _ASSEMBLY_ALIASES.get(key)
    return _PSEUDOAUTOSOMAL_REGIONS[canonical] if canonical else None


def sex_chromosome(chromosome: str | None) -> SexChromosome | None:
    """``"X"`` or ``"Y"`` for a sex chromosome under any of its names, else None."""
    name = normalize_chromosome(str(chromosome or "")).upper()
    for chrom, names in XY_CHROMOSOME_NAMES.items():
        if name in names:
            return chrom
    return None


def hemizygous_chromosome(
    assembly_name: str | None, chromosome: str | None, position: int
) -> SexChromosome | None:
    """``"X"`` or ``"Y"`` where a male carries one copy, else None.

    None on an autosome, inside a PAR, and on an assembly whose PARs are not known.
    """
    chrom = sex_chromosome(chromosome)
    if chrom is None:
        return None
    regions = pseudoautosomal_regions(assembly_name)
    if regions is None:
        return None
    if any(start <= position <= end for start, end in regions[chrom]):
        return None
    return chrom
