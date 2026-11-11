"""The diseases each gene of a page of variant cards is linked to, on real Postgres.

A small-variant or monogenic NIPT card names its gene's diseases under its heading and lists
them all in its expanded detail, from one lookup for the page
(``gene_metadata_service.list_gene_disease_links``). It reads Monarch's gene–disease links and
the OMIM diseases of the gene reference (``gene_info.extra``), matches a symbol whatever its
case, leaves out a Monarch link without a disease name, and orders Monarch's links causal first,
then by name, as the gene profile does.

Here one gene has three Monarch links (two causal, stored out of order) and two without a name,
its symbol stored in mixed case, and an OMIM disease in the gene reference of two assemblies; a
second gene has neither. Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see
conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import fresh_assembly, fresh_species, run_closing_stores

pytestmark = pytest.mark.integration


async def _seed(session_factory, gene: str) -> None:
    from sqlalchemy import text

    async with session_factory() as session:
        species = await fresh_species(session, f"gene-disease-links {gene}")
        for assembly, label in (("A", "Syndrome from assembly A"), ("B", "Syndrome from assembly B")):
            assembly_id = await fresh_assembly(session, species, f"GENEDISEASE{assembly}{gene}")
            await session.execute(
                text(
                    "INSERT INTO gene_info (assembly_id, hgnc_symbol, extra) "
                    "VALUES (CAST(:a AS uuid), :s, CAST(:e AS jsonb))"
                ),
                {"a": assembly_id, "s": gene, "e": json.dumps({"omim_diseases": [{"label": label, "omim_id": "100100"}]})},
            )
        for mondo, label, causal in (
            ("1", "zeta syndrome", True),
            ("2", "Alpha disease", False),
            ("3", "beta syndrome", True),
            ("4", None, True),
            ("5", "", True),
        ):
            await session.execute(
                text(
                    "INSERT INTO monarch_gene_disease "
                    "(hgnc_id, gene_symbol, mondo_id, disease_label, predicate, sources, causal) "
                    "VALUES (:h, :s, :m, :l, 'causes', CAST(:src AS jsonb), :c)"
                ),
                {
                    "h": f"HGNC:{gene}",
                    "s": gene,
                    "m": f"MONDO:{gene}{mondo}",
                    "l": label,
                    "src": json.dumps(["omim"]),
                    "c": causal,
                },
            )
        await session.commit()


async def _clean(session_factory, gene: str) -> None:
    from sqlalchemy import text

    async with session_factory() as session:
        await session.execute(text("DELETE FROM monarch_gene_disease WHERE gene_symbol = :s"), {"s": gene})
        await session.execute(text("DELETE FROM gene_info WHERE hgnc_symbol = :s"), {"s": gene})
        await session.commit()


def test_a_page_reads_each_genes_disease_links_whatever_the_symbols_case() -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.gene_metadata_service import list_gene_disease_links

    tag = uuid4().hex[:10].upper()
    gene, other = f"Gdl{tag}", f"GDLNONE{tag}"

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        await _seed(session_factory, gene)
        try:
            async with session_factory() as session:
                links = await list_gene_disease_links(session, symbols=[gene.upper(), other, gene.lower()])
        finally:
            await _clean(session_factory, gene)

        # One entry per gene asked, under the spelling asked first.
        assert [entry.symbol for entry in links] == [gene.upper(), other]
        found, none = links
        # Causal first, then by name whatever its case; no link without a name.
        assert [(link.disease_label, link.causal) for link in found.monarch] == [
            ("beta syndrome", True),
            ("zeta syndrome", True),
            ("Alpha disease", False),
        ]
        assert found.monarch[0].monarch_url == f"https://monarchinitiative.org/MONDO:{gene}3"
        # One assembly's gene reference, by the assembly order (here: by name).
        assert [(disease.label, disease.href) for disease in found.omim] == [
            ("Syndrome from assembly A", "https://www.omim.org/entry/100100"),
        ]
        assert (none.monarch, none.omim) == ([], [])

    run_closing_stores(_scenario)
