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
# the `coverage` job's combined report (#678). A bracket dated 2026-11-03 is the coverage the
# test-suite review of that date measured, in a local replay of the unit, smoke and e2e jobs;
# its floor was raised to 3 points under it. The local replay reads at most what CI reads.

# Global floor for the whole backend/app package. Keep in step with --cov-fail-under in ci.yml
# (backend/tests/test_ci_workflows.py fails when the two differ).
GLOBAL_FLOOR = 74.0  # (77.6)

# Per-module floors (percent of lines covered) for clinical-critical modules. Every module
# the type-check gate lists ([tool.mypy] files in pyproject.toml) needs a floor here or in
# COMBINED_FLOORS; main() fails when one has neither.
MODULE_FLOORS: dict[str, float] = {
    # (97.3 measured locally when it was added: the import-worker process, which runs the
    # package imports without the API)
    "backend/app/import_worker.py": 94.0,
    "backend/app/services/acmg_points.py": 97.0,  # (100.0)
    "backend/app/services/annotation_manifest_service.py": 94.0,  # (97.1, 2026-11-03)
    "backend/app/services/assembly_scope.py": 97.0,  # (100.0)
    "backend/app/services/classification_drift_service.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/clickhouse_variant_storage.py": 95.0,  # (98.0, 2026-11-03; also floored combined)
    "backend/app/services/clinical_audit_service.py": 96.0,  # (99.7, 2026-11-03)
    "backend/app/services/clinical_cnv_kb_jobs.py": 96.0,  # (99.4, 2026-11-03)
    "backend/app/services/cnv_acmg_points.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/compound_het_phase.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/coverage_content.py": 97.0,  # (100.0, measured when added for TRACE-9)
    "backend/app/services/coverage_read_depth.py": 97.0,  # (100.0, measured when added for CLIN-46)
    "backend/app/services/family_identifiers.py": 97.0,  # (100.0)
    # (100.0 measured locally when it was added: the package fingerprint an import is queued
    # and checked with)
    "backend/app/services/family_package_fingerprint.py": 97.0,
    "backend/app/services/family_package_jobs.py": 94.0,  # (97.3, 2026-11-03; also floored combined)
    "backend/app/services/family_structure_audit.py": 95.0,  # (98.6, 2026-11-03; 96.7 when added for SEC-7)
    "backend/app/services/family_variant_filters.py": 96.0,  # (99.3)
    "backend/app/services/family_variant_write_lock.py": 97.0,  # (100.0)
    "backend/app/services/genotypes.py": 97.0,  # (100.0)
    "backend/app/services/haplotype_lineage_service.py": 91.0,  # (94.6, 2026-11-03)
    "backend/app/services/hash_chain.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/integrity_anchor_service.py": 97.0,  # (100.0, 2026-11-03; also floored combined)
    "backend/app/services/mitochondrial_analysis.py": 90.0,  # (93.0, 2026-11-03)
    "backend/app/services/monarch_semsim.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/nipt_analysis.py": 94.0,  # (97.7)
    # (100.0 measured locally when it was added: the NIPT quality checks' verdicts, which
    # the NIPT page shows and the signed record freezes)
    "backend/app/services/nipt_quality_checks.py": 97.0,
    "backend/app/services/nipt_service.py": 96.0,  # (99.0, 2026-11-03)
    "backend/app/services/nipt_target_coverage.py": 95.0,  # (98.6, 2026-11-03)
    "backend/app/services/nipt_triage.py": 96.0,  # (99.7, 2026-11-03)
    # (100.0 measured locally when it was added: the signed analysis record, the gene-panel
    # versions a family's searches applied)
    "backend/app/services/panel_use_audit.py": 97.0,
    # (100.0 measured locally when they were added: the PGT embryo calls the family page
    # shows and a sign-out freezes, REG-2)
    "backend/app/services/pgt_analysis.py": 97.0,
    "backend/app/services/pgt_embryo_calls.py": 97.0,
    # (100.0 measured locally when it was added: the phasing-QC thresholds and verdict of an
    # embryo call, the owner's decision of 2026-10-20)
    "backend/app/services/pgt_embryo_qc.py": 97.0,
    "backend/app/services/qc_threshold_service.py": 96.0,  # (99.5, 2026-11-03)
    # (97.6 measured locally when it was added: the repeat count-unit warning and QA's record)
    "backend/app/services/repeat_count_units.py": 96.0,  # (99.1, 2026-11-03)
    # (100.0 measured locally when it was added: the report code the EPD report quotes)
    "backend/app/services/report_code.py": 97.0,
    "backend/app/services/report_signout_service.py": 96.0,  # (99.9, 2026-11-03)
    "backend/app/services/review_pg_utils.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/sample_integrity_qc.py": 96.0,  # (99.1, 2026-11-03)
    "backend/app/services/sample_integrity_service.py": 95.0,  # (98.6, 2026-11-03) feeds the sign-out gate
    # (100.0 measured locally when it was added: the NIPT section of the signed analysis
    # record)
    "backend/app/services/signed_record_nipt.py": 97.0,
    # (100.0 measured locally when it was added: what the signed analysis record freezes of
    # what the analysis looked at)
    "backend/app/services/signed_record_sections.py": 97.0,
    "backend/app/services/small_variant_review_tags.py": 90.0,  # (93.8, 2026-11-03)
    # (100.0 measured locally when it was added, ENG-8: the stored-value readers the
    # mitochondrial, repeat and annotation-manifest services share)
    "backend/app/services/stored_values.py": 97.0,
    "backend/app/services/structural_variant_evidence.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/structural_variant_review_pg.py": 95.0,  # (98.3, 2026-11-03) CNV ACMG persistence
    # (100.0 measured locally when it was added: CLIN-47, the copy-loss rule of the SV
    # second hit and the SV ranking)
    "backend/app/services/sv_copy_change.py": 97.0,
    "backend/app/services/sv_gene_index_service.py": 96.0,  # (99.6, 2026-11-03)
    "backend/app/services/variant_ranking_cache.py": 86.0,  # (89.5, 2026-11-03)
}

# The combined unit + smoke + e2e report (the ``coverage`` CI job), for the modules the unit
# job alone under-reports because real datastores exercise them.
COMBINED_GLOBAL_FLOOR = 89.0  # (92.7, 2026-11-03)
COMBINED_FLOORS: dict[str, float] = {
    "backend/app/services/annotation_manifest_service.py": 95.0,  # (98.8, 2026-11-03)
    "backend/app/services/clickhouse_family_variants.py": 90.0,  # (93.9, 2026-11-03)
    "backend/app/services/clickhouse_variant_storage.py": 96.0,  # (99.2, 2026-11-03)
    "backend/app/services/clinical_audit_service.py": 96.0,  # (99.7, 2026-11-03)
    "backend/app/services/family_package_datasets.py": 83.0,  # (86.7, 2026-11-03)
    "backend/app/services/family_package_import.py": 93.0,  # (96.0)
    "backend/app/services/family_package_jobs.py": 96.0,  # (99.3, 2026-11-03)
    "backend/app/services/family_package_registration.py": 85.0,  # (88.6, 2026-11-03)
    # (95.4 measured locally when it was added: the reviews of repeat loci and Paraphase
    # regions, whose saves and record the e2e tests exercise)
    "backend/app/services/finding_review_pg.py": 93.0,  # (96.0, 2026-11-03)
    "backend/app/services/integrity_anchor_service.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/report_signout_service.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/small_variant_review_tags.py": 94.0,  # (97.9, 2026-11-03)
    "backend/app/services/structural_variant_evidence.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/structural_variant_review_pg.py": 95.0,  # (98.3, 2026-11-03) CNV ACMG persistence
    "backend/app/services/sv_gene_index_service.py": 97.0,  # (100.0, 2026-11-03)
    "backend/app/services/variant_ranking_cache.py": 97.0,  # (100.0, 2026-11-03)
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
