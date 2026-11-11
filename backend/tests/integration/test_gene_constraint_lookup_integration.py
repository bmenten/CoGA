"""The gene constraint a small variant is served and classified with, on real Postgres.

A variant's gene pLI and missense Z come from its gene's profile in ``gene_info``, which holds
one profile per assembly. The variant list, the ranking, the evidence snapshot a classification
freezes and the drift check read them through one lookup
(``gene_constraint_metrics.fetch_gene_constraint_metrics``). It reads the profile of the
family's assembly, never another assembly's refreshed later, and matches a symbol in any case,
so a gene written otherwise than its profile has its constraint.

Here two assemblies each hold a profile of one gene, the other assembly's refreshed later, and
the first holds a gene whose symbol is stored in mixed case. The lookup in each assembly reads
that assembly's profile, a symbol is found whatever its case, and without an assembly nothing is
read. Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import fresh_assembly, fresh_species, run_closing_stores

pytestmark = pytest.mark.integration


async def _seed(session_factory, tag: str) -> dict[str, str]:
    """Two assemblies, each with a profile of the gene ``T15G1<tag>``, the second refreshed a day
    later; and in the first, the gene ``T15g2<tag>``, its symbol stored in mixed case."""
    from sqlalchemy import text

    async with session_factory() as session:
        species = await fresh_species(session, f"gene-constraint {tag}")
        assemblies: dict[str, str] = {}
        for name in ("first", "second"):
            assemblies[name] = await fresh_assembly(session, species, f"GENECONSTRAINT{name.upper()}{tag}")
        for assembly, symbol, extra, days_old in (
            ("first", f"T15G1{tag}", {"mis_z": 2.0, "pLI": 0.1}, 1),
            ("second", f"T15G1{tag}", {"mis_z": 9.0, "pLI": 0.9}, 0),
            ("first", f"T15g2{tag}", {"mis_z": 3.3}, 1),
        ):
            await session.execute(
                text(
                    "INSERT INTO gene_info (assembly_id, hgnc_symbol, extra, updated_at) "
                    "VALUES (CAST(:a AS uuid), :s, CAST(:e AS jsonb), "
                    "now() - make_interval(days => CAST(:days AS integer)))"
                ),
                {"a": assemblies[assembly], "s": symbol, "e": json.dumps(extra), "days": days_old},
            )
        await session.commit()
    return assemblies


def test_each_familys_gene_constraint_is_read_from_its_own_assembly_whatever_the_symbols_case() -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.gene_constraint_metrics import fetch_gene_constraint_metrics

    tag = uuid4().hex[:10].upper()
    gene, mixed_case = f"T15G1{tag}", f"T15g2{tag}"

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        assemblies = await _seed(session_factory, tag)
        genes = [
            ("gene", gene, None),
            # The mixed-case symbol asked for as a variant's annotation may write it.
            ("upper", mixed_case.upper(), None),
            ("lower", mixed_case.lower(), None),
        ]
        async with session_factory() as session:
            first = await fetch_gene_constraint_metrics(session, genes, assembly_id=assemblies["first"])
            second = await fetch_gene_constraint_metrics(session, genes, assembly_id=assemblies["second"])
            none = await fetch_gene_constraint_metrics(session, genes, assembly_id=None)

        # Each assembly's own profile, not the second assembly's, the newer one.
        assert first["gene"] == {"gene_pli": 0.1, "gene_missense_z": 2.0}
        assert second["gene"] == {"gene_pli": 0.9, "gene_missense_z": 9.0}
        # Found whatever the case it is asked in.
        assert first["upper"] == first["lower"] == {"gene_pli": None, "gene_missense_z": 3.3}
        # Only the first assembly holds it.
        assert "upper" not in second and "lower" not in second
        # Without the family's assembly no profile is read.
        assert none == {}

    run_closing_stores(_scenario)
