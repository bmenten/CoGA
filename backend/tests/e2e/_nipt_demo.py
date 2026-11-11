"""The committed NIPT demo package (``demo/nipt_family``) as the NIPT end-to-end modules
import it.

Each run imports a copy of the package with every hardcoded id suffixed, with the manifest a
delivery of the copy keeps (``kept_manifest``, read from its package file
``coga-package.json``), and with the variations a module needs: the maternal plasma on an assay
panel of the run's own (the scope of the artifact list and of the de novo recurrence), the
fetus's status, a region of interest, a coverage BED that declares nothing, the project the
package names. The copy's package file lists its files as the copy holds them. Used by
``test_e2e_nipt_signed_record.py``, ``test_e2e_nipt_qc_database_error.py`` and
``test_e2e_demo_smoke.py``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.services.coga_package import PACKAGE_FILE_NAME
from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import ensure_postgres_schema

DEMO = Path(__file__).resolve().parents[3] / "demo" / "nipt_family"
# The committed package hardcodes these ids, and sample ids are unique across families: each
# run imports a copy with every id suffixed.
TOKENS = ("FAM_NIPT_DEMO", "FATHER_NIPT", "CFDNA_NIPT", "FETUS_NIPT")


async def import_demo(root: Path) -> dict:
    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            manifest_text=kept_manifest(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="overwrite",
        )
    return {
        "completed": result.completed,
        "error": result.error,
        "dataset_status": {dataset.dataset_type: dataset.status for dataset in result.datasets},
        "assembly_id": assembly_id,
        "project_id": project_id,
    }


def _suffixed(text: str, ids: dict[str, str]) -> str:
    for old, new in ids.items():
        text = text.replace(old, new)
    return text


def _file_entry(root: Path, path: str) -> dict[str, Any]:
    content = (root / path).read_bytes()
    return {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def prepare_copy(
    parent: Path,
    *,
    fetus_status: str | None = None,
    roi: str | None = None,
    declare_coverage: bool = True,
    own_panel: bool = True,
    project: str | None = None,
) -> dict[str, str]:
    """Copy the demo package under ``parent`` with every hardcoded id suffixed; the new ids, the
    copy's folder (``root``) and its panel (``panel``). With ``own_panel``, the maternal plasma is
    on an assay panel of this run's own; without, on none, so the default scope of the assay
    holds it. With ``fetus_status`` (``affected``, ``unaffected``, ``unknown``), the package
    gives the fetus that status instead of the demo's (affected). With ``roi``, the package gives
    the family that region of interest; without ``declare_coverage``, the plasma's coverage BED
    declares nothing. With ``project``, the package names that project."""
    suffix = uuid4().hex[:8]
    ids = {token: f"{token}_{suffix}" for token in TOKENS}
    panel = f"e2e-nipt-signed-record-{suffix}"
    root = parent / ids["FAM_NIPT_DEMO"]
    shutil.copytree(DEMO, root)
    vcf = root / "nipt_combined.vcf"
    vcf.write_text(_suffixed(vcf.read_text(encoding="utf-8"), ids), encoding="utf-8")
    package_path = root / PACKAGE_FILE_NAME
    package = json.loads(_suffixed(package_path.read_text(encoding="utf-8"), ids))
    members = {member["sample_id"]: member for member in package["members"]}
    if own_panel:
        # The panel is the scope of the artifact list and of the de novo recurrence.
        members[ids["CFDNA_NIPT"]]["assay_panel"] = panel
    if fetus_status is not None:
        members[ids["FETUS_NIPT"]]["clinical_status"] = fetus_status
    if roi is not None:
        package["family"]["roi"] = roi
    if not declare_coverage:
        del package["datasets"]["coverage"]["per_sample"][ids["CFDNA_NIPT"]]["content"]
    if project is not None:
        package["project"] = project
    # The files as the copy holds them, its VCF with the suffixed ids.
    package["files"] = [_file_entry(root, entry["path"]) for entry in package["files"]]
    package_path.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"root": str(root), "panel": panel, **ids}
