"""Cytoband resolution shared by small and structural variants.

The band is not carried in either payload — it is looked up against the assembly's
cytoband track at display time.
"""

import json

import pytest

from backend.app.services.clickhouse_family_variants import _fetch_cytoband_label_map

# Shaped like the stored track: zero-based half-open bands, `name` without the chromosome.
CHR13_BANDS = [
    {"name": "p11.2", "start": 10100000, "end": 16500000, "stain": "gvar"},
    {"name": "q13.1", "start": 32000000, "end": 33500000, "stain": "gneg"},
    {"name": "q13.2", "start": 33500000, "end": 35100000, "stain": "gpos"},
]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.params: dict = {}

    async def execute(self, _query, params):
        self.params = params
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_small_variant_position_resolves_to_its_band() -> None:
    session = _FakeSession([{"chr": "13", "bands": CHR13_BANDS}])

    labels = await _fetch_cytoband_label_map(
        session,
        assembly_id="11111111-1111-1111-1111-111111111111",
        loci=[("brca2-var", "13", 32316461, 32316461)],
    )

    assert labels == {"brca2-var": "13q13.1"}


@pytest.mark.asyncio
async def test_span_across_two_bands_is_written_as_a_range() -> None:
    session = _FakeSession([{"chr": "13", "bands": CHR13_BANDS}])

    labels = await _fetch_cytoband_label_map(
        session,
        assembly_id="assembly",
        loci=[("sv1", "13", 32316461, 34000000)],
    )

    assert labels == {"sv1": "13q13.1-13q13.2"}


@pytest.mark.asyncio
async def test_bands_stored_as_json_text_are_parsed() -> None:
    session = _FakeSession([{"chr": "13", "bands": json.dumps(CHR13_BANDS)}])

    labels = await _fetch_cytoband_label_map(
        session,
        assembly_id="assembly",
        loci=[("brca2-var", "13", 32316461, 32316461)],
    )

    assert labels == {"brca2-var": "13q13.1"}


@pytest.mark.asyncio
async def test_position_outside_every_band_yields_no_label() -> None:
    session = _FakeSession([{"chr": "13", "bands": CHR13_BANDS}])

    labels = await _fetch_cytoband_label_map(
        session,
        assembly_id="assembly",
        loci=[("intergenic", "13", 999, 999)],
    )

    assert labels == {}


@pytest.mark.asyncio
async def test_chromosome_is_queried_under_both_aliases() -> None:
    session = _FakeSession([{"chr": "chr13", "bands": CHR13_BANDS}])

    labels = await _fetch_cytoband_label_map(
        session,
        assembly_id="assembly",
        loci=[("brca2-var", "chr13", 32316461, 32316461)],
    )

    # The track may store either spelling, so both are asked for and matched normalized.
    assert session.params["chromosomes"] == ["13", "chr13"]
    assert labels == {"brca2-var": "13q13.1"}


@pytest.mark.asyncio
async def test_no_assembly_or_no_loci_skips_the_query() -> None:
    session = _FakeSession([{"chr": "13", "bands": CHR13_BANDS}])

    assert await _fetch_cytoband_label_map(session, assembly_id=None, loci=[("v", "13", 1, 1)]) == {}
    assert await _fetch_cytoband_label_map(session, assembly_id="assembly", loci=[]) == {}
    assert session.params == {}
