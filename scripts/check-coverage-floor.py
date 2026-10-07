#!/usr/bin/env python3
"""Enforce per-module coverage floors on clinical-critical backend modules.

Reads a coverage.json (``pytest --cov-report=json:coverage.json``) and fails if any listed
module — or overall — has dropped below its floor, or if a module the type-check gate lists
has no floor. Floors sit a few points under the measured coverage so they RATCHET against
regression without churning on minor edits; raise them when coverage rises.

Modules whose bulk coverage comes from integration tests (skipped in the unit ``backend``
job — e.g. integrity_anchor_service) are floored on the *combined* report instead: the
``coverage`` CI job merges the unit, smoke and e2e runs and checks COMBINED_FLOORS (#526).

Usage: python scripts/check-coverage-floor.py [--combined] [coverage.json]
"""

from __future__ import annotations

import json
import sys
import tomllib

# Floors are set 3 points under the coverage the CI jobs measure at 02a7f73b with greenlets
# followed (in brackets), rounded down and never lowered: the unit job's coverage.json and
# the `coverage` job's combined report (#678).

# Global floor for the whole backend/app package. Keep in step with --cov-fail-under in ci.yml.
GLOBAL_FLOOR = 74.0  # (77.6)

# Per-module floors (percent of lines covered) for clinical-critical modules. Every module
# the type-check gate lists ([tool.mypy] files in pyproject.toml) needs a floor here or in
# COMBINED_FLOORS; main() fails when one has neither.
MODULE_FLOORS: dict[str, float] = {
    "backend/app/services/acmg_points.py": 97.0,  # (100.0)
    "backend/app/services/annotation_manifest_service.py": 91.0,  # (94.5)
    "backend/app/services/assembly_scope.py": 97.0,  # (100.0)
    "backend/app/services/classification_drift_service.py": 95.0,  # (98.4)
    "backend/app/services/clinical_audit_service.py": 92.0,  # (95.8)
    "backend/app/services/clinical_cnv_kb_jobs.py": 94.0,  # (97.1)
    "backend/app/services/cnv_acmg_points.py": 94.0,  # (97.7)
    "backend/app/services/compound_het_phase.py": 95.0,  # (98.7)
    "backend/app/services/family_identifiers.py": 97.0,  # (100.0)
    "backend/app/services/family_variant_filters.py": 96.0,  # (99.3)
    "backend/app/services/family_variant_write_lock.py": 97.0,  # (100.0)
    "backend/app/services/genotypes.py": 97.0,  # (100.0)
    "backend/app/services/haplotype_lineage_service.py": 89.0,  # (92.7)
    "backend/app/services/hash_chain.py": 94.0,  # (97.7)
    "backend/app/services/mitochondrial_analysis.py": 72.0,  # (75.4)
    "backend/app/services/monarch_semsim.py": 65.0,  # (68.8)
    "backend/app/services/nipt_analysis.py": 94.0,  # (97.7)
    "backend/app/services/nipt_service.py": 92.0,  # (95.9)
    "backend/app/services/nipt_target_coverage.py": 93.0,  # (95.6)
    "backend/app/services/nipt_triage.py": 93.0,  # (96.8)
    "backend/app/services/qc_threshold_service.py": 83.0,  # (86.2)
    "backend/app/services/report_signout_service.py": 92.0,  # (95.7)
    "backend/app/services/review_pg_utils.py": 87.0,  # (90.2)
    "backend/app/services/sample_integrity_qc.py": 90.0,  # (93.4)
    "backend/app/services/sample_integrity_service.py": 91.0,  # (94.4) feeds the sign-out gate
    "backend/app/services/small_variant_review_tags.py": 63.0,  # (66.5)
    "backend/app/services/structural_variant_evidence.py": 74.0,  # (77.8)
    "backend/app/services/structural_variant_review_pg.py": 59.0,  # (62.2) CNV ACMG persistence
    "backend/app/services/sv_gene_index_service.py": 82.0,  # (85.0)
    "backend/app/services/variant_ranking_cache.py": 63.0,  # (66.7)
}

# The combined unit + smoke + e2e report (the ``coverage`` CI job), for the modules the unit
# job alone under-reports because real datastores exercise them.
COMBINED_GLOBAL_FLOOR = 80.0  # (83.4)
COMBINED_FLOORS: dict[str, float] = {
    "backend/app/services/annotation_manifest_service.py": 92.0,  # (95.6)
    "backend/app/services/clickhouse_family_variants.py": 82.0,  # (85.7)
    "backend/app/services/clickhouse_variant_storage.py": 92.0,  # (94.7)
    "backend/app/services/clinical_audit_service.py": 95.0,  # (98.4)
    "backend/app/services/family_package_datasets.py": 72.0,  # (75.8)
    "backend/app/services/family_package_import.py": 93.0,  # (96.0)
    "backend/app/services/family_package_jobs.py": 94.0,  # (97.2)
    "backend/app/services/family_package_registration.py": 81.0,  # (84.2)
    "backend/app/services/integrity_anchor_service.py": 79.0,  # (82.3)
    "backend/app/services/report_signout_service.py": 96.0,  # (99.5)
    "backend/app/services/small_variant_review_tags.py": 90.0,  # (93.1)
    "backend/app/services/structural_variant_evidence.py": 93.0,  # (96.0)
    "backend/app/services/structural_variant_review_pg.py": 69.0,  # (72.4) CNV ACMG persistence
    "backend/app/services/sv_gene_index_service.py": 92.0,  # (95.5)
    "backend/app/services/variant_ranking_cache.py": 84.0,  # (87.3)
}


def type_checked_modules(pyproject: str = "pyproject.toml") -> list[str]:
    """The modules the type-check gate lists: the clinical-critical set (AGENTS.md)."""
    with open(pyproject, "rb") as handle:
        return list(tomllib.load(handle)["tool"]["mypy"]["files"])


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

    unfloored = sorted(set(type_checked_modules()) - set(MODULE_FLOORS) - set(COMBINED_FLOORS))
    for module in unfloored:
        failures.append(f"{module}: type-checked as clinical-critical but has no coverage floor")
        print(f"[ERR] {module}: no coverage floor")

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
