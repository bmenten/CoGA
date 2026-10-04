"""M5 end-to-end: realistic demo-bundle smoke (Tier 2).

The golden trio is tiny and hand-curated; this runs the two committed *realistic*
demo bundles through their real ingestion paths to catch integration breakage the
golden trio is too small to surface:

* ``demo/nipt_family`` — a real package (``analysis_type: monogenic_nipt``).
  Imported via ``execute_family_package_import``, then the family NIPT analysis is
  run over the imported data and checked against the bundle's documented ground
  truth (fetal fraction ~12%, 40 paternal-transmitted sites, all 8 categories).
* ``demo/quartet_family`` — a full multi-data-type family loaded via the real
  ``scripts/load_demo_quartet`` loader (the direct-upload ingestion path: small
  variants, structural variants, BED tracks, repeats). Asserts coarse invariants.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NIPT_BUNDLE = _REPO_ROOT / "demo" / "nipt_family"
_QUARTET_BUNDLE = _REPO_ROOT / "demo" / "quartet_family"
# The committed NIPT bundle hardcodes these ids; the dev DB may already hold them
# (sample ids are globally unique), so each run imports a uniquely-suffixed copy.
_NIPT_TOKENS = ("FAM_NIPT_DEMO", "FATHER_NIPT", "CFDNA_NIPT", "FETUS_NIPT")


def _prepare_nipt_copy(dest: Path) -> str:
    """Copy demo/nipt_family into dest with every hardcoded id suffixed uniquely.
    Returns the new family id."""
    suffix = uuid4().hex[:8]
    rename = {tok: f"{tok}_{suffix}" for tok in _NIPT_TOKENS}
    shutil.copytree(_NIPT_BUNDLE, dest)
    for name in ("manifest.yaml", "nipt_trio.ped", "nipt_combined.vcf"):
        path = dest / name
        text_value = path.read_text(encoding="utf-8")
        for old, new in rename.items():
            text_value = text_value.replace(old, new)
        path.write_text(text_value, encoding="utf-8")
    return rename["FAM_NIPT_DEMO"]


def _load_quartet_module():
    """Import scripts/load_demo_quartet by path (scripts/ is not a package). Its
    top-level ensure_backend_runtime() is a no-op when deps are importable, which
    they are under the test interpreter."""
    import sys

    path = _REPO_ROOT / "scripts" / "load_demo_quartet.py"
    spec = importlib.util.spec_from_file_location("load_demo_quartet_e2e", path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses defined in the module can resolve
    # cls.__module__ via sys.modules (otherwise @dataclass raises at definition).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


async def _collect(nipt_root: Path, nipt_family_id: str) -> dict:
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        count_family_structural_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_source_rows
    from backend.app.services.nipt_artifact_pg import add_nipt_artifact
    from backend.app.services.nipt_service import run_family_nipt_analysis
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)

    sm = get_postgres_sessionmaker()
    async with sm() as s:
        admin, project_id, assembly_id = await _harness.ensure_e2e_project(s)

    out: dict = {}

    # ---- NIPT bundle: real package import + family NIPT analysis recovery ----
    async with sm() as s:
        res = await package_import.execute_family_package_import(
            s,
            folder_path=str(nipt_root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    out["nipt_import"] = {
        "completed": res.completed,
        "error": res.error,
        "statuses": {d.dataset_type: d.status for d in res.datasets},
    }

    async with sm() as s:
        nipt_uuid = (
            await s.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": nipt_family_id})
        ).scalar_one_or_none()
    out["nipt_family_uuid"] = nipt_uuid
    out["nipt_small_count"] = (
        await count_family_small_variants(_harness.ASSEMBLY, nipt_uuid, project_ids=[project_id])
        if nipt_uuid
        else 0
    )

    # Seed the recurrent-artifact site (EXP_CAT=ARTIFACT in the demo) for this
    # assembly+assay so the analysis filters it — making filter_counts exact.
    async with sm() as s:
        await add_nipt_artifact(
            s,
            assembly_id=assembly_id,
            assay_key="nipt_cfdna",
            variant_id="1-9501-A-G",
            label="e2e recurrent artifact",
        )

    async with sm() as s:
        nipt = await run_family_nipt_analysis(s, family_id=nipt_family_id, user=admin, project_id=project_id)
    out["nipt_analysis"] = {
        "ff_computed": nipt.fetal_fraction.ff_computed,
        "n_sites": nipt.fetal_fraction.n_sites,
        "category_counts": {int(k): int(v) for k, v in nipt.category_counts.items()},
        "filter_counts": dict(nipt.filter_counts),
    }

    # ---- Quartet bundle: the real demo loader (direct-upload ingestion path) ----
    quartet = _load_quartet_module()
    bundle = quartet.load_demo_bundle(_QUARTET_BUNDLE)
    args = quartet.build_parser().parse_args(["--bundle-root", str(_QUARTET_BUNDLE), "--overwrite"])
    # run_loader manages (and closes) its own engine/client; we re-open after.
    await quartet.run_loader(args)

    sm = get_postgres_sessionmaker()
    async with sm() as s:
        q_uuid = (
            await s.execute(
                text("SELECT id::text FROM families WHERE family_id = :f"), {"f": bundle.family_id}
            )
        ).scalar_one_or_none()
        q_repeats = (
            (
                await s.execute(
                    text(
                        """
                        SELECT count(*) FROM repeat_expansions re
                        JOIN samples s ON s.id = re.sample_id
                        WHERE s.family_id = CAST(:f AS uuid)
                        """
                    ),
                    {"f": q_uuid},
                )
            ).scalar_one()
            if q_uuid
            else 0
        )
        q_coverage = (
            await count_interval_track_source_rows(s, family_uuid=q_uuid) if q_uuid else 0
        )
    out["quartet"] = {
        "family_id": bundle.family_id,
        "family_uuid": q_uuid,
        "assembly": bundle.assembly_name,
        "small": await count_family_small_variants(bundle.assembly_name, q_uuid) if q_uuid else 0,
        "sv": await count_family_structural_variants(bundle.assembly_name, q_uuid) if q_uuid else 0,
        "repeats": q_repeats,
        "coverage_rows": q_coverage,
    }
    return out


@pytest.fixture(scope="module")
def smoke(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_NIPT_BUNDLE / "manifest.yaml").exists() or not (_QUARTET_BUNDLE / "metadata").exists():
        pytest.fail("demo bundles missing: they are committed under demo/, so restore them")

    base = tmp_path_factory.mktemp("demo_smoke")
    nipt_root = base / "nipt_family"
    nipt_family_id = _prepare_nipt_copy(nipt_root)

    # Authorize the temp NIPT copy for package import.
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)

    return _harness.run_async(lambda: _collect(nipt_root, nipt_family_id))


# ------------------------------------------------------------------------------- NIPT


def test_nipt_bundle_imports(smoke):
    imp = smoke["nipt_import"]
    assert imp["completed"] is True, imp
    assert imp["statuses"].get("snv") == "imported", imp
    assert imp["statuses"].get("coverage") == "imported", imp
    assert smoke["nipt_family_uuid"] is not None
    assert smoke["nipt_small_count"] > 0, smoke["nipt_small_count"]


def test_nipt_analysis_recovers_fetal_fraction(smoke):
    a = smoke["nipt_analysis"]
    assert a["ff_computed"] == pytest.approx(0.12, abs=0.02), a
    assert a["n_sites"] == 40, a


def test_nipt_analysis_recovers_categories(smoke):
    # With the artifact seeded, the import -> analysis chain recovers the demo's
    # documented ground truth exactly (matches the direct-parse unit test).
    counts = smoke["nipt_analysis"]["category_counts"]
    assert counts == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 40, 8: 1}, counts


def test_nipt_analysis_filter_counts_exact(smoke):
    # The funnel counts the plasma's 48 calls: QUAL_DROP fails quality; the seeded
    # artifact fails the artifact filter; the remaining 46 pass. The category-8 site,
    # where the plasma has no alt read and only the father has the allele, is counted
    # apart (matches the direct-parse unit test).
    assert smoke["nipt_analysis"]["filter_counts"] == {
        "total_in": 48,
        "passed": 46,
        "failed_quality": 1,
        "failed_artifact": 1,
        "paternal_only": 1,
    }, smoke["nipt_analysis"]["filter_counts"]


# ---------------------------------------------------------------------------- quartet


def test_quartet_bundle_loads_all_data_types(smoke):
    q = smoke["quartet"]
    assert q["family_uuid"] is not None, q
    assert q["small"] > 0, q
    assert q["sv"] > 0, q
    assert q["repeats"] > 0, q
    assert q["coverage_rows"] > 0, q
