"""The package files a validation read whole, remembered in Postgres (``package_file_reads``).

What one validation read whole is stored after it, and loaded before the next validation of the
folder: an unchanged file is then not read again. A folder's reads are loaded by its path, never
another folder's whose name begins the same. Synthetic files only; skipped unless
``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration

_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n"
    "1\t100\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
)


def test_a_file_read_whole_by_one_validation_is_not_read_by_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import family_package_common as common
    from backend.app.services import package_file_reads as reads

    folder, sibling = tmp_path / "FAM1", tmp_path / "FAM10"
    for each in (folder, sibling):
        each.mkdir()
        (each / "family.vcf").write_text(_VCF)
    made: list[str] = []
    read = common._read_package_vcf_with

    def counting(path: Path, *, label: str, reader: Any) -> Any:
        made.append(str(path))
        return read(path, label=label, reader=reader)

    monkeypatch.setattr(common, "_read_package_vcf_with", counting)

    async def _validate(at: Path) -> Any:
        remembered = await reads.load_remembered_reads(at)
        with reads.remembering(remembered):
            found = common.read_package_vcf_whole(at / "family.vcf", label="The SNV VCF", readers=["loader"])
        await reads.store_remembered_reads(remembered)
        return found

    async def _run() -> tuple[Any, Any, Any, dict[Any, Any]]:
        first = await _validate(folder)
        again = await _validate(folder)
        other = await _validate(sibling)
        return first, again, other, (await reads.load_remembered_reads(folder)).known

    first, again, other, known = run_closing_stores(_run, init_schema=True)
    # FAM1's file read once; FAM10's, whose name begins as FAM1's, read on its own.
    assert made == [str(folder / "family.vcf"), str(sibling / "family.vcf")]
    assert first == again == other == common.PackageVcfRead(columns=["PROBAND"], holds_a_line=True)
    assert list(known) == [(os.path.abspath(folder / "family.vcf"), "loader")]
