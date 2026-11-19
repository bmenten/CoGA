"""The analyses a family holds, and the assays a sample was taken for.

A family may hold several analyses: the monogenic and the conventional NIPT of one pregnancy, each
delivered as a package of its own (the owner's decision of 2026-11-19). So the family's metadata
holds a list of analysis types (``analysis_types``), each the ``analysis_type`` of a package
imported into it (``coga_package.AnalysisType``) or the one the Family Builder set, and a sample's
metadata a list of assays (``assays``), each the ``assay`` a package named for it. An import adds
its own to each list (``family_package_registration``); it never takes another's away. Every
reader asks whether a list holds a value, never which one it holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Family.metadata["analysis_types"]: the analyses the family holds, sorted.
FAMILY_ANALYSIS_TYPES_KEY = "analysis_types"
# Sample.metadata["assays"]: the assays the sample was taken for, sorted (``nipt.NIPT_CFDNA_ASSAY``,
# ``nipt.CNIPT_CFDNA_ASSAY``).
SAMPLE_ASSAYS_KEY = "assays"


def _values(metadata: Mapping[str, Any] | None, key: str) -> frozenset[str]:
    values = (metadata or {}).get(key)
    if not isinstance(values, list):
        return frozenset()
    return frozenset(value for value in values if isinstance(value, str) and value)


def family_analysis_types(metadata: Mapping[str, Any] | None) -> frozenset[str]:
    """The analysis types a family's metadata holds; empty when it holds none."""
    return _values(metadata, FAMILY_ANALYSIS_TYPES_KEY)


def sample_assays(metadata: Mapping[str, Any] | None) -> frozenset[str]:
    """The assays a sample's metadata holds; empty when it holds none."""
    return _values(metadata, SAMPLE_ASSAYS_KEY)


def added_to(values: Iterable[str], value: str | None) -> list[str]:
    """``values`` with ``value`` added (when it is a non-empty string), sorted and without
    repeats: how an import adds its analysis type to a family's, or its assay to a sample's."""
    kept = {item for item in values if isinstance(item, str) and item}
    if isinstance(value, str) and value.strip():
        kept.add(value.strip())
    return sorted(kept)
