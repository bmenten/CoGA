"""Cytoband resolution shared by small variants, structural variants and the clinical CNV
catalogue.

The band is not carried in any of their payloads — it is looked up against the assembly's
cytoband track at display time. A clinical CNV keeps the band its file stated.
"""

import json

import pytest

from backend.app.services import reference_metadata_service
from backend.app.services.cytoband_labels import fetch_cytoband_labels as _fetch_cytoband_label_map

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

    def first(self):
        return self._rows[0] if self._rows else None


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


@pytest.mark.asyncio
async def test_a_position_on_a_band_edge_is_in_the_band_that_starts_there() -> None:
    # Bands are zero-based and half-open: q13.1 ends where q13.2 starts, at zero-based
    # 33,500,000, the 1-based position 33,500,001.
    session = _FakeSession([{"chr": "13", "bands": CHR13_BANDS}])

    labels = await _fetch_cytoband_label_map(
        session,
        assembly_id="assembly",
        loci=[("last", "13", 33500000, 33500000), ("first", "13", 33500001, 33500001)],
    )

    assert labels == {"last": "13q13.1", "first": "13q13.2"}


@pytest.mark.asyncio
async def test_a_chromosome_without_cytobands_gives_no_label() -> None:
    # T2T has no cytoband table: each chromosome is one band named after it, which names no
    # band. The mitochondrion has none either.
    session = _FakeSession(
        [
            {"chr": "chr21", "bands": [{"name": "chr21", "start": 0, "end": 45090682, "stain": "gneg"}]},
            {"chr": "chrM", "bands": [{"name": "", "start": 0, "end": 16569, "stain": "gneg"}]},
        ]
    )

    labels = await _fetch_cytoband_label_map(
        session,
        assembly_id="assembly",
        loci=[("t2t", "chr21", 1000, 2000), ("mito", "chrM", 3243, 3243)],
    )

    assert labels == {}


class _CatalogSession:
    """The assembly, the catalogue's rows and the cytoband track, answered by what each
    statement reads."""

    def __init__(self, cnv_rows: list[dict], band_rows: list[dict]) -> None:
        self._cnv_rows = cnv_rows
        self._band_rows = band_rows
        self.track_reads = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM assemblies" in sql:
            return _FakeResult([{"id": "11111111-1111-1111-1111-111111111111", "assembly_name": "GRCh38", "version": "p14"}])
        if "FROM clinical_cnvs" in sql:
            return _FakeResult(self._cnv_rows)
        if "FROM chromosomes" in sql:
            self.track_reads += 1
            return _FakeResult(self._band_rows)
        raise AssertionError(f"unexpected statement: {sql}")


def _cnv_row(cnv_id: str, start: int, end: int, cytoband: str | None) -> dict:
    return {
        "id": cnv_id,
        "chr": "13",
        "start": start,
        "end": end,
        "type": "Loss",
        "label": f"SYNTHETIC {cnv_id}",
        "details_html": None,
        "omim_id": None,
        "omim_title": None,
        "decipher_id": None,
        "description": None,
        "cytoband": cytoband,
        "source_id": None,
        "orpha_id": None,
        "orpha_name": None,
        "clinvar_pathogenic_loss_count": None,
        "clinvar_pathogenic_gain_count": None,
        "clinvar_pathogenic_accessions": None,
    }


@pytest.mark.asyncio
async def test_the_cnv_catalogue_reads_a_missing_cytoband_from_the_track() -> None:
    session = _CatalogSession(
        [
            _cnv_row("cnv-span", 32316461, 34000000, None),
            _cnv_row("cnv-empty", 32316461, 32400000, ""),
            _cnv_row("cnv-stated", 32316461, 34000000, "13q13"),
            _cnv_row("cnv-off-track", 999, 1999, None),
        ],
        [{"chr": "13", "bands": CHR13_BANDS}],
    )

    cnvs = await reference_metadata_service.list_clinical_cnvs_catalog_data(session, assembly="GRCh38")

    # A band the file stated stays as stated; a region outside every band has none.
    assert {cnv.id: cnv.cytoband for cnv in cnvs} == {
        "cnv-span": "13q13.1-13q13.2",
        "cnv-empty": "13q13.1",
        "cnv-stated": "13q13",
        "cnv-off-track": None,
    }
    assert session.track_reads == 1


@pytest.mark.asyncio
async def test_a_clinical_cnv_read_by_id_or_region_gets_its_cytoband_too() -> None:
    row = {**_cnv_row("22222222-2222-2222-2222-222222222222", 32316461, 32400000, None), "assembly_id": "11111111-1111-1111-1111-111111111111"}
    session = _CatalogSession([row], [{"chr": "chr13", "bands": CHR13_BANDS}])

    by_id = await reference_metadata_service.get_clinical_cnv_by_id_data(
        session, cnv_id="22222222-2222-2222-2222-222222222222"
    )
    by_region = await reference_metadata_service.get_clinical_cnvs_data(
        session, assembly="GRCh38", chrom="13", start=0, end=0
    )

    assert by_id.cytoband == "13q13.1"
    assert [cnv.cytoband for cnv in by_region] == ["13q13.1"]


@pytest.mark.asyncio
async def test_a_catalogue_without_unlabelled_cnvs_does_not_read_the_track() -> None:
    session = _CatalogSession([_cnv_row("cnv-stated", 1, 2, "13p11.2")], [])

    cnvs = await reference_metadata_service.list_clinical_cnvs_catalog_data(session, assembly="GRCh38")

    assert [cnv.cytoband for cnv in cnvs] == ["13p11.2"]
    assert session.track_reads == 0
