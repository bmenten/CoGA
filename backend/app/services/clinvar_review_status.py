"""ClinVar's review status as stars, and its conditions as a list.

The ClinVar VCF states each record's review status (CLNREVSTAT) and conditions (CLNDN). The
parser keeps the status as words (``variant_annotation_parser._tidy_clinvar``) and the
conditions as written; the variant list serves the stars ClinVar shows for the status and the
conditions one by one. One definition, for the card, the ACMG dialog and the report.
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

# CLNDN lists its conditions with "|"; VEP writes that as "&" in a CSQ.
_CONDITION_SEPARATORS = re.compile(r"[|&]")


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


def clinvar_conditions(value: Any) -> list[str]:
    """The conditions a ClinVar record names (CLNDN), each once, as words."""
    if not isinstance(value, str):
        return []
    conditions: list[str] = []
    for part in _CONDITION_SEPARATORS.split(value):
        condition = re.sub(r"\s+", " ", part.replace("_", " ")).strip()
        if condition and condition not in conditions:
            conditions.append(condition)
    return conditions
