"""T1 end-to-end: import the golden-trio package through the real front door and
assert every stage produced its known-correct output.

Drives ``execute_family_package_import`` against real Postgres + ClickHouse (no
mocks of the pipeline), then reads results back through the same service-layer
queries the API serves. Expected outcomes are pinned in ``fixtures/golden_trio/
EXPECTED.yaml`` (the verification record).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"


@pytest.fixture(scope="module")
def expected() -> dict:
    return yaml.safe_load((_FIXTURE / "EXPECTED.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def facts(tmp_path_factory, request) -> dict:
    """Copy the committed fixture into an authorized temp root and run the import
    once for the whole module."""
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    root = tmp_path_factory.mktemp("golden") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    # The importer rejects paths outside FAMILY_IMPORT_ROOTS; authorize the temp root.
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    return _harness.run_async(lambda: _harness.import_golden_trio(root))


def test_import_completes_with_all_datasets_imported(facts, expected) -> None:
    assert facts["completed"] is True, facts
    assert facts["error"] is None, facts
    assert facts["family_id"] == expected["family_id"]
    for dataset, status in expected["dataset_status"].items():
        assert facts["dataset_status"].get(dataset) == status, (
            dataset,
            facts["dataset_status"].get(dataset),
            facts["dataset_message"].get(dataset),
        )


def test_pedigree_and_phenotypes_persisted(facts, expected) -> None:
    assert facts["n_samples"] == len(expected["sample_ids"])
    assert facts["n_members"] == len(expected["sample_ids"])
    assert facts["member_roles"] == expected["member_roles"]
    assert facts["n_hpo"] == len(expected["hpo_terms"])


def test_small_variants_ingested(facts, expected) -> None:
    assert facts["n_small_variants"] == expected["snv"]["count"]


def test_compound_het_pair_detected(facts, expected) -> None:
    genes = [g["gene"] for g in facts["comp_het_groups"]]
    assert expected["snv"]["compound_het_gene"] in genes, facts["comp_het_groups"]


def test_structural_variants_ingested(facts, expected) -> None:
    assert facts["n_structural_variants"] == expected["structural_variants"]["count"]


def test_snv_sv_compound_het_second_hit(facts, expected) -> None:
    hits = facts["brca2_sv_second_hits"]
    assert hits, "expected an SV second-hit overlay on the BRCA2 SNV"
    hit = hits[0]
    exp = expected["sv_second_hit"]
    assert hit["phase"] == exp["phase"], hit
    assert hit["phase_evidence"] == exp["phase_evidence"], hit
    assert hit["has_deletion"] == exp["has_deletion"], hit
    assert hit["deletion_unmasked"] == exp["deletion_unmasked"], hit


def test_repeat_expansion_pathogenic(facts, expected) -> None:
    proband = [r for r in facts["repeat_rows"] if r["sample_id"] == "PROBAND"]
    assert proband, facts["repeat_rows"]
    row = proband[0]
    assert row["status"] == expected["repeat_expansion"]["proband_status"]
    assert row["gene"] == expected["repeat_expansion"]["gene"]
    assert row["pathogenic_min"] == expected["repeat_expansion"]["pathogenic_min"]


def test_coverage_tracks_registered(facts, expected) -> None:
    # one interval-track source row per sample's coverage BED, summing to all bins
    assert facts["coverage_source_count"] == expected["coverage"]["sources"]
    assert facts["coverage_total_rows"] == expected["coverage"]["total_rows"]


def test_paraphase_results_persisted(facts, expected) -> None:
    assert facts["paraphase_genes"] == expected["paraphase_genes"]
