"""GRCh38 and GRCh37 under each name ``grc_build`` reads (found while fixing #1013).

The UCSC reference import names an assembly as UCSC's description of it does, ``GRCh38``, and
by UCSC's own name for the genome, ``hg38``, where the description does not parse
(``reference_source_service._parse_assembly_name``). ``sex_chromosomes.grc_build`` reads GRCh38
or hg38, GRCh37 or hg19, in any case and with or without a patch suffix (``GRCh38.p14``); the
PARs are looked up by it (#1006). The gene page's UCSC and gnomAD links and the order of a gene's
assemblies and of a panel's regions read the exact name ``GRCh38`` alone (GRCh37 or hg19 for
GRCh37): a GRCh38 assembly named ``hg38`` or ``GRCh38.p14`` had no UCSC or gnomAD link on the
gene page and came last, after T2T and GRCh37. They now read ``grc_build``, and both lists take
one order, ``sex_chromosomes.assembly_display_rank``: GRCh38, T2T-CHM13, GRCh37, then the rest.

The exact names read as before. All values are synthetic.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.services import gene_info_jobs_pg, sex_chromosomes
from backend.app.services import panel_metadata_service as pms
from backend.app.services.gene_metadata_service import (
    _build_external_links,
    _gnomad_dataset_name,
    _ucsc_db_name,
)
from backend.app.services.reference_source_service import _parse_assembly_name

# Each name grc_build reads, with the build it denotes. The exact names GRCh38, GRCh37 and
# hg19 were read before; the others were not.
GRC_NAMES = [
    ("GRCh38", "GRCh38"),
    ("hg38", "GRCh38"),
    ("GRCh38.p14", "GRCh38"),
    ("grch38", "GRCh38"),
    ("GRCh37", "GRCh37"),
    ("hg19", "GRCh37"),
    ("GRCh37.p13", "GRCh37"),
    ("HG19", "GRCh37"),
]

UCSC_DB = {"GRCh38": "hg38", "GRCh37": "hg19"}
GNOMAD_DATASET = {"GRCh38": "gnomad_r4", "GRCh37": "gnomad_r2_1"}
RANK = {"GRCh38": 0, "GRCh37": 2}

# The UCSC import's name for T2T-CHM13v2.0 (#1013), and an assembly of none of the three:
# another CHM13 release, whose name sorts before GRCh37.
T2T_NAME = "T2T CHM13v2.0"
OTHER_NAME = "CHM13v1.1"


def test_the_ucsc_import_names_grch38_hg38_where_the_description_does_not_parse() -> None:
    assert _parse_assembly_name("Dec. 2013 (GRCh38/hg38)", "hg38") == "GRCh38"
    assert _parse_assembly_name("", "hg38") == "hg38"
    assert _parse_assembly_name("", "hg19") == "hg19"


@pytest.mark.parametrize(("name", "build"), GRC_NAMES)
def test_grc_build_reads_each_name(name: str, build: str) -> None:
    # The rule the PARs are looked up by (#1006), which the links and the order now follow.
    assert sex_chromosomes.grc_build(name) == build


def _gene_page_links(assembly_name: str) -> dict[str, str]:
    return {
        link.label: link.href
        for link in _build_external_links(
            symbol="BRCA1",
            gene_doc={"chr": "17", "start": 43_044_295, "end": 43_125_483},
            assembly_name=assembly_name,
            ensembl_gene_id="ENSG00000012048",
            ncbi_gene_id="672",
            hgnc_id="HGNC:1100",
            omim_gene_id="113705",
        )
    }


@pytest.mark.parametrize(("name", "build"), GRC_NAMES)
def test_the_gene_page_links_ucsc_and_gnomad_under_each_grc_name(name: str, build: str) -> None:
    # Before: no UCSC and no gnomAD link for hg38, GRCh38.p14, grch38, GRCh37.p13 or HG19.
    assert _ucsc_db_name(name) == UCSC_DB[build]
    assert _gnomad_dataset_name(name) == GNOMAD_DATASET[build]
    links = _gene_page_links(name)
    assert f"db={UCSC_DB[build]}" in links["UCSC"]
    assert links["gnomAD"].endswith(f"?dataset={GNOMAD_DATASET[build]}")


@pytest.mark.parametrize(("name", "build"), GRC_NAMES)
def test_the_gene_reference_ranks_each_grc_name_by_its_build(name: str, build: str) -> None:
    # Before: 9, the rank of an unknown assembly, for every name but GRCh38, GRCh37 and hg19.
    assert gene_info_jobs_pg._assembly_priority(name) == (RANK[build], name)


def test_one_order_for_a_gene_s_assemblies_and_a_panel_s_regions() -> None:
    # GRCh38, then T2T-CHM13, then GRCh37, each under any of its names; then any other assembly.
    # The frontend orders a panel's sizes the same way (assemblyDisplayRank).
    names = {
        0: ["GRCh38", "hg38", "GRCh38.p14", "grch38"],
        1: [T2T_NAME, "T2T-CHM13v2.0", "hs1"],
        2: ["GRCh37", "hg19", "GRCh37.p13", "HG19"],
        9: [OTHER_NAME, "GRCh38_no_alt", "GRCm39", "", None],
    }
    for rank, assembly_names in names.items():
        ranks = [sex_chromosomes.assembly_display_rank(name) for name in assembly_names]
        assert ranks == [rank] * len(assembly_names)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _HumanAssembliesSession:
    """Answers ``_get_human_context``: the human species, then its assemblies in table order."""

    def __init__(self, assembly_names: list[str]) -> None:
        self.assemblies = [
            {"id": f"00000000-0000-4000-8000-00000000000{index}", "assembly_name": name, "version": "v"}
            for index, name in enumerate(assembly_names, start=1)
        ]

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM species" in sql:
            return _Result([{"id": "species-1", "name": "Homo sapiens", "common_name": "human"}])
        if "FROM assemblies" in sql:
            return _Result(self.assemblies)
        raise AssertionError(f"unexpected SQL: {sql}")


# The exact names, as before; UCSC's names; and the patch releases.
ORDER_CASES = [("GRCh38", "GRCh37"), ("hg38", "hg19"), ("GRCh38.p14", "GRCh37.p13")]


@pytest.mark.parametrize(("grch38_name", "grch37_name"), ORDER_CASES)
def test_the_gene_reference_lists_grch38_t2t_grch37_then_the_rest(grch38_name: str, grch37_name: str) -> None:
    # The gene page lists a gene's loci in this order, and opens on the first that has the gene.
    session = _HumanAssembliesSession([OTHER_NAME, grch37_name, T2T_NAME, grch38_name])

    context = asyncio.run(gene_info_jobs_pg._get_human_context(session))  # type: ignore[arg-type]

    assert [row["assembly_name"] for row in context.assemblies] == [grch38_name, T2T_NAME, grch37_name, OTHER_NAME]


class _GeneRegionsSession:
    """Answers ``_resolve_gene_regions``: BRCA1 on four assemblies, GRCh38 last."""

    def __init__(self, assembly_names: list[str]) -> None:
        self.rows = [
            {
                "symbol_key": "BRCA1",
                "assembly_id": f"00000000-0000-4000-8000-00000000000{index}",
                "assembly_name": name,
                "chr": "17",
                "start": 43_000_000 + index,
                "end": 43_080_000 + index,
            }
            for index, name in enumerate(assembly_names, start=1)
        ]

    async def execute(self, statement, params=None):
        assert "FROM genes g" in str(statement)
        return _Result(self.rows)


@pytest.mark.parametrize(("grch38_name", "grch37_name"), ORDER_CASES)
def test_a_panel_lists_regions_grch38_t2t_grch37_then_the_rest(grch38_name: str, grch37_name: str) -> None:
    session = _GeneRegionsSession([OTHER_NAME, grch37_name, T2T_NAME, grch38_name])

    regions, missing = asyncio.run(pms._resolve_gene_regions(session, ["BRCA1"]))  # type: ignore[arg-type]

    assert missing == []
    assert [region.assembly for region in regions] == [grch38_name, T2T_NAME, grch37_name, OTHER_NAME]


def test_the_exact_names_and_the_other_assemblies_read_as_before() -> None:
    assert [_ucsc_db_name(name) for name in ("GRCh38", "GRCh37", "hg19", T2T_NAME)] == ["hg38", "hg19", "hg19", "hs1"]
    assert [_ucsc_db_name(name) for name in ("GRCm39", "GRCm38", "EquCab3.0")] == ["mm39", "mm10", "equCab3"]
    assert [_gnomad_dataset_name(name) for name in ("GRCh38", "GRCh37", "hg19")] == [
        "gnomad_r4",
        "gnomad_r2_1",
        "gnomad_r2_1",
    ]
    # gnomAD publishes no T2T and no mouse release.
    assert [_gnomad_dataset_name(name) for name in (T2T_NAME, "GRCm38")] == [None, None]


def test_no_grc_link_for_a_name_grc_build_does_not_read() -> None:
    # The rule IGV, the PARs and the reference loading read an assembly by: a name it does not
    # read is no GRC build anywhere, and gets no GRCh38 or GRCh37 link rather than a guessed one.
    for name in ("GRCh38_no_alt", "GRCh37-lite", "hg18", OTHER_NAME):
        assert sex_chromosomes.grc_build(name) is None
        assert (_ucsc_db_name(name), _gnomad_dataset_name(name)) == (None, None)
        assert gene_info_jobs_pg._assembly_priority(name) == (9, name)
        links = _gene_page_links(name)
        assert "UCSC" not in links and "gnomAD" not in links
