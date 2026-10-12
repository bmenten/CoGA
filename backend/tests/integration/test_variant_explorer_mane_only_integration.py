"""The Variant Explorer's *MANE only* against real ClickHouse (CLIN-5).

The family search keeps a variant with a MANE Select or a MANE Plus Clinical transcript;
the explorer read ``has_mane_select`` alone, so a variant whose MANE transcript is MANE
Plus Clinical was missing from its results. Three variants go in through the import's
write path (``insert_small_variant_records``), annotated as the import parses VEP's CSQ:
one whose only MANE transcript is MANE Plus Clinical, one with a MANE Select transcript and
one with neither. With *MANE only* the explorer's search and its CSV export keep the first
two and drop the third; without it they list all three.

As in ``test_variant_explorer_keyset_integration``, ``resolve_scope`` is monkeypatched to
a fresh project, so only these rows are counted. The variants sit at positions no other
test writes, because the annotation index is keyed by variant, not by project.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_MANE_PLUS_CLINICAL = "1-81500101-C-T"
_MANE_SELECT = "1-81500201-C-T"
_NEITHER = "1-81500301-C-T"


def _vep_annotations(*entries: str) -> list[dict[str, Any]]:
    from backend.app.services.variant_annotation_parser import (
        AnnotationHeaderState,
        extract_small_variant_annotations,
        update_annotation_header_state,
    )

    state = AnnotationHeaderState()
    update_annotation_header_state(
        state,
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl'
        " VEP. Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL"
        '|MANE_SELECT|MANE_PLUS_CLINICAL">',
    )
    return extract_small_variant_annotations({"CSQ": ",".join(entries)}, state)


def _record(variant_id: str, annotations: list[dict[str, Any]]):
    from backend.app.services.clickhouse_variant_records import (
        SmallVariantCall,
        SmallVariantRecord,
    )

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos),
        ref=ref, alt=alt, source="test", rsid=None, filters=["PASS"], gene_symbols=[],
        annotations=annotations,
        calls=[SmallVariantCall(sample="S1", gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
        qual=100.0,
    )


def test_explorer_mane_only_keeps_mane_select_and_mane_plus_clinical(monkeypatch) -> None:
    import asyncio

    import backend.app.services.variant_explorer_service as svc
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    project = str(uuid4())
    scope = svc.ExplorerScope(assembly_id=str(uuid4()), assembly_name=_ASSEMBLY, project_ids=[project])

    async def _fake_scope(session, user, assembly_id):
        return scope

    monkeypatch.setattr(svc, "resolve_scope", _fake_scope)

    async def _run() -> None:
        # The schema and tables are made here, so the test does not depend on an earlier
        # test (or the app's startup) having made them.
        await init_postgres_schema()
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        await insert_small_variant_records(
            _ASSEMBLY,
            str(uuid4()),
            [project],
            [
                # The canonical transcript is not MANE; the other one is MANE Plus Clinical.
                _record(
                    _MANE_PLUS_CLINICAL,
                    _vep_annotations(
                        "T|missense_variant|MODERATE|GENE1|ENSG1|Transcript|ENST11|||NM_PLUS1.1",
                        "T|intron_variant|MODIFIER|GENE1|ENSG1|Transcript|ENST12|YES||",
                    ),
                ),
                _record(
                    _MANE_SELECT,
                    _vep_annotations("T|missense_variant|MODERATE|GENE2|ENSG2|Transcript|ENST21|YES|NM_SELECT2.1|"),
                ),
                _record(
                    _NEITHER,
                    _vep_annotations("T|missense_variant|MODERATE|GENE3|ENSG3|Transcript|ENST31|YES||"),
                ),
            ],
        )

        async with get_postgres_sessionmaker()() as session:

            async def listed(**filters: Any) -> tuple[int, set[str]]:
                page = await svc.search_global_small_variants(
                    session,
                    user=None,
                    filters=svc.GlobalVariantFilters(**filters),
                    assembly_id=scope.assembly_id,
                )
                return page.total, {variant.variant_id for variant in page.variants}

            async def exported(**filters: Any) -> set[str]:
                _assembly, rows = await svc.export_global_small_variants(
                    session,
                    user=None,
                    filters=svc.GlobalVariantFilters(**filters),
                    assembly_id=scope.assembly_id,
                )
                return {variant.variant_id for variant in rows}

            assert await listed() == (3, {_MANE_PLUS_CLINICAL, _MANE_SELECT, _NEITHER})
            assert await listed(mane_only=True) == (2, {_MANE_PLUS_CLINICAL, _MANE_SELECT})
            assert await exported(mane_only=True) == {_MANE_PLUS_CLINICAL, _MANE_SELECT}

    async def _wrapped() -> None:
        try:
            await _run()
        finally:
            await close_clickhouse_client()
            await close_postgres_engine()

    asyncio.run(_wrapped())
