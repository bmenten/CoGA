"""A family's monogenic NIPT analysis, kept between requests.

Every NIPT view (the summary, the variant list, the statistics, the report's candidates, the
sign-out) reads the family's analysis (``nipt_service._FamilyAnalysis``): the fetal fraction,
what the assay's artifact list did and what the family's records give each other's
observations. Computing it reads the family's whole callset, which a plasma's every low-level
Mutect2 call makes a million rows or more: close to a minute and gigabytes of memory. So each
worker process keeps the last few analyses it computed, without the records they were computed
from.

A kept analysis is served only to a request that reads the same analysis (``NiptAnalysisKey``)
from inputs equal to those it was computed from (``NiptAnalysisInputs``): the family's
small-variant data version, which every write of the family's variants moves, and every other
input, read again for each request. An analysis is therefore never served once any of them
changed. One computation of an analysis from given inputs runs at a time, and the requests
that ask for it meanwhile (the NIPT page asks for its summary and its variant list at once)
share it; it runs on when they go away. A computation during which the family's data version
moved is answered, as each request would have read it without a cache, but not kept, and a
failed one is not kept either.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from .analysis_scope import AnalysisScope
from .clinvar_coga import ClinvarCogaRead
from .nipt_qc_gate import NiptGateBound
from .nipt_target_coverage import TargetCoverageRow

# How many analyses a worker process keeps. One holds no record: its size is that of its
# summary (the sites past the filters), its artifact list and its per-target coverage.
MAX_KEPT_ANALYSES = 8

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class NiptAnalysisKey:
    """Which analysis: the family's, on one assembly, in the projects it is read in, of one
    monogenic NIPT (its pregnancy's analysis, father, plasma and fetuses), against one assay's
    artifact list, with given thresholds (``astuple`` of ``NiptQualityThresholds``)."""

    family_uuid: str
    assembly_name: str | None
    project_ids: tuple[str, ...]
    analysis: str | None
    father_sample_id: str
    cfdna_sample_id: str
    fetus_sample_ids: tuple[str, ...]
    assay_key: str
    thresholds: tuple[float, ...]


@dataclass(slots=True)
class NiptAnalysisInputs:
    """What an analysis is computed from besides the family's records: their data version
    (``get_family_small_variant_data_version``), what they are read with (the analysis's
    scope and the family's samples), the assay's artifact list, the QC gate's bound and the
    plasma's and the father's per-target coverage, and CoGA's ClinVar release."""

    data_version: str | None
    scope: AnalysisScope
    samples: Mapping[str, str]
    listed: dict[str, int]
    gate_bound: NiptGateBound
    plasma_targets: list[TargetCoverageRow]
    father_targets: list[TargetCoverageRow]
    # CoGA's ClinVar release the analysis reads: the protection of the listed artefacts, the
    # triage and the diagnostic candidates read ClinVar there alone.
    clinvar_coga: ClinvarCogaRead


@dataclass(slots=True)
class _Entry(Generic[T]):
    """An analysis computed, or being computed, from ``inputs``."""

    inputs: NiptAnalysisInputs
    task: asyncio.Task[T]


class NiptAnalysisCache(Generic[T]):
    """The analyses one worker process keeps (see the module's docstring), the most recently
    read last."""

    def __init__(self, max_kept: int = MAX_KEPT_ANALYSES) -> None:
        self._max_kept = max_kept
        self._entries: OrderedDict[NiptAnalysisKey, _Entry[T]] = OrderedDict()

    async def analysis(
        self,
        key: NiptAnalysisKey,
        inputs: NiptAnalysisInputs,
        compute: Callable[[], Awaitable[T]],
        data_version: Callable[[], Awaitable[str | None]],
    ) -> T:
        """The analysis ``key`` names, computed from ``inputs`` (``compute``): the one kept or
        being computed from equal inputs, else a new computation. ``data_version`` reads the
        family's data version again once it is computed."""
        entry = self._entries.get(key)
        if entry is None or entry.inputs != inputs:
            entry = _Entry(inputs, asyncio.ensure_future(self._computed(key, inputs, compute, data_version)))
            self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_kept:
            self._entries.popitem(last=False)
        # Shielded: a request that goes away leaves the computation running for the others.
        return await asyncio.shield(entry.task)

    async def _computed(
        self,
        key: NiptAnalysisKey,
        inputs: NiptAnalysisInputs,
        compute: Callable[[], Awaitable[T]],
        data_version: Callable[[], Awaitable[str | None]],
    ) -> T:
        """The analysis ``compute`` gives; let go when it failed or when the family's data
        version moved while it was computed."""
        try:
            analysis = await compute()
            moved = await data_version() != inputs.data_version
        except BaseException:
            self._let_go(key)
            raise
        if moved:
            self._let_go(key)
        return analysis

    def _let_go(self, key: NiptAnalysisKey) -> None:
        """Forget the running computation's entry, unless a newer one has replaced it."""
        entry = self._entries.get(key)
        if entry is not None and entry.task is asyncio.current_task():
            del self._entries[key]

    def clear(self) -> None:
        """Forget every kept and running analysis: the next request computes its own."""
        self._entries.clear()
