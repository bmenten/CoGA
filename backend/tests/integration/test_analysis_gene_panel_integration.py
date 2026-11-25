"""A monogenic NIPT's diagnostic gene panel, set, changed and cleared on real Postgres.

The analysis is read and reported within the gene panel the analyst sets for it
(REQ-NIPT-019). This sets a panel for one pregnancy's analysis, sets the same one again, changes
it, edits the panel (a new version), clears it, and checks that each change, and only a change,
left one clinical audit event with the panel before and after on the family's chain, that the
other pregnancy's analysis kept its own panel, that a panel that does not exist is refused, and
that a panel an analysis is read within cannot be deleted.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.tests.integration._datastores import admin_account, fresh_family

pytestmark = pytest.mark.integration


async def _panel(session, user_id: str, genes: list[str]) -> str:
    panel = (
        await session.execute(
            text("INSERT INTO gene_panels (name, created_by) VALUES (:n, CAST(:u AS uuid)) RETURNING id::text"),
            {"n": f"diagnostic panel {uuid4()}", "u": user_id},
        )
    ).scalar_one()
    for gene in genes:
        await session.execute(
            text("INSERT INTO gene_panel_genes (panel_id, gene) VALUES (CAST(:p AS uuid), :g)"),
            {"p": panel, "g": gene},
        )
    return panel


def test_a_diagnostic_panel_is_set_changed_and_cleared_on_the_audit_trail() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.analysis_gene_panel import (
        DIAGNOSTIC_PANEL_ACTION,
        load_analysis_gene_panel,
        set_analysis_gene_panel,
    )
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain
    from backend.app.services.hash_chain import canonical_hash
    from backend.app.services.panel_metadata_service import delete_panel_data

    first, second = "monogenic_nipt:ZW1", "monogenic_nipt:ZW2"

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                user = await admin_account(s, f"panel-analyst-{uuid4().hex[:8]}")
                family_uuid = await fresh_family(s, f"DIAGPANEL{uuid4().hex[:8].upper()}")
                family_id = (
                    await s.execute(
                        text("SELECT family_id FROM families WHERE id = CAST(:f AS uuid)"), {"f": family_uuid}
                    )
                ).scalar_one()
                panel_a = await _panel(s, user.id, ["GENEB", "GENEA"])
                panel_b = await _panel(s, user.id, ["GENEC"])
                await s.commit()

            def change(panel_id: str | None, analysis: str = first):
                return set_analysis_gene_panel(
                    s,
                    family_uuid=family_uuid,
                    family_identifier=family_id,
                    analysis=analysis,
                    analysis_label=f"monogenic NIPT of pregnancy {analysis.split(':')[1]}",
                    panel_id=panel_id,
                    user=user,
                )

            async with sm() as s:
                assert await load_analysis_gene_panel(s, family_uuid=family_uuid, analysis=first) is None
                set_a = await change(panel_a)
                assert set_a is not None and set_a.panel_id == panel_a
                assert (set_a.genes, set_a.gene_symbols) == (("GENEA", "GENEB"), ("GENEA", "GENEB"))
                # The same panel again changes nothing.
                again = await change(panel_a)
                assert again is not None and again.panel_id == panel_a
                # The other pregnancy keeps a panel of its own.
                await change(panel_b, analysis=second)
                set_b = await change(panel_b)
                assert set_b is not None and set_b.panel_id == panel_b
                # A panel that does not exist, or no panel id at all, is refused.
                for missing in (str(uuid4()), "not-a-uuid"):
                    with pytest.raises(HTTPException) as refused:
                        await change(missing)
                    assert refused.value.status_code == 404
                # A panel an analysis is read within stays.
                with pytest.raises(HTTPException) as in_use:
                    await delete_panel_data(s, panel_b, user)
                assert in_use.value.status_code == 409
                assert "diagnostic panel of 2 analyses" in str(in_use.value.detail)
                assert await change(None) is None
                assert await change(None) is None
                other = await load_analysis_gene_panel(s, family_uuid=family_uuid, analysis=second)
                assert other is not None and other.panel_id == panel_b

            async with sm() as s:
                events = (
                    await s.execute(
                        text(
                            "SELECT before, after, metadata, actor FROM clinical_audit_events "
                            "WHERE family_id = CAST(:f AS uuid) AND action = :action ORDER BY created_at, id"
                        ),
                        {"f": family_uuid, "action": DIAGNOSTIC_PANEL_ACTION},
                    )
                ).mappings().all()
                # Set A, set B on the second pregnancy, change A to B, clear: four changes.
                assert [(e["metadata"]["analysis"], (e["before"] or {}).get("panel_id"), (e["after"] or {}).get("panel_id"))
                        for e in events] == [
                    (first, None, panel_a),
                    (second, None, panel_b),
                    (first, panel_a, panel_b),
                    (first, panel_b, None),
                ]
                assert {e["actor"] for e in events} == {user.username}
                assert events[0]["after"]["genes_digest"] == canonical_hash(["GENEA", "GENEB"])
                chain = await verify_clinical_audit_chain(s, family_id)
                assert chain.verified, chain.reason
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
