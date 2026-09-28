"""One classification of a VCF genotype, for every filter, count and presence check (#511).

Genotypes are stored exactly as the VCF writes them. The filters, presets, inheritance
checks and counts each used to carry a literal diploid, biallelic list (``0/1``, ``1/0``,
``0|1``, ``1|0``, ``1/1``, ``1|1``, ``0/0``), so a haploid call (``1`` or ``0``: chrM, and
chrX/chrY in males from callers that emit ploidy-1 calls) and a multi-allelic call
(``1/2``, ``0/2``, ``2/2``) matched none of them: a "Hom" or X-linked filter dropped a
hemizygous alt call, and a sample whose calls were all haploid could look as if it had no
small variants at all.

Every genotype now falls in exactly one class:

* ``hom_alt`` — every allele called and the same ALT allele: ``1/1``, ``2|2``, haploid ``1``.
* ``het`` — at least one ALT allele, and not ``hom_alt``: ``0/1``, ``1/2``, ``0/2``, and a
  half call such as ``./1``, which carries an ALT allele but cannot be called homozygous.
* ``hom_ref`` — every allele ``0``: ``0/0``, ``0|0``, haploid ``0``.
* ``no_call`` — everything else: ``./.``, ``.``, empty, a half reference call ``./0``, and
  anything that is not a VCF genotype.

Python code calls :func:`classify_genotype`. ClickHouse queries use
:func:`clickhouse_genotype_condition`: a set lookup against every genotype string of up to
three characters (all that single-digit allele indices can produce), with an exact
allele-by-allele fallback for longer strings (allele index 10 or more), so both paths
classify every genotype the same way.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable, Literal

GenotypeClass = Literal["hom_alt", "het", "hom_ref", "no_call"]

HOM_ALT: GenotypeClass = "hom_alt"
HET: GenotypeClass = "het"
HOM_REF: GenotypeClass = "hom_ref"
NO_CALL: GenotypeClass = "no_call"
GENOTYPE_CLASSES: tuple[GenotypeClass, ...] = (HOM_ALT, HET, HOM_REF, NO_CALL)
# Carries at least one ALT allele.
ALT_CLASSES: frozenset[GenotypeClass] = frozenset({HOM_ALT, HET})

_ALLELE_SEPARATOR = re.compile(r"[/|]")
_MISSING_ALLELES = frozenset({".", ""})


def _is_allele(value: str) -> bool:
    return value in _MISSING_ALLELES or (value.isascii() and value.isdigit())


def classify_genotype(gt: str | None) -> GenotypeClass:
    """The class of a VCF ``GT`` value (see the module docstring)."""
    raw = (gt or "").strip()
    if not raw:
        return NO_CALL
    alleles = _ALLELE_SEPARATOR.split(raw)
    if not all(_is_allele(allele) for allele in alleles):
        return NO_CALL
    if all(allele == "0" for allele in alleles):
        return HOM_REF
    if not any(allele not in _MISSING_ALLELES and allele != "0" for allele in alleles):
        return NO_CALL
    if not any(allele in _MISSING_ALLELES for allele in alleles) and len(set(alleles)) == 1:
        return HOM_ALT
    return HET


def genotype_has_alt(gt: str | None) -> bool:
    """True when the genotype carries at least one ALT allele (``het`` or ``hom_alt``)."""
    return classify_genotype(gt) in ALT_CLASSES


@lru_cache(maxsize=1)
def _short_genotype_classes() -> dict[str, GenotypeClass]:
    """Every genotype string of up to three characters, with its class.

    That is: empty, each single allele (haploid) and each pair of single-character alleles
    joined by ``/`` or ``|``. Longer strings need a multi-digit allele index and are
    classified in SQL by :func:`_long_genotype_sql`.
    """
    symbols = [str(index) for index in range(10)] + ["."]
    strings = [""] + symbols + [f"{a}{separator}{b}" for a in symbols for b in symbols for separator in "/|"]
    return {value: classify_genotype(value) for value in strings}


def genotype_vocabulary(*classes: GenotypeClass) -> tuple[str, ...]:
    """The genotype strings of up to three characters that fall in ``classes``, sorted."""
    wanted = set(classes)
    return tuple(sorted(value for value, cls in _short_genotype_classes().items() if cls in wanted))


def _long_genotype_sql(column: str, classes: frozenset[GenotypeClass]) -> str:
    """Classify a genotype string longer than three characters from its alleles, in SQL.

    Mirrors :func:`classify_genotype` for the called classes: an allele is a digit string
    or missing (``.`` or empty), and anything else is not a called genotype.
    """
    alleles = f"splitByRegexp('[/|]', {column})"
    valid = f"arrayAll(x -> x IN ('.', '') OR isNotNull(toUInt64OrNull(x)), {alleles})"
    has_alt = f"arrayExists(x -> x NOT IN ('0', '.', ''), {alleles})"
    has_missing = f"arrayExists(x -> x IN ('.', ''), {alleles})"
    single_allele = f"length(arrayDistinct({alleles})) = 1"
    expressions: dict[GenotypeClass, str] = {
        HOM_REF: f"({valid} AND arrayAll(x -> x = '0', {alleles}))",
        HOM_ALT: f"({valid} AND {has_alt} AND NOT {has_missing} AND {single_allele})",
        HET: f"({valid} AND {has_alt} AND ({has_missing} OR NOT {single_allele}))",
    }
    return " OR ".join(expressions[cls] for cls in GENOTYPE_CLASSES if cls in classes)


def _called_condition(column: str, classes: frozenset[GenotypeClass], param: str, params: dict[str, Any]) -> str:
    params[param] = genotype_vocabulary(*classes)
    return (
        f"({column} IN %({param})s OR "
        f"(length({column}) > 3 AND ({_long_genotype_sql(column, classes)})))"
    )


def clickhouse_genotype_condition(
    column: str,
    classes: Iterable[GenotypeClass],
    *,
    param: str,
    params: dict[str, Any],
) -> str:
    """SQL condition: the genotype in ``column`` falls in one of ``classes``.

    The common case is a set lookup; the allele-by-allele fallback only runs for strings
    longer than three characters, which ClickHouse short-circuits past for the rest.
    ``no_call`` is the complement of the three called classes, so every string lands in
    exactly one class, as in Python. ``column`` may be a lambda parameter (for use inside
    ``arrayExists``).
    """
    wanted = frozenset(classes)
    unknown = wanted.difference(GENOTYPE_CLASSES)
    if unknown:
        raise ValueError(f"unknown genotype class(es): {sorted(unknown)}")
    if not wanted:
        return "0"
    called = wanted - {NO_CALL}
    parts: list[str] = []
    if called:
        parts.append(_called_condition(column, called, param, params))
    if NO_CALL in wanted:
        every_called = frozenset({HOM_ALT, HET, HOM_REF})
        parts.append(f"NOT {_called_condition(column, every_called, f'{param}_called', params)}")
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
