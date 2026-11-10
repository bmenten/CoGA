"""The committed NIPT demo package (``demo/nipt_family``) as the NIPT end-to-end modules
import it.

Each run imports a copy of the package with every hardcoded id suffixed, its maternal plasma on
an assay panel of the run's own (the scope of the artifact list and of the de novo
recurrence), with the variations a module needs: the fetus's status, a region of interest, a
coverage BED that declares nothing. Used by ``test_e2e_nipt_signed_record.py`` and
``test_e2e_nipt_qc_database_error.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import ensure_postgres_schema

DEMO = Path(__file__).resolve().parents[3] / "demo" / "nipt_family"
# The committed package hardcodes these ids, and sample ids are unique across families: each
# run imports a copy with every id suffixed (as test_e2e_demo_smoke.py does).
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


def prepare_copy(
    parent: Path, *, fetus_status: str | None = None, roi: str | None = None, declare_coverage: bool = True
) -> dict[str, str]:
    """Copy the demo package under ``parent`` with every hardcoded id suffixed, and the maternal
    plasma on an assay panel of this run's own; the new ids and the panel (``panel``). With
    ``fetus_status``, the PED gives the fetus that status instead of the demo's (affected). With
    ``roi``, the manifest gives the family that region of interest; without
    ``declare_coverage``, the plasma's coverage BED declares nothing."""
    suffix = uuid4().hex[:8]
    ids = {token: f"{token}_{suffix}" for token in TOKENS}
    panel = f"e2e-nipt-signed-record-{suffix}"
    root = parent / ids["FAM_NIPT_DEMO"]
    shutil.copytree(DEMO, root)
    for name in ("manifest.yaml", "nipt_trio.ped", "nipt_combined.vcf"):
        path = root / name
        content = path.read_text(encoding="utf-8")
        for old, new in ids.items():
            content = content.replace(old, new)
        if name == "nipt_trio.ped" and fetus_status is not None:
            # The fetus's row: family, id, father, mother, sex (unknown) and status.
            fetus = "\t".join((ids["FAM_NIPT_DEMO"], ids["FETUS_NIPT"], ids["FATHER_NIPT"], ids["CFDNA_NIPT"], "0"))
            assert content.count(f"{fetus}\t2") == 1, content
            content = content.replace(f"{fetus}\t2", f"{fetus}\t{fetus_status}")
        if name == "manifest.yaml":
            # The panel is the scope of the artifact list and of the de novo recurrence.
            plasma = f"  {ids['CFDNA_NIPT']}:\n    assay: nipt_cfdna\n"
            assert content.count(plasma) == 1, content
            content = content.replace(plasma, f"{plasma}    assay_panel: {panel}\n")
            if roi is not None:
                ped = "ped: nipt_trio.ped\n"
                assert content.count(ped) == 1, content
                content = content.replace(ped, f'{ped}roi: "{roi}"\n')
            if not declare_coverage:
                declared = "        content: read_depth\n"
                assert content.count(declared) == 1, content
                content = content.replace(declared, "")
        path.write_text(content, encoding="utf-8")
    return {"root": str(root), "panel": panel, **ids}
