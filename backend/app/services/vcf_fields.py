"""A VCF record's INFO column, and a sample's column read by the record's FORMAT keys.

One parser of each, for every reader that splits a record itself: the TRGT repeat calls
(``repeat_expansion_pg``), the structural-variant uploads (``structural_variant_ingest``), the
small-variant uploads and per-sample callsets (``variant_upload_service``,
``per_sample_small_variants``) and the family-package readers (``family_package_variants``,
``family_package_tracks``). And ``field_int``, a whole number from such a field's text, for
the structural-variant uploads and the VEP table parser (``annotation_table_parser``).
"""

from __future__ import annotations

from typing import Any


def field_int(value: Any) -> int | None:
    """A field's text read as a whole number, tolerating a decimal (``"12.0"`` and ``"12.7"``
    are 12) and surrounding spaces. None for a missing value (``None``, ``""``, ``"."``) and
    for one that reads as no finite number (``"junk"``, ``"inf"``, ``"nan"``), so that a
    malformed record is skipped rather than ending the whole read."""
    if value is None or str(value).strip() in {"", "."}:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError, OverflowError):
            return None


def parse_info(info_field: str, *, flags: bool = False) -> dict[str, str]:
    """The INFO column as key -> value: each ``KEY=VALUE`` item, split at its first ``=``.

    A flag (an item without ``=``) is left out, or with ``flags`` read as ``"true"``: the
    family-package readers keep flags, the other readers leave them out. An empty item, and a
    missing INFO (``""`` or ``"."``), give nothing. A key given twice keeps its last value.
    """
    info: dict[str, str] = {}
    if not info_field or info_field == ".":
        return info
    for item in info_field.split(";"):
        key, separator, value = item.partition("=")
        if separator:
            info[key] = value
        elif flags and item:
            info[item] = "true"
    return info


def parse_format(format_field: str, sample_field: str) -> dict[str, str]:
    """A sample's column as FORMAT key -> value, the two split at ``:`` and paired in order.

    A sample field may carry fewer values than the FORMAT keys (trailing fields dropped, as
    VCF allows): the keys without a value are left out, never paired with another key's value.
    An empty field splits into one empty item, so an empty sample field gives the first key
    the value ``""`` (``{"GT": ""}`` for ``GT:DP``).
    """
    return dict(zip(format_field.split(":"), sample_field.split(":")))
