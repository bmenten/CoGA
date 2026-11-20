"""Two samples of one person: the family's same-individual links and the people they make.

A same-individual link (``family_relationships.relationship_type`` ``same_individual``) says that
two samples are one person: the plasmas of a monogenic and a conventional NIPT of one pregnancy
under two sample IDs, say. The links join samples into people, directly or through another
sample (A with B and B with C: A, B and C are one person). What reads the pedigree per person
(the structure checks, Sample QC, the carrier screen, the mtDNA maternal line) reads it here.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Collection, Iterable, Mapping

# The relationship type of a same-individual link.
SAME_INDIVIDUAL_LINK = "same_individual"


def same_individual_groups(pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """The people the same-individual links ``pairs`` make: each group of samples the links join,
    directly or through another sample, in sorted order. A sample no link names is in none."""
    parent: dict[str, str] = {}

    def root(item: str) -> str:
        while parent.setdefault(item, item) != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for first, second in pairs:
        parent[root(first)] = root(second)
    groups: dict[str, list[str]] = defaultdict(list)
    for item in list(parent):
        groups[root(item)].append(item)
    return sorted(sorted(group) for group in groups.values())


def same_individual_pairs(relationship_rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """The same-individual links among a family's relationship rows (``relationship_type``,
    ``sample_id_a``, ``sample_id_b``), as pairs of sample IDs."""
    return [
        (str(row["sample_id_a"]).strip(), str(row["sample_id_b"]).strip())
        for row in relationship_rows
        if row.get("relationship_type") == SAME_INDIVIDUAL_LINK
        and str(row.get("sample_id_a") or "").strip()
        and str(row.get("sample_id_b") or "").strip()
    ]


def person_of(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Each sample the links ``pairs`` name, by the person it is: the first sample (in sorted
    order) of its group. A sample no link names is its own person (``person_of(...).get(s, s)``)."""
    return {sample: group[0] for group in same_individual_groups(pairs) for sample in group}


def persons(samples: Collection[str], pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """``samples`` grouped by person, each group in the order ``samples`` gives them, the groups
    in the order of their first sample."""
    person = person_of(pairs)
    grouped: dict[str, list[str]] = {}
    for sample in samples:
        grouped.setdefault(person.get(sample, sample), []).append(sample)
    return list(grouped.values())
