"""The monogenic NIPT analyses a worker process keeps between requests (nipt_analysis_cache).

A kept analysis is served only for the same analysis from equal inputs; concurrent requests
share one computation; one during which the family's data version moved is answered but not
kept, and a failed one is not kept. Synthetic inputs only.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from backend.app.services.analysis_scope import AnalysisScope
from backend.app.services.clinvar_coga import NO_CLINVAR_COGA
from backend.app.services.nipt_analysis_cache import NiptAnalysisCache, NiptAnalysisInputs, NiptAnalysisKey
from backend.app.services.nipt_qc_gate import EXPECTED_FETAL_READS, resolve_nipt_gate_bound
from backend.app.services.nipt_target_coverage import TargetCoverageRow

KEY = NiptAnalysisKey(
    family_uuid="family-uuid",
    assembly_name="GRCh38",
    project_ids=("project-uuid",),
    analysis="monogenic_nipt",
    father_sample_id="FATHER1",
    cfdna_sample_id="CFDNA1",
    fetus_sample_ids=("FETUS1",),
    assay_key="panel-v2",
    thresholds=(20, 5),
)
INPUTS = NiptAnalysisInputs(
    data_version="1:7",
    scope=AnalysisScope(analysis="monogenic_nipt"),
    samples={"FATHER1": "father-uuid", "CFDNA1": "cfdna-uuid"},
    listed={"7-100-A-G": 10},
    gate_bound=resolve_nipt_gate_bound({}),
    plasma_targets=[TargetCoverageRow(chrom="7", start=0, end=200, gene="GENEA", attribute="GENEA", mean=900.0)],
    father_targets=[],
    clinvar_coga=NO_CLINVAR_COGA,
)


class _Analyses:
    """A computation of the analysis, counted, and the family's data version it reads after."""

    def __init__(self, *, version: str | None = "1:7") -> None:
        self.computed: list[str] = []
        self.version = version
        self.release = asyncio.Event()
        self.release.set()

    async def compute(self) -> dict[str, Any]:
        self.computed.append("analysis")
        await self.release.wait()
        return {"analysis": len(self.computed)}

    async def data_version(self) -> str | None:
        return self.version


async def _read(cache: NiptAnalysisCache[Any], analyses: _Analyses, key=KEY, inputs=INPUTS) -> Any:
    return await cache.analysis(key, inputs, analyses.compute, analyses.data_version)


@pytest.mark.asyncio
async def test_an_analysis_is_kept_for_the_same_analysis_from_equal_inputs() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses()
    first = await _read(cache, analyses)
    # Equal, not the same objects: each request reads its inputs again.
    again = await _read(cache, analyses, inputs=replace(INPUTS, listed=dict(INPUTS.listed)))
    assert again is first and analyses.computed == ["analysis"]


@pytest.mark.parametrize(
    "changed",
    [
        {"data_version": "2:9"},
        {"scope": AnalysisScope(analysis="monogenic_nipt_2")},
        {"samples": {"FATHER1": "father-uuid", "CFDNA1": "other-cfdna-uuid"}},
        {"listed": {}},
        {"gate_bound": resolve_nipt_gate_bound({EXPECTED_FETAL_READS: {"warn_value": 60.0, "error_value": 50.0}})},
        {"plasma_targets": [replace(INPUTS.plasma_targets[0], mean=400.0)]},
        {"father_targets": INPUTS.plasma_targets},
    ],
    ids=lambda changed: next(iter(changed)),
)
@pytest.mark.asyncio
async def test_an_analysis_is_computed_again_once_an_input_changed(changed: dict[str, Any]) -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses(version=changed.get("data_version", INPUTS.data_version))
    first = await _read(cache, analyses)
    second = await _read(cache, analyses, inputs=replace(INPUTS, **changed))
    assert second != first and len(analyses.computed) == 2


@pytest.mark.asyncio
async def test_another_analysis_is_computed_apart() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses()
    await _read(cache, analyses)
    await _read(cache, analyses, key=replace(KEY, fetus_sample_ids=("FETUS1", "FETUS2")))
    await _read(cache, analyses, key=replace(KEY, project_ids=("other-project",)))
    assert len(analyses.computed) == 3


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_computation() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses()
    analyses.release.clear()
    summary = asyncio.ensure_future(_read(cache, analyses))
    variants = asyncio.ensure_future(_read(cache, analyses))
    await asyncio.sleep(0)
    analyses.release.set()
    assert await summary is await variants
    assert analyses.computed == ["analysis"]


@pytest.mark.asyncio
async def test_an_analysis_whose_data_changed_while_it_ran_is_answered_but_not_kept() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    # The family's variants were written while the analysis read them: the version moved.
    analyses = _Analyses(version="2:9")
    assert await _read(cache, analyses) == {"analysis": 1}
    assert await _read(cache, analyses) == {"analysis": 2}


@pytest.mark.asyncio
async def test_a_failed_computation_fails_each_request_and_is_not_kept() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses()
    analyses.release.clear()
    failures = 0

    async def unreadable() -> Any:
        nonlocal failures
        failures += 1
        await analyses.release.wait()
        raise RuntimeError("ClickHouse is not answering")

    reads = [asyncio.ensure_future(cache.analysis(KEY, INPUTS, unreadable, analyses.data_version)) for _ in range(2)]
    await asyncio.sleep(0)
    analyses.release.set()
    outcomes = await asyncio.gather(*reads, return_exceptions=True)
    assert [type(outcome) for outcome in outcomes] == [RuntimeError, RuntimeError] and failures == 1
    assert await _read(cache, analyses) == {"analysis": 1}


@pytest.mark.asyncio
async def test_a_request_that_goes_away_leaves_the_computation_to_the_others() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses()
    analyses.release.clear()
    gone = asyncio.ensure_future(_read(cache, analyses))
    staying = asyncio.ensure_future(_read(cache, analyses))
    await asyncio.sleep(0)
    gone.cancel()
    analyses.release.set()
    assert await staying == {"analysis": 1}
    assert await _read(cache, analyses) == {"analysis": 1} and analyses.computed == ["analysis"]


@pytest.mark.asyncio
async def test_the_least_recently_read_analysis_goes_first() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache(max_kept=2)
    analyses = _Analyses()
    first, second, third = (replace(KEY, family_uuid=f"family-{index}") for index in range(3))
    await _read(cache, analyses, key=first)
    await _read(cache, analyses, key=second)
    await _read(cache, analyses, key=first)
    await _read(cache, analyses, key=third)
    assert len(analyses.computed) == 3
    await _read(cache, analyses, key=first)
    assert len(analyses.computed) == 3
    await _read(cache, analyses, key=second)
    assert len(analyses.computed) == 4


@pytest.mark.asyncio
async def test_a_cleared_cache_computes_again() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses()
    await _read(cache, analyses)
    cache.clear()
    await _read(cache, analyses)
    assert len(analyses.computed) == 2


@pytest.mark.asyncio
async def test_an_older_computation_that_ends_last_leaves_the_newer_analysis_kept() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    older, newer = _Analyses(), _Analyses()
    older.release.clear()
    newer.release.clear()
    before = asyncio.ensure_future(_read(cache, older))
    listed_since = replace(INPUTS, listed={})
    after = asyncio.ensure_future(_read(cache, newer, inputs=listed_since))
    await asyncio.sleep(0)
    newer.release.set()
    kept = await after
    older.release.set()
    await before
    assert await _read(cache, newer, inputs=listed_since) is kept and newer.computed == ["analysis"]


@pytest.mark.asyncio
async def test_a_computation_running_when_the_cache_is_cleared_is_not_kept() -> None:
    cache: NiptAnalysisCache[Any] = NiptAnalysisCache()
    analyses = _Analyses()
    analyses.release.clear()
    running = asyncio.ensure_future(_read(cache, analyses))
    await asyncio.sleep(0)
    cache.clear()
    analyses.release.set()
    await running
    await _read(cache, analyses)
    assert len(analyses.computed) == 2
