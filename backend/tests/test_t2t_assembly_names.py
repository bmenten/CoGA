"""T2T-CHM13 is recognised under each name it carries (found while fixing OPS-17).

The UCSC reference import names T2T-CHM13v2.0 the way UCSC's description of it does,
``T2T CHM13v2.0`` with a space (``reference_source_service._parse_assembly_name``). NCBI
writes ``T2T-CHM13v2.0``, its ClickHouse tables are keyed ``T2T_CHM13v2.0``, and UCSC calls
it ``hs1``. Several places recognised it by ``startswith("T2T-CHM13")`` alone, so the
assembly the import creates had no UCSC link on the gene page and sorted after GRCh37 in the
gene reference and in a panel's regions. One rule now reads every name,
``sex_chromosomes.is_t2t_chm13``; GRCh38 and GRCh37 read as before.

T2T is outside the validated scope (GRCh38 only, REQ-TRACE-009): this is the correct
behaviour of an off-scope assembly, not a clinical claim. All values are synthetic.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.core.clickhouse import clickhouse_dataset_key
from backend.app.services import gene_info_jobs_pg, sex_chromosomes
from backend.app.services import panel_metadata_service as pms
from backend.app.services.gene_metadata_service import _build_external_links, _ucsc_db_name
from backend.app.services.reference_source_service import _parse_assembly_name
from backend.app.services.repeat_expansion_pg import _strchive_coordinates_of

# The name the UCSC import gives the assembly (UCSC's description reads "(T2T CHM13v2.0/hs1)").
UCSC_IMPORT_NAME = "T2T CHM13v2.0"

T2T_NAMES = [
    UCSC_IMPORT_NAME,
    "T2T-CHM13v2.0",
    "T2T_CHM13v2.0",
    "T2T-CHM13",
    "t2t chm13v2.0",
    "T2T-CHM13v2",
    "CHM13v2.0",
    "hs1",
    "HS1",
    f" {UCSC_IMPORT_NAME} ",
]

NOT_T2T_CHM13_NAMES = [
    "GRCh38",
    "hg38",
    "GRCh37",
    "hg19",
    "GRCm39",
    # Another release of CHM13, with coordinates of its own on the acrocentric arms: hs1 is v2.0.
    "T2T-CHM13v1.0",
    # Another telomere-to-telomere genome.
    "T2T-YAO",
    "hs2",
    "T2T",
    "",
    None,
]


def test_the_ucsc_import_names_t2t_with_a_space() -> None:
    # The finding: the import's own name, which the startswith("T2T-CHM13") checks missed.
    assert _parse_assembly_name("Jan. 2022 (T2T CHM13v2.0/hs1)", "hs1") == UCSC_IMPORT_NAME
    # Without a parseable description it keeps UCSC's name for the genome.
    assert _parse_assembly_name("", "hs1") == "hs1"
    # Its ClickHouse tables carry the dataset key, which is a name of the same assembly.
    assert clickhouse_dataset_key(UCSC_IMPORT_NAME) == "T2T_CHM13v2.0"


@pytest.mark.parametrize("name", T2T_NAMES)
def test_every_name_of_t2t_chm13_is_recognised(name: str) -> None:
    assert sex_chromosomes.is_t2t_chm13(name) is True


@pytest.mark.parametrize("name", NOT_T2T_CHM13_NAMES)
def test_no_other_assembly_is_read_as_t2t_chm13(name: str | None) -> None:
    assert sex_chromosomes.is_t2t_chm13(name) is False


def test_t2t_has_no_grc_build_and_no_pars() -> None:
    # The PAR rule (grc_build) is unchanged: T2T has no listed PARs, so the diploid rules apply.
    assert [sex_chromosomes.grc_build(name) for name in T2T_NAMES] == [None] * len(T2T_NAMES)
    assert sex_chromosomes.pseudoautosomal_regions(UCSC_IMPORT_NAME) is None


@pytest.mark.parametrize("name", T2T_NAMES)
def test_the_gene_page_links_t2t_to_ucsc_hs1(name: str) -> None:
    assert _ucsc_db_name(name) == "hs1"
    links = {
        link.label: link.href
        for link in _build_external_links(
            symbol="BRCA1",
            gene_doc={"chr": "17", "start": 43_110_000, "end": 43_190_000},
            assembly_name=name,
            ensembl_gene_id="ENSG00000012048",
            ncbi_gene_id="672",
            hgnc_id="HGNC:1100",
            omim_gene_id="113705",
        )
    }
    assert "db=hs1" in links["UCSC"]
    # gnomAD publishes no T2T release.
    assert "gnomAD" not in links


def test_the_gene_page_links_of_grch38_and_grch37_are_unchanged() -> None:
    assert _ucsc_db_name("GRCh38") == "hg38"
    assert _ucsc_db_name("GRCh37") == "hg19"
    assert _ucsc_db_name("hg19") == "hg19"


def test_no_ucsc_link_to_hs1_for_another_chm13_release_or_assembly() -> None:
    # hs1 is T2T-CHM13v2.0: a link to it would show another release's genes at other positions.
    assert [_ucsc_db_name(name) for name in ("T2T-CHM13v1.0", "T2T-YAO", "GRCz11")] == [None] * 3


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


@pytest.mark.parametrize("t2t_name", [UCSC_IMPORT_NAME, "T2T_CHM13v2.0", "hs1", "T2T-CHM13v2.0"])
def test_the_gene_reference_puts_t2t_after_grch38_and_before_grch37(t2t_name: str) -> None:
    session = _HumanAssembliesSession(["GRCh37", "GRCm39-like", t2t_name, "GRCh38"])

    context = asyncio.run(gene_info_jobs_pg._get_human_context(session))  # type: ignore[arg-type]

    assert [row["assembly_name"] for row in context.assemblies] == ["GRCh38", t2t_name, "GRCh37", "GRCm39-like"]


class _GeneRegionsSession:
    """Answers ``_resolve_gene_regions``: BRCA1 on three assemblies, T2T and GRCh37 first."""

    def __init__(self, t2t_name: str) -> None:
        self.rows = [
            _gene_row("33333333-3333-4333-8333-333333333333", "GRCh37", 41_196_312),
            _gene_row("22222222-2222-4222-8222-222222222222", t2t_name, 44_000_000),
            _gene_row("11111111-1111-4111-8111-111111111111", "GRCh38", 43_044_295),
        ]

    async def execute(self, statement, params=None):
        assert "FROM genes g" in str(statement)
        return _Result(self.rows)


def _gene_row(assembly_id: str, assembly_name: str, start: int) -> dict:
    return {
        "symbol_key": "BRCA1",
        "assembly_id": assembly_id,
        "assembly_name": assembly_name,
        "chr": "17",
        "start": start,
        "end": start + 80_000,
    }


@pytest.mark.parametrize("t2t_name", [UCSC_IMPORT_NAME, "T2T_CHM13v2.0", "hs1", "T2T-CHM13v2.0"])
def test_a_panel_lists_the_t2t_region_after_grch38_and_before_grch37(t2t_name: str) -> None:
    regions, missing = asyncio.run(pms._resolve_gene_regions(_GeneRegionsSession(t2t_name), ["BRCA1"]))  # type: ignore[arg-type]

    assert missing == []
    assert [region.assembly for region in regions] == ["GRCh38", t2t_name, "GRCh37"]


@pytest.mark.parametrize("name", T2T_NAMES)
def test_strchive_t2t_coordinates_apply_under_every_t2t_name(name: str) -> None:
    assert _strchive_coordinates_of(name) == "t2t"


def test_strchive_coordinates_of_grch38_and_grch37_are_unchanged() -> None:
    assert {_strchive_coordinates_of(name) for name in ("GRCh38", "hg38", "grch38", "GRCh38.p14")} == {"hg38"}
    assert {_strchive_coordinates_of(name) for name in ("GRCh37", "hg19", "GRCh37.p13")} == {"hg19"}


def test_no_strchive_coordinates_on_another_chm13_release_or_assembly() -> None:
    # STRchive's T2T coordinates are on T2T-CHM13v2.0; another release's acrocentric arms
    # differ, so its calls read not assessed rather than by another position's tract.
    assert [_strchive_coordinates_of(name) for name in ("T2T-CHM13v1.0", "T2T-YAO", "GRCm39", None)] == [None] * 4
