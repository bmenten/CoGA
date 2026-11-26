"""ClinVar's review status as stars, and its conditions and a conflict's classifications as
lists.

The ClinVar VCF states each record's review status (CLNREVSTAT), conditions (CLNDN) and, for a
conflicting record, the classifications its submitters gave with their number of submissions
(CLNSIGCONF). The parser keeps the status as words (``variant_annotation_parser._tidy_clinvar``)
and the others as written; the variant list serves the stars ClinVar shows for the status, and
the conditions and the conflict's classifications one by one. One definition, for the card, the
ACMG dialog and the report.
"""

from __future__ import annotations

import re
from typing import Any

# ClinVar's review statuses by the stars it gives them
# (https://www.ncbi.nlm.nih.gov/clinvar/docs/review_status/), checked in this order on the
# status read as lower-case words: the ones without a star first, as "no assertion criteria
# provided" also holds "criteria provided".
_STARS: tuple[tuple[str, int], ...] = (
    ("no assertion", 0),
    ("no classification", 0),
    ("no interpretation", 0),
    ("practice guideline", 4),
    ("reviewed by expert panel", 3),
    ("multiple submitters, no conflicts", 2),
    ("criteria provided", 1),
)

# CLNDN and CLNSIGCONF list their values with "|"; VEP writes that as "&" in a CSQ.
_LIST_SEPARATORS = re.compile(r"[|&]")
# A CLNSIGCONF classification's number of submissions: ``Pathogenic(2)``.
_SUBMISSIONS = re.compile(r"\s*\((\d+)\)$")


def clinvar_stars(review_status: Any) -> int | None:
    """The stars ClinVar gives a review status (0–4); None without one, or for a status it
    does not list."""
    if not isinstance(review_status, str) or not review_status.strip():
        return None
    status = re.sub(r"\s+", " ", review_status.replace("_", " ")).strip().lower()
    for words, stars in _STARS:
        if words in status:
            return stars
    return None


def clinvar_listed(value: Any) -> list[str]:
    """The values a ClinVar field lists (the conditions of CLNDN, the classifications of
    CLNSIGCONF), each once, as words."""
    if not isinstance(value, str):
        return []
    listed: list[str] = []
    for part in _LIST_SEPARATORS.split(value):
        words = re.sub(r"\s+", " ", part.replace("_", " ")).strip()
        if words and words not in listed:
            listed.append(words)
    return listed


def clinvar_conflicts(value: Any) -> list[str]:
    """The classifications a conflicting ClinVar record's submitters gave (CLNSIGCONF), each
    with its number of submissions, as words: ``Pathogenic (2)``, ``Uncertain significance (1)``."""
    return [_SUBMISSIONS.sub(r" (\1)", classification) for classification in clinvar_listed(value)]


def clinvar_conflict_terms(value: Any) -> set[str]:
    """The classifications of ``clinvar_conflicts`` without their submissions, as lower-case
    words: the terms the import stores for the track's salient variants
    (``clinvar_conflict_terms``)."""
    return {_SUBMISSIONS.sub("", classification).lower() for classification in clinvar_listed(value)}
