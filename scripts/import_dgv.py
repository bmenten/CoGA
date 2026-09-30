"""Stream-import a Database of Genomic Variants (DGV) TSV into dgv_variants.

The hg38 DGV file is ~2M rows / ~360 MB, so this reads it line by line and
bulk-inserts in bounded batches (committing periodically) — memory stays flat
regardless of file size. Run inside the backend container:

    PYTHONPATH=/app python /app/scripts/import_dgv.py \
        --assembly GRCh38 --file /data/ref-data/GRCh38_hg38_variants_2025-12-01.txt
"""
import argparse
import asyncio
import csv
import sys

from sqlalchemy import text

from app.core.postgres import get_postgres_sessionmaker
from app.services.reference_metadata_service import (
    _DGV_INSERT_CHUNK,
    insert_dgv_batch,
    parse_dgv_row,
    record_reference_import,
)

_COMMIT_EVERY = 200_000


async def _resolve_assembly_id(session, assembly: str) -> str:
    row = (
        await session.execute(
            text(
                "SELECT id::text AS id FROM assemblies "
                "WHERE lower(assembly_name) = lower(:name) "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"name": assembly},
        )
    ).mappings().first()
    if row is None:
        sys.exit(f"Assembly not found: {assembly}")
    return row["id"]


async def main(assembly: str, file_path: str, replace: bool, performed_by: str) -> None:
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        assembly_id = await _resolve_assembly_id(session, assembly)
        if replace:
            await session.execute(
                text("DELETE FROM dgv_variants WHERE assembly_id = CAST(:aid AS uuid)"),
                {"aid": assembly_id},
            )
            await session.commit()

        header_index: dict[str, int] | None = None
        batch: list[dict[str, object]] = []
        inserted = 0
        with open(file_path, newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            for row in reader:
                if not row:
                    continue
                first = row[0].strip().lower()
                if header_index is None and first in {"variantaccession", "variant_accession"}:
                    header_index = {name.strip().lower(): idx for idx, name in enumerate(row)}
                    continue
                if first.startswith("#") or first.startswith("track"):
                    continue
                parsed = parse_dgv_row(row, header_index=header_index, assembly_id=assembly_id)
                if parsed is None:
                    continue
                batch.append(parsed)
                if len(batch) >= _DGV_INSERT_CHUNK:
                    await insert_dgv_batch(session, batch)
                    inserted += len(batch)
                    batch = []
                    if inserted % _COMMIT_EVERY == 0:
                        await session.commit()
                        print(f"  inserted {inserted:,}...", flush=True)
        if batch:
            await insert_dgv_batch(session, batch)
            inserted += len(batch)

        # The DGV file states no release inside it, so the import is recorded as one whose
        # source states none.
        await record_reference_import(
            session,
            assembly_id=assembly_id,
            dataset_type="dgv",
            inserted=inserted,
            replaced=replace,
            source="dgv",
            performed_by=performed_by,
        )
        await session.commit()
        print(f"Done: {inserted:,} DGV variants imported for {assembly}.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembly", required=True, help="Local assembly name, e.g. GRCh38")
    parser.add_argument("--file", required=True, help="Path to the DGV variants TSV")
    parser.add_argument("--performed-by", default="dgv-import-script")
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Append instead of replacing existing DGV rows for the assembly",
    )
    args = parser.parse_args()
    asyncio.run(main(args.assembly, args.file, not args.no_replace, args.performed_by))
