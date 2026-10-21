"""A family's PED text, built from its stored structure (SAFE-8).

The family's members (their sex and clinical status) and its parent-child relationships are the
pedigree; the PED text the family page draws is written from them, one row per active member, so
the two never disagree. The text stored on the family (``families.pedigree``) is the one its
last import or Family Builder save wrote, and could: a package import of an existing family
replaces it but not the stored relationships or sexes (#893), and a Family Builder request with a
parent ID of spaces alone wrote a row of five fields, which shifted the drawing's columns.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# The order the rows are written in: the parents, the proband, the siblings, then the others,
# each group by sample ID.
_ROLE_ORDER = {"father": 0, "mother": 1, "proband": 2, "sibling": 3}


def sex_code(sex: str | None) -> str:
    """PED's sex column: 1 male, 2 female, 0 unknown."""
    return {"male": "1", "female": "2"}.get(sex or "", "0")


def phenotype_code(clinical_status: str | None) -> str:
    """PED's phenotype column: 2 affected, 1 unaffected, 0 unknown."""
    if clinical_status == "affected":
        return "2"
    if clinical_status == "unaffected":
        return "1"
    return "0"


@dataclass(frozen=True)
class PedMember:
    sample_id: str
    sex: str | None
    clinical_status: str | None
    role: str | None


def pedigree_text(
    family_id: str,
    members: Iterable[PedMember],
    *,
    fathers: Mapping[str, str],
    mothers: Mapping[str, str],
    order: Sequence[str] = (),
) -> str:
    """The PED text of ``members``, each with the father and mother its sample ID maps to in
    ``fathers`` and ``mothers`` (0 for none), one space-separated row per member, ending in a
    newline; empty without members.

    The rows follow ``order`` (sample IDs: the order the family's own PED text lists them in,
    which the drawing places siblings and couples by), then the members it does not list, by
    role and sample ID."""
    position = {sample_id: index for index, sample_id in enumerate(order)}
    ordered = sorted(
        members,
        key=lambda member: (
            position.get(member.sample_id, len(position)),
            _ROLE_ORDER.get(member.role or "", 4),
            member.sample_id.lower(),
        ),
    )
    rows = [
        " ".join(
            [
                family_id,
                member.sample_id,
                fathers.get(member.sample_id, "0"),
                mothers.get(member.sample_id, "0"),
                sex_code(member.sex),
                phenotype_code(member.clinical_status or "unknown"),
            ]
        )
        for member in ordered
    ]
    return "\n".join(rows) + "\n" if rows else ""
