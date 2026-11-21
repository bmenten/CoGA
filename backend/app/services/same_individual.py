"""Two samples of one person: the family's same-individual links and the people they make.

A same-individual link (``family_relationships.relationship_type`` ``same_individual``) says that
two samples are one person: the plasmas of a monogenic and a conventional NIPT of one pregnancy
under two sample IDs, or the plasmas of two pregnancies of one woman, say. One person is no one
pregnancy: the analyses of two pregnancies keep their results apart, and Sample QC never compares a
plasma, a mixture of the mother's and a fetus's DNA, for identity (``PedigreeSpec.mixtures``). The links join samples into people, directly or through another
sample (A with B and B with C: A, B and C are one person). What reads the pedigree per person
(the structure checks, Sample QC, the carrier screen, the mtDNA maternal line) reads it here.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Collection, Iterable, Mapping, TypeVar

T = TypeVar("T")

# The relationship type of a same-individual link.
SAME_INDIVIDUAL_LINK = "same_individual"

# What one person has once, whichever of its samples holds the value: a clinical status and a
# carrier status, and a sex, each as (what a message calls it, the member's field, the value that
# is not known). A value that is not known agrees with any (``person_value_conflict``).
PERSON_STATUS_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("clinical status", "clinical_status", "unknown"),
    ("carrier status", "carrier_status", "unknown"),
)
PERSON_FIELDS: tuple[tuple[str, str, str], ...] = (("sex", "sex", "und"), *PERSON_STATUS_FIELDS)


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


def person_value_conflict(
    members: Iterable[Mapping[str, Any]], fields: Iterable[tuple[str, str, str]] = PERSON_FIELDS
) -> tuple[str, list[str]] | None:
    """The first of ``fields`` (``PERSON_FIELDS``) on which the samples of one person (``members``,
    each a member's values) differ: what a message calls it, and the known values, sorted. None
    when they agree."""
    rows = list(members)
    for label, field, unknown in fields:
        known = sorted({str(row.get(field) or unknown) for row in rows} - {unknown})
        if len(known) > 1:
            return label, known
    return None


def links_per_person(
    links: Iterable[tuple[str, str, T]], pairs: Iterable[tuple[str, str]]
) -> list[tuple[str, str, T]]:
    """``links`` (two samples and what links them: a child, its parent and the parent's role, say)
    read per person, with the same-individual links ``pairs``: a link between a sample of one person
    and a sample of another holds for every sample of each. In the order of ``links``, each once."""
    samples_of = {sample: group for group in same_individual_groups(pairs) for sample in group}
    return list(
        dict.fromkeys(
            (first_sample, second_sample, value)
            for first, second, value in links
            for first_sample in samples_of.get(first, [first])
            for second_sample in samples_of.get(second, [second])
        )
    )


def persons(samples: Collection[str], pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """``samples`` grouped by person, each group in the order ``samples`` gives them, the groups
    in the order of their first sample."""
    person = person_of(pairs)
    grouped: dict[str, list[str]] = {}
    for sample in samples:
        grouped.setdefault(person.get(sample, sample), []).append(sample)
    return list(grouped.values())
