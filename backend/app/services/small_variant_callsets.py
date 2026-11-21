"""The small-variant callsets a family holds, each named by the caller that made it.

A family's small variants are stored per callset (ClickHouse ``source``). A callset of direct
calls is named by its caller: ``haplotypecaller`` (GATK HaplotypeCaller, short-read WGS),
``mutect2`` (GATK Mutect2, a NIPT plasma's calls), ``clair3`` and ``deepvariant`` (long-read
WGS), or any other caller a package names. So short-read and long-read WGS of the same DNA keep
their calls apart, and each analysis's sign-out reads its own (the owner's decisions of
2026-11-19). Which caller a pipeline runs can change with its preprocessing, so a package
declares it (``source_format``) or the VCF header names it; a VCF whose caller cannot be told is
refused until it is named, never stored under a guessed name.

Beside them: the imputed genotypes (``glimpse2``, ``shapeit``), the mtDNA calls (``mito``) and a
monogenic NIPT's per-sample calls (``nipt``), each read by rules of its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .family_package_records import source_base

# Callsets of imputed (not directly called) genotypes.
IMPUTED_CALLSETS: tuple[str, ...] = ("glimpse2", "shapeit")
# The mtDNA calls, kept apart from the nuclear callset of the same family.
MITO_CALLSET = "mito"
# A monogenic NIPT's calls stored one file per sample (the plasma's and the father's), each
# pregnancy's under its own source (``family_package_records.pregnancy_source``: ``nipt_p1a2b3c4d``).
NIPT_CALLSET = "nipt"

# The callers CoGA names in words, in the order a reader that reads one direct callset of a
# family prefers them (the Sample QC, the ancestry estimate); any other direct callset follows,
# by name.
DIRECT_CALLER_LABELS: dict[str, str] = {
    "clair3": "Clair3",
    "deepvariant": "DeepVariant",
    "haplotypecaller": "GATK HaplotypeCaller",
    "mutect2": "GATK Mutect2",
}

# A callset's name: lower-case letters, digits and underscores, starting with a letter; never
# ``auto``, which asks the loader to read the caller from the VCF header.
CALLSET_NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
AUTO = "auto"

# The header lines each caller writes, by which a VCF whose package names no caller is read.
_CALLER_LINES: dict[str, tuple[str, ...]] = {
    "haplotypecaller": ("##gatkcommandline=<id=haplotypecaller,", "##gatkcommandline.haplotypecaller="),
    "mutect2": ("##gatkcommandline=<id=mutect2,", "##gatkcommandline.mutect2=", "##source=mutect2", "##mutectversion="),
    "deepvariant": ("##deepvariant_version=", "##source=deepvariant"),
    "clair3": ("##source=clair3", "##clair3_version="),
}


class UnnamedCallerError(ValueError):
    """A small-variant VCF whose caller neither its package nor its header names."""


UNNAMED_CALLER_MESSAGE = (
    "CoGA cannot tell which caller made this small-variant VCF: its header names none of "
    + ", ".join(DIRECT_CALLER_LABELS.values())
    + ", and it holds no imputed genotypes. Name the caller (the dataset's source_format, or the "
    "callset of the upload), as each callset is stored under its caller's name."
)


# A header line naming an imputation or statistical phasing tool, outside the lines that declare a
# field, a filter or a contig (whose free text says nothing about the tool that wrote the file:
# GATK describes its physical phasing there).
_IMPUTATION_TOOL = re.compile(r"glimpse|beagle|impute|minimac|shapeit", re.IGNORECASE)
_FIELD_DECLARATION = re.compile(r"##(INFO|FORMAT|FILTER|ALT|contig)=", re.IGNORECASE)

AMBIGUOUS_CALLER_MESSAGE = (
    "This small-variant VCF's header names its caller, but the file also holds imputed or "
    "statistically phased genotypes (genotype posteriors, or an imputation or phasing tool in its "
    "header). Name its callset (the dataset's source_format, or the callset of the upload): the "
    "caller's name for its direct calls, glimpse2 for imputed genotypes."
)


def header_shows_imputation(lines: Iterable[str]) -> bool:
    """Whether a VCF's ``##`` header lines show imputed or statistically phased genotypes: they
    declare genotype posteriors (FORMAT ``GP``), or a line that declares no field names an
    imputation or statistical phasing tool."""
    return any(
        line.startswith("##FORMAT=<ID=GP,")
        or (not _FIELD_DECLARATION.match(line) and _IMPUTATION_TOOL.search(line) is not None)
        for line in lines
    )


def is_family_vcf_callset(name: object) -> bool:
    """Whether a family VCF of small variants may be stored as the callset ``name``: a caller's
    direct calls, or GLIMPSE2's imputed genotypes. The mtDNA calls, a monogenic NIPT's
    per-sample calls and SHAPEIT's phased markers are callsets read by rules of their own,
    never a family VCF's."""
    return is_direct_callset(name) or name == IMPUTED_CALLSETS[0]


def is_callset_name(value: object) -> bool:
    """Whether ``value`` can name a callset (``CALLSET_NAME``, not ``AUTO``)."""
    return isinstance(value, str) and value != AUTO and CALLSET_NAME.fullmatch(value) is not None


def is_nipt_callset(name: object) -> bool:
    """Whether a callset is a monogenic NIPT's per-sample calls: a pregnancy's ``nipt`` source."""
    return source_base(name) == NIPT_CALLSET


def is_direct_callset(name: object) -> bool:
    """Whether a callset holds a caller's own nuclear calls: any callset but the imputed
    genotypes, the mtDNA calls and a monogenic NIPT's per-sample calls."""
    return is_callset_name(name) and name not in (*IMPUTED_CALLSETS, MITO_CALLSET) and not is_nipt_callset(name)


def callset_label(name: str) -> str:
    """A callset in words: its caller's name, or the callset's own."""
    return DIRECT_CALLER_LABELS.get(name, name)


def callset_preference(name: str) -> tuple[int, int, str]:
    """The order a reader that reads one callset of a family takes them in: the direct callsets
    (the known callers first, in ``DIRECT_CALLER_LABELS`` order, then any other by name), a
    monogenic NIPT's per-sample calls, the imputed genotypes, then the rest."""
    known = list(DIRECT_CALLER_LABELS)
    if name in known:
        return (0, known.index(name), name)
    if is_direct_callset(name):
        return (0, len(known), name)
    if is_nipt_callset(name):
        return (1, 0, name)
    if name in IMPUTED_CALLSETS:
        return (2, IMPUTED_CALLSETS.index(name), name)
    return (3, 0, name)


def caller_from_vcf_header(lines: Iterable[str]) -> str | None:
    """The caller a VCF's ``##`` header lines name (``_CALLER_LINES``); None when they name
    none, or more than one (a merge of two callers' files, which its package must name)."""
    named = {
        caller
        for line in lines
        for caller, prefixes in _CALLER_LINES.items()
        if line.strip().lower().startswith(prefixes)
    }
    return named.pop() if len(named) == 1 else None
