"""The package-validation command (scripts/validate_family_package.py, #678): it checks a
family-package folder without importing it, prints the result as JSON and exits 0 only
for a valid package."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "validate_family_package.py"
FIXTURES = REPO / "backend" / "tests" / "e2e" / "fixtures"


def _run(folder: Path, *, import_root: Path) -> tuple[int, dict]:
    env = {**os.environ, "APP_ENV": "test", "FAMILY_IMPORT_ROOTS": str(import_root)}
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(folder)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_a_valid_package_is_reported_valid() -> None:
    code, result = _run(FIXTURES / "golden_trio", import_root=FIXTURES)

    assert code == 0
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["family_id"] == "FAM_TRIO"
    assert result["sample_ids"] == ["FATHER", "MOTHER", "PROBAND"]


def test_a_folder_without_a_package_fails_with_its_reasons(tmp_path: Path) -> None:
    code, result = _run(tmp_path, import_root=tmp_path)

    assert code == 1
    assert result["valid"] is False
    assert result["errors"], "an invalid package names what is wrong"


def test_a_folder_outside_the_import_roots_is_refused(tmp_path: Path) -> None:
    code, result = _run(FIXTURES / "golden_trio", import_root=tmp_path)

    assert code == 1
    assert [error["code"] for error in result["errors"]] == ["package_folder_not_allowed"]
