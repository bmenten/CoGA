"""Integration test: a reference import writes its release into the real schema.

The unit tests (test_reference_import_versions.py) pin what each import path records; this
one runs the loaders against the real `reference_dataset_imports` table, so the INSERT
they share is checked against the schema itself. Everything is written in a transaction
that is rolled back, on a species and assembly of its own.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_the_loaders_record_the_stated_release_or_not_stated() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services import reference_metadata_service as rms

    gene_row = {
        "gene_id": "ENST00000357654.9",
        "hgnc_symbol": "BRCA1",
        "chr": "17",
        "start": 43044295,
        "end": 43125364,
        "exons": json.dumps([{"name": "exon1", "start": 43125271, "end": 43125364}]),
        "strand": -1,
        "biotype": "protein_coding",
        "description": "",
        "source": "gencode",
        "extra": json.dumps({}),
    }

    async def _run() -> None:
        try:
            await init_postgres_schema()
            async with get_postgres_sessionmaker()() as session:
                try:
                    species_id = (
                        await session.execute(
                            text(
                                "INSERT INTO species (name, common_name, tax_id) "
                                "VALUES ('Integratio testis', 'integration', 999999001) "
                                "RETURNING id::text"
                            )
                        )
                    ).scalar_one()
                    assembly_id = (
                        await session.execute(
                            text(
                                "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                                "VALUES (CAST(:species_id AS uuid), 'ITEST1', 'v1', DATE '2020-01-01') "
                                "RETURNING id::text"
                            ),
                            {"species_id": species_id},
                        )
                    ).scalar_one()

                    await rms.apply_reference_gene_rows(
                        session,
                        assembly_id=assembly_id,
                        rows=[{**gene_row, "assembly_id": assembly_id}],
                        overwrite=False,
                        commit=False,
                        source="gencode v50 (Ensembl 116)",
                        source_url="https://example.org/gencode.v50.basic.annotation.gtf.gz",
                        source_version="v50 (Ensembl 116)",
                        source_release_date=date(2026, 4, 8),
                    )
                    await rms.apply_reference_dataset_text(
                        session,
                        assembly_id=assembly_id,
                        dataset_type="segmental_duplications",
                        text_value="chr1\t100\t200\tLCR22A\tWGAC\t.\t100\t200\t0,0,0\n",
                        overwrite=False,
                        commit=False,
                        performed_by="admin@example.com",
                        source="upload",
                    )

                    rows = (
                        await session.execute(
                            text(
                                "SELECT dataset_type, source, source_url, source_version, "
                                "source_release_date, performed_by "
                                "FROM reference_dataset_imports "
                                "WHERE assembly_id = CAST(:assembly_id AS uuid) ORDER BY dataset_type"
                            ),
                            {"assembly_id": assembly_id},
                        )
                    ).mappings().all()
                finally:
                    await session.rollback()

            assert [dict(row) for row in rows] == [
                {
                    "dataset_type": "genes",
                    "source": "gencode v50 (Ensembl 116)",
                    "source_url": "https://example.org/gencode.v50.basic.annotation.gtf.gz",
                    "source_version": "v50 (Ensembl 116)",
                    "source_release_date": date(2026, 4, 8),
                    "performed_by": None,
                },
                {
                    "dataset_type": "segmental_duplications",
                    "source": "upload",
                    "source_url": None,
                    "source_version": rms.SOURCE_VERSION_NOT_STATED,
                    "source_release_date": None,
                    "performed_by": "admin@example.com",
                },
            ]
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
