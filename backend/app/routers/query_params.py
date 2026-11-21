"""Query parameters the variant routes share."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from ..services.family_analysis_types import ANALYSIS_KEY_MAX_LENGTH

# A filter threshold: a finite number or absent. ``nan`` and ``inf`` are refused (422): the
# SQL and the Python filter paths compare a NaN differently, so one filter would keep what
# the other drops.
FiniteThreshold = Annotated[float | None, Query(allow_inf_nan=False)]

# The family's analysis whose small or structural variants a request reads or reviews
# (analysis_scope.py), by its key: named when the family holds several analyses with variants of
# that kind of their own. A review is that analysis's (analysis_reviews.py).
AnalysisParam = Annotated[
    str | None,
    Query(
        max_length=ANALYSIS_KEY_MAX_LENGTH,
        description="The family's analysis to read or review (its key: monogenic_nipt:ZW1).",
    ),
]

# The family's analysis a request of one of its NIPT pages reads, by its key (one pregnancy's
# monogenic or conventional NIPT: ``monogenic_nipt:ZW1``): named when the family holds that NIPT of
# several pregnancies (nipt.nipt_analysis_of).
NiptAnalysisParam = Annotated[
    str | None,
    Query(
        max_length=ANALYSIS_KEY_MAX_LENGTH,
        description="The pregnancy's NIPT to read, by the analysis's key (monogenic_nipt:ZW1).",
    ),
]
