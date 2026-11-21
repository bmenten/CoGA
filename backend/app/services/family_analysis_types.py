"""The analyses a family holds, and the assays a sample was taken for.

A family may hold several analyses, each delivered as a package of its own: short-read and then
long-read WGS of the same DNA, or the NIPTs of several pregnancies of one woman (the owner's
decisions of 2026-11-19 and 2026-11-21). Each analysis has a key: its analysis type
(``coga_package.AnalysisType``), and for the analysis of a pregnancy (a monogenic or a conventional
NIPT) the type and the pregnancy its package names (``coga_package.CogaPackage.pregnancy_id``):
``monogenic_nipt:ZW1``. A monogenic and a conventional NIPT whose packages name the same pregnancy
are one pregnancy; two of one type with another pregnancy are two analyses.

The family's metadata holds the keys of its analyses (``analyses``), each one a package imported
into it registered (``family_package_registration``) or the one the Family Builder set, and a
sample's metadata a list of assays (``assays``), each the ``assay`` a package named for it. An
import adds its own to each list; it never takes another's away. A reader asks whether the family
holds an analysis type (``family_analysis_types``) or reads one of its analyses by its key
(``family_analyses``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .family_identifiers import identifier_problem

# Family.metadata["analyses"]: the keys of the analyses the family holds, sorted.
FAMILY_ANALYSES_KEY = "analyses"
# Sample.metadata["assays"]: the assays the sample was taken for, sorted (``nipt.NIPT_CFDNA_ASSAY``,
# ``nipt.CNIPT_CFDNA_ASSAY``).
SAMPLE_ASSAYS_KEY = "assays"
# Sample.metadata["assay_panels"]: the capture panel each analysis's package named for the sample,
# keyed by the analysis (its key, ``analysis_key``): one plasma may be the sample of a monogenic
# NIPT on a capture panel and of a conventional NIPT without one, and neither import changes the
# other's.
SAMPLE_ASSAY_PANELS_KEY = "assay_panels"

# The analysis types whose analyses are each one pregnancy's: a family holds one of each type per
# pregnancy, named by the pregnancy its package gives (the owner's decision of 2026-11-21).
PREGNANCY_ANALYSIS_TYPES: tuple[str, ...] = ("monogenic_nipt", "conventional_nipt")
# Between the analysis type and the pregnancy in the key of a pregnancy's analysis.
PREGNANCY_SEPARATOR = ":"
# The member role of a pregnancy's fetus: a member without data of its own, listed by its
# pregnancy's package (the owner's decision of 2026-11-21). A family holds one per pregnancy, two
# or more in a multiple pregnancy; a fetus is neither a proband nor a PGT embryo.
FETUS_ROLE = "fetus"
# The longest pregnancy ID a package may give.
PREGNANCY_ID_MAX_LENGTH = 64
# The longest key of an analysis a request may name: an analysis type, its separator and a
# pregnancy ID.
ANALYSIS_KEY_MAX_LENGTH = 128


def pregnancy_id_problem(value: str) -> str | None:
    """What is wrong with ``value`` as a pregnancy ID: the rule of a family or sample ID
    (``family_identifiers.identifier_problem``), at most ``PREGNANCY_ID_MAX_LENGTH`` characters.
    None when it is one."""
    problem = identifier_problem(value)
    if problem is None and len(value) > PREGNANCY_ID_MAX_LENGTH:
        problem = f"is longer than {PREGNANCY_ID_MAX_LENGTH} characters"
    return problem


def is_pregnancy_analysis_type(analysis_type: object) -> bool:
    """Whether ``analysis_type`` is one whose analyses are each one pregnancy's."""
    return isinstance(analysis_type, str) and analysis_type.strip() in PREGNANCY_ANALYSIS_TYPES


def analysis_key(analysis_type: str, pregnancy_id: str | None) -> str:
    """The key of the analysis of ``analysis_type``, of the pregnancy ``pregnancy_id`` for a
    pregnancy's analysis (``monogenic_nipt:ZW1``)."""
    analysis_type = analysis_type.strip()
    if pregnancy_id is None or not is_pregnancy_analysis_type(analysis_type):
        return analysis_type
    return f"{analysis_type}{PREGNANCY_SEPARATOR}{pregnancy_id}"


def analysis_type_of(key: object) -> str:
    """The analysis type of the analysis whose key is ``key``: the key up to the pregnancy."""
    text = str(key or "")
    return text.split(PREGNANCY_SEPARATOR, 1)[0]


def pregnancy_of(key: object) -> str | None:
    """The pregnancy of the analysis whose key is ``key``; None for an analysis of no pregnancy."""
    text = str(key or "")
    analysis_type, separator, pregnancy = text.partition(PREGNANCY_SEPARATOR)
    if not separator or not is_pregnancy_analysis_type(analysis_type):
        return None
    return pregnancy


def _values(metadata: Mapping[str, Any] | None, key: str) -> frozenset[str]:
    values = (metadata or {}).get(key)
    if not isinstance(values, list):
        return frozenset()
    return frozenset(value for value in values if isinstance(value, str) and value)


def family_analyses(metadata: Mapping[str, Any] | None) -> frozenset[str]:
    """The keys of the analyses a family's metadata holds; empty when it holds none."""
    return _values(metadata, FAMILY_ANALYSES_KEY)


def family_analysis_types(metadata: Mapping[str, Any] | None) -> frozenset[str]:
    """The analysis types of the analyses a family's metadata holds; empty when it holds none."""
    return frozenset(analysis_type_of(key) for key in family_analyses(metadata))


def family_analyses_of_type(metadata: Mapping[str, Any] | None, analysis_type: str) -> list[str]:
    """The keys of the family's analyses of ``analysis_type``, sorted: one per pregnancy for a
    pregnancy's analysis type."""
    return sorted(key for key in family_analyses(metadata) if analysis_type_of(key) == analysis_type)


def sample_assays(metadata: Mapping[str, Any] | None) -> frozenset[str]:
    """The assays a sample's metadata holds; empty when it holds none."""
    return _values(metadata, SAMPLE_ASSAYS_KEY)


def added_to(values: Iterable[str], value: str | None) -> list[str]:
    """``values`` with ``value`` added (when it is a non-empty string), sorted and without
    repeats: how an import adds its analysis to a family's, or its assay to a sample's."""
    kept = {item for item in values if isinstance(item, str) and item}
    if isinstance(value, str) and value.strip():
        kept.add(value.strip())
    return sorted(kept)


def sample_assay_panel(metadata: Mapping[str, Any] | None, analysis: str) -> str | None:
    """The capture panel the package of the analysis ``analysis`` (its key) named for a sample;
    None when it named none."""
    panels = (metadata or {}).get(SAMPLE_ASSAY_PANELS_KEY)
    panel = panels.get(analysis) if isinstance(panels, Mapping) else None
    return panel.strip() if isinstance(panel, str) and panel.strip() else None
