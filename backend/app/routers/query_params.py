"""Query parameters the variant routes share."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

# A filter threshold: a finite number or absent. ``nan`` and ``inf`` are refused (422): the
# SQL and the Python filter paths compared a NaN differently, so one filter kept what the
# other dropped (CLIN-50).
FiniteThreshold = Annotated[float | None, Query(allow_inf_nan=False)]
