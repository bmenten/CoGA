"""Where a male carries a single copy of the genome: chrX and chrY outside the PARs (#545).

A male has one X and one Y. Outside the pseudo-autosomal regions (PAR1 and PAR2) he
carries one copy of each, so a variant there is hemizygous. A caller writes it as a
haploid ``1`` or as a diploid ``1/1``, never as ``0/1``. Inside the PARs, X and Y pair like
an autosome, and a male is diploid.

The boundaries are 1-based and inclusive: the Genome Reference Consortium's for GRCh38 and
GRCh37, and for T2T-CHM13v2.0 (UCSC's hs1) the T2T consortium's, as NCBI's assembly states them
too (CLIN-12; the sources are named beside them). An assembly whose boundaries are not listed
here (another species, another CHM13 release) has no hemizygous positions as far as this module
is concerned, so the diploid rules apply there, rather than guessed boundaries.
"""

from __future__ import annotations

import re
from typing import Literal

from .data_scope import normalize_chromosome

SexChromosome = Literal["X", "Y"]
Interval = tuple[int, int]

# The key of T2T-CHM13v2.0's PARs, under whichever of its names ``is_t2t_chm13`` reads.
T2T_CHM13 = "T2T-CHM13v2.0"

_PSEUDOAUTOSOMAL_REGIONS: dict[str, dict[SexChromosome, tuple[Interval, ...]]] = {
    "GRCh38": {
        "X": ((10_001, 2_781_479), (155_701_383, 156_030_895)),
        "Y": ((10_001, 2_781_479), (56_887_903, 57_217_415)),
    },
    "GRCh37": {
        "X": ((60_001, 2_699_520), (154_931_044, 155_260_560)),
        "Y": ((10_001, 2_649_520), (59_034_050, 59_363_566)),
    },
    # T2T-CHM13v2.0 (CLIN-12), from two sources that agree, both read on 2026-11-04:
    # - the T2T consortium's analysis set, chm13v2.0_PAR.bed
    #   (https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/CHM13/assemblies/analysis_set/chm13v2.0_PAR.bed,
    #   linked from https://github.com/marbl/CHM13; 78 bytes, SHA-256
    #   9ba7f1ed5dfd88107356ff71965655a40f1d29ead912e7fc926c3f14d54300b3), in BED's 0-based,
    #   half-open coordinates: chrX 0-2394410 and 153925834-154259566, chrY 0-2458320 and
    #   62122809-62460029;
    # - NCBI's region report of the same assembly, GCF_009914755.1 (GCA_009914755.4)
    #   (https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/009/914/755/GCF_009914755.1_T2T-CHM13v2.0/
    #   GCF_009914755.1_T2T-CHM13v2.0_assembly_regions.txt), 1-based and inclusive: PAR#1 X
    #   1-2394410, PAR#2 X 153925835-154259566, PAR#1 Y 1-2458320, PAR#2 Y 62122810-62460029.
    # The BED's start plus one is NCBI's start, its end NCBI's end. Held here as NCBI states them,
    # 1-based and inclusive like the GRC's above. Both PARs reach the chromosome's ends: PAR1
    # starts at its first base (a T2T assembly has no telomeric gap), and PAR2 ends at its last
    # (chrX 154,259,566 and chrY 62,460,029 bases, NCBI's assembly report).
    T2T_CHM13: {
        "X": ((1, 2_394_410), (153_925_835, 154_259_566)),
        "Y": ((1, 2_458_320), (62_122_810, 62_460_029)),
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
    species). The PARs of GRCh38 and GRCh37 are looked up by it (T2T-CHM13's by
    ``is_t2t_chm13``), and the startup loads the shipped GRCh38 segmental-duplication file only
    into an assembly it reads as GRCh38 (OPS-17). The gene
    page's UCSC and gnomAD links and the order of a list of assemblies
    (``assembly_display_rank``) read it too; the frontend reads the same rule (``grcBuild``)."""
    key = _PATCH_SUFFIX.sub("", str(assembly_name or "").strip().lower())
    return _ASSEMBLY_ALIASES.get(key)


def is_t2t_chm13(assembly_name: str | None) -> bool:
    """Does an assembly's name denote T2T-CHM13v2.0, UCSC's hs1?

    Under any of its names, in any case: ``T2T-CHM13``, ``T2T CHM13`` or ``T2T_CHM13``, with or
    without ``v2.0``, ``CHM13v2.0`` and ``hs1``. Not another CHM13 release (``T2T-CHM13v1.0``,
    whose acrocentric arms differ) nor another telomere-to-telomere genome. The UCSC link and
    the order of a gene's or a panel's assemblies read it, the STRchive coordinates a repeat
    call is placed by, and the PARs (CLIN-12); the frontend reads the same rule
    (``isT2tChm13``)."""
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
    """The PARs of an assembly, per sex chromosome, or None when they are not known.

    GRCh38 and GRCh37 under any name ``grc_build`` reads, T2T-CHM13v2.0 under any name
    ``is_t2t_chm13`` reads; None for any other assembly. Every reader of the PARs comes here:
    the hemizygous calls and their SQL, the de novo and X-linked rules, the haplotype blocks and
    their lineage, the phased markers, the PGT blocks' one-copy mark and the repeat calls of a
    male's chrX."""
    build = grc_build(assembly_name)
    if build is not None:
        return _PSEUDOAUTOSOMAL_REGIONS[build]
    if is_t2t_chm13(assembly_name):
        return _PSEUDOAUTOSOMAL_REGIONS[T2T_CHM13]
    return None


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
