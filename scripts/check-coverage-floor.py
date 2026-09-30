#!/usr/bin/env python3
"""Enforce per-module coverage floors on clinical-critical backend modules.

Reads a coverage.json (``pytest --cov-report=json:coverage.json``) and fails if any listed
module — or overall — has dropped below its floor. Floors are set just below the measured
baseline (2026-06-28) so they RATCHET against regression without churning on minor edits.

Modules whose bulk coverage comes from integration tests (skipped in the unit ``backend``
job — e.g. integrity_anchor_service) are floored on the *combined* report instead: the
``coverage`` CI job merges the unit, smoke and e2e runs and checks COMBINED_FLOORS (#526).

Usage: python scripts/check-coverage-floor.py [--combined] [coverage.json]
"""

from __future__ import annotations

import json
import sys

# Global floor for the whole backend/app package (66.0% measured for #526). Keep in step
# with --cov-fail-under in ci.yml.
GLOBAL_FLOOR = 63.0

# Per-module floors (percent of lines covered) for clinical-critical modules.
MODULE_FLOORS: dict[str, float] = {
    "backend/app/services/acmg_points.py": 95.0,
    "backend/app/services/cnv_acmg_points.py": 90.0,
    "backend/app/services/classification_drift_service.py": 90.0,
    "backend/app/services/clinical_audit_service.py": 85.0,
    "backend/app/services/hash_chain.py": 90.0,
    "backend/app/services/haplotype_lineage_service.py": 85.0,
    "backend/app/services/nipt_analysis.py": 88.0,
    "backend/app/services/sample_integrity_qc.py": 82.0,
    # Raised with #526 (86.0% measured); the unit tests now cover the sign-out gates.
    "backend/app/services/report_signout_service.py": 83.0,
    # Added with #526, a few points under the measured unit coverage (in brackets).
    "backend/app/services/sample_integrity_service.py": 84.0,  # (87.3) feeds the sign-out gate
    "backend/app/services/variant_ranking_cache.py": 57.0,  # (60.3)
    "backend/app/services/annotation_manifest_service.py": 71.0,  # (74.6)
    "backend/app/services/qc_threshold_service.py": 69.0,  # (72.4)
    # Added with #526 part 2, under the coverage the new unit tests reach (in brackets).
    "backend/app/services/structural_variant_review_pg.py": 53.0,  # (56.8) CNV ACMG persistence
    "backend/app/services/small_variant_review_tags.py": 57.0,  # (60.6)
    "backend/app/services/clinical_cnv_kb_jobs.py": 85.0,  # (89.3)
    # Added with #670: one write at a time to a family's variants.
    "backend/app/services/family_variant_write_lock.py": 95.0,  # (100.0)
}

# The combined unit + smoke + e2e report (the ``coverage`` CI job). Floors a few points
# under the combined coverage measured for #526 (in brackets), for the modules the unit
# job alone under-reports because real datastores exercise them.
COMBINED_GLOBAL_FLOOR = 69.0  # (72.6)
COMBINED_FLOORS: dict[str, float] = {
    "backend/app/services/family_package_import.py": 85.0,  # (89.2)
    "backend/app/services/family_package_jobs.py": 83.0,  # (87.8)
    "backend/app/services/family_package_registration.py": 72.0,  # (76.0)
    "backend/app/services/family_package_datasets.py": 37.0,  # (41.0) thin; see #526
    "backend/app/services/integrity_anchor_service.py": 78.0,  # (82.3)
    "backend/app/services/report_signout_service.py": 90.0,  # (94.9)
    "backend/app/services/clinical_audit_service.py": 92.0,  # (96.1)
    "backend/app/services/annotation_manifest_service.py": 80.0,  # (84.4)
    "backend/app/services/clickhouse_variant_storage.py": 88.0,  # (92.1)
    "backend/app/services/clickhouse_family_variants.py": 75.0,  # (79.9)
    "backend/app/services/variant_ranking_cache.py": 69.0,  # (73.0)
}


def main(path: str, *, combined: bool = False) -> int:
    with open(path) as handle:
        data = json.load(handle)
    files = data.get("files", {})
    failures: list[str] = []
    global_floor = COMBINED_GLOBAL_FLOOR if combined else GLOBAL_FLOOR
    module_floors = COMBINED_FLOORS if combined else MODULE_FLOORS

    overall = data.get("totals", {}).get("percent_covered", 0.0)
    status = "OK " if overall >= global_floor else "LOW"
    print(f"[{status}] overall {overall:5.1f}%  (floor {global_floor:.0f}%)")
    if overall < global_floor:
        failures.append(f"overall {overall:.1f}% < {global_floor:.0f}%")

    for module, floor in sorted(module_floors.items()):
        info = files.get(module)
        if info is None:
            failures.append(f"{module}: NOT FOUND in coverage report")
            print(f"[ERR] {module}: not found in coverage report")
            continue
        covered = info["summary"]["percent_covered"]
        status = "OK " if covered >= floor else "LOW"
        print(f"[{status}] {covered:5.1f}%  (floor {floor:4.0f}%)  {module}")
        if covered < floor:
            failures.append(f"{module}: {covered:.1f}% < {floor:.0f}%")

    if failures:
        print("\nCoverage floor FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        print("\nAdd tests, or (if intentional) adjust the floor in scripts/check-coverage-floor.py.")
        return 1
    print("\nAll coverage floors met.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    combined = "--combined" in args
    paths = [arg for arg in args if arg != "--combined"]
    sys.exit(main(paths[0] if paths else "coverage.json", combined=combined))
