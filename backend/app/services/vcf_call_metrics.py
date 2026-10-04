"""The caller metrics CoGA keeps with a call of a per-sample VCF.

A VCF that holds one sample describes that sample's call in its record: the record's QUAL,
FILTER and INFO are the call's own. For such a file the upload keeps the FILTER values and
a fixed set of caller metrics with each call (``calls.filters`` and ``calls.metrics``), so
an analysis of a callset stored one file per sample (the cfDNA and paternal files of a
monogenic NIPT) can apply the caller's own quality measures. A multi-sample VCF's QUAL,
FILTER and INFO describe the site, not one call, and are not copied to its calls.

Mutect2 tumour-only calls have no QUAL: their quality is TLOD. VarDict calls have a QUAL
and their own strand-bias and repeat metrics. The names are the callers' own INFO keys.
"""

from __future__ import annotations

import math

# INFO keys kept under their own name, with one value per record or per ALT allele (the
# first ALT's value is kept: CoGA stores the first ALT's depth as the call's alt reads).
_SINGLE_VALUE_KEYS: tuple[str, ...] = (
    # Mutect2
    "TLOD",
    "FS",
    "SOR",
    "MQ",
    "ECNT",
    "GERMQ",
    "POPAF",
    "MPOS",
    "ROQ",
    "SEQQ",
    "STRANDQ",
    "STRQ",
    "CONTQ",
    # VarDict
    "SBF",
    "NM",
    "MSI",
    "MSILEN",
    "VD",
)
# INFO keys with one value per allele, the reference first (VCF Number=R): the first ALT's
# value is kept under the key, the reference's under ``<key>_REF``.
_PER_ALLELE_KEYS: tuple[str, ...] = ("MMQ", "MBQ", "MFRL", "RPA")


def _number(value: str | None) -> float | None:
    if value in (None, "", "."):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def single_sample_call_metrics(info_field: str, qual: str | None) -> dict[str, float]:
    """The caller metrics of the one call of a per-sample VCF record.

    ``info_field`` is the record's raw INFO column, ``qual`` its QUAL column. QUAL is kept
    as ``QUAL`` when the record has one; the INFO flag ``STR`` (Mutect2: the variant is a
    short tandem repeat) as ``STR`` = 1, and the repeat unit ``RU`` as its length
    ``RU_LEN``. A value that is missing or not a number is left out.
    """
    metrics: dict[str, float] = {}
    record_qual = _number(qual)
    if record_qual is not None:
        metrics["QUAL"] = record_qual
    if not info_field or info_field == ".":
        return metrics
    for item in info_field.split(";"):
        if not item:
            continue
        key, separator, raw = item.partition("=")
        if not separator:
            if key == "STR":
                metrics["STR"] = 1.0
            continue
        values = raw.split(",")
        if key in _SINGLE_VALUE_KEYS:
            number = _number(values[0])
            if number is not None:
                metrics[key] = number
        elif key in _PER_ALLELE_KEYS:
            reference = _number(values[0])
            alternate = _number(values[1]) if len(values) > 1 else None
            if reference is not None:
                metrics[f"{key}_REF"] = reference
            if alternate is not None:
                metrics[key] = alternate
        elif key == "RU" and raw not in ("", "."):
            metrics["RU_LEN"] = float(len(raw))
    return metrics


def record_filter_values(filter_field: str) -> list[str]:
    """The FILTER values of a record ("PASS" kept as is); [] when FILTER is missing."""
    if filter_field in ("", "."):
        return []
    return [value for value in filter_field.split(";") if value]
