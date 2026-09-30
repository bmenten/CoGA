#!/usr/bin/env python3
"""Seed the two demo bundles for the frontend style diff (frontend/scripts/stylediff).

Run it after scripts/seed_playwright_e2e.py, which imports the golden trio and creates the
known e2e user. This adds the families the golden trio lacks: the monogenic NIPT demo (package
import plus its NIPT analysis) and the demo quartet (structural variants, BED tracks and repeat
expansions, through the demo loader). Both go into the e2e project, so the e2e user sees them.

Not idempotent: run it once against a fresh database. From the repo root:

    RUN_INTEGRATION=1 python scripts/seed_style_diff_demo.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REFERENCE_BOOTSTRAP_ENABLED", "false")
os.environ.setdefault("HPO_BOOTSTRAP_ON_STARTUP", "false")
os.environ.setdefault("GENE_REFERENCE_BOOTSTRAP_ON_STARTUP", "false")

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


async def _seed() -> None:
    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import family_package_import as package_import
    from backend.app.services.nipt_service import run_family_nipt_analysis
    from backend.tests.e2e import _harness

    # Import the NIPT bundle from an authorized staging copy.
    staging = Path(tempfile.mkdtemp(prefix="coga-stylediff-seed-"))
    nipt_root = staging / "nipt_family"
    shutil.copytree(_REPO_ROOT / "demo" / "nipt_family", nipt_root)
    settings.family_import_roots = [str(staging)]

    sm = get_postgres_sessionmaker()
    async with sm() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
    async with sm() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(nipt_root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    if not result.completed:
        raise SystemExit(f"NIPT demo import failed: {result.error}")
    async with sm() as session:
        await run_family_nipt_analysis(
            session, family_id="FAM_NIPT_DEMO", user=admin, project_id=project_id
        )
    print("seeded family=FAM_NIPT_DEMO (package import + NIPT analysis)")

    # The quartet through its own loader (scripts/ is not a package).
    path = _REPO_ROOT / "scripts" / "load_demo_quartet.py"
    spec = importlib.util.spec_from_file_location("load_demo_quartet_stylediff", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so the loader's dataclasses resolve their module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    bundle = _REPO_ROOT / "demo" / "quartet_family"
    args = module.build_parser().parse_args(["--bundle-root", str(bundle), "--overwrite"])
    await module.run_loader(args)
    print("seeded family=demo_family (demo quartet)")


if __name__ == "__main__":
    asyncio.run(_seed())
