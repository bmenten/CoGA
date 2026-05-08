#!/usr/bin/env python3
"""Load the bundled demo quartet into the current Postgres + ClickHouse schema."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import AsyncIterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKEND_VENV_PYTHON_CANDIDATES = (
    ROOT / "backend" / ".venv" / "bin" / "python",
    ROOT / "backend" / ".venv" / "Scripts" / "python.exe",
)
REQUIRED_RUNTIME_MODULES = ("fastapi", "sqlalchemy")


def _runtime_missing_modules() -> list[str]:
    return [name for name in REQUIRED_RUNTIME_MODULES if importlib.util.find_spec(name) is None]


def _find_backend_venv_python() -> Path | None:
    for candidate in BACKEND_VENV_PYTHON_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def ensure_backend_runtime(*, is_main_module: bool | None = None) -> None:
    missing_modules = _runtime_missing_modules()
    if not missing_modules:
        return

    backend_python = _find_backend_venv_python()
    should_reexec = (__name__ == "__main__") if is_main_module is None else is_main_module
    current_python = Path(sys.executable).absolute()
    if (
        should_reexec
        and backend_python is not None
        and current_python != backend_python.absolute()
    ):
        os.execv(
            str(backend_python),
            [str(backend_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )

    missing_text = ", ".join(missing_modules)
    raise ModuleNotFoundError(
        "Missing Python dependencies for scripts/load_demo_quartet.py "
        f"({missing_text}). Run `backend/.venv/bin/python scripts/load_demo_quartet.py ...` "
        "or install the backend requirements with "
        "`cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`."
    )


ensure_backend_runtime()

from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.clickhouse import (
    close_clickhouse_client,
    init_clickhouse_schema,
    wait_for_clickhouse,
)
from backend.app.core.config import settings
from backend.app.core.postgres import (
    close_postgres_engine,
    get_postgres_sessionmaker,
    init_postgres_schema,
    wait_for_postgres,
)
from backend.app.dependencies import get_password_hash
from backend.app.schemas import ManualPedFamilyCreate
from backend.app.services.bed_service import upload_bed_data
from backend.app.services.family_metadata_context import (
    FamilyMetadataContext,
    SampleMetadataContext,
    build_family_metadata_context,
)
from backend.app.services.metadata_service import CurrentUser, update_family_project_assignments
from backend.app.services.ped_service import create_manual_family_data
from backend.app.services.repeat_expansion_pg import (
    clear_sample_repeat_expansions,
    decode_repeat_upload_text,
    ingest_trgt_text,
    seed_builtin_repeat_catalog,
)
from backend.app.services.variant_upload_service import (
    upload_family_small_variant_file,
    upload_structural_variant_file,
)

DEFAULT_BUNDLE_ROOT = ROOT / "demo" / "quartet_family"
DEFAULT_BED_TYPES = ("coverage", "segments", "apcad")
SPECIES_DEFAULTS = {
    "Homo sapiens": {"common_name": "Human", "tax_id": 9606},
    "Mus musculus": {"common_name": "Mouse", "tax_id": 10090},
}
ASSEMBLY_RELEASE_DATES = {
    "GRCh38": date(2013, 12, 24),
    "GRCh37": date(2009, 2, 1),
    "T2T-CHM13": date(2022, 4, 1),
    "GRCm39": date(2020, 6, 1),
    "GRCm38": date(2011, 1, 1),
}


@dataclass(slots=True)
class DemoBundle:
    root: Path
    manifest: dict[str, object]
    family: ManualPedFamilyCreate
    family_id: str
    project_name: str
    species_name: str
    assembly_name: str
    sample_ids: list[str]


def load_demo_bundle(bundle_root: Path) -> DemoBundle:
    manifest_path = bundle_root / "metadata" / "manifest.json"
    family_path = bundle_root / "metadata" / "family_manual.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Demo manifest not found: {manifest_path}")
    if not family_path.exists():
        raise FileNotFoundError(f"Demo family definition not found: {family_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    family = ManualPedFamilyCreate.model_validate_json(family_path.read_text(encoding="utf-8"))
    sample_ids = [member.sample_id for member in family.members]
    return DemoBundle(
        root=bundle_root,
        manifest=manifest,
        family=family,
        family_id=str(manifest.get("family_id") or family.family_id),
        project_name=str(manifest.get("project_name") or "CoGA demo family"),
        species_name=str(manifest.get("species") or "Homo sapiens"),
        assembly_name=str(manifest.get("assembly") or "GRCh38"),
        sample_ids=sample_ids,
    )


def small_variant_upload_path(bundle: DemoBundle, source: str) -> Path:
    if source not in {"clair3", "glimpse2"}:
        raise ValueError(f"Unsupported small-variant source: {source}")
    path = bundle.root / "imports" / "small_variants" / f"{bundle.family_id}.{source}.vcf"
    if not path.exists():
        raise FileNotFoundError(f"Small-variant file not found: {path}")
    return path


def structural_variant_upload_path(bundle: DemoBundle, sample_id: str, source: str) -> Path:
    if source == "manual":
        path = bundle.root / "uploads" / "structural_variants" / f"{sample_id}.structural.tsv"
    elif source in {"sniffles", "spectre"}:
        path = (
            bundle.root
            / "imports"
            / "structural_variants"
            / source
            / f"{sample_id}.{source}.vcf"
        )
    else:
        raise ValueError(f"Unsupported structural-variant source: {source}")
    if not path.exists():
        raise FileNotFoundError(f"Structural-variant file not found: {path}")
    return path


def bed_upload_path(bundle: DemoBundle, sample_id: str, bed_type: str) -> Path:
    suffix = {
        "coverage": "coverage.bed",
        "segments": "segments.bed",
        "apcad": "apcad.bed",
    }.get(bed_type)
    if suffix is None:
        raise ValueError(f"Unsupported BED type: {bed_type}")
    path = bundle.root / "uploads" / "bed" / bed_type / f"{sample_id}.{suffix}"
    if not path.exists():
        raise FileNotFoundError(f"BED file not found: {path}")
    return path


def repeat_expansion_upload_path(bundle: DemoBundle, sample_id: str) -> Path:
    path = bundle.root / "uploads" / "repeat_expansions" / f"{sample_id}.trgt.vcf"
    if not path.exists():
        raise FileNotFoundError(f"Repeat-expansion file not found: {path}")
    return path


@asynccontextmanager
async def local_upload(path: Path) -> AsyncIterator[UploadFile]:
    handle = path.open("rb")
    upload = UploadFile(file=handle, filename=path.name)
    try:
        yield upload
    finally:
        await upload.close()


def family_sample_contexts(context: FamilyMetadataContext) -> dict[str, SampleMetadataContext]:
    return {
        row["sample_id"]: SampleMetadataContext(
            sample_uuid=row["sample_uuid"],
            sample_id=row["sample_id"],
            family_uuid=context.family_uuid,
            family_id=context.family_id,
            sex=row["sex"],
            project_ids=context.project_ids,
            assembly_id=context.assembly_id,
            assembly_name=context.assembly_name,
        )
        for row in context.sample_rows
    }


async def ensure_admin_user(session: AsyncSession) -> tuple[str, str, str]:
    existing = await session.execute(
        text("SELECT id::text AS id, username, email FROM users WHERE username = :username"),
        {"username": settings.admin_username},
    )
    row = existing.mappings().first()
    if row is not None:
        return str(row["id"]), str(row["username"]), str(row["email"])

    created = await session.execute(
        text(
            """
            INSERT INTO users (
                username,
                hashed_password,
                role,
                email,
                metadata,
                created_at
            )
            VALUES (
                :username,
                :hashed_password,
                'admin',
                :email,
                '{}'::jsonb,
                :created_at
            )
            RETURNING id::text AS id, username, email
            """
        ),
        {
            "username": settings.admin_username,
            "hashed_password": get_password_hash(settings.admin_password),
            "email": settings.admin_email,
            "created_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()
    row = created.mappings().one()
    return str(row["id"]), str(row["username"]), str(row["email"])


async def ensure_species(session: AsyncSession, species_name: str) -> str:
    result = await session.execute(
        text("SELECT id::text AS id FROM species WHERE lower(name) = lower(:name)"),
        {"name": species_name},
    )
    existing = result.scalar_one_or_none()
    if existing:
        return str(existing)

    defaults = SPECIES_DEFAULTS.get(species_name, {"common_name": species_name, "tax_id": 0})
    created = await session.execute(
        text(
            """
            INSERT INTO species (name, common_name, tax_id)
            VALUES (:name, :common_name, :tax_id)
            RETURNING id::text AS id
            """
        ),
        {
            "name": species_name,
            "common_name": str(defaults["common_name"]),
            "tax_id": int(defaults["tax_id"]),
        },
    )
    await session.commit()
    return str(created.scalar_one())


async def ensure_assembly(session: AsyncSession, species_id: str, assembly_name: str) -> str:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id
            FROM assemblies
            WHERE species_id = CAST(:species_id AS uuid)
              AND assembly_name = :assembly_name
            ORDER BY release_date DESC, version DESC
            LIMIT 1
            """
        ),
        {"species_id": species_id, "assembly_name": assembly_name},
    )
    existing = result.scalar_one_or_none()
    if existing:
        return str(existing)

    created = await session.execute(
        text(
            """
            INSERT INTO assemblies (species_id, assembly_name, version, release_date)
            VALUES (
                CAST(:species_id AS uuid),
                :assembly_name,
                :version,
                :release_date
            )
            RETURNING id::text AS id
            """
        ),
        {
            "species_id": species_id,
            "assembly_name": assembly_name,
            "version": "demo-bootstrap",
            "release_date": ASSEMBLY_RELEASE_DATES.get(assembly_name, date.today()),
        },
    )
    await session.commit()
    return str(created.scalar_one())


async def ensure_project(
    session: AsyncSession,
    *,
    project_name: str,
    species_id: str,
    assembly_id: str,
    admin_user_id: str,
) -> str:
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                species_id::text AS species_id,
                assembly_id::text AS assembly_id
            FROM projects
            WHERE name = :name
            """
        ),
        {"name": project_name},
    )
    row = result.mappings().first()
    if row is None:
        created = await session.execute(
            text(
                """
                INSERT INTO projects (name, description, species_id, assembly_id, metadata)
                VALUES (
                    :name,
                    :description,
                    CAST(:species_id AS uuid),
                    CAST(:assembly_id AS uuid),
                    '{}'::jsonb
                )
                RETURNING id::text AS id
                """
            ),
            {
                "name": project_name,
                "description": "Synthetic demo family dataset",
                "species_id": species_id,
                "assembly_id": assembly_id,
            },
        )
        project_id = str(created.scalar_one())
    else:
        project_id = str(row["id"])
        if str(row["species_id"]) != species_id or str(row["assembly_id"]) != assembly_id:
            raise RuntimeError(
                f"Existing project '{project_name}' does not match the requested species/assembly"
            )

    await session.execute(
        text(
            """
            INSERT INTO project_users (project_id, user_id)
            VALUES (CAST(:project_id AS uuid), CAST(:user_id AS uuid))
            ON CONFLICT DO NOTHING
            """
        ),
        {"project_id": project_id, "user_id": admin_user_id},
    )
    await session.commit()
    return project_id


async def build_admin_user(
    session: AsyncSession,
    *,
    admin_user_id: str,
    username: str,
    email: str,
) -> CurrentUser:
    result = await session.execute(
        text(
            """
            SELECT created_at
            FROM users
            WHERE id = CAST(:user_id AS uuid)
            """
        ),
        {"user_id": admin_user_id},
    )
    created_at = result.scalar_one()
    return CurrentUser(
        id=admin_user_id,
        username=username,
        email=email,
        first_name="",
        last_name="",
        affiliation="",
        is_active=True,
        role="admin",
        projects=[],
        metadata_project_ids=[],
        created_at=created_at,
    )


async def import_repeat_expansions(
    session: AsyncSession,
    *,
    bundle: DemoBundle,
    sample_contexts: dict[str, SampleMetadataContext],
    overwrite: bool,
) -> dict[str, dict[str, int | str]]:
    results: dict[str, dict[str, int | str]] = {}
    for sample_id, sample_context in sample_contexts.items():
        path = repeat_expansion_upload_path(bundle, sample_id)
        if overwrite:
            await clear_sample_repeat_expansions(session, sample_uuid=sample_context.sample_uuid)
        async with local_upload(path) as upload:
            text_value = await decode_repeat_upload_text(upload)
            results[sample_id] = await ingest_trgt_text(
                session,
                sample_context=sample_context,
                text_value=text_value,
                metadata={
                    "source": "trgt",
                    "filename": path.name,
                    "uploaded_from": "demo-script",
                },
            )
    return results


async def import_bed_tracks(
    session: AsyncSession,
    *,
    bundle: DemoBundle,
    sample_contexts: dict[str, SampleMetadataContext],
    bed_types: tuple[str, ...],
    overwrite: bool,
) -> dict[str, dict[str, dict[str, int]]]:
    results: dict[str, dict[str, dict[str, int]]] = {}
    for sample_id, sample_context in sample_contexts.items():
        sample_results: dict[str, dict[str, int]] = {}
        for bed_type in bed_types:
            path = bed_upload_path(bundle, sample_id, bed_type)
            async with local_upload(path) as upload:
                sample_results[bed_type] = await upload_bed_data(
                    session,
                    sample_context=sample_context,
                    bed_type=bed_type,
                    file=upload,
                    overwrite=overwrite,
                )
        results[sample_id] = sample_results
    return results


async def import_structural_variants(
    session: AsyncSession,
    *,
    bundle: DemoBundle,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    source: str,
    overwrite: bool,
) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for sample_id, sample_context in sample_contexts.items():
        path = structural_variant_upload_path(bundle, sample_id, source)
        async with local_upload(path) as upload:
            results[sample_id] = await upload_structural_variant_file(
                session,
                family_context=family_context,
                sample_context=sample_context,
                file=upload,
                overwrite=overwrite,
                format_hint=source,  # type: ignore[arg-type]
            )
    return results


async def import_small_variants(
    session: AsyncSession,
    *,
    bundle: DemoBundle,
    family_context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    source: str,
    overwrite: bool,
) -> dict[str, object]:
    path = small_variant_upload_path(bundle, source)
    async with local_upload(path) as upload:
        return await upload_family_small_variant_file(
            session,
            context=family_context,
            sample_contexts=sample_contexts,
            file=upload,
            overwrite=overwrite,
            format_hint=source,  # type: ignore[arg-type]
        )


async def run_loader(args: argparse.Namespace) -> None:
    bundle = load_demo_bundle(Path(args.bundle_root).resolve())
    bed_types = tuple(item.strip() for item in args.bed_types.split(",") if item.strip())

    print("Waiting for Postgres...")
    await wait_for_postgres()
    print("Initializing Postgres schema...")
    await init_postgres_schema()
    print("Waiting for ClickHouse...")
    await wait_for_clickhouse()
    print("Initializing ClickHouse schema...")
    await init_clickhouse_schema()

    session_factory = get_postgres_sessionmaker()
    try:
        async with session_factory() as session:
            admin_user_id, admin_username, admin_email = await ensure_admin_user(session)
            await seed_builtin_repeat_catalog(session)

            species_id = await ensure_species(session, bundle.species_name)
            assembly_id = await ensure_assembly(session, species_id, bundle.assembly_name)
            project_name = args.project_name or bundle.project_name
            project_id = await ensure_project(
                session,
                project_name=project_name,
                species_id=species_id,
                assembly_id=assembly_id,
                admin_user_id=admin_user_id,
            )
            admin_user = await build_admin_user(
                session,
                admin_user_id=admin_user_id,
                username=admin_username,
                email=admin_email,
            )

            family_payload = bundle.family
            if args.family_id and args.family_id != bundle.family_id:
                family_payload = family_payload.model_copy(update={"family_id": args.family_id})
                bundle = DemoBundle(
                    root=bundle.root,
                    manifest=bundle.manifest,
                    family=family_payload,
                    family_id=args.family_id,
                    project_name=project_name,
                    species_name=bundle.species_name,
                    assembly_name=bundle.assembly_name,
                    sample_ids=bundle.sample_ids,
                )

            await create_manual_family_data(
                session,
                family_payload,
                overwrite=args.overwrite,
                user=admin_user,
            )
            await update_family_project_assignments(session, bundle.family_id, [project_id])
            family_context = await build_family_metadata_context(
                session,
                family_identifier=bundle.family_id,
                user=admin_user,
                project_id=project_id,
            )
            sample_contexts = family_sample_contexts(family_context)

            summary: dict[str, object] = {
                "bundle_root": str(bundle.root),
                "project_name": project_name,
                "project_id": project_id,
                "family_id": bundle.family_id,
                "family_uuid": family_context.family_uuid,
                "assembly_name": family_context.assembly_name,
                "samples": list(sample_contexts),
            }

            if args.small_variants != "none":
                print(f"Loading small variants from {args.small_variants}...")
                summary["small_variants"] = await import_small_variants(
                    session,
                    bundle=bundle,
                    family_context=family_context,
                    sample_contexts=sample_contexts,
                    source=args.small_variants,
                    overwrite=args.overwrite,
                )

            if bed_types:
                print(f"Loading BED tracks: {', '.join(bed_types)}...")
                summary["bed_tracks"] = await import_bed_tracks(
                    session,
                    bundle=bundle,
                    sample_contexts=sample_contexts,
                    bed_types=bed_types,
                    overwrite=args.overwrite,
                )

            if args.structural_variants != "none":
                print(f"Loading structural variants from {args.structural_variants}...")
                summary["structural_variants"] = await import_structural_variants(
                    session,
                    bundle=bundle,
                    family_context=family_context,
                    sample_contexts=sample_contexts,
                    source=args.structural_variants,
                    overwrite=args.overwrite,
                )

            if not args.skip_repeat_expansions:
                print("Loading repeat expansions...")
                summary["repeat_expansions"] = await import_repeat_expansions(
                    session,
                    bundle=bundle,
                    sample_contexts=sample_contexts,
                    overwrite=args.overwrite,
                )

            print(json.dumps(summary, indent=2, sort_keys=True))
    finally:
        await close_clickhouse_client()
        await close_postgres_engine()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-root",
        default=str(DEFAULT_BUNDLE_ROOT),
        help="Path to the generated demo dataset bundle",
    )
    parser.add_argument(
        "--project-name",
        default=None,
        help="Override the project name from the manifest",
    )
    parser.add_argument(
        "--family-id",
        default=None,
        help="Override the family id from the manifest",
    )
    parser.add_argument(
        "--small-variants",
        choices=["clair3", "glimpse2", "none"],
        default="glimpse2",
        help="Small-variant dataset to import",
    )
    parser.add_argument(
        "--structural-variants",
        choices=["manual", "sniffles", "spectre", "none"],
        default="manual",
        help="Structural-variant dataset to import",
    )
    parser.add_argument(
        "--bed-types",
        default="coverage,segments,apcad",
        help="Comma-separated BED track types to import",
    )
    parser.add_argument(
        "--skip-repeat-expansions",
        action="store_true",
        help="Do not import TRGT repeat expansions",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the existing demo family and assay data if present",
    )
    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    await run_loader(args)


if __name__ == "__main__":
    asyncio.run(main())
