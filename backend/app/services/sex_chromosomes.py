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

# T2T-CHM13v2.0 under the names it carries: NCBI's T2T-CHM13v2.0, the "T2T CHM13v2.0" the UCSC
# reference import takes from UCSC's description, the T2T_CHM13v2.0 key of its ClickHouse
# tables, CHM13v2.0, and UCSC's hs1. A version, where the name gives one, is v2.0: hs1's.
_T2T_CHM13_NAME = re.compile(r"(?:t2t[-_ ]?)?chm13(?:v2(?:\.0)?)?|hs1")


def grc_build(assembly_name: str | None) -> str | None:
    """The Genome Reference Consortium build an assembly's name denotes, or None.

    ``GRCh38`` for GRCh38 or UCSC's hg38, ``GRCh37`` for GRCh37 or hg19, in any case and with
    or without a patch suffix (``GRCh38.p14``); None for any other assembly (T2T-CHM13, another
    species). The PARs are looked up by it, and the startup loads the shipped GRCh38
    segmental-duplication file only into an assembly it reads as GRCh38 (OPS-17). The gene
    page's UCSC and gnomAD links and the order of a list of assemblies
    (``assembly_display_rank``) read it too; the frontend reads the same rule (``grcBuild``)."""
    key = _PATCH_SUFFIX.sub("", str(assembly_name or "").strip().lower())
    return _ASSEMBLY_ALIASES.get(key)


def is_t2t_chm13(assembly_name: str | None) -> bool:
    """Does an assembly's name denote T2T-CHM13v2.0, UCSC's hs1?

    Under any of its names, in any case: ``T2T-CHM13``, ``T2T CHM13`` or ``T2T_CHM13``, with or
    without ``v2.0``, ``CHM13v2.0`` and ``hs1``. Not another CHM13 release (``T2T-CHM13v1.0``,
    whose acrocentric arms differ) nor another telomere-to-telomere genome. The UCSC link and
    the order of a gene's or a panel's assemblies read it, and the STRchive coordinates a
    repeat call is placed by; the frontend reads the same rule (``isT2tChm13``)."""
    return _T2T_CHM13_NAME.fullmatch(str(assembly_name or "").strip().lower()) is not None


def assembly_display_rank(assembly_name: str | None) -> int:
    """Where an assembly comes in a list of assemblies: GRCh38, T2T-CHM13, GRCh37, then the rest.

    0 for GRCh38, 1 for T2T-CHM13, 2 for GRCh37, each under any name ``grc_build`` and
    ``is_t2t_chm13`` read; 9 for any other assembly, which the callers sort by name. The gene
    reference lists a gene's loci in this order, and the gene page opens on the first that has
    the gene; a panel's regions follow it, and the frontend's list of panels gives their sizes
    in it (``compareAssemblyNames``)."""
    build = grc_build(assembly_name)
    if build == "GRCh38":
        return 0
    if is_t2t_chm13(assembly_name):
        return 1
    if build == "GRCh37":
        return 2
    return 9


def pseudoautosomal_regions(assembly_name: str | None) -> dict[SexChromosome, tuple[Interval, ...]] | None:
    """The PARs of an assembly, per sex chromosome, or None when they are not known."""
    build = grc_build(assembly_name)
    return _PSEUDOAUTOSOMAL_REGIONS[build] if build else None


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


def hemizygous_interval(
    assembly_name: str | None, chromosome: str | None, start: int, end: int
) -> SexChromosome | None:
    """``"X"`` or ``"Y"`` where a male carries one copy at every position of ``[start, end]``.

    None on an autosome, on an interval that reaches into a PAR, and on an assembly whose PARs
    are not known: an interval that may hold a PAR is read as two copies, never as one.
    """
    chrom = sex_chromosome(chromosome)
    if chrom is None:
        return None
    regions = pseudoautosomal_regions(assembly_name)
    if regions is None:
        return None
    if any(start <= par_end and end >= par_start for par_start, par_end in regions[chrom]):
        return None
    return chrom


# The last position of the stretch after PAR2: past the end of any chromosome.
X_STRETCH_LAST_POSITION = 2**32 - 1


def x_stretches(assembly_name: str | None, chromosome: str | None) -> tuple[tuple[int, int], ...] | None:
    """chrX cut at the PAR edges of the assembly into stretches ``(first, last)``, inclusive
    and in order: before PAR1, PAR1, the X between the PARs, PAR2 and after PAR2. None off
    chrX and on an assembly whose PAR bounds are not listed here. The haplotype blocks and
    their lineage read chrX one stretch at a time."""
    regions = pseudoautosomal_regions(assembly_name)
    if regions is None or sex_chromosome(chromosome) != "X":
        return None
    stretches: list[tuple[int, int]] = []
    first = 1
    for par_start, par_end in sorted(regions["X"]):
        if par_start > first:
            stretches.append((first, par_start - 1))
        stretches.append((par_start, par_end))
        first = par_end + 1
    stretches.append((first, X_STRETCH_LAST_POSITION))
    return tuple(stretches)
