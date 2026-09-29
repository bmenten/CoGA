#!/usr/bin/env python3
"""Seed the database for the Playwright browser E2E (M6).

Imports the golden-trio package (the same fixture the Python e2e suite uses) and
creates a KNOWN-credential user the browser test logs in as — the existing admin
is seeded from secret env, so the spec can't know its password. The e2e user is
role=admin so it can see the golden family's project.

Idempotent. Run from the repo root against a live Postgres + ClickHouse:

    RUN_INTEGRATION=1 python scripts/seed_playwright_e2e.py

Honours E2E_USER_EMAIL / E2E_USER_PASSWORD (defaults below) — the spec reads the
same env so the two stay in sync.
"""

from __future__ import annotations

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

# example.com is reserved but a VALID email domain; pydantic EmailStr rejects .test
E2E_EMAIL = os.environ.get("E2E_USER_EMAIL", "e2e.playwright@example.com")
E2E_PASSWORD = os.environ.get("E2E_USER_PASSWORD", "e2e-playwright-pw")
_FIXTURE = _REPO_ROOT / "backend" / "tests" / "e2e" / "fixtures" / "golden_trio"


async def _seed() -> dict:
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import get_password_hash
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    # Import the golden trio from an authorized staging copy.
    staging = Path(tempfile.mkdtemp(prefix="coga-pw-seed-")) / "FAM_TRIO"
    shutil.copytree(_FIXTURE, staging)
    settings.family_import_roots = [str(staging.parent)]
    facts = await _harness.import_golden_trio(staging)

    # Known-credential e2e user (admin -> sees every project, incl. the golden one).
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        existing = (
            await session.execute(
                text("SELECT id::text FROM users WHERE email = :e"), {"e": E2E_EMAIL}
            )
        ).scalar_one_or_none()
        if existing is None:
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, role, email)
                    VALUES (:u, :p, 'admin', :e)
                    """
                ),
                {"u": E2E_EMAIL, "p": get_password_hash(E2E_PASSWORD), "e": E2E_EMAIL},
            )
            await session.commit()

    # Tag the P/LP variant for reporting so the report page has a reported variant
    # (the browser sign-out journey needs a non-empty report).
    await _seed_report_review()
    return facts


async def _seed_report_review() -> None:
    from backend.app.schemas import (
        AcmgClassificationPayload,
        AcmgCriterionSelection,
        SmallVariantReviewUpdate,
    )
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_metadata_context import build_family_metadata_context
    from backend.app.services.small_variant_review_pg import upsert_small_variant_review
    from backend.tests.e2e import _harness

    sm = get_postgres_sessionmaker()
    async with sm() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        context = await build_family_metadata_context(
            session, family_identifier=_harness.FAMILY_ID, user=admin, project_id=project_id
        )
        await upsert_small_variant_review(
            session,
            context=context,
            variant_id="1-2000-C-T",
            payload=SmallVariantReviewUpdate(
                tags=["report", "acmg_class_4"],
                note="E2E: reported for the browser sign-out journey",
                acmg=AcmgClassificationPayload(
                    criteria=[
                        AcmgCriterionSelection(code="PVS1", strength="very_strong", accepted=True),
                        AcmgCriterionSelection(code="PM2", strength="supporting", accepted=True),
                    ]
                ),
            ),
            user=admin,
        )


def main() -> None:
    from backend.tests.e2e import _harness

    facts = _harness.run_async(lambda: _seed())
    print(
        f"seeded family={facts['family_id']} "
        f"small_variants={facts['n_small_variants']} sv={facts['n_structural_variants']}"
    )
    print(f"e2e user={E2E_EMAIL}")


if __name__ == "__main__":
    main()
